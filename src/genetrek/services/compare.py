"""Comparing gene sets: background restriction, pairwise statistics, regions.

Pure: sets in, numbers out. No UI, no I/O. The Compare view builds
:class:`GeneSet` objects from the current results, database gene lists and the
visitor's own lists; everything after that happens here.

Every set is first restricted to the background (the universe), because a
gene outside the universe cannot take part in a 2x2 table. How many genes each
set lost to that is kept and reported -- a list of non-coding RNAs compared
"within protein-coding genes" should say it is mostly gone, not quietly shrink.

Two kinds of overlap are expected **by construction** and are flagged rather
than presented as findings: a derived column against one of the columns it is
derived from, directly or through others, and two versions of the same
gene-list family. Their p-values
are still computed, because hiding a number is worse than labelling it.
"""

from __future__ import annotations

import itertools
import math
import string
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

import polars as pl

from genetrek.services.enrichment import fisher_2x2
from genetrek.services.odds import mantel_haenszel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from genetrek.schema import FieldMeta

#: Where a set comes from, which decides what the "sets" table can say about it.
SetKind = Literal["results", "database", "my_list"]

#: Fewest and most sets one comparison takes. Two is the least that compares
#: anything; past twelve an UpSet plot has more possible intersections (4,095)
#: than anyone reads, and the pairwise table has 66 rows.
MIN_SETS = 2
MAX_SETS = 12
#: Largest comparison drawn as a Venn diagram. Five sets is 31 regions, the
#: most a fixed ellipse layout shows legibly.
MAX_VENN_SETS = 5
#: Share of the background that coverage may remove before the comparison
#: warns: past this, one low-coverage set has reshaped every pair.
COVERAGE_WARNING_SHARE = 0.2


@dataclass(frozen=True)
class GeneSet:
    """One set to compare.

    Attributes:
        id: Stable within a tab: ``results``, ``db:<column>`` or ``list:<id>``.
        label: Short label used in figures and table headers. Editable.
        name: Full name -- a database list's ``fullname``, a list's label.
        kind: Where it comes from.
        genes: Key values, as strings, before any background restriction.
        column: The database column, for ``kind == "database"``.
        details: Free provenance for the sets table (version, DOI, created...).
        measured: The genes the set's source measured, when that is not every
            gene -- the non-null rows of a database column with nulls ("not
            measured"). ``None`` means full coverage: a curated list, or any
            list of genes a visitor supplies.
    """

    id: str
    label: str
    name: str
    kind: SetKind
    genes: frozenset[str]
    column: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)
    measured: frozenset[str] | None = None


@dataclass(frozen=True)
class AdjustedStat:
    """A pair re-tested within strata of a covariate (Mantel-Haenszel)."""

    odds_ratio: float
    ci_lower: float
    ci_upper: float
    p_value: float
    fdr: float
    #: Strata that held at least two genes and so contributed.
    strata: int


@dataclass(frozen=True)
class PairStat:
    """Two sets measured against each other within one universe."""

    a: int  # index into Comparison.sets
    b: int
    n_a: int
    n_b: int
    overlap: int
    expected: float
    fold: float
    odds_ratio: float
    ci_lower: float
    ci_upper: float
    p_value: float
    fdr: float
    jaccard: float
    #: |A n B| / min(|A|, |B|): 1.0 when the smaller set sits wholly inside
    #: the larger, whatever their sizes -- which Jaccard cannot say.
    overlap_coefficient: float
    #: Why the overlap is expected by construction, or ``""``.
    construction: str = ""
    #: The same pair stratified by the comparison's covariate, if it has one.
    adjusted: AdjustedStat | None = None

    @property
    def enriched(self) -> bool:
        """Whether the sets share more genes than expected (direction of the test)."""
        return self.fold > 1


@dataclass(frozen=True)
class Region:
    """Genes in exactly these sets and no other: one UpSet bar, one Venn area."""

    members: tuple[int, ...]
    genes: frozenset[str]

    @property
    def size(self) -> int:
        return len(self.genes)


@dataclass(frozen=True)
class Comparison:
    """Everything computed for one set of sets in one universe.

    The universe is the background *intersected with* the genes measured by
    every set that does not cover the whole background: one joint universe per
    comparison, so every pair, every region and every stratum count the same
    genes. ``background_size`` is the background before that restriction.
    """

    #: The sets, restricted to the analysed universe.
    sets: tuple[GeneSet, ...]
    #: Genes each set had before the restriction, in ``sets`` order.
    original_sizes: tuple[int, ...]
    background: str
    #: Genes in the analysed universe: background and measured by every set.
    universe_size: int
    pairs: tuple[PairStat, ...]
    #: Non-empty exclusive regions, largest first.
    regions: tuple[Region, ...]
    #: The sets as given, before the background restriction: what reproduces
    #: this comparison (``request.json``, ``POST /api/compare``).
    inputs: tuple[GeneSet, ...] = ()
    #: The covariate the adjusted results are stratified by, or ``""``.
    covariate: str = ""
    #: Genes in the background, before the coverage restriction.
    background_size: int = 0
    #: Per set: genes outside the background.
    outside_background: tuple[int, ...] = ()
    #: Per set: genes inside the background but not measured by some set.
    not_measured: tuple[int, ...] = ()
    #: Labels of the sets whose coverage shrank the universe.
    restricted_by: tuple[str, ...] = ()

    @property
    def coverage_loss(self) -> int:
        """Background genes left out because some set did not measure them."""
        return self.background_size - self.universe_size

    @property
    def coverage_warning(self) -> str:
        """A sentence when coverage removed a large share of the background."""
        if not self.background_size or (
            self.coverage_loss / self.background_size <= COVERAGE_WARNING_SHARE
        ):
            return ""
        share = 100 * self.coverage_loss / self.background_size
        return (
            f"Only {self.universe_size:,} of the {self.background_size:,} genes in "
            f"{self.background!r} were measured by every set ({share:.0f}% left out, "
            f"because of {', '.join(self.restricted_by)}). Every pair is tested in "
            f"that smaller universe."
        )

    @property
    def dropped(self) -> tuple[int, ...]:
        """Genes each set lost: outside the background or not measured."""
        return tuple(
            before - len(s.genes)
            for before, s in zip(self.original_sizes, self.sets, strict=True)
        )

    @property
    def union(self) -> frozenset[str]:
        return frozenset().union(*(s.genes for s in self.sets))

    def region(self, members: Sequence[int]) -> Region | None:
        wanted = tuple(sorted(members))
        return next((r for r in self.regions if r.members == wanted), None)

    def region_label(self, members: Sequence[int], *, short: bool = True) -> str:
        """``A & C only`` style label for a region."""
        names = [(self.sets[i].label if short else self.sets[i].name) for i in members]
        if len(members) == len(self.sets):
            return " & ".join(names) + " (all)"
        return " & ".join(names) + " only"


def letters(n: int) -> list[str]:
    """``A``, ``B``, ... -- the default short labels."""
    return list(string.ascii_uppercase[:n])


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, in input order, capped at 1.

    NaN inputs stay NaN and do not count towards the number of tests.
    """
    indexed = [(p, i) for i, p in enumerate(p_values) if not math.isnan(p)]
    m = len(indexed)
    adjusted = [float("nan")] * len(p_values)
    running = 1.0
    for rank, (p, i) in reversed(list(enumerate(sorted(indexed), start=1))):
        running = min(running, p * m / rank)
        adjusted[i] = min(running, 1.0)
    return adjusted


def exclusive_regions(sets: Sequence[frozenset[str]]) -> list[Region]:
    """Partition the union by membership signature. Largest region first.

    Ties go to fewer members, then to set order, so the layout is stable.
    """
    signature: dict[str, tuple[int, ...]] = {}
    for index, genes in enumerate(sets):
        for gene in genes:
            signature[gene] = (*signature.get(gene, ()), index)
    grouped: dict[tuple[int, ...], set[str]] = {}
    for gene, members in signature.items():
        grouped.setdefault(members, set()).add(gene)
    regions = [Region(members, frozenset(genes)) for members, genes in grouped.items()]
    return sorted(regions, key=lambda r: (-r.size, len(r.members), r.members))


def _ancestors(column: str, fields: Mapping[str, FieldMeta]) -> dict[str, list[str]]:
    """Every column ``column`` is derived from, directly or through others.

    Maps each ancestor to the columns in between, empty for a direct parent.
    Breadth-first, so the path given is a shortest one; a column seen twice is
    not walked again, so a malformed cycle cannot loop.
    """
    found: dict[str, list[str]] = {}
    queue: list[tuple[str, list[str]]] = [(column, [])]
    while queue:
        current, via = queue.pop(0)
        meta = fields.get(current)
        if meta is None:
            continue
        for parent in meta.derived_from:
            if parent != column and parent not in found:
                found[parent] = via
                queue.append((parent, [*via, parent]))
    return found


def construction_reasons(
    sets: Sequence[GeneSet],
    fields: Mapping[str, FieldMeta],
    aliases: Mapping[str, str],
) -> dict[tuple[int, int], str]:
    """Pairs whose overlap is expected by construction, and why.

    Only database lists can be related this way -- the metadata says nothing
    about a visitor's list or the current results.

    A list is related to everything it is derived from, directly or not:
    ``hc_ndd_v7`` is a union over ``autism_genes_v7``, which is a union over
    SFARI, so ``hc_ndd_v7`` against SFARI overlaps by construction too.

    ``aliases`` is alias -> column. A family is the alias's current column
    plus every column named ``<alias>_...``, the same rule
    :func:`genetrek.services.versions.version_families` uses, never a version
    regex.
    """
    family: dict[str, str] = {}
    for alias, current in aliases.items():
        family[current] = alias
        for column in fields:
            if column.startswith(f"{alias}_"):
                family.setdefault(column, alias)

    lineage = {
        s.column: _ancestors(s.column, fields) for s in sets if s.column is not None
    }

    def derived(child: GeneSet, parent: GeneSet, via: list[str]) -> str:
        reason = f"{child.label} is derived from {parent.label}"
        if via:
            through = ", ".join(fields[c].fullname if c in fields else c for c in via)
            reason += f" (through {through})"
        return reason

    reasons: dict[tuple[int, int], str] = {}
    for i, j in itertools.combinations(range(len(sets)), 2):
        a, b = sets[i].column, sets[j].column
        if a is None or b is None:
            continue
        if (via := lineage[b].get(a)) is not None:
            reasons[(i, j)] = derived(sets[j], sets[i], via)
        elif (via := lineage[a].get(b)) is not None:
            reasons[(i, j)] = derived(sets[i], sets[j], via)
        elif a in family and family[a] == family.get(b):
            reasons[(i, j)] = f"two versions of the {family[a]} gene list"
    return reasons


def effective_universe(
    sets: Sequence[GeneSet],
    background: frozenset[str],
) -> frozenset[str]:
    """The background, less every gene some set did not measure.

    A gene a set never measured cannot be counted as "not in" that set: it is
    unknown. So a comparison runs on the genes every set measured -- one joint
    universe, not one per pair, which keeps the Venn/UpSet counts and every
    statistic on the same genes.
    """
    universe = background
    for gene_set in sets:
        if gene_set.measured is not None:
            universe &= gene_set.measured
    return universe


def strata_for(
    frame: pl.DataFrame,
    key: str,
    column: str,
    bins: int,
) -> dict[str, str]:
    """Gene key -> stratum: ``bins`` quantile bins of ``column``, plus ``"none"``.

    ``frame`` must already be restricted to the analysed universe (see
    :func:`effective_universe`): the quantiles are the universe's own, so
    "decile 1" means the most constrained tenth of the genes actually compared,
    and the strata stay balanced when coverage shrinks the background. Ties are kept in one bin, so a heavily tied column
    may yield fewer bins. Genes with no value form their own stratum rather than
    being dropped -- 1,490 of the protein-coding genes have no LOEUF.
    """
    values = frame.select(
        pl.col(key).cast(pl.String).alias("_key"),
        pl.col(column).cast(pl.Float64).alias("_value"),
    )
    known = values.filter(
        pl.col("_value").is_not_null() & pl.col("_value").is_not_nan()
    )
    labelled = known.with_columns(
        pl.col("_value")
        .qcut(bins, labels=[f"q{i + 1}" for i in range(bins)], allow_duplicates=True)
        .cast(pl.String)
        .alias("_stratum"),
    )
    strata = dict(zip(labelled["_key"], labelled["_stratum"], strict=True))
    for gene in values["_key"]:
        strata.setdefault(gene, "none")
    return strata


def _adjusted(
    a_genes: frozenset[str],
    b_genes: frozenset[str],
    groups: Mapping[str, frozenset[str]],
) -> tuple[float, float, float, float, int]:
    tables = []
    for members in groups.values():
        a_k, b_k = a_genes & members, b_genes & members
        both = len(a_k & b_k)
        n_k = len(members)
        tables.append(
            (both, len(a_k) - both, len(b_k) - both, n_k - len(a_k | b_k)),
        )
    result = mantel_haenszel(tables)
    ratio = result.odds_ratio
    return ratio.estimate, ratio.ci_lower, ratio.ci_upper, result.p_value, result.strata


def compare(  # noqa: PLR0913
    sets: Sequence[GeneSet],
    universe: frozenset[str],
    *,
    background: str,
    construction: Mapping[tuple[int, int], str] | None = None,
    strata: Mapping[str, str] | None = None,
    covariate: str = "",
) -> Comparison:
    """Restrict every set to the analysed universe and measure them.

    ``universe`` is the background. The analysed universe is the background
    less the genes some set did not measure (:func:`effective_universe`).

    With ``strata`` (gene key -> stratum, see :func:`strata_for`) every pair is
    also tested within strata, and FDR is applied separately to the crude and
    to the adjusted p-values: they answer different questions. The strata must
    be computed within the analysed universe, not the background.

    Raises:
        ValueError: Fewer than :data:`MIN_SETS` or more than :data:`MAX_SETS`.
    """
    if not MIN_SETS <= len(sets) <= MAX_SETS:
        msg = f"compare between {MIN_SETS} and {MAX_SETS} sets, not {len(sets)}"
        raise ValueError(msg)
    reasons = construction or {}
    background_genes = universe
    universe = effective_universe(sets, background_genes)
    original = tuple(len(s.genes) for s in sets)
    restricted = tuple(replace(s, genes=s.genes & universe) for s in sets)
    restricted_by = tuple(
        s.label
        for s in sets
        if s.measured is not None and not background_genes <= s.measured
    )
    n = len(universe)
    groups: dict[str, frozenset[str]] = {}
    if strata:
        grouped: dict[str, set[str]] = {}
        for gene in universe:
            grouped.setdefault(strata.get(gene, "none"), set()).add(gene)
        groups = {name: frozenset(members) for name, members in grouped.items()}

    raw: list[dict] = []
    adjusted_raw: list[tuple[float, float, float, float, int]] = []
    for i, j in itertools.combinations(range(len(restricted)), 2):
        a_genes, b_genes = restricted[i].genes, restricted[j].genes
        n_a, n_b = len(a_genes), len(b_genes)
        overlap = len(a_genes & b_genes)
        union = n_a + n_b - overlap
        expected = n_a * n_b / n if n else float("nan")
        fisher = fisher_2x2(overlap, n_a - overlap, n_b - overlap, n - union)
        raw.append(
            {
                "a": i,
                "b": j,
                "n_a": n_a,
                "n_b": n_b,
                "overlap": overlap,
                "expected": expected,
                "fold": overlap / expected if expected else float("nan"),
                "odds_ratio": fisher.odds_ratio,
                "ci_lower": fisher.ci_lower,
                "ci_upper": fisher.ci_upper,
                "p_value": fisher.p_value,
                "jaccard": overlap / union if union else float("nan"),
                "overlap_coefficient": (
                    overlap / min(n_a, n_b) if min(n_a, n_b) else float("nan")
                ),
                "construction": reasons.get((i, j), ""),
            },
        )
        if groups:
            adjusted_raw.append(_adjusted(a_genes, b_genes, groups))
    fdrs = benjamini_hochberg([r["p_value"] for r in raw])
    adjusted_fdrs = benjamini_hochberg([r[3] for r in adjusted_raw])
    adjusted = [
        AdjustedStat(est, lo, hi, p, fdr, used)
        for (est, lo, hi, p, used), fdr in zip(adjusted_raw, adjusted_fdrs, strict=True)
    ]
    pairs = tuple(
        PairStat(**r, fdr=fdr, adjusted=adjusted[k] if adjusted else None)
        for k, (r, fdr) in enumerate(zip(raw, fdrs, strict=True))
    )
    return Comparison(
        sets=restricted,
        original_sizes=original,
        background=background,
        universe_size=n,
        pairs=pairs,
        regions=tuple(exclusive_regions([s.genes for s in restricted])),
        inputs=tuple(sets),
        covariate=covariate if groups else "",
        background_size=len(background_genes),
        outside_background=tuple(len(s.genes - background_genes) for s in sets),
        not_measured=tuple(len((s.genes & background_genes) - universe) for s in sets),
        restricted_by=restricted_by,
    )


def measured_keys(
    frame: pl.DataFrame,
    column: str,
    key: str,
) -> frozenset[str] | None:
    """The keys of the genes ``column`` measured, or ``None`` if it has no nulls.

    ``None`` rather than every key, so a fully covered column costs nothing and
    is never mistaken for one that restricts the universe.
    """
    series = frame[column]
    if not series.null_count():
        return None
    return keys_where(frame.filter(series.is_not_null())[key])


def keys_where(values: Iterable[object]) -> frozenset[str]:
    """Key values as the strings sets are compared on."""
    return frozenset(str(v) for v in values if v is not None)
