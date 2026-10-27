"""``POST /api/compare``, ``GET /api/backgrounds`` and ``POST /api/convert``.

The point of the endpoint is reproducibility: the numbers must be the Compare
view's, a My lists file or share link must work as a set, and the
``request.json`` a Compare download carries must regenerate the same table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genetrek.api.routes import create_router
from genetrek.config.loader import load_config
from genetrek.models.schemas import CompareRequest, CompareSet
from genetrek.paths import ENV_VAR
from genetrek.services import compare as cmp
from genetrek.services import compare_export as export
from genetrek.services import user_lists as ul

BACKGROUNDS = {
    "backgrounds": [
        {"name": "Coding", "column": "gene_type", "equals": "protein-coding"},
        {"name": "All genes"},
    ],
    "covariates": [{"name": "Score tertile", "column": "score", "bins": 3}],
}


@pytest.fixture
def tree(aliased_resources, monkeypatch):
    (aliased_resources.root / "config" / "backgrounds.yaml").write_text(
        yaml.safe_dump(BACKGROUNDS, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_VAR, str(aliased_resources.root))
    return aliased_resources


@pytest.fixture
def client(tree) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(load_config(tree.root)))
    return TestClient(app)


def _keys(frame: pl.DataFrame, column: str) -> frozenset[str]:
    return cmp.keys_where(frame.filter(pl.col(column))["ncbi_id"])


@pytest.fixture
def measured_client(measured_resources, monkeypatch) -> TestClient:
    (measured_resources.root / "config" / "backgrounds.yaml").write_text(
        yaml.safe_dump(BACKGROUNDS, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_VAR, str(measured_resources.root))
    app = FastAPI()
    app.include_router(create_router(load_config(measured_resources.root)))
    return TestClient(app)


class TestCoverage:
    """A set that did not measure every gene shrinks the whole comparison."""

    def test_the_universe_is_the_measured_background(self, measured_client):
        body = measured_client.post(
            "/api/compare",
            json={
                "sets": [{"field": "flag_a"}, {"field": "flag_m"}],
                "background": "All genes",
            },
        ).json()
        background = body["background"]
        assert (background["size"], background["effective_size"]) == (20, 12)
        assert background["restricted_by"] == ["Flag M"]
        flag_a = body["sets"][0]
        # flag_a is every even gene: 10, of which 14..20 (4) were not measured.
        assert (flag_a["n_not_measured"], flag_a["n_outside_background"]) == (4, 0)
        assert flag_a["n_in_background"] == 6
        assert "40% left out" in body["warnings"][0]
        assert "less the 8 that Flag M did not measure" in body["methods"]

    def test_the_pair_is_tested_in_the_measured_universe(self, measured_client):
        body = measured_client.post(
            "/api/compare",
            json={
                "sets": [{"field": "flag_a"}, {"field": "flag_m"}],
                "background": "All genes",
                "adjust_for": [],
            },
        ).json()
        (pair,) = body["pairs"]
        # Within genes 1-12: flag_a = {2,4,...,12} (6), flag_m = {1..4} (4).
        expected = cmp.fisher_2x2(2, 4, 2, 4)
        assert pair["overlap"] == 2
        assert pair["p_value"] == pytest.approx(expected.p_value)

    def test_full_coverage_reports_no_restriction(self, client):
        body = client.post(
            "/api/compare",
            json={"sets": [{"field": "flag_a"}, {"field": "flag_b"}]},
        ).json()
        assert body["background"]["effective_size"] == body["background"]["size"]
        assert body["background"]["restricted_by"] == []
        assert body["warnings"] == []


class TestBackgrounds:
    def test_lists_backgrounds_and_covariates(self, client):
        body = client.get("/api/backgrounds").json()
        assert [b["name"] for b in body["backgrounds"]] == ["Coding", "All genes"]
        assert body["backgrounds"][1]["size"] == 20
        assert body["covariates"][0]["name"] == "Score tertile"


class TestCompare:
    def post(self, client, **body: object):
        return client.post("/api/compare", json=body)

    def test_matches_the_service(self, client, tree):
        response = self.post(
            client,
            sets=[{"field": "flag_a"}, {"field": "flag_b"}],
            background="All genes",
            adjust_for=[],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        frame = tree.frame
        expected = cmp.compare(
            [
                cmp.GeneSet("a", "A", "A", "database", _keys(frame, "flag_a")),
                cmp.GeneSet("b", "B", "B", "database", _keys(frame, "flag_b")),
            ],
            cmp.keys_where(frame["ncbi_id"]),
            background="All genes",
        ).pairs[0]
        (pair,) = body["pairs"]
        assert pair["overlap"] == expected.overlap
        assert pair["p_value"] == pytest.approx(expected.p_value)
        assert pair["adjusted"] is None
        assert body["adjusted_for"] == []
        assert "POST /api/compare" in body["methods"]

    def test_an_alias_names_the_field(self, client):
        body = self.post(client, sets=[{"field": "flag"}, {"field": "flag_b"}]).json()
        assert body["sets"][0]["field"] == "flag_a"

    def test_covariates_apply_by_default(self, client):
        body = self.post(client, sets=[{"field": "flag_a"}, {"field": "flag_b"}]).json()
        assert body["adjusted_for"] == ["Score tertile"]
        assert body["pairs"][0]["adjusted"]["adjusted_for"] == "Score tertile"
        assert body["background"]["name"] == "Coding"

    def test_identifiers_are_resolved_and_reported(self, client):
        body = self.post(
            client,
            sets=[
                {"field": "flag_a"},
                {"label": "Mine", "genes": ["GENE02", "4", "NOPE"]},
            ],
            background="All genes",
        ).json()
        mine = body["sets"][1]
        assert (mine["label"], mine["n_input"], mine["n_genes"]) == ("Mine", 3, 2)
        assert mine["unmatched"] == ["NOPE"]

    def test_a_lists_file_and_a_share_token_are_sets(self, client, tree):
        now = datetime.now(UTC)
        lst = ul.create(
            "My list",
            ["2", "4", "6", "999999"],
            source="paste",
            release="",
            key="ncbi_id",
            now=now,
        )
        file = ul.to_export_json(
            [lst],
            df=tree.frame,
            key="ncbi_id",
            symbol="symbol",
            release="",
            now=now,
        )
        token = ul.encode_share([lst], release="", key="ncbi_id")
        body = self.post(
            client,
            sets=[
                {"lists_file": yaml.safe_load(file)},
                {"share": token, "label": "Shared"},
                {"field": "flag_a"},
            ],
            background="All genes",
        ).json()
        from_file, from_link = body["sets"][0], body["sets"][1]
        assert from_file["source"] == "lists_file"
        assert from_link["source"] == "share"
        assert from_file["n_not_in_database"] == 1
        assert body["pairs"][0]["jaccard"] == pytest.approx(1.0)

    def test_regions_carry_genes_only_on_request(self, client):
        sets = [{"field": "flag_a"}, {"field": "flag_b"}]
        bare = self.post(client, sets=sets).json()
        assert all(r["genes"] is None for r in bare["regions"])
        full = self.post(client, sets=sets, include_genes=True).json()
        assert all(len(r["genes"]) == r["size"] for r in full["regions"])

    def test_a_compare_download_replays_identically(self, client, tree):
        """request.json from the Compare view regenerates the same statistics."""
        frame = tree.frame
        universe = cmp.keys_where(frame["ncbi_id"])
        comparison = cmp.compare(
            [
                cmp.GeneSet(
                    "db:flag_a",
                    "Flag A",
                    "Flag A",
                    "database",
                    _keys(frame, "flag_a"),
                    "flag_a",
                ),
                cmp.GeneSet(
                    "list:x", "Mine", "Mine", "my_list", frozenset({"1", "2", "3", "5"})
                ),
            ],
            universe,
            background="All genes",
        )
        request = export.request_body(comparison)
        CompareRequest.model_validate(request)  # the body is a valid request
        body = client.post("/api/compare", json=request).json()
        (pair,) = body["pairs"]
        expected = comparison.pairs[0]
        assert pair["overlap"] == expected.overlap
        assert pair["p_value"] == pytest.approx(expected.p_value)
        assert pair["odds_ratio"] == pytest.approx(expected.odds_ratio)

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ({"sets": [{"field": "flag_a"}]}, "at least 2"),
            (
                {"sets": [{"field": "flag_a", "genes": ["1"]}, {"field": "flag_b"}]},
                "exactly one",
            ),
            ({"sets": [{"field": "nope"}, {"field": "flag_b"}]}, "nope"),
            ({"sets": [{"field": "score"}, {"field": "flag_b"}]}, "not a gene list"),
            (
                {"sets": [{"field": "flag_a"}, {"field": "flag_b"}], "background": "X"},
                "unknown background",
            ),
            (
                {
                    "sets": [{"field": "flag_a"}, {"field": "flag_b"}],
                    "adjust_for": ["X"],
                },
                "unknown covariate",
            ),
            ({"sets": [{"field": "flag_a"}, {"share": "!!!"}]}, "set 2"),
            (
                {"sets": [{"field": "flag_a"}, {"field": "flag_b"}], "colour": "red"},
                "extra",
            ),
        ],
    )
    def test_bad_requests_are_422(self, client, body, fragment):
        response = client.post("/api/compare", json=body)
        assert response.status_code == 422
        assert fragment.lower() in response.text.lower()

    def test_a_share_for_another_key_is_refused(self, client):
        lst = ul.create(
            "x",
            ["HGNC:5"],
            source="paste",
            release="",
            key="hgnc_id",
            now=datetime.now(UTC),
        )
        token = ul.encode_share([lst], release="", key="hgnc_id")
        response = client.post(
            "/api/compare",
            json={"sets": [{"field": "flag_a"}, {"share": token}]},
        )
        assert response.status_code == 422
        assert "keyed on" in response.text


class TestConvertPost:
    def test_matches_the_get(self, client):
        got = client.get("/api/convert", params={"ids": "GENE02,4,NOPE"}).json()
        posted = client.post(
            "/api/convert", json={"ids": ["GENE02", "4", "NOPE"]}
        ).json()
        assert posted == got

    def test_role_selection(self, client):
        posted = client.post(
            "/api/convert",
            json={"ids": ["GENE02"], "from": "symbol", "to": ["ncbi"]},
        ).json()
        assert list(posted["id_columns"]) == ["ncbi"]


def test_the_api_reference_documents_every_request_key():
    """A key the endpoint accepts but the prose never mentions is a key nobody
    will find."""
    reference = (
        Path(__file__).parent.parent / "resources" / "content" / "api-reference.md"
    ).read_text(encoding="utf-8")
    for name in (*CompareRequest.model_fields, *CompareSet.model_fields):
        assert f"`{name}`" in reference, name
    for route in ("POST /api/compare", "/api/backgrounds", "POST /api/convert"):
        assert route in reference, route
