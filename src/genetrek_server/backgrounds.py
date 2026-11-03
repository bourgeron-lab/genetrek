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
      - name: All genes

The first entry is the default. An entry with no ``column`` is every gene. The
column is named in the YAML rather than in code, like a link placeholder in
``external_links.yaml``, so no identifier literal leaks into the view layer.

The file is optional, as ``categories.yaml`` is: without one there is a single
background, every gene, which is what enrichment always used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl
import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

ALL_GENES = "All genes"


@dataclass(frozen=True)
class Background:
    """One universe: every gene, or the genes where ``column == equals``."""

    name: str
    column: str | None = None
    equals: Any = None
    description: str = ""

    def expr(self) -> pl.Expr:
        """The rows this background keeps."""
        if self.column is None:
            return pl.lit(value=True)
        matches: pl.Expr = pl.col(self.column) == self.equals
        return matches.fill_null(value=False)

    def describe(self) -> str:
        """How the background is defined, for a methods text."""
        if self.column is None:
            return "all genes in the database"
        return f"genes whose {self.column} is {self.equals!r}"


EVERY_GENE = Background(ALL_GENES)


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
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            problems.append(f"entry {index + 1} needs a name")
            continue
        name = str(entry["name"]).strip()
        if name in seen:
            problems.append(f"{name!r} is declared twice")
            continue
        if unknown := sorted(set(entry) - {"name", "column", "equals", "description"}):
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
        seen.add(name)
        backgrounds.append(
            Background(
                name=name,
                column=column,
                equals=entry.get("equals"),
                description=str(entry.get("description") or ""),
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
    covariate splits the background into quantile ``bins`` of ``column`` --
    computed within the chosen background -- plus one stratum for genes with no
    value, and the comparison is repeated as a Mantel-Haenszel analysis across
    them. Declared in ``backgrounds.yaml``::

        covariates:
          - name: LOEUF decile
            column: loeuf
            bins: 10
    """

    name: str
    column: str
    bins: int = 10
    description: str = ""

    def describe(self) -> str:
        """How the strata are built, for a methods text."""
        return (
            f"{self.bins} quantile bins of {self.column} within the analysed "
            f"universe (the background, less any gene a set did not measure), "
            f"plus one stratum for genes without a value"
        )


#: Fewest and most bins a covariate may declare. One bin is no stratification;
#: past 50 the strata get too thin for the Mantel-Haenszel sums to mean much.
_MIN_BINS, _MAX_BINS = 2, 50


def parse_covariates(
    raw: object,
    columns: Iterable[str],
) -> tuple[list[Covariate], list[str]]:
    """Read the optional ``covariates`` list; bad entries are dropped with a reason."""
    problems: list[str] = []
    available = set(columns)
    entries = raw.get("covariates") if isinstance(raw, dict) else None
    if entries is None:
        return [], problems
    if not isinstance(entries, list):
        return [], ["'covariates' must be a list"]
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
        if unknown := sorted(set(entry) - {"name", "column", "bins", "description"}):
            problems.append(f"covariate {name!r} has unknown key(s) {unknown}")
            continue
        column = entry.get("column")
        if column not in available:
            problems.append(
                f"covariate {name!r} uses column {column!r}, which is not in the data",
            )
            continue
        bins = entry.get("bins", 10)
        if not isinstance(bins, int) or not _MIN_BINS <= bins <= _MAX_BINS:
            problems.append(
                f"covariate {name!r}: bins must be an integer from {_MIN_BINS} "
                f"to {_MAX_BINS}",
            )
            continue
        seen.add(name)
        covariates.append(
            Covariate(name, str(column), bins, str(entry.get("description") or "")),
        )
    return covariates, problems


def load_covariates(
    path: Path,
    columns: Iterable[str],
) -> tuple[list[Covariate], list[str]]:
    """Load the ``covariates`` of ``backgrounds.yaml``, or none without the file."""
    if not path.is_file():
        return [], []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        # Already reported by load_backgrounds, which reads the same file.
        return [], []
    return parse_covariates(raw, columns)
