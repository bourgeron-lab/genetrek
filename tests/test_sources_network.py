"""Live smoke tests against the real upstreams.

Deselected by default (``addopts`` carries ``-m "not network"``); run them with
``uv run pytest -m network``. They assert only what should stay true across
releases -- that a URL resolves, that the shapes are what the parsers expect --
so a new HGNC or GENCODE release does not turn the suite red. The behaviour
itself is pinned by ``test_sources.py``, which never touches a network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from genetrek.db.sources import gencode, hgnc, ncbi
from genetrek.db.sources.fetcher import FetchError, HttpFetcher

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def fetcher() -> Iterator[HttpFetcher]:
    # 0.34 s between calls keeps eutils inside its keyless 3 req/s limit.
    with HttpFetcher(timeout=120, min_interval=0.34) as client:
        yield client


def test_hgnc_latest_resolves_to_a_dated_release(fetcher: HttpFetcher):
    url, stamp = hgnc.resolve_release(fetcher)
    assert url == hgnc.LATEST_URL
    assert len(stamp) == 8
    assert stamp.startswith("20")


def test_hgnc_archive_still_carries_a_quarterly_date(fetcher: HttpFetcher):
    url, stamp = hgnc.resolve_release(fetcher, "2025-04-01")
    assert stamp == "20250401"
    assert "archive" in url


def test_hgnc_refuses_a_date_that_was_never_published(fetcher: HttpFetcher):
    with pytest.raises(FetchError, match="monthly or quarterly archive"):
        hgnc.resolve_release(fetcher, "1999-01-01")


def test_hgnc_complete_set_has_the_columns_a_build_needs(
    fetcher: HttpFetcher,
    tmp_path: Path,
):
    release = hgnc.fetch(fetcher, tmp_path)
    frame = hgnc.read(release.cached)
    assert frame.height > 40_000
    assert {"hgnc_id", "symbol", "entrez_id", "ensembl_gene_id"} <= set(frame.columns)
    # hgnc_id is the only identifier without nulls -- the primary key rests on it.
    assert frame["hgnc_id"].null_count() == 0
    assert frame["hgnc_id"].n_unique() == frame.height
    # Read as text, so no identifier arrives mangled into a float string.
    assert not any(v.endswith(".0") for v in frame["entrez_id"].drop_nulls())


def test_gencode_newest_release_carries_both_assemblies(fetcher: HttpFetcher):
    resolved = gencode.resolve_release(fetcher)
    assert resolved.release >= 50
    assert set(resolved.urls) == {"GRCh38", "GRCh37"}
    assert "lift37" in resolved.url("GRCh37")


def test_ncbi_locationhist_still_covers_both_assemblies(fetcher: HttpFetcher):
    # SHANK3, whose GRCh38 and GRCh37 loci are both well known.
    frame = ncbi.fetch_coordinates(fetcher, ["85358"])
    by_assembly = {row["assembly"]: row for row in frame.to_dicts()}
    assert set(by_assembly) == {"GRCh38", "GRCh37"}
    assert by_assembly["GRCh38"]["chrom"] == by_assembly["GRCh37"]["chrom"] == "22"
    assert by_assembly["GRCh38"]["start"] == pytest.approx(50_674_408, abs=5_000)
    assert by_assembly["GRCh37"]["start"] == pytest.approx(51_112_843, abs=5_000)
