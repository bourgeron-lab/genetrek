"""Statistical backgrounds: which genes a set comparison counts as "all genes".

Every overlap test needs a universe, and the answer changes with it: two
protein-coding lists look far more enriched against all 45,083 rows -- 14,671
of them pseudogenes -- than against the 19,297 protein-coding genes they were
drawn from. So the universe is chosen explicitly, from a short list declared in
``config/backgrounds.yaml``::

    backgrounds:
      - name: Protein-coding genes
        column: hgnc_locus_group
        equals: protein-coding gene
      - name: Brain-expressed protein-coding genes
        where:
          - {column: hgnc_locus_group, equals: protein-coding gene}
          - {column: gtex_v11_brain_expressed, equals: true}
      - name: All genes

The first entry is the default. An entry with no condition is every gene; with
several (``where``), a gene must meet all of them. The columns are named in the
YAML rather than in code, like a link placeholder in ``external_links.yaml``, so
no identifier literal leaks into the view layer.

Those are the *presets*. A comparison can also run within any database gene list
(:func:`gene_list_background`) or within genes a visitor supplies
(:func:`user_background`) -- a My list, the current results, a panel's genes.

The file is optional, as ``categories.yaml`` is: without one there is a single
background, every gene, which is what enrichment always used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import polars as pl
import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

ALL_GENES = "All genes"

#: Fewest genes a supplied background may have: below it, every stratum and
#: most 2x2 cells are near empty and the tests mean little.
MIN_USER_BACKGROUND = 200
#: Below this a supplied background is accepted with a warning.
SMALL_USER_BACKGROUND = 1000

BackgroundKind = Literal["preset", "gene_list", "user"]


@dataclass(frozen=True)
class Condition:
    """One test a gene must pass to be in a preset: ``column == equals``."""

    column: str
    equals: Any


@dataclass(frozen=True)
class Background:
    """One universe.

    A preset keeps the genes meeting its conditions (``column == equals``,
    and every ``where`` condition); with none it is every gene. A gene list or
    a visitor's genes are given as ``genes``, the primary-key values.
    """

    name: str
    column: str | None = None
    equals: Any = None
    description: str = ""
    where: tuple[Condition, ...] = ()
    genes: frozenset[str] | None = None
    kind: BackgroundKind = "preset"

    @property
    def conditions(self) -> tuple[Condition, ...]:
        """Every test a gene must pass, the ``column``/``equals`` shorthand first."""
        first = (Condition(self.column, self.equals),) if self.column else ()
        return (*first, *self.where)

    @property
    def columns(self) -> tuple[str, ...]:
        """The columns the conditions read."""
        return tuple(c.column for c in self.conditions)

    def expr(self, key: str | None = None) -> pl.Expr:
        """The rows this background keeps. ``key`` is needed for given genes."""
        if self.genes is not None:
            if key is None:
                msg = f"{self.name!r} is given as genes; expr() needs the key column"
                raise ValueError(msg)
            return pl.col(key).cast(pl.String).is_in(sorted(self.genes))
        kept: pl.Expr = pl.lit(value=True)
        for condition in self.conditions:
            kept = kept & (pl.col(condition.column) == condition.equals).fill_null(
                value=False
            )
        return kept

    def describe(self) -> str:
        """How the background is defined, for a methods text."""
        if self.kind == "user":
            size = len(self.genes or ())
            return f"the user-supplied gene list “{self.name}” ({size:,} genes)"
        if self.kind == "gene_list":
            return f"the genes of the database gene list “{self.name}”"
        if not self.conditions:
            return "all genes in the database"
        return "genes whose " + " and ".join(
            f"{c.column} is {c.equals!r}" for c in self.conditions
        )


EVERY_GENE = Background(ALL_GENES)


def gene_list_background(name: str, genes: frozenset[str]) -> Background:
    """A database gene list as the universe: its ``True`` genes only.

    Null is "not measured", never membership, so it never enters the universe.
    """
    return Background(name, genes=genes, kind="gene_list")


def user_background(label: str, genes: frozenset[str]) -> Background:
    """Genes a visitor supplied as the universe.

    Raises:
        ValueError: With fewer than :data:`MIN_USER_BACKGROUND` genes.
    """
    if len(genes) < MIN_USER_BACKGROUND:
        msg = (
            f"a background needs at least {MIN_USER_BACKGROUND} genes; "
            f"{label!r} has {len(genes)}"
        )
        raise ValueError(msg)
    return Background(label, genes=genes, kind="user")


def background_warning(background: Background) -> str:
    """A sentence when a supplied background is small enough to weaken the tests."""
    size = len(background.genes or ())
    if background.kind != "user" or size >= SMALL_USER_BACKGROUND:
        return ""
    return (
        f"The background “{background.name}” has only {size:,} genes: "
        f"expect wide confidence intervals, and thin covariate strata."
    )


def _conditions(
    name: str,
    entry: Mapping[str, Any],
    available: set[str],
) -> tuple[tuple[Condition, ...], str]:
    """The ``where`` list of an entry, or a problem."""
    raw = entry.get("where", [])
    if not isinstance(raw, list):
        return (), f"{name!r}: 'where' must be a list of {{column, equals}}"
    conditions: list[Condition] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"column", "equals"}:
            return (), f"{name!r}: each 'where' item needs exactly column and equals"
        if item["column"] not in available:
            return (), (
                f"{name!r} uses column {item['column']!r}, which is not in the data"
            )
        conditions.append(Condition(str(item["column"]), item["equals"]))
    return tuple(conditions), ""


def parse_backgrounds(
    raw: object,
    columns: Iterable[str],
) -> tuple[list[Background], list[str]]:
    """Read the YAML payload into backgrounds, and list what was wrong with it.

    A bad entry is dropped with a problem rather than failing the whole file:
    a background is an analysis default, not something the site must refuse to
    start over. An empty or absent file yields only :data:`EVERY_GENE`.
    """
    problems: list[str] = []
    available = set(columns)
    entries = raw.get("backgrounds") if isinstance(raw, dict) else None
    if raw is not None and not isinstance(entries, list):
        problems.append("expected a top-level 'backgrounds' list")
        entries = []
    backgrounds: list[Background] = []
    seen: set[str] = set()
    allowed = {"name", "column", "equals", "where", "description"}
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            problems.append(f"entry {index + 1} needs a name")
            continue
        name = str(entry["name"]).strip()
        if name in seen:
            problems.append(f"{name!r} is declared twice")
            continue
        if unknown := sorted(set(entry) - allowed):
            problems.append(f"{name!r} has unknown key(s) {unknown}")
            continue
        column = entry.get("column")
        if column is not None and column not in available:
            problems.append(
                f"{name!r} uses column {column!r}, which is not in the data"
            )
            continue
        if column is not None and "equals" not in entry:
            problems.append(f"{name!r} names a column but no 'equals' value")
            continue
        where, problem = _conditions(name, entry, available)
        if problem:
            problems.append(problem)
            continue
        seen.add(name)
        backgrounds.append(
            Background(
                name=name,
                column=column,
                equals=entry.get("equals"),
                description=str(entry.get("description") or ""),
                where=where,
            ),
        )
    if not backgrounds:
        backgrounds.append(EVERY_GENE)
    return backgrounds, problems


def load_backgrounds(
    path: Path,
    columns: Iterable[str],
) -> tuple[list[Background], list[str]]:
    """Load ``backgrounds.yaml``, or the single every-gene background without one."""
    if not path.is_file():
        return [EVERY_GENE], []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [EVERY_GENE], [f"not valid YAML: {exc}"]
    return parse_backgrounds(raw, columns)


# -- covariates -----------------------------------------------------------------


@dataclass(frozen=True)
class Covariate:
    """A gene property to stratify comparisons by, so it cannot drive the result.

    NDD gene lists are dominated by constrained genes, so almost any brain-
    related list looks enriched in them against all protein-coding genes. A
    covariate splits the background into quantile ``bins`` of a gene property
    -- computed within the chosen background -- plus one stratum for genes with
    no value, and the comparison is repeated as a Mantel-Haenszel analysis
    across them. Declared in ``backgrounds.yaml``::

        covariates:
          - name: LOEUF decile
            column: loeuf
            bins: 10
          - name: Gene length decile
            span: GRCh38          # end - start of the assembly's coordinates
            bins: 10
        combinations:
          - [LOEUF decile, Gene length decile]

    The property is a column, or with ``span`` the gene's length in an
    assembly -- read from the coordinate columns the assembly's role names, so
    no new column is stored. Crossed with another covariate (a declared
    combination), each uses ``joint_bins`` instead, so the cross stays a few
    dozen strata rather than a hundred.
    """

    name: str
    column: str = ""
    bins: int = 10
    description: str = ""
    span: str = ""
    joint_bins: int = 5
    #: For ``span``: the assembly's (start, end) columns, resolved when parsed.
    span_columns: tuple[str, str] | None = None

    def expr(self) -> pl.Expr:
        """The gene property, as a float."""
        if self.span_columns is not None:
            start, end = self.span_columns
            return pl.col(end).cast(pl.Float64) - pl.col(start).cast(pl.Float64)
        return pl.col(self.column).cast(pl.Float64)

    @property
    def what(self) -> str:
        """The property in words: the column, or the gene length."""
        return f"gene length ({self.span})" if self.span else self.column

    def describe(self, *, bins: int | None = None) -> str:
        """How the strata are built, for a methods text."""
        return (
            f"{bins or self.bins} quantile bins of {self.what} within the analysed "
            f"universe (the background, less any gene a set did not measure), "
            f"plus one stratum for genes without a value"
        )


#: Fewest and most bins a covariate may declare. One bin is no stratification;
#: past 50 the strata get too thin for the Mantel-Haenszel sums to mean much.
_MIN_BINS, _MAX_BINS = 2, 50

#: Most covariates one comparison is stratified by at once.
MAX_JOINT = 2


def _bins(entry: Mapping[str, Any], key: str, default: int) -> int | None:
    value = entry.get(key, default)
    if not isinstance(value, int) or not _MIN_BINS <= value <= _MAX_BINS:
        return None
    return value


def parse_covariates(
    raw: object,
    columns: Iterable[str],
    assemblies: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[list[Covariate], list[str]]:
    """Read the optional ``covariates`` list; bad entries are dropped with a reason.

    ``assemblies`` maps an assembly name to its (start, end) columns, for a
    ``span`` covariate; without it a ``span`` entry is dropped.
    """
    problems: list[str] = []
    available = set(columns)
    entries = raw.get("covariates") if isinstance(raw, dict) else None
    if entries is None:
        return [], problems
    if not isinstance(entries, list):
        return [], ["'covariates' must be a list"]
    allowed = {"name", "column", "span", "bins", "joint_bins", "description"}
    covariates: list[Covariate] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            problems.append(f"covariate {index + 1} needs a name")
            continue
        name = str(entry["name"]).strip()
        if name in seen:
            problems.append(f"covariate {name!r} is declared twice")
            continue
        if unknown := sorted(set(entry) - allowed):
            problems.append(f"covariate {name!r} has unknown key(s) {unknown}")
            continue
        column, span = entry.get("column"), entry.get("span")
        if (column is None) == (span is None):
            problems.append(f"covariate {name!r} needs exactly one of column, span")
            continue
        span_columns: tuple[str, str] | None = None
        if span is not None:
            span_columns = (assemblies or {}).get(str(span))
            if span_columns is None:
                problems.append(
                    f"covariate {name!r} spans assembly {span!r}, which has no "
                    f"coordinate columns",
                )
                continue
        elif column not in available:
            problems.append(
                f"covariate {name!r} uses column {column!r}, which is not in the data",
            )
            continue
        bins, joint = _bins(entry, "bins", 10), _bins(entry, "joint_bins", 5)
        if bins is None or joint is None:
            problems.append(
                f"covariate {name!r}: bins and joint_bins must be integers from "
                f"{_MIN_BINS} to {_MAX_BINS}",
            )
            continue
        seen.add(name)
        covariates.append(
            Covariate(
                name,
                str(column or ""),
                bins,
                str(entry.get("description") or ""),
                span=str(span or ""),
                joint_bins=joint,
                span_columns=span_columns,
            ),
        )
    return covariates, problems


def parse_combinations(
    raw: object,
    covariates: Iterable[Covariate],
) -> tuple[list[tuple[str, ...]], list[str]]:
    """The declared covariate pairs the Compare view offers, and what was wrong."""
    entries = raw.get("combinations") if isinstance(raw, dict) else None
    if entries is None:
        return [], []
    if not isinstance(entries, list):
        return [], ["'combinations' must be a list of covariate-name lists"]
    known = {c.name for c in covariates}
    combinations: list[tuple[str, ...]] = []
    problems: list[str] = []
    for entry in entries:
        names = tuple(str(n) for n in entry) if isinstance(entry, list) else ()
        if len(names) != MAX_JOINT or len(set(names)) != MAX_JOINT:
            problems.append(f"combination {entry!r} must name {MAX_JOINT} covariates")
            continue
        if unknown := [n for n in names if n not in known]:
            problems.append(
                f"combination {entry!r} names unknown covariate(s) {unknown}"
            )
            continue
        combinations.append(names)
    return combinations, problems


def _read(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        # Already reported by load_backgrounds, which reads the same file.
        return None


def load_covariates(
    path: Path,
    columns: Iterable[str],
    assemblies: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[list[Covariate], list[str]]:
    """Load the ``covariates`` of ``backgrounds.yaml``, or none without the file."""
    raw = _read(path)
    if raw is None:
        return [], []
    return parse_covariates(raw, columns, assemblies)


def load_combinations(
    path: Path,
    covariates: Iterable[Covariate],
) -> tuple[list[tuple[str, ...]], list[str]]:
    """Load the ``combinations`` of ``backgrounds.yaml``, or none without the file."""
    raw = _read(path)
    if raw is None:
        return [], []
    return parse_combinations(raw, covariates)
