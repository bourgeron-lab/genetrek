"""Tests for the upstream fetchers and the coordinate cascade.

Every test here runs against a fake fetcher backed by in-memory fixtures: the
suite must not depend on a network, on an upstream being up, or on a release not
having moved. The live smoke tests live in ``test_sources_network.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest
from fakes import GENCODE_LISTING, FakeFetcher, gtf, hgnc_body

from genetrek.db.sources import coordinates as co
from genetrek.db.sources import gencode, hgnc, ncbi
from genetrek.db.sources.fetcher import Fetcher, FetchError

if TYPE_CHECKING:
    from pathlib import Path


def test_fake_fetcher_satisfies_the_protocol():
    assert isinstance(FakeFetcher(), Fetcher)


# --------------------------------------------------------------------- HGNC


def test_resolve_release_reads_the_last_modified_date():
    fetcher = FakeFetcher(
        heads={hgnc.LATEST_URL: {"last-modified": "Fri, 28 Aug 2026 01:02:03 GMT"}},
    )
    url, stamp = hgnc.resolve_release(fetcher)
    assert (url, stamp) == (hgnc.LATEST_URL, "20260828")


def test_resolve_release_refuses_a_latest_without_a_date():
    fetcher = FakeFetcher(heads={hgnc.LATEST_URL: {}})
    with pytest.raises(FetchError, match="no usable Last-Modified"):
        hgnc.resolve_release(fetcher)


def test_resolve_release_rejects_a_malformed_pin():
    with pytest.raises(FetchError, match="latest' or YYYY-MM-DD"):
        hgnc.resolve_release(FakeFetcher(), "August 2026")


def test_resolve_release_falls_through_to_the_quarterly_archive():
    quarterly = hgnc.ARCHIVE_URLS[1].format(date="2025-04-01")
    fetcher = FakeFetcher(heads={quarterly: {}})
    url, stamp = hgnc.resolve_release(fetcher, "2025-04-01")
    assert (url, stamp) == (quarterly, "20250401")
    # The monthly URL was tried first, and its absence was not fatal.
    assert fetcher.calls[0][1] == hgnc.ARCHIVE_URLS[0].format(date="2025-04-01")


def test_resolve_release_reports_a_date_in_neither_archive():
    with pytest.raises(FetchError, match="monthly or quarterly archive"):
        hgnc.resolve_release(FakeFetcher(), "1999-01-01")


def test_fetch_caches_under_the_release_stamped_name(tmp_path: Path):
    fetcher = FakeFetcher(
        heads={hgnc.LATEST_URL: {"last-modified": "Fri, 28 Aug 2026 00:00:00 GMT"}},
        text={hgnc.LATEST_URL: hgnc_body()},
    )
    release = hgnc.fetch(fetcher, tmp_path)
    assert release.cached.name == "hgnc_complete_set_20260828.txt"
    assert release.cached.name == release.filename

    # A second call must reuse the file rather than download it again.
    before = len(fetcher.calls)
    again = hgnc.fetch(fetcher, tmp_path)
    assert again.cached == release.cached
    assert [k for k, _ in fetcher.calls[before:]] == ["HEAD"]


def test_fetch_refresh_downloads_again(tmp_path: Path):
    fetcher = FakeFetcher(
        heads={hgnc.LATEST_URL: {"last-modified": "Fri, 28 Aug 2026 00:00:00 GMT"}},
        text={hgnc.LATEST_URL: hgnc_body()},
    )
    hgnc.fetch(fetcher, tmp_path)
    before = len(fetcher.calls)
    hgnc.fetch(fetcher, tmp_path, refresh=True)
    assert ("GET", hgnc.LATEST_URL) in fetcher.calls[before:]


def test_read_keeps_identifiers_as_text(tmp_path: Path):
    path = tmp_path / "hgnc.txt"
    path.write_text(hgnc_body(), encoding="utf-8")
    frame = hgnc.read(path, ["symbol", "entrez_id"])
    assert frame.schema["entrez_id"] == pl.String
    # The float-string mangling that dtype inference causes must not happen.
    assert frame["entrez_id"].to_list()[:3] == ["85358", "31", "100"]


def test_read_always_keeps_the_columns_resolution_needs(tmp_path: Path):
    path = tmp_path / "hgnc.txt"
    path.write_text(hgnc_body(), encoding="utf-8")
    frame = hgnc.read(path, ["omim_id"])
    assert set(hgnc.REQUIRED_COLUMNS) <= set(frame.columns)


def test_read_names_the_missing_column(tmp_path: Path):
    path = tmp_path / "hgnc.txt"
    path.write_text(hgnc_body(), encoding="utf-8")
    with pytest.raises(FetchError, match=r"no column\(s\) \['vega_id'\]"):
        hgnc.read(path, ["vega_id"])


# ------------------------------------------------------------------ GENCODE


def test_url_for_builds_the_lift37_path():
    assert gencode.url_for(50, "GRCh37").endswith(
        "release_50/GRCh37_mapping/gencode.v50lift37.annotation.gtf.gz",
    )
    assert gencode.url_for(50, "GRCh38").endswith(
        "release_50/gencode.v50.annotation.gtf.gz",
    )


def test_url_for_rejects_an_unknown_assembly():
    with pytest.raises(FetchError, match="unknown assembly"):
        gencode.url_for(50, "hg18")


def test_resolve_release_skips_a_release_without_lift37():
    """A new release appears before its GRCh37_mapping directory does."""
    fetcher = FakeFetcher(
        text={
            "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/": (
                "release_51/ release_50/ release_49/"
            )
        },
        heads={
            gencode.url_for(51, "GRCh38"): {},
            gencode.url_for(50, "GRCh38"): {},
            gencode.url_for(50, "GRCh37"): {},
        },
    )
    resolved = gencode.resolve_release(fetcher)
    assert resolved.release == 50
    assert set(resolved.urls) == {"GRCh38", "GRCh37"}


def test_resolve_release_honours_a_pin():
    fetcher = FakeFetcher(
        heads={
            gencode.url_for(44, "GRCh38"): {},
            gencode.url_for(44, "GRCh37"): {},
        },
    )
    assert gencode.resolve_release(fetcher, 44).release == 44


def test_resolve_release_reports_what_it_tried():
    fetcher = FakeFetcher(heads={})
    with pytest.raises(FetchError, match="no GRCh38"):
        gencode.resolve_release(fetcher, 44)


def test_release_url_refuses_an_assembly_it_lacks():
    release = gencode.GencodeRelease(release=50, urls={"GRCh38": "u"})
    with pytest.raises(FetchError, match="no GRCh37 annotation"):
        release.url("GRCh37")


def test_fetch_genes_strips_versions_and_mapping_suffixes(tmp_path: Path):
    url = gencode.url_for(50, "GRCh37")
    fetcher = FakeFetcher(
        streams={
            url: gtf(
                ("ENSG00000251322.9", "SHANK3", "chr22", 51112843, 51171726, "+"),
                ("ENSG00000308415.1_2", "DUP", "chr7", 100, 200, "-"),
                ("ENSG00000223274.1_PAR_Y", "PAR", "chrY", 1, 900, "+"),
            ),
        },
    )
    release = gencode.GencodeRelease(release=50, urls={"GRCh37": url})
    frame = gencode.read(gencode.fetch_genes(fetcher, tmp_path, release, "GRCh37"))
    assert frame["ensg"].to_list() == [
        "ENSG00000251322",
        "ENSG00000308415",
        "ENSG00000223274",
    ]
    # The chr prefix goes, the transcript line and the comment do not appear.
    assert frame["chrom"].to_list() == ["22", "7", "Y"]
    assert frame.height == 3


def test_fetch_genes_caches_the_derived_table(tmp_path: Path):
    url = gencode.url_for(50, "GRCh38")
    fetcher = FakeFetcher(
        streams={url: gtf(("ENSG00000251322.9", "SHANK3", "chr22", 1, 2, "+"))},
    )
    release = gencode.GencodeRelease(release=50, urls={"GRCh38": url})
    first = gencode.fetch_genes(fetcher, tmp_path, release, "GRCh38")
    assert first.name == "gencode_v50_grch38.parquet"
    before = len(fetcher.calls)
    assert gencode.fetch_genes(fetcher, tmp_path, release, "GRCh38") == first
    assert fetcher.calls[before:] == []


def test_fetch_genes_refuses_a_gtf_without_genes(tmp_path: Path):
    url = gencode.url_for(50, "GRCh38")
    fetcher = FakeFetcher(streams={url: gtf()})
    release = gencode.GencodeRelease(release=50, urls={"GRCh38": url})
    with pytest.raises(FetchError, match="no gene features"):
        gencode.fetch_genes(fetcher, tmp_path, release, "GRCh38")


# --------------------------------------------------------------------- NCBI


def payload(
    *records: tuple[str, list[tuple[str, str, int, int]]],
) -> dict[str, object]:
    """An esummary body built from positional (gene id, entries) pairs."""
    result: dict[str, object] = {"uids": [uid for uid, _ in records]}
    for uid, entries in records:
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


def test_fetch_coordinates_maps_accessions_and_normalises_bounds():
    fetcher = FakeFetcher(
        json={
            ncbi.ESUMMARY: payload(
                (
                    "85358",
                    [
                        ("GCF_000001405.40", "NC_000022.11", 50672822, 50733211),
                        ("GCF_000001405.25", "NC_000022.10", 51111250, 51171639),
                    ],
                ),
                # A minus-strand gene: esummary reports start > stop.
                ("4204", [("GCF_000001405.40", "NC_000023.11", 154097716, 154021572)]),
            ),
        },
    )
    frame = ncbi.fetch_coordinates(fetcher, ["85358", "4204"])
    rows = {(r["ncbi_id"], r["assembly"]): r for r in frame.to_dicts()}

    grch38 = rows[("85358", "GRCh38")]
    # 0-based to 1-based, and the accession resolved to a chromosome name.
    assert (grch38["chrom"], grch38["start"], grch38["end"]) == (
        "22",
        50672823,
        50733212,
    )
    assert grch38["strand"] == "+"
    assert rows[("85358", "GRCh37")]["chrom"] == "22"

    minus = rows[("4204", "GRCh38")]
    assert minus["strand"] == "-"
    assert minus["start"] < minus["end"]
    assert minus["chrom"] == "X"


def test_fetch_coordinates_keeps_the_newest_annotation_per_assembly():
    """GRCh38 appears under two accessions, newest first."""
    fetcher = FakeFetcher(
        json={
            ncbi.ESUMMARY: payload(
                (
                    "1",
                    [
                        ("GCF_000001405.40", "NC_000001.11", 100, 200),
                        ("GCF_000001405.39", "NC_000001.11", 999, 1099),
                    ],
                ),
            ),
        },
    )
    frame = ncbi.fetch_coordinates(fetcher, ["1"], ["GRCh38"])
    assert frame.height == 1
    assert frame["start"][0] == 101


def test_fetch_coordinates_drops_scaffolds_and_unwanted_assemblies():
    fetcher = FakeFetcher(
        json={
            ncbi.ESUMMARY: payload(
                ("1", [("GCF_000001405.40", "NW_009646201.1", 1, 2)]),
                ("2", [("GCF_000001405.25", "NC_000001.10", 1, 2)]),
                ("3", [("GCF_000000002.1", "NC_000001.11", 1, 2)]),
            ),
        },
    )
    frame = ncbi.fetch_coordinates(fetcher, ["1", "2", "3"], ["GRCh38"])
    assert frame.is_empty()


def test_fetch_coordinates_batches_and_deduplicates_ids(monkeypatch):
    monkeypatch.setattr(ncbi, "BATCH_SIZE", 2)
    fetcher = FakeFetcher(json={ncbi.ESUMMARY: payload()})
    ncbi.fetch_coordinates(fetcher, ["1", "2", "3", "1"])
    assert [call["id"] for call in fetcher.posts] == ["1,2", "3"]


def test_fetch_coordinates_short_circuits_on_no_ids():
    fetcher = FakeFetcher()
    frame = ncbi.fetch_coordinates(fetcher, ["", "  "])
    assert frame.is_empty()
    assert frame.schema == pl.Schema(ncbi.COORD_SCHEMA)
    assert fetcher.calls == []


def test_fetch_coordinates_sends_the_api_key_when_set(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "sekret")
    fetcher = FakeFetcher(json={ncbi.ESUMMARY: payload()})
    ncbi.fetch_coordinates(fetcher, ["1"])
    assert fetcher.posts[0]["api_key"] == "sekret"


def test_fetch_coordinates_survives_an_unexpected_body():
    fetcher = FakeFetcher(json={ncbi.ESUMMARY: {"error": "boom"}})
    assert ncbi.fetch_coordinates(fetcher, ["1"]).is_empty()


def test_chromosome_for_covers_the_nuclear_genome_and_mitochondrion():
    assert ncbi.chromosome_for("NC_000001.11") == "1"
    assert ncbi.chromosome_for("NC_000023.11") == "X"
    assert ncbi.chromosome_for("NC_000024.10") == "Y"
    assert ncbi.chromosome_for("NC_012920.1") == "MT"
    assert ncbi.chromosome_for("NT_187361.1") is None


# -------------------------------------------------------------- coordinates


def _table(*rows: tuple[str, str, str, int, int, str]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=gencode.GENE_SCHEMA, orient="row")


@pytest.fixture
def cascade() -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """Genes exercising every step of the cascade, and the tables to place them.

    ``AKR1C8`` is in GRCh38 but absent from lift37; ``ACTBP3`` has no Ensembl ID
    at all; ``FRA1A`` has neither, standing in for the OMIM locus records that no
    source can place; ``PAR`` is annotated twice, on X and on Y.
    """
    genes = pl.DataFrame(
        {
            "symbol": ["SHANK3", "ACACA", "AKR1C8", "ACTBP3", "FRA1A", "PAR"],
            # A stored version suffix must not break the join.
            "ensg": [
                "ENSG00000251322",
                "ENSG00000278540.5",
                "ENSG00000288704",
                None,
                None,
                "ENSG00000223274",
            ],
            "ncbi_id": ["85358", "31", "100", "5", None, "9"],
        },
    )
    tables = {
        "GRCh38": _table(
            ("ENSG00000251322", "SHANK3", "22", 50674408, 50733212, "+"),
            ("ENSG00000278540", "ACACA", "17", 35441927, 35766944, "-"),
            ("ENSG00000288704", "AKR1C8", "10", 5000, 6000, "+"),
            # The Y copy first, so ordering cannot be what picks X.
            ("ENSG00000223274", "PAR", "Y", 1, 900, "+"),
            ("ENSG00000223274", "PAR", "X", 1, 900, "+"),
        ),
        "GRCh37": _table(
            ("ENSG00000251322", "SHANK3", "22", 51112843, 51171726, "+"),
            ("ENSG00000278540", "ACACA", "17", 35441927, 35766944, "-"),
            ("ENSG00000223274", "PAR", "X", 1, 900, "+"),
            ("ENSG00000223274", "PAR", "Y", 1, 900, "+"),
        ),
    }
    return genes, tables


def _fallback(rows: dict[str, list[tuple[str, str, int, int, str]]]) -> co.Fallback:
    def fallback(gene_ids):
        fallback.asked = list(gene_ids)  # type: ignore[attr-defined]
        out = [
            (gene_id, *row)
            for gene_id, entries in rows.items()
            if gene_id in gene_ids
            for row in entries
        ]
        return pl.DataFrame(out, schema=ncbi.COORD_SCHEMA, orient="row")

    fallback.asked = []  # type: ignore[attr-defined]
    return fallback


def test_columns_for_names_the_assembly_block():
    assert co.columns_for("GRCh38").names() == (
        "grch38_chr",
        "grch38_start",
        "grch38_end",
        "grch38_source",
    )


def test_resolve_labels_every_coordinate_with_its_source(cascade):
    genes, tables = cascade
    fallback = _fallback(
        {
            "5": [("GRCh38", "7", 100, 200, "+"), ("GRCh37", "7", 150, 250, "+")],
            "100": [("GRCh37", "10", 4800, 5800, "+")],
        },
    )
    frame, report = co.resolve(
        genes,
        tables,
        fallback=fallback,
        assemblies=["GRCh38", "GRCh37"],
        ensg_column="ensg",
        gencode_release=50,
    )
    rows = dict(zip(genes["symbol"], frame.to_dicts(), strict=True))

    assert rows["SHANK3"]["grch38_source"] == "gencode"
    assert rows["SHANK3"]["grch37_start"] == 51112843
    # The version suffix was stripped, so lift37's identical ID matched.
    assert rows["ACACA"]["grch37_source"] == "gencode"
    # In GRCh38's annotation only: GENCODE there, NCBI for GRCh37.
    assert rows["AKR1C8"]["grch38_source"] == "gencode"
    assert rows["AKR1C8"]["grch37_source"] == "ncbi"
    assert rows["AKR1C8"]["grch37_start"] == 4800
    # No Ensembl ID at all: both from NCBI.
    assert rows["ACTBP3"]["grch38_source"] == "ncbi"
    assert rows["ACTBP3"]["grch37_source"] == "ncbi"
    # No usable identifier: left null, never guessed from the symbol.
    assert rows["FRA1A"]["grch38_source"] is None
    assert rows["FRA1A"]["grch38_chr"] is None

    assert report.rows == 6
    assert report.gencode_release == 50
    assert report.chromosome_conflicts == 0
    tally = {step.assembly: step for step in report.steps}
    assert (tally["GRCh38"].gencode, tally["GRCh38"].ncbi) == (4, 1)
    assert (tally["GRCh37"].gencode, tally["GRCh37"].ncbi) == (3, 2)
    assert tally["GRCh38"].missing == tally["GRCh37"].missing == 1


def test_resolve_asks_the_fallback_only_for_unresolved_genes(cascade):
    genes, tables = cascade
    fallback = _fallback({})
    co.resolve(genes, tables, fallback=fallback, assemblies=["GRCh38", "GRCh37"])
    # SHANK3, ACACA and PAR resolved from GENCODE in both; FRA1A has no NCBI id.
    assert fallback.asked == ["100", "5"]


def test_resolve_works_without_a_fallback(cascade):
    genes, tables = cascade
    frame, report = co.resolve(genes, tables, assemblies=["GRCh38", "GRCh37"])
    assert frame["grch38_source"].to_list().count(None) == 2
    assert {step.ncbi for step in report.steps} == {0}


def test_resolve_keeps_a_duplicated_gene_on_one_chromosome(cascade):
    """A pseudoautosomal gene is annotated twice; both assemblies must agree."""
    genes, tables = cascade
    frame, report = co.resolve(genes, tables, assemblies=["GRCh38", "GRCh37"])
    par = frame.to_dicts()[5]
    assert par["grch38_chr"] == par["grch37_chr"] == "X"
    assert {step.ambiguous for step in report.steps} == {1}
    assert report.chromosome_conflicts == 0


def test_resolve_counts_a_cross_assembly_chromosome_conflict():
    genes = pl.DataFrame({"ensg": ["ENSG1"], "ncbi_id": ["1"]})
    tables = {
        "GRCh38": _table(("ENSG1", "G", "1", 10, 20, "+")),
        "GRCh37": _table(("ENSG1", "G", "4", 10, 20, "+")),
    }
    _, report = co.resolve(genes, tables, assemblies=["GRCh38", "GRCh37"])
    assert report.conflict_rows == (0,)
    assert report.chromosome_conflicts == 1
    assert "disagree on chromosome" in report.describe()


def test_resolve_takes_strand_from_the_first_assembly_that_has_it(cascade):
    genes, tables = cascade
    fallback = _fallback({"5": [("GRCh37", "7", 150, 250, "-")]})
    frame, _ = co.resolve(
        genes,
        tables,
        fallback=fallback,
        assemblies=["GRCh38", "GRCh37"],
    )
    strands = dict(zip(genes["symbol"], frame["strand"], strict=True))
    assert strands["ACACA"] == "-"
    # Placed by GRCh37 alone, so its strand is the one that survives.
    assert strands["ACTBP3"] == "-"
    assert strands["FRA1A"] is None
    assert co.STRAND_COLUMN not in [
        name for name in frame.columns if name.startswith("grch")
    ]


def test_resolve_preserves_input_row_order(cascade):
    genes, tables = cascade
    frame, _ = co.resolve(genes, tables, assemblies=["GRCh38", "GRCh37"])
    assert frame.height == genes.height
    assert frame["grch38_chr"].to_list() == ["22", "17", "10", None, None, "X"]


def test_resolve_needs_a_table_for_every_assembly(cascade):
    genes, tables = cascade
    with pytest.raises(ValueError, match=r"no GENCODE table supplied for \['GRCh37'\]"):
        co.resolve(genes, {"GRCh38": tables["GRCh38"]}, assemblies=["GRCh38", "GRCh37"])


def test_resolve_names_a_missing_identifier_column(cascade):
    genes, tables = cascade
    with pytest.raises(ValueError, match="no 'entrez' column"):
        co.resolve(genes, tables, ncbi_column="entrez")


def test_resolve_treats_a_blank_identifier_as_absent():
    genes = pl.DataFrame({"ensg": ["", "  "], "ncbi_id": ["", None]})
    tables = {"GRCh38": _table(("ENSG1", "G", "1", 10, 20, "+"))}
    fallback = _fallback({})
    frame, report = co.resolve(genes, tables, fallback=fallback)
    assert frame["grch38_source"].to_list() == [None, None]
    assert fallback.asked == []
    assert report.steps[0].missing == 2


def test_report_describe_summarises_each_step(cascade):
    genes, tables = cascade
    _, report = co.resolve(
        genes, tables, assemblies=["GRCh38", "GRCh37"], gencode_release=50
    )
    text = report.describe()
    assert "6 genes from GENCODE v50" in text
    assert "GRCh38: 4 gencode (66.7%)" in text
    assert "release not recorded" not in text


def test_build_wires_the_fetchers_together(tmp_path: Path, cascade):
    genes, _ = cascade
    urls = {a: gencode.url_for(50, a) for a in ("GRCh38", "GRCh37")}
    fetcher = FakeFetcher(
        text={GENCODE_LISTING: "release_50/"},
        heads={url: {} for url in urls.values()},
        streams={
            urls["GRCh38"]: gtf(
                ("ENSG00000251322.9", "SHANK3", "chr22", 50674408, 50733212, "+"),
            ),
            urls["GRCh37"]: gtf(
                ("ENSG00000251322.9", "SHANK3", "chr22", 51112843, 51171726, "+"),
            ),
        },
        json={
            ncbi.ESUMMARY: payload(
                ("5", [("GCF_000001405.40", "NC_000007.14", 99, 199)]),
            ),
        },
    )
    frame, report = co.build(fetcher, tmp_path, genes)
    assert report.gencode_release == 50
    rows = dict(zip(genes["symbol"], frame.to_dicts(), strict=True))
    assert rows["SHANK3"]["grch38_source"] == "gencode"
    assert rows["ACTBP3"]["grch38_chr"] == "7"
    assert (tmp_path / "gencode_v50_grch37.parquet").is_file()


def test_build_can_skip_the_ncbi_fallback(tmp_path: Path, cascade):
    genes, _ = cascade
    urls = {a: gencode.url_for(50, a) for a in ("GRCh38", "GRCh37")}
    fetcher = FakeFetcher(
        heads={url: {} for url in urls.values()},
        streams={
            urls["GRCh38"]: gtf(
                ("ENSG00000251322.9", "SHANK3", "chr22", 50674408, 50733212, "+"),
            ),
            urls["GRCh37"]: gtf(
                ("ENSG00000251322.9", "SHANK3", "chr22", 51112843, 51171726, "+"),
            ),
        },
    )
    _, report = co.build(fetcher, tmp_path, genes, release=50, use_ncbi=False)
    assert {step.ncbi for step in report.steps} == {0}
    assert ("POST", ncbi.ESUMMARY) not in fetcher.calls
