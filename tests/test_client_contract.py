"""The ``genetrek`` client against this server: the contract between the two.

The real client, the real routers, the synthetic tree (``conftest.py``): the
client is handed the server's ``TestClient`` as its HTTP connection, so every
method runs end to end with no network. A change on either side that breaks
the other fails here, in the same PR.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import polars as pl
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from genetrek import (
    AmbiguousGene,
    Comparison,
    GeneTrek,
    NotAGeneList,
    Set,
    UnknownField,
    UnknownGene,
    UnresolvedWarning,
)
from genetrek import lists as codec

from genetrek_server import backgrounds
from genetrek_server.api import compare_routes
from genetrek_server.api.errors import API_VERSION
from genetrek_server.api.routes import create_router
from genetrek_server.config.loader import load_config
from genetrek_server.paths import ENV_VAR

BACKGROUNDS = {
    "backgrounds": [
        {"name": "Coding", "column": "gene_type", "equals": "protein-coding"},
        {"name": "All genes"},
    ],
    "covariates": [
        {"name": "Score tertile", "column": "score", "bins": 3},
        {"name": "Length", "span": "hg38", "bins": 3, "joint_bins": 2},
    ],
    "combinations": [["Score tertile", "Length"]],
}


@pytest.fixture
def gt(aliased_resources, monkeypatch) -> GeneTrek:
    (aliased_resources.root / "config" / "backgrounds.yaml").write_text(
        yaml.safe_dump(BACKGROUNDS, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_VAR, str(aliased_resources.root))
    # 20 synthetic genes: lower the floor so a gene list can be a background.
    monkeypatch.setattr(compare_routes, "MIN_USER_BACKGROUND", 3)
    monkeypatch.setattr(backgrounds, "MIN_USER_BACKGROUND", 3)
    app = FastAPI()
    app.include_router(create_router(load_config(aliased_resources.root)))
    return GeneTrek(http=TestClient(app))


class TestServer:
    def test_info_and_version(self, gt):
        info = gt.info()
        assert info["api_version"] == API_VERSION
        assert gt.api_version() == tuple(int(p) for p in API_VERSION.split("."))
        assert "database_release" in info
        assert info["server"] == gt.url


class TestColumns:
    def test_fields_is_a_table_in_display_order(self, gt):
        fields = gt.fields()
        assert isinstance(fields, pl.DataFrame)
        assert fields["name"][0] == "symbol"
        assert "loeuf" not in fields["name"].to_list()
        assert "loeuf" in gt.fields(include_hidden=True)["name"].to_list()

    def test_field_resolves_an_alias(self, gt):
        assert gt.field("constraint")["field"] == "loeuf"

    def test_unknown_field_raises(self, gt):
        with pytest.raises(UnknownField) as caught:
            gt.field("nope")
        assert caught.value.code == "unknown_field"

    def test_gene_list_and_values(self, gt):
        assert gt.gene_list("flag_b", id_type="ncbi") == ["1", "2", "3"]
        values = gt.values("score", id_type="ncbi")
        assert values.height == 20
        assert set(values.columns) == {"gene", "value"}

    def test_universe(self, gt):
        assert len(gt.universe("flag_a", id_type="ncbi")) == 20


class TestGenes:
    def test_genes_keeps_order_and_warns(self, gt):
        with pytest.warns(UnresolvedWarning, match="NOPE"):
            table = gt.genes(["GENE04", "NOPE", "3", "GENE02"], columns=["flag_a"])
        assert table["input"].to_list() == ["GENE04", "3", "GENE02"]
        assert table["flag_a"].to_list() == [True, False, True]
        assert "symbol" in table.columns

    def test_gene_and_its_errors(self, gt):
        row = gt.gene("GENE02", columns=["score"])
        assert row["key"] == "2"
        assert row["values"] == {"score": 0.5}
        with pytest.raises(UnknownGene):
            gt.gene("NOPE")
        with pytest.raises(AmbiguousGene):
            gt.gene("GENE01")

    def test_genesets(self, gt):
        sets = gt.genesets("GENE02")
        assert set(sets["name"]) == {"flag_a", "flag_b"}

    def test_convert_keeps_every_input(self, gt):
        table = gt.convert(["GENE02", "NOPE"], to=["ncbi"])
        assert table["input"].to_list() == ["GENE02", "NOPE"]
        assert table["matched_by"].to_list() == ["symbol", "unmatched"]
        assert str(table["ncbi"][0]) == "2"

    def test_search(self, gt):
        found = gt.search("['GENE02', 'GENE05']")
        assert set(found["symbol"]) == {"GENE02", "GENE05"}
        with pytest.raises(NotAGeneList):
            gt.search(filters=["score"])


class TestCompare:
    def test_compare_returns_tables(self, gt):
        result = gt.compare(
            [
                Set.field("flag_a"),
                Set.genes(["GENE02", "GENE04", "GENE05"], label="mine"),
            ]
        )
        assert isinstance(result, Comparison)
        assert result.pairs.height == 1
        assert {"odds_ratio", "ci95_low", "adjusted_odds_ratio"} <= set(
            result.pairs.columns
        )
        assert result.sets["label"].to_list()[1] == "mine"
        assert result.methods
        assert result.server == gt.url

    def test_infinite_ratios_are_floats(self, gt):
        # Every one of "mine" is on flag_a: the odds ratio is infinite.
        result = gt.compare(["flag_a", ["GENE02", "GENE04"]], background="All genes")
        assert math.isinf(result.pairs["odds_ratio"][0])

    def test_replay_reproduces(self, gt, tmp_path):
        first = gt.compare(["flag_a", "flag_b"], background="All genes")
        path = tmp_path / "request.json"
        first.to_request_json(path)
        again = gt.replay(path)
        assert again.pairs.equals(first.pairs)

    def test_a_share_link_set(self, gt):
        now = datetime.now(UTC)
        made = codec.create(
            "shared", ["2", "4"], source="paste", release="", key="ncbi_id", now=now
        )
        token = codec.encode_share([made], release="", key="ncbi_id")
        result = gt.compare(["flag_a", Set.lists(f"https://g.example/#lists={token}")])
        assert result.sets["n_genes"].to_list()[1] == 2

    def test_the_request_is_the_browsers(self, gt):
        """`request.json` is exactly the POST body, so the site's can be replayed."""
        result = gt.compare(["flag_a", "flag_b"], adjust_for=[])
        assert json.loads(result.to_request_json()) == {
            "sets": [{"field": "flag_a"}, {"field": "flag_b"}],
            "include_genes": False,
            "adjust_for": [],
        }


class TestBackgroundsAndCovariates:
    def test_a_gene_list_as_background(self, gt):
        result = gt.compare(
            ["flag_b", ["GENE02", "GENE04"]],
            background=Set.field("flag"),
            adjust_for=[],
        )
        assert result.background["kind"] == "gene_list"
        assert result.background["size"] == 10

    def test_two_covariates_crossed(self, gt):
        result = gt.compare(
            ["flag_a", "flag_b"],
            background="All genes",
            adjust_for=["Score tertile", "Length"],
        )
        assert result.adjusted_for == ["Score tertile + Length"]
        assert "adjusted_unavailable" in result.pairs.columns
        assert "adjusted_caveat" in result.pairs.columns

    def test_the_backgrounds_list_spans_and_pairs(self, gt):
        info = gt.backgrounds()
        assert info["combinations"] == [["Score tertile", "Length"]]


class TestDownload:
    def test_parquet_keeps_types(self, gt):
        table = gt.download()
        assert table.height == 20
        assert table.schema["flag_a"] == pl.Boolean

    def test_to_a_file(self, gt, tmp_path):
        path = gt.download("tsv", tmp_path / "db.tsv")
        assert path.read_text().startswith("ncbi_id\t") or path.stat().st_size > 0
