"""Tests for the HTTP surface: the health probe and the legacy-URL routes.

The legacy routes exist because the previous GeneTrek published them and third
parties cite them, so what is asserted here is the *URL contract* rather than
the implementation behind it.
"""

from __future__ import annotations

import mimetypes
import zipfile
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from genetrek import media_types
from genetrek.api.routes import create_compat_router, create_router
from genetrek.config.loader import load_config
from genetrek.paths import ENV_VAR

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def client(resources, monkeypatch) -> TestClient:
    """An app carrying only the routers, so no NiceGUI mount is involved."""
    monkeypatch.setenv(ENV_VAR, str(resources.root))
    app = FastAPI()
    app.include_router(create_router(load_config(resources.root)))
    app.include_router(create_compat_router())
    return TestClient(app)


@pytest.fixture
def vcf_archive(resources) -> Path:
    archive = resources.root / "static" / "Rolland_NatMed_2023.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("cohort.vcf", "##fileformat=VCFv4.2\n")
    return archive


class TestHealth:
    def test_reports_the_loaded_frame(self, client, resources):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["rows"] == resources.frame.height
        assert body["columns"] == resources.frame.width

    def test_release_defaults_to_unknown(self, client, monkeypatch):
        monkeypatch.delenv("GENETREK_RELEASE", raising=False)
        assert client.get("/api/health").json()["release"] == "unknown"

    def test_release_is_reported_when_set(self, client, monkeypatch):
        """The deployed image tag, so one request identifies what is running."""
        monkeypatch.setenv("GENETREK_RELEASE", "abc1234")
        assert client.get("/api/health").json()["release"] == "abc1234"

    def test_returns_only_scalars(self, client):
        """No row data: probes run every few seconds for the pod's lifetime."""
        body = client.get("/api/health").json()
        assert set(body) == {"status", "rows", "columns", "release"}
        assert all(isinstance(v, (str, int)) for v in body.values())


class TestDownloadVCF:
    def test_serves_the_archive(self, client, vcf_archive):
        response = client.get("/downloadVCF")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.content == vcf_archive.read_bytes()

    def test_missing_archive_is_a_404_not_a_crash(self, client):
        assert client.get("/downloadVCF").status_code == 404


class TestDownloadAllData:
    def test_redirects_onto_the_current_endpoint(self, client):
        response = client.get("/downloadAllData?filetype=tsv", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == (
            "/api/download/database?file_format=tsv"
        )

    def test_absent_filetype_defaults_to_tsv(self, client):
        response = client.get("/downloadAllData", follow_redirects=False)
        assert response.headers["location"].endswith("file_format=tsv")

    @pytest.mark.parametrize("filetype", ["json", "xlsx"])
    def test_retired_or_unknown_filetype_falls_back_to_tsv(self, client, filetype):
        """The old endpoint was lenient; a stale bookmark should still work.

        ``json`` is the interesting case: the old URL advertised it, and the
        export behind it is gone, so it has to land on the TSV rather than on a
        400 from the current endpoint.
        """
        response = client.get(
            f"/downloadAllData?filetype={filetype}",
            follow_redirects=False,
        )
        assert response.status_code == 307
        assert response.headers["location"].endswith("file_format=tsv")

    def test_the_redirect_target_actually_exists(self, client):
        """Guards against renaming the download route out from under this."""
        assert client.get("/downloadAllData?filetype=tsv").status_code == 200


class TestData:
    """The oldest endpoint, and until now the only one with no tests."""

    def test_defaults_to_the_primary_columns(self, client):
        body = client.get("/api/data").json()
        assert set(body) == {"data", "total", "page", "per_page"}
        assert list(body["data"][0]) == ["symbol", "flag_a"]
        assert body["total"] == 20
        assert len(body["data"]) == 20

    def test_pages_without_changing_the_total(self, client):
        body = client.get("/api/data?page=2&per_page=5").json()
        assert body["total"] == 20
        assert body["page"] == 2
        assert [row["symbol"] for row in body["data"]] == [
            "GENE06",
            "GENE07",
            "GENE08",
            "GENE09",
            "GENE10",
        ]

    def test_a_page_past_the_end_is_empty_not_an_error(self, client):
        assert client.get("/api/data?page=99").json()["data"] == []

    def test_columns_are_selected_in_the_order_asked_for(self, client):
        body = client.get("/api/data?columns=score,symbol&per_page=1").json()
        assert list(body["data"][0]) == ["score", "symbol"]

    def test_unknown_columns_are_dropped_silently(self, client):
        """Long-standing behaviour, pinned so a change to it is deliberate."""
        body = client.get("/api/data?columns=symbol,nope&per_page=1").json()
        assert list(body["data"][0]) == ["symbol"]

    def test_a_hidden_column_cannot_be_reached_here(self, client):
        """Unlike the lookup endpoints. /api/data gates on visibility."""
        assert client.get("/api/data?columns=loeuf").status_code == 400

    def test_an_alias_is_not_resolved_here(self, aliased_resources, monkeypatch):
        """Deliberate: the published endpoint keeps its exact-name contract."""
        monkeypatch.setenv(ENV_VAR, str(aliased_resources.root))
        app = FastAPI()
        app.include_router(create_router(load_config(aliased_resources.root)))
        assert TestClient(app).get("/api/data?columns=flag").status_code == 400

    def test_no_valid_columns_is_a_400(self, client):
        assert client.get("/api/data?columns=nope").status_code == 400

    @pytest.mark.parametrize("query", ["page=0", "per_page=0", "per_page=101"])
    def test_out_of_range_paging_is_rejected(self, client, query):
        assert client.get(f"/api/data?{query}").status_code == 422


class TestLegacyRoutesAreHidden:
    def test_absent_from_the_schema(self, client):
        """Compatibility shims, not part of the documented API."""
        paths_in_schema = client.get("/openapi.json").json()["paths"]
        assert "/downloadVCF" not in paths_in_schema
        assert "/downloadAllData" not in paths_in_schema
        assert "/api/health" in paths_in_schema


class TestGenomicsMediaTypes:
    """The files under /data are binary and are fetched by range request.

    Python maps ".vcf" to a vCard, so without this a bgzf VCF is served as
    "text/x-vcard; charset=utf-8" -- a text type, with a charset, on binary that
    igv.js reads a few bytes at a time through a tabix index.
    """

    @pytest.fixture(autouse=True)
    def _registered(self):
        media_types.register()

    @pytest.mark.parametrize(
        "filename",
        [
            "track.bb",
            "cohort.vcf.gz",
            "cohort.vcf.gz.tbi",
            "reads.bam",
            "reads.bam.bai",
        ],
    )
    def test_binary_formats_are_octet_stream(self, filename):
        assert mimetypes.guess_type(filename)[0] == "application/octet-stream"

    def test_vcard_mapping_is_displaced(self):
        """Guards the reason ".vcf" is in the list at all."""
        assert mimetypes.guess_type("cohort.vcf")[0] != "text/x-vcard"

    def test_gzip_is_still_reported_as_an_encoding(self):
        """Not as Content-Encoding by StaticFiles, but the file is bgzf.

        If it were ever sent as Content-Encoding: gzip, clients would silently
        decompress and every tabix range query would read the wrong offsets.
        """
        assert mimetypes.guess_type("cohort.vcf.gz")[1] == "gzip"

    def test_html_and_zip_are_left_alone(self):
        assert mimetypes.guess_type("description.html")[0] == "text/html"
        assert mimetypes.guess_type("archive.zip")[0] == "application/zip"

    def test_a_served_file_carries_the_type_and_no_charset(self, tmp_path: Path):
        """End to end through StaticFiles, which is what actually serves these."""
        (tmp_path / "track.bb").write_bytes(b"\xeb\xf2\x89\x87binary")
        app = FastAPI()
        app.mount("/data", StaticFiles(directory=tmp_path), name="data")
        response = TestClient(app).get("/data/track.bb")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert "charset" not in response.headers["content-type"]

    def test_a_served_file_supports_range_requests(self, tmp_path: Path):
        """UCSC reads a bigBed by seeking into it, never whole."""
        (tmp_path / "track.bb").write_bytes(bytes(range(256)))
        app = FastAPI()
        app.mount("/data", StaticFiles(directory=tmp_path), name="data")
        response = TestClient(app).get(
            "/data/track.bb",
            headers={"Range": "bytes=4-11"},
        )
        assert response.status_code == 206
        assert response.headers["content-range"] == "bytes 4-11/256"
        assert response.content == bytes(range(4, 12))
