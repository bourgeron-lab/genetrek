"""Test doubles for the upstream sources.

Shared by ``test_sources.py``, ``test_build.py`` and ``test_init_cli.py`` so the
canned HGNC, GENCODE and NCBI bodies are described once. Nothing here opens a
socket -- the live checks live in ``test_sources_network.py``.
"""

from __future__ import annotations

import gzip
import io
from typing import TYPE_CHECKING, Self

from genetrek.db.sources import gencode, hgnc, ncbi
from genetrek.db.sources.fetcher import FetchError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

GENCODE_LISTING = "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/"


class FakeFetcher:
    """A :class:`~genetrek.db.sources.fetcher.Fetcher` serving canned bodies.

    Records every call, so a test can assert that a cache was reused or that a
    fallback was never reached.
    """

    def __init__(
        self,
        *,
        text: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        heads: dict[str, dict[str, str]] | None = None,
        streams: dict[str, bytes] | None = None,
    ) -> None:
        self.text = text or {}
        self.json = json or {}
        self.heads = heads or {}
        self.streams = streams or {}
        self.calls: list[tuple[str, str]] = []
        self.posts: list[Mapping[str, str]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def close(self) -> None:
        """Match HttpFetcher's surface; there is nothing to release."""

    def _lookup(self, kind: str, url: str, table: dict[str, object]) -> object:
        self.calls.append((kind, url))
        if url not in table:
            msg = f"{kind} {url}: not in the fixture"
            raise FetchError(msg)
        return table[url]

    def get_json(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        del params
        return self._lookup("GET", url, dict(self.json))

    def post_form_json(self, url: str, data: Mapping[str, str]) -> object:
        self.posts.append(dict(data))
        return self._lookup("POST", url, dict(self.json))

    def get_text(self, url: str) -> str:
        return str(self._lookup("GET", url, dict(self.text)))

    def head(self, url: str) -> Mapping[str, str]:
        result = self._lookup("HEAD", url, dict(self.heads))
        return dict(result)  # type: ignore[call-overload]

    def stream_lines(self, url: str, *, gunzip: bool = False) -> Iterator[str]:
        body = self._lookup("STREAM", url, dict(self.streams))
        raw = bytes(body)  # type: ignore[arg-type]
        if gunzip:
            raw = gzip.decompress(raw)
        yield from io.StringIO(raw.decode("utf-8"))


#: Genes covering every step of the coordinate cascade.
#:
#: ``SHANK3`` and ``ACACA`` come from GENCODE in both assemblies (``ACACA``
#: exercising lift37 keeping the GRCh38 identifier); ``AKR1C8`` is in the GRCh38
#: annotation but not lift37; ``ACTBP3`` has no Ensembl ID at all; ``FRA1A``
#: stands in for the OMIM locus records that carry neither identifier.
HGNC_ROWS: tuple[dict[str, str], ...] = (
    {
        "hgnc_id": "HGNC:14294",
        "symbol": "SHANK3",
        "name": "SH3 and multiple ankyrin repeat domains 3",
        "locus_group": "protein-coding gene",
        "location": "22q13.33",
        "alias_symbol": "PSAP2|PROSAP2",
        "prev_symbol": "",
        "entrez_id": "85358",
        "ensembl_gene_id": "ENSG00000251322",
        "omim_id": "606230",
        "mane_select": "ENST00000445220.7|NM_033517.1",
    },
    {
        "hgnc_id": "HGNC:84",
        "symbol": "ACACA",
        "name": "acetyl-CoA carboxylase alpha",
        "locus_group": "protein-coding gene",
        "location": "17q12",
        "alias_symbol": "",
        "prev_symbol": "ACAC",
        "entrez_id": "31",
        "ensembl_gene_id": "ENSG00000278540",
        "omim_id": "200350",
        "mane_select": "ENST00000353139.9|NM_198834.3",
    },
    {
        "hgnc_id": "HGNC:23443",
        "symbol": "AKR1C8",
        "name": "aldo-keto reductase family 1 member C8",
        "locus_group": "pseudogene",
        "location": "10p15.1",
        "alias_symbol": "",
        "prev_symbol": "AKR1CL2",
        "entrez_id": "100",
        "ensembl_gene_id": "ENSG00000288704",
        "omim_id": "",
        "mane_select": "",
    },
    {
        "hgnc_id": "HGNC:136",
        "symbol": "ACTBP3",
        "name": "actin beta pseudogene 3",
        "locus_group": "pseudogene",
        "location": "18q21.1",
        "alias_symbol": "",
        "prev_symbol": "",
        "entrez_id": "5",
        "ensembl_gene_id": "",
        "omim_id": "",
        "mane_select": "",
    },
    {
        "hgnc_id": "HGNC:3773",
        "symbol": "FRA1A",
        "name": "fragile site 1A",
        "locus_group": "other",
        "location": "1p21.3",
        "alias_symbol": "",
        "prev_symbol": "",
        "entrez_id": "",
        "ensembl_gene_id": "",
        "omim_id": "136510",
        "mane_select": "",
    },
)

#: GENCODE gene features per assembly: (gene_id, name, chrom, start, end, strand).
GENCODE_GENES: dict[str, tuple[tuple[str, str, str, int, int, str], ...]] = {
    "GRCh38": (
        ("ENSG00000251322.9", "SHANK3", "chr22", 50674408, 50733212, "+"),
        ("ENSG00000278540.5", "ACACA", "chr17", 37084992, 37406853, "-"),
        ("ENSG00000288704.1", "AKR1C8", "chr10", 4977433, 4993103, "+"),
    ),
    "GRCh37": (
        # The same identifiers, on GRCh37 coordinates: this is what lift37 gives.
        ("ENSG00000251322.9_1", "SHANK3", "chr22", 51112836, 51171640, "+"),
        ("ENSG00000278540.5_1", "ACACA", "chr17", 35441927, 35766944, "-"),
    ),
}

#: NCBI locationhist entries: gene id -> (assembly accession, chrom accession,
#: 0-based start, 0-based stop).
NCBI_RECORDS: dict[str, tuple[tuple[str, str, int, int], ...]] = {
    # ACTBP3, which has no Ensembl ID, so only NCBI can place it.
    "5": (
        ("GCF_000001405.40", "NC_000018.10", 59693045, 59693472),
        ("GCF_000001405.25", "NC_000018.9", 57360277, 57360704),
    ),
    # AKR1C8 in GRCh37 only: GENCODE's lift37 has no entry for it.
    "100": (("GCF_000001405.25", "NC_000010.10", 5019395, 5035065),),
}


def hgnc_body(rows: tuple[dict[str, str], ...] = HGNC_ROWS) -> str:
    """A complete-set TSV with the columns a default build asks for."""
    header = list(rows[0])
    lines = ["\t".join(header)]
    lines += ["\t".join(row.get(column, "") for column in header) for row in rows]
    return "\n".join(lines) + "\n"


def gtf(*genes: tuple[str, str, str, int, int, str]) -> bytes:
    """A gzipped GTF: a comment, a transcript line to ignore, then gene lines."""
    lines = [
        "##description: test",
        'chr1\tHAVANA\ttranscript\t1\t2\t.\t+\t.\tgene_id "ENSG00000000001.1";',
    ]
    lines += [
        f"{chrom}\tHAVANA\tgene\t{start}\t{end}\t.\t{strand}\t.\t"
        f'gene_id "{gene_id}"; gene_type "protein_coding"; gene_name "{name}";'
        for gene_id, name, chrom, start, end, strand in genes
    ]
    return gzip.compress(("\n".join(lines) + "\n").encode())


def esummary(
    records: Mapping[str, tuple[tuple[str, str, int, int], ...]] = NCBI_RECORDS,
) -> dict[str, object]:
    """An esummary JSON body carrying a ``locationhist`` block per gene."""
    result: dict[str, object] = {"uids": list(records)}
    for uid, entries in records.items():
        result[uid] = {
            "locationhist": [
                {
                    "assemblyaccver": accession,
                    "chraccver": chrom,
                    "chrstart": start,
                    "chrstop": stop,
                }
                for accession, chrom, start, stop in entries
            ],
        }
    return {"result": result}


def full_fetcher(release: int = 50, stamp: str = "28 Aug 2026") -> FakeFetcher:
    """A fetcher wired for a complete build: HGNC, both GTFs, and the fallback."""
    urls = {a: gencode.url_for(release, a) for a in gencode.ASSEMBLIES}
    return FakeFetcher(
        text={
            hgnc.LATEST_URL: hgnc_body(),
            GENCODE_LISTING: f"release_{release}/",
        },
        heads={
            hgnc.LATEST_URL: {"last-modified": f"Fri, {stamp} 00:00:00 GMT"},
            **{url: {} for url in urls.values()},
        },
        streams={url: gtf(*GENCODE_GENES[assembly]) for assembly, url in urls.items()},
        json={ncbi.ESUMMARY: esummary()},
    )
