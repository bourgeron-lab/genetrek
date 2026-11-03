"""API 1.1: the version, error codes, ``POST /api/genes`` and ``POST /api/search``.

The synthetic tree (``conftest.py``): 20 genes, ncbi_id 1..20, ``GENE01`` ..
``GENE19`` plus ``GENE01`` again on row 20 (symbols are not unique), loci on
hg38, ``flag_a`` true on even ids and ``flag_b`` on ids 1..3.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genetrek_server.api import errors
from genetrek_server.api.routes import create_router
from genetrek_server.config.loader import load_config
from genetrek_server.paths import ENV_VAR

REFERENCE = Path(__file__).parents[1] / "resources" / "content" / "api-reference.md"


@pytest.fixture
def client(resources, monkeypatch) -> TestClient:
    monkeypatch.setenv(ENV_VAR, str(resources.root))
    app = FastAPI()
    app.include_router(create_router(load_config(resources.root)))
    return TestClient(app)


class TestVersion:
    def test_health_reports_api_and_database_release(self, client):
        body = client.get("/api/health").json()
        assert body["api_version"] == errors.API_VERSION
        assert "database_release" in body
        assert "release" in body  # the build, kept for existing callers

    def test_every_api_response_carries_the_header(self, client):
        for response in (
            client.get("/api/health"),
            client.get("/api/fields"),
            client.get("/api/fields/nope"),
            client.post("/api/genes", json={"ids": ["GENE02"]}),
        ):
            assert response.headers[errors.VERSION_HEADER] == errors.API_VERSION


class TestErrorCodes:
    @pytest.mark.parametrize(
        ("method", "path", "body", "status", "code"),
        [
            ("get", "/api/fields/nope", None, 404, "unknown_field"),
            ("get", "/api/genes/NOPE999", None, 404, "unknown_gene"),
            (
                "get",
                "/api/fields/flag_a/genes?id_type=bogus",
                None,
                400,
                "unknown_id_type",
            ),
            ("get", "/api/data?columns=nope", None, 400, "no_columns"),
            (
                "get",
                "/api/download/database?file_format=xml",
                None,
                400,
                "unknown_format",
            ),
            ("post", "/api/convert", {"ids": [" "]}, 400, "no_identifiers"),
            (
                "post",
                "/api/genes",
                {"ids": ["GENE02"], "columns": ["nope"]},
                404,
                "unknown_field",
            ),
            (
                "post",
                "/api/search",
                {"tokens": ["GENE02"], "genome": "hg99"},
                400,
                "unknown_genome",
            ),
            ("post", "/api/search", {"filters": ["score"]}, 400, "not_a_gene_list"),
            (
                "post",
                "/api/search",
                {"tokens": [], "text": "x"},
                422,
                "invalid_request",
            ),
        ],
    )
    def test_each_error_carries_its_code(
        self, client, method, path, body, status, code
    ):
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == status, response.text
        assert response.json()["code"] == code

    def test_detail_stays_a_string(self, client):
        assert isinstance(client.get("/api/fields/nope").json()["detail"], str)

    def test_validation_errors_keep_fastapis_list(self, client):
        body = client.post("/api/genes", json={"ids": ["A"], "extra": 1}).json()
        assert body["code"] == "invalid_request"
        assert isinstance(body["detail"], list)

    def test_an_undeclared_code_cannot_be_raised(self):
        with pytest.raises(ValueError, match="undeclared"):
            errors.ApiError(400, "made_up", "x")

    def test_every_code_is_documented(self):
        text = REFERENCE.read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.MULTILINE))
        assert set(errors.ERROR_CODES) <= documented
        assert "X-GeneTrek-API-Version" in text


class TestGenes:
    def test_input_order_and_values(self, client):
        body = client.post(
            "/api/genes",
            json={"ids": ["GENE04", "3", "GENE02"], "columns": ["flag_a", "score"]},
        ).json()
        assert [g["input"] for g in body["genes"]] == ["GENE04", "3", "GENE02"]
        assert [g["key"] for g in body["genes"]] == ["4", "3", "2"]
        assert body["columns"] == ["flag_a", "score"]
        assert body["genes"][0]["values"] == {"flag_a": True, "score": 1.0}
        assert body["genes"][0]["identifiers"]["symbol"] == "GENE04"

    def test_unknown_and_ambiguous_are_reported(self, client):
        body = client.post(
            "/api/genes", json={"ids": ["GENE02", "NOPE", "GENE01"]}
        ).json()
        assert body["not_found"] == ["NOPE"]
        assert body["ambiguous"] == ["GENE01"]
        assert body["n_found"] == 1

    def test_a_gene_named_twice_comes_back_once(self, client):
        body = client.post("/api/genes", json={"ids": ["GENE02", "2"]}).json()
        assert [g["input"] for g in body["genes"]] == ["GENE02"]

    def test_hidden_columns_by_name_only(self, client):
        default = client.post("/api/genes", json={"ids": ["GENE02"]}).json()
        assert "loeuf" not in default["columns"]
        named = client.post(
            "/api/genes", json={"ids": ["GENE02"], "columns": ["loeuf"]}
        ).json()
        assert named["columns"] == ["loeuf"]

    def test_unknown_keys_are_refused(self, client):
        response = client.post("/api/genes", json={"ids": ["GENE02"], "colums": []})
        assert response.status_code == 422


class TestSearch:
    def test_tokens_and_loci(self, client):
        body = client.post(
            "/api/search", json={"tokens": ["GENE02", "chr5:4000-4100", "NOPE"]}
        ).json()
        found = {row["ncbi_id"] for row in body["rows"]}
        assert found == {2, 4}
        assert body["unmatched"] == ["NOPE"]
        assert body["genome"] == "hg38"

    def test_text_reads_a_pasted_python_list(self, client):
        body = client.post("/api/search", json={"text": "['GENE02', 'GENE05']"}).json()
        assert {row["symbol"] for row in body["rows"]} == {"GENE02", "GENE05"}

    def test_filters_and_merge(self, client):
        only_filters = client.post("/api/search", json={"filters": ["flag_b"]}).json()
        assert only_filters["total"] == 3
        both = client.post(
            "/api/search",
            json={
                "tokens": ["GENE02", "GENE05"],
                "filters": ["flag_b"],
                "merge": "intersection",
            },
        ).json()
        assert {row["ncbi_id"] for row in both["rows"]} == {2}

    def test_paging(self, client):
        body = client.post(
            "/api/search", json={"filters": ["flag_a"], "limit": 3, "offset": 2}
        ).json()
        assert body["total"] == 10
        assert len(body["rows"]) == 3

    def test_needs_tokens_or_filters(self, client):
        response = client.post("/api/search", json={"tokens": []})
        assert response.json()["code"] == "no_identifiers"

    def test_same_genes_as_the_drawer(self, client, resources):
        """The endpoint is filter_dataframe; check one call against it directly."""
        from genetrek_server.services.search import filter_dataframe  # noqa: PLC0415

        config = load_config(resources.root)
        expected = filter_dataframe(
            config.df,
            "GENE02,GENE07",
            exact_match=True,
            selected_bool_cols=["flag_a"],
            results_merge="union",
            annotations_merge="union",
            selected_genome="hg38",
            roles=config.roles,
        )
        body = client.post(
            "/api/search", json={"tokens": ["GENE02", "GENE07"], "filters": ["flag_a"]}
        ).json()
        assert body["total"] == expected.height


@pytest.mark.parametrize("model", ["GenesRequest", "SearchRequest"])
def test_every_request_key_is_documented(model):
    """As for CompareRequest: a key nobody can find is a key nobody uses."""
    from genetrek_server.models import schemas  # noqa: PLC0415

    fields = getattr(schemas, model).model_fields
    keys = {info.alias or name for name, info in fields.items()}
    text = REFERENCE.read_text(encoding="utf-8")
    missing = sorted(k for k in keys if f"`{k}`" not in text and f'"{k}"' not in text)
    assert not missing, f"{model}: undocumented keys {missing}"
