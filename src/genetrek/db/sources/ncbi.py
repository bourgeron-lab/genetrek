"""Gene coordinates from NCBI, for the genes GENCODE cannot supply.

Reached only for rows GENCODE misses: those with no Ensembl ID at all, and those
whose ID is absent from an assembly's annotation. NCBI's ``esummary`` carries a
``locationhist`` block covering several assemblies, so one call yields both
GRCh38 and GRCh37.

Bounds come from a different annotator than GENCODE's and differ slightly --
SHANK3 on GRCh37 is 51,112,843 (Ensembl) against 51,111,250 (NCBI). That is not
reconcilable, which is why the caller records a per-assembly source.

No credential is required. ``NCBI_API_KEY`` lifts the rate limit from 3 to 10
requests a second if you have one.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from genetrek.db.sources.fetcher import Fetcher

ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

#: Genes per request. esummary accepts far more, but a smaller batch keeps a
#: single failure cheap to retry.
BATCH_SIZE = 200

#: RefSeq assembly accessions, mapped to the assembly name they annotate.
#: ``locationhist`` identifies assemblies this way and nothing else.
ASSEMBLY_ACCESSIONS: dict[str, str] = {
    "GCF_000001405.40": "GRCh38",
    "GCF_000001405.39": "GRCh38",
    "GCF_000001405.25": "GRCh37",
}

#: RefSeq chromosome accessions to chromosome names. ``locationhist`` gives
#: ``NC_000022.11``; the database stores ``22``.
_CHROM_ACCESSIONS: dict[str, str] = {
    **{f"NC_{i:06d}": str(i) for i in range(1, 23)},
    "NC_000023": "X",
    "NC_000024": "Y",
    "NC_012920": "MT",
}

#: Column layout of a coordinate table.
COORD_SCHEMA: dict[str, pl.DataType] = {
    "ncbi_id": pl.String(),
    "assembly": pl.String(),
    "chrom": pl.String(),
    "start": pl.Int64(),
    "end": pl.Int64(),
    "strand": pl.String(),
}


def chromosome_for(accession: str) -> str | None:
    """Map a RefSeq chromosome accession to a chromosome name."""
    return _CHROM_ACCESSIONS.get(accession.split(".")[0])


def api_key() -> str | None:
    """The NCBI API key from the environment, if one is set."""
    return os.environ.get("NCBI_API_KEY") or None


def fetch_coordinates(
    fetcher: Fetcher,
    gene_ids: Sequence[str],
    assemblies: Iterable[str] = ("GRCh38", "GRCh37"),
) -> pl.DataFrame:
    """Look up coordinates for Entrez gene IDs.

    Args:
        fetcher: Where to send the requests.
        gene_ids: Entrez gene IDs, as strings.
        assemblies: Which assembly names to keep.

    Returns:
        One row per (gene, assembly) found, with :data:`COORD_SCHEMA`. Genes NCBI
        does not know, or has no coordinates for, are simply absent -- the caller
        decides what a missing coordinate means.
    """
    wanted = set(assemblies)
    unique = list(dict.fromkeys(str(g) for g in gene_ids if str(g).strip()))
    if not unique:
        return pl.DataFrame(schema=COORD_SCHEMA)

    rows: list[tuple[str, str, str, int, int, str]] = []
    key = api_key()
    for offset in range(0, len(unique), BATCH_SIZE):
        batch = unique[offset : offset + BATCH_SIZE]
        form = {"db": "gene", "retmode": "json", "id": ",".join(batch)}
        if key:
            form["api_key"] = key
        payload = fetcher.post_form_json(ESUMMARY, form)
        rows.extend(_rows_from_payload(payload, wanted))

    if not rows:
        return pl.DataFrame(schema=COORD_SCHEMA)
    return pl.DataFrame(rows, schema=COORD_SCHEMA, orient="row").unique(
        subset=["ncbi_id", "assembly"],
        keep="first",
        maintain_order=True,
    )


def _rows_from_payload(
    payload: object,
    wanted: set[str],
) -> Iterable[tuple[str, str, str, int, int, str]]:
    """Pull (gene, assembly, chrom, start, end, strand) out of an esummary body."""
    if not isinstance(payload, dict):
        return
    result = payload.get("result")
    if not isinstance(result, dict):
        return

    for gene_id in result.get("uids") or []:
        record = result.get(str(gene_id))
        if not isinstance(record, dict):
            continue
        # Newest annotation of each assembly wins: locationhist is ordered
        # newest-first per assembly, and GRCh38 appears under two accessions.
        seen: set[str] = set()
        for entry in record.get("locationhist") or []:
            if not isinstance(entry, dict):
                continue
            assembly = ASSEMBLY_ACCESSIONS.get(str(entry.get("assemblyaccver", "")))
            if assembly is None or assembly not in wanted or assembly in seen:
                continue
            chrom = chromosome_for(str(entry.get("chraccver", "")))
            start, end = entry.get("chrstart"), entry.get("chrstop")
            if chrom is None or start is None or end is None:
                continue
            low, high = int(start), int(end)
            # esummary reports 0-based coordinates, and start > stop on the
            # minus strand. Normalise to 1-based ascending, as GENCODE gives.
            strand = "-" if low > high else "+"
            if low > high:
                low, high = high, low
            seen.add(assembly)
            yield (str(gene_id), assembly, chrom, low + 1, high + 1, strand)
