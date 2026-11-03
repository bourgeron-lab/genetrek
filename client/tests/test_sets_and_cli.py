"""Sets, the shared codec and tokens, and the command line."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import httpx
import respx
from typer.testing import CliRunner

from genetrek import Set
from genetrek import lists as codec
from genetrek.cli import app
from genetrek.models import ratio
from genetrek.tokens import split_tokens

URL = "https://genetrek.example.org"


class TestSets:
    def test_field_and_genes(self):
        assert Set.field("hc_ndd", label="HC").to_request() == {
            "field": "hc_ndd",
            "label": "HC",
        }
        assert Set.genes([" SHANK3", None, "", 85358]).to_request() == {
            "genes": ["SHANK3", "85358"],
            "id_type": "auto",
        }

    def test_a_file_of_identifiers(self, tmp_path):
        path = tmp_path / "mine.tsv"
        path.write_text("# my genes\nSHANK3\textra\nCHD8\t\n", encoding="utf-8")
        assert Set.file(path).to_request() == {
            "genes": ["SHANK3", "CHD8"],
            "id_type": "auto",
            "label": "mine",
        }

    def test_a_share_link_sends_only_the_token(self):
        spec = Set.lists("https://g.example/#lists=abc_-123", index=1).to_request()
        assert spec == {"share": "abc_-123", "list_index": 1}

    def test_an_export_file(self, tmp_path):
        now = datetime.now(timezone.utc)
        made = codec.create(
            "x", ["1", "2"], source="paste", release="r", key="k", now=now
        )
        text = codec.to_export_json(
            [made],
            df=__import__("polars").DataFrame({"k": ["1", "2"]}),
            key="k",
            symbol=None,
            release="r",
            now=now,
        )
        path = tmp_path / "lists.json"
        path.write_text(text, encoding="utf-8")
        spec = Set.file(path).to_request()
        assert spec["lists_file"]["key"] == "k"


class TestSharedCode:
    def test_codec_round_trip(self):
        now = datetime.now(timezone.utc)
        made = codec.create(
            "a",
            ["HGNC:5", "HGNC:10"],
            source="paste",
            release="r",
            key="hgnc_id",
            now=now,
        )
        shared = codec.decode_share(
            codec.encode_share([made], release="r", key="hgnc_id")
        )
        assert shared.lists[0].genes == frozenset({"HGNC:5", "HGNC:10"})

    def test_tokens_read_a_pasted_list(self):
        assert split_tokens("genes <- c('SHANK3', \"CHD8\")") == ["SHANK3", "CHD8"]

    def test_ratios(self):
        assert math.isinf(ratio("Inf"))
        assert ratio(None) is None
        assert ratio(2) == 2.0


class TestCli:
    def test_config_add_list_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENETREK_CONFIG", str(tmp_path / "c.toml"))
        runner = CliRunner()
        result = runner.invoke(
            app, ["config", "add", "dev", "https://dev.example.org", "--default"]
        )
        assert result.exit_code == 0, result.output
        listed = runner.invoke(app, ["config", "list"])
        assert "dev\thttps://dev.example.org (default)" in listed.output
        assert runner.invoke(app, ["config", "add", "bad name", "x"]).exit_code == 1

    @respx.mock
    def test_list_prints_one_gene_per_line(self, monkeypatch):
        monkeypatch.setenv("GENETREK_URL", URL)
        respx.get(f"{URL}/api/fields/hc_ndd/genes").mock(
            return_value=httpx.Response(
                200,
                json={
                    "genes": [
                        {"gene": "SHANK3", "value": True},
                        {"gene": "CHD8", "value": True},
                    ]
                },
            )
        )
        result = CliRunner().invoke(app, ["list", "hc_ndd"])
        assert result.exit_code == 0, result.output
        assert result.stdout.split() == ["SHANK3", "CHD8"]

    @respx.mock
    def test_a_server_error_exits_1_with_its_code(self, monkeypatch):
        monkeypatch.setenv("GENETREK_URL", URL)
        respx.get(f"{URL}/api/fields/nope").mock(
            return_value=httpx.Response(
                404, json={"detail": "unknown field", "code": "unknown_field"}
            )
        )
        result = CliRunner().invoke(app, ["field", "nope"])
        assert result.exit_code == 1
        assert "[unknown_field]" in result.output

    @respx.mock
    def test_json_output(self, monkeypatch):
        monkeypatch.setenv("GENETREK_URL", URL)
        respx.get(f"{URL}/api/fields/hc_ndd/genes").mock(
            return_value=httpx.Response(
                200, json={"genes": [{"gene": "SHANK3", "value": True}]}
            )
        )
        result = CliRunner().invoke(app, ["--json", "list", "hc_ndd"])
        assert json.loads(result.stdout) == ["SHANK3"]
