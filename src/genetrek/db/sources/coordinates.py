"""Assemble per-assembly gene coordinates from GENCODE, with an NCBI fallback.

Two steps per assembly, in this order, and no heuristics:

1. join the stored Ensembl ID against that assembly's GENCODE table;
2. for whatever is left and has an NCBI gene ID, take NCBI's ``locationhist``
   bounds.

Nothing here matches on symbol. Symbol matching was measured to be unsafe --
``AAVS1`` resolves through Ensembl's xref lookup to ``ENSG00000125503``, which is
``PPP1R12C``, a different gene -- so a row that neither step resolves is left
null and counted rather than guessed at.

Every coordinate records which step produced it, because the two annotators call
gene boundaries differently: SHANK3 on GRCh37 starts at 51,112,843 per Ensembl
and 51,111,250 per NCBI. That 1.6 kb is not reconcilable, only auditable.

:func:`resolve` is pure -- it takes the GENCODE tables and a fallback callable,
so the test suite exercises the whole cascade without a network. :func:`build`
is the wiring that fetches them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import polars as pl

from genetrek.db.sources import gencode, ncbi

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from genetrek.db.sources.fetcher import Fetcher

#: Values a ``*_source`` column takes, besides null.
SOURCE_GENCODE = "gencode"
SOURCE_NCBI = "ncbi"

#: Strand is one column, not one per assembly: a gene does not change strand
#: between GRCh38 and GRCh37. Taken from the first assembly that supplies it.
STRAND_COLUMN = "strand"

#: Canonical chromosome ordering, used only to break a tie deterministically
#: when one gene has several annotated loci in the same assembly.
_CHROM_ORDER: dict[str, int] = {
    **{str(n): n for n in range(1, 23)},
    "X": 23,
    "Y": 24,
    "MT": 25,
}
_UNKNOWN_CHROM = 99

_JOIN_SCHEMA: dict[str, pl.DataType] = {
    "ensg": pl.String(),
    "chrom": pl.String(),
    "start": pl.Int64(),
    "end": pl.Int64(),
    "strand": pl.String(),
}


class Fallback(Protocol):
    """Supplies coordinates for genes GENCODE could not resolve."""

    def __call__(self, gene_ids: Sequence[str]) -> pl.DataFrame:
        """Return rows shaped like :data:`genetrek.db.sources.ncbi.COORD_SCHEMA`."""
        ...


@dataclass(frozen=True)
class CoordinateColumns:
    """The column names one assembly contributes."""

    chrom: str
    start: str
    end: str
    source: str

    def names(self) -> tuple[str, str, str, str]:
        """The four names, in table order."""
        return (self.chrom, self.start, self.end, self.source)


@dataclass(frozen=True)
class StepTally:
    """How one assembly's coordinates were obtained."""

    assembly: str
    gencode: int
    ncbi: int
    missing: int
    #: Genes with more than one locus in this assembly's annotation, resolved by
    #: agreement with the reference assembly and then by chromosome order.
    ambiguous: int


@dataclass(frozen=True)
class CoordinateReport:
    """What a coordinate build produced, in enough detail to spot a drift."""

    rows: int
    steps: tuple[StepTally, ...]
    #: Input row indices the assemblies place on different chromosomes. Measured
    #: at 16 of 45,045 against GENCODE v50, every one a short multi-copy RNA gene
    #: or pseudogene (miRNA, 7SK/7SL, U6/U7, all but one under 1 kb) whose lift37
    #: mapping landed on a near-identical paralogue. The two annotations really
    #: do disagree, so these are reported rather than silently reconciled.
    conflict_rows: tuple[int, ...] = ()
    gencode_release: int | None = None

    @property
    def chromosome_conflicts(self) -> int:
        """How many genes the assemblies place on different chromosomes."""
        return len(self.conflict_rows)

    def describe(self) -> str:
        """A short human-readable summary, for the build log."""
        release = (
            f"GENCODE v{self.gencode_release}"
            if self.gencode_release is not None
            else "GENCODE (release not recorded)"
        )
        lines = [f"{self.rows:,} genes from {release}"]
        for step in self.steps:
            share = 100 * step.gencode / self.rows if self.rows else 0.0
            lines.append(
                f"  {step.assembly}: {step.gencode:,} gencode ({share:.1f}%), "
                f"{step.ncbi:,} ncbi, {step.missing:,} missing"
                + (f", {step.ambiguous:,} ambiguous" if step.ambiguous else ""),
            )
        if self.chromosome_conflicts:
            lines.append(
                f"  WARNING: {self.chromosome_conflicts:,} genes disagree on "
                "chromosome between assemblies",
            )
        return "\n".join(lines)


def columns_for(assembly: str) -> CoordinateColumns:
    """The coordinate column names for an assembly (``GRCh38`` -> ``grch38_*``)."""
    prefix = assembly.lower()
    return CoordinateColumns(
        chrom=f"{prefix}_chr",
        start=f"{prefix}_start",
        end=f"{prefix}_end",
        source=f"{prefix}_source",
    )


def resolve(
    genes: pl.DataFrame,
    tables: dict[str, pl.DataFrame],
    *,
    fallback: Fallback | None = None,
    assemblies: Iterable[str] | None = None,
    ensg_column: str = "ensg",
    ncbi_column: str = "ncbi_id",
    gencode_release: int | None = None,
) -> tuple[pl.DataFrame, CoordinateReport]:
    """Run the cascade over a gene table.

    Args:
        genes: The genes to place. Only ``ensg_column`` and ``ncbi_column`` are
            read; either may be null on any row.
        tables: One GENCODE gene table per assembly, as
            :func:`genetrek.db.sources.gencode.read` returns.
        fallback: Called once with the NCBI IDs no GENCODE table resolved. Pass
            ``None`` to skip the fallback entirely.
        assemblies: Which assemblies to produce, reference first. Defaults to
            the order of ``tables``. The first one breaks ties for the others.
        ensg_column: Column holding the Ensembl gene ID.
        ncbi_column: Column holding the NCBI gene ID.
        gencode_release: Recorded in the report, for provenance.

    Returns:
        A frame of coordinate columns only -- one row per input row, same order,
        four columns per assembly plus :data:`STRAND_COLUMN` -- and a report.
    """
    order = list(assemblies) if assemblies is not None else list(tables)
    missing_tables = [a for a in order if a not in tables]
    if missing_tables:
        msg = f"no GENCODE table supplied for {missing_tables}"
        raise ValueError(msg)

    base = _base_frame(genes, ensg_column, ncbi_column)
    hits: dict[str, pl.DataFrame] = {}
    ambiguities: dict[str, int] = {}
    reference: pl.DataFrame | None = None

    for assembly in order:
        collapsed, ambiguities[assembly] = _one_locus_per_gene(
            tables[assembly],
            reference,
        )
        hits[assembly] = base.join(
            collapsed,
            left_on="_ensg",
            right_on="ensg",
            how="left",
        )
        if reference is None:
            reference = collapsed.select("ensg", "chrom")

    extra = _fallback_rows(base, hits, order, fallback)

    parts: list[pl.DataFrame] = []
    tallies: list[StepTally] = []
    for assembly in order:
        part, tally = _merge_assembly(
            hits[assembly],
            extra,
            assembly,
            ambiguities[assembly],
        )
        parts.append(part)
        tallies.append(tally)

    # Every part carries the same strand; keep one, at the end of the table.
    strand = _strand_column(parts)
    frame = pl.concat([part.drop(STRAND_COLUMN) for part in parts], how="horizontal")
    frame = frame.insert_column(frame.width, strand)
    return frame, CoordinateReport(
        rows=frame.height,
        steps=tuple(tallies),
        conflict_rows=_chromosome_conflicts(frame, order),
        gencode_release=gencode_release,
    )


def build(
    fetcher: Fetcher,
    cache_dir: Path,
    genes: pl.DataFrame,
    *,
    release: int | None = None,
    assemblies: Iterable[str] = gencode.ASSEMBLIES,
    refresh: bool = False,
    use_ncbi: bool = True,
    ensg_column: str = "ensg",
    ncbi_column: str = "ncbi_id",
) -> tuple[pl.DataFrame, CoordinateReport]:
    """Fetch the GENCODE tables a build needs, then :func:`resolve` with them."""
    order = list(assemblies)
    resolved = gencode.resolve_release(fetcher, release, order)
    tables = {
        assembly: gencode.read(
            gencode.fetch_genes(
                fetcher,
                cache_dir,
                resolved,
                assembly,
                refresh=refresh,
            ),
        )
        for assembly in order
    }

    def fallback(gene_ids: Sequence[str]) -> pl.DataFrame:
        return ncbi.fetch_coordinates(fetcher, gene_ids, order)

    return resolve(
        genes,
        tables,
        fallback=fallback if use_ncbi else None,
        assemblies=order,
        ensg_column=ensg_column,
        ncbi_column=ncbi_column,
        gencode_release=resolved.release,
    )


def _base_frame(
    genes: pl.DataFrame, ensg_column: str, ncbi_column: str
) -> pl.DataFrame:
    """The two identifier columns, normalised, one row per input row."""
    for column in (ensg_column, ncbi_column):
        if column not in genes.columns:
            msg = f"gene table has no {column!r} column"
            raise ValueError(msg)
    return genes.select(
        # A stored ENSG may carry a version (``ENSG00000251322.9``); GENCODE IDs
        # are cut back to the bare accession, so cut these the same way.
        _accession(ensg_column).alias("_ensg"),
        _accession(ncbi_column).alias("_ncbi"),
    )


def _accession(column: str) -> pl.Expr:
    """An identifier column as a trimmed, suffix-free, non-empty string."""
    trimmed = (
        pl.col(column).cast(pl.String).str.strip_chars().str.replace(r"[._].*$", "")
    )
    return pl.when(trimmed == "").then(None).otherwise(trimmed)


def _one_locus_per_gene(
    table: pl.DataFrame,
    reference: pl.DataFrame | None,
) -> tuple[pl.DataFrame, int]:
    """Collapse a GENCODE table to one locus per gene.

    GENCODE's lift37 files annotate a handful of genes more than once, marking
    the copies ``_1``, ``_2`` or ``_PAR_Y``; stripping those suffixes to recover
    the accession therefore leaves duplicates. Whichever copy sits on the same
    chromosome as the reference assembly's locus wins, so the two assemblies
    describe the same gene. Failing that -- and for the reference assembly
    itself, which has nothing to agree with -- the canonical chromosome order
    breaks the tie, putting the X copy of a pseudoautosomal gene ahead of its Y
    copy.
    """
    frame = (
        table.select(pl.col(name).cast(dtype) for name, dtype in _JOIN_SCHEMA.items())
        .filter(pl.col("ensg").is_not_null())
        .unique(maintain_order=True)
    )
    ambiguous = frame.group_by("ensg").len().filter(pl.col("len") > 1).height
    if ambiguous == 0:
        return frame, 0

    ranked = frame.with_columns(
        pl.col("chrom")
        .replace_strict(_CHROM_ORDER, default=_UNKNOWN_CHROM, return_dtype=pl.Int16)
        .alias("_rank"),
    )
    if reference is None:
        ranked = ranked.with_columns(pl.lit(value=False).alias("_agrees"))
    else:
        ranked = ranked.join(
            reference.rename({"chrom": "_ref_chrom"}),
            on="ensg",
            how="left",
        ).with_columns(
            (pl.col("chrom") == pl.col("_ref_chrom"))
            .fill_null(value=False)
            .alias("_agrees"),
        )

    collapsed = (
        ranked.sort(
            ["ensg", "_agrees", "_rank", "start"],
            descending=[False, True, False, False],
        )
        .unique(subset=["ensg"], keep="first", maintain_order=True)
        .select(*_JOIN_SCHEMA)
    )
    return collapsed, ambiguous


def _fallback_rows(
    base: pl.DataFrame,
    hits: dict[str, pl.DataFrame],
    order: list[str],
    fallback: Fallback | None,
) -> pl.DataFrame:
    """Ask the fallback for the NCBI IDs no assembly resolved from GENCODE."""
    empty = pl.DataFrame(schema=ncbi.COORD_SCHEMA)
    if fallback is None:
        return empty

    unresolved = pl.Series("_gap", [False] * base.height, dtype=pl.Boolean)
    for assembly in order:
        unresolved = unresolved | hits[assembly]["chrom"].is_null()
    gene_ids = (
        base.filter(unresolved & pl.col("_ncbi").is_not_null())["_ncbi"]
        .unique(maintain_order=True)
        .to_list()
    )
    if not gene_ids:
        return empty

    rows = fallback(gene_ids)
    if rows.is_empty():
        return empty
    return rows.select(
        pl.col(name).cast(dtype) for name, dtype in ncbi.COORD_SCHEMA.items()
    )


def _merge_assembly(
    hit: pl.DataFrame,
    extra: pl.DataFrame,
    assembly: str,
    ambiguous: int,
) -> tuple[pl.DataFrame, StepTally]:
    """Fill one assembly's GENCODE misses from the fallback and label the source."""
    supplement = extra.filter(pl.col("assembly") == assembly).drop("assembly")
    merged = hit.join(
        supplement,
        left_on="_ncbi",
        right_on="ncbi_id",
        how="left",
        suffix="_ncbi",
    )
    columns = columns_for(assembly)
    from_gencode = pl.col("chrom").is_not_null()
    from_ncbi = from_gencode.not_() & pl.col("chrom_ncbi").is_not_null()
    part = merged.select(
        pl.coalesce("chrom", "chrom_ncbi").alias(columns.chrom),
        pl.coalesce("start", "start_ncbi").alias(columns.start),
        pl.coalesce("end", "end_ncbi").alias(columns.end),
        pl.when(from_gencode)
        .then(pl.lit(SOURCE_GENCODE))
        .when(from_ncbi)
        .then(pl.lit(SOURCE_NCBI))
        .alias(columns.source),
        pl.coalesce("strand", "strand_ncbi").alias(STRAND_COLUMN),
    )
    counts = part[columns.source].value_counts()
    tally = StepTally(
        assembly=assembly,
        gencode=_count(counts, columns.source, SOURCE_GENCODE),
        ncbi=_count(counts, columns.source, SOURCE_NCBI),
        missing=int(part[columns.source].is_null().sum()),
        ambiguous=ambiguous,
    )
    return part, tally


def _count(counts: pl.DataFrame, column: str, value: str) -> int:
    """One row out of a ``value_counts`` frame, or zero."""
    matched = counts.filter(pl.col(column) == value)
    return int(matched["count"][0]) if matched.height else 0


def _strand_column(parts: list[pl.DataFrame]) -> pl.Series:
    """One strand per gene: the reference assembly's, else the next that has it."""
    strand = parts[0][STRAND_COLUMN]
    for part in parts[1:]:
        strand = strand.fill_null(part[STRAND_COLUMN])
    return strand.rename(STRAND_COLUMN)


def _chromosome_conflicts(frame: pl.DataFrame, order: list[str]) -> tuple[int, ...]:
    """Row indices the assemblies place on different chromosomes."""
    if not order:
        return ()
    reference = columns_for(order[0]).chrom
    conflict = pl.Series([False] * frame.height, dtype=pl.Boolean)
    for assembly in order[1:]:
        other = columns_for(assembly).chrom
        conflict = conflict | (
            frame[reference].is_not_null()
            & frame[other].is_not_null()
            & (frame[reference] != frame[other])
        )
    return tuple(conflict.arg_true().to_list())
