"""Tests for ``genetrek-db init``.

The command is driven with a fake fetcher, so the whole build -- download, the
coordinate cascade, the write, and the validation afterwards -- runs offline and
deterministically.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from fakes import full_fetcher
from typer.testing import CliRunner

from genetrek import paths
from genetrek.cli import app, init_cmds
from genetrek.content import CONTENT_PAGES
from genetrek.db import Database

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

BUILD_CONFIG: dict[str, Any] = {
    "release": "latest",
    "assemblies": ["GRCh38", "GRCh37"],
    "gencode_release": None,
    "use_ncbi_fallback": True,
    "columns": {
        "symbol": "primary",
        "hgnc_id": "secondary",
        "name": "secondary",
        "locus_group": "secondary",
        "location": "secondary",
        "alias_symbol": "secondary",
        "prev_symbol": "secondary",
        "entrez_id": "secondary",
        "ensembl_gene_id": "secondary",
        "omim_id": "secondary",
        "mane_select": "secondary",
    },
    "coordinates": "secondary",
}


@pytest.fixture
def fetcher(monkeypatch: pytest.MonkeyPatch):
    """Replace the HTTP client with the canned one, keeping every call visible."""
    fake = full_fetcher()
    monkeypatch.setattr(init_cmds, "HttpFetcher", lambda **_: fake)
    return fake


@pytest.fixture
def source(resources):
    """The fixture resources tree, with a build config added."""
    (resources.root / paths.LAYOUT["parquet"]).parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path = resources.root / init_cmds.BUILD_CONFIG
    path.write_text(yaml.safe_dump(BUILD_CONFIG), encoding="utf-8")
    static = paths.static_dir(resources.root) / "ucsc"
    static.mkdir(parents=True, exist_ok=True)
    (static / "hub.txt").write_text("hub genetrek\n", encoding="utf-8")
    return resources


@pytest.fixture
def run(source, fetcher):
    """Invoke ``init`` against the fixture tree. ``fetcher`` is always installed."""
    del fetcher

    def invoke(*args: str, **kwargs: Any):
        return runner.invoke(
            app,
            ["--resources", str(source.root), "init", *args],
            **kwargs,
        )

    return invoke


class TestDryRun:
    def test_it_reports_the_plan_and_writes_nothing(self, run, tmp_path):
        target = tmp_path / "built"
        result = run("--output", str(target), "--dry-run")
        assert result.exit_code == 0
        assert "keeping 11 HGNC column(s)" in result.output
        assert "--dry-run" in result.output
        assert not target.exists()

    def test_it_names_the_renames_and_the_visibilities(self, run, tmp_path):
        output = run("--output", str(tmp_path / "b"), "--dry-run").output
        assert "symbol" in output
        assert "hgnc_symbol" in output
        assert "entrez_id" in output
        assert "ncbi_id" in output
        assert "primary" in output

    def test_it_does_not_fetch_any_coordinates(self, run, fetcher, tmp_path):
        run("--output", str(tmp_path / "b"), "--dry-run")
        assert not [kind for kind, _ in fetcher.calls if kind == "STREAM"]

    def test_it_leaves_the_live_database_untouched(self, run, source):
        before = (source.root / paths.LAYOUT["parquet"]).read_bytes()
        run("--dry-run")
        assert (source.root / paths.LAYOUT["parquet"]).read_bytes() == before


class TestBuildIntoANewTree:
    @pytest.fixture
    def built(self, run, tmp_path: Path):
        target = tmp_path / "built"
        result = run("--output", str(target), "--yes")
        assert result.exit_code == 0, result.output
        return target, result

    def test_it_writes_a_complete_resources_tree(self, built):
        target, _ = built
        for kind in ("parquet", "fields_yaml", "external_links", "badges"):
            assert (target / paths.LAYOUT[kind]).is_file(), kind

    def test_the_result_opens_and_validates(self, built):
        target, _ = built
        database = Database.open(target)
        assert database.n_rows == 5
        assert database.primary_key == "hgnc_id"
        report = database.validate()
        assert not report.errors, report.describe()

    def test_it_reports_every_file_it_wrote(self, built):
        _, result = built
        assert "genetrek.parquet" in result.output
        assert "fields_description.yaml" in result.output
        assert "external_links.yaml" in result.output
        assert "validate:" in result.output

    def test_the_links_reference_the_new_column_names(self, built):
        target, _ = built
        links = yaml.safe_load(
            (target / paths.LAYOUT["external_links"]).read_text(),
        )["links"]
        urls = " ".join(link["url"] for link in links)
        assert "{hgnc_symbol}" in urls
        assert "{symbol}" not in urls.replace("{hgnc_symbol}", "")

    def test_the_webapp_config_the_build_cannot_generate_is_copied(self, built):
        target, result = built
        assert (target / paths.LAYOUT["token_colors"]).is_file()
        assert (target / paths.LAYOUT["site_description"]).is_file()
        # Every prose page, not just the one that used to be a LAYOUT entry:
        # a built tree that is missing one does not start.
        for page in CONTENT_PAGES:
            assert paths.content_path(page.slug, target).is_file(), page.slug
        assert "copied" in result.output

    def test_the_static_companion_files_are_carried_over(self, built):
        """A rebuild must not drop the UCSC hub: nothing can regenerate it."""
        target, _ = built
        assert (paths.static_dir(target) / "ucsc" / "hub.txt").is_file()

    def test_an_existing_static_tree_is_left_alone(self, run, tmp_path: Path):
        """Curated files win over anything a build would copy in."""
        target = tmp_path / "preserved"
        static = paths.make_tree(target) / "static" / "ucsc"
        static.mkdir(parents=True, exist_ok=True)
        (static / "hub.txt").write_text("mine\n", encoding="utf-8")
        assert run("--output", str(target), "--yes").exit_code == 0
        assert (static / "hub.txt").read_text() == "mine\n"

    def test_a_new_tree_needs_no_backup(self, built):
        target, _ = built
        assert not (target / "processed_data" / ".backups").exists()

    def test_the_history_records_the_release(self, built):
        target, _ = built
        entries = Database.open(target).history()
        assert entries
        assert "HGNC 20260828" in entries[-1]["note"]
        assert "GENCODE v50" in entries[-1]["note"]

    def test_it_suggests_how_to_run_the_result(self, built):
        target, result = built
        assert f"GENETREK_RESOURCES={target}" in result.output


class TestOverrideFlags:
    def test_visibility_flags_reach_the_metadata(self, run, tmp_path):
        target = tmp_path / "built"
        result = run(
            "--output",
            str(target),
            "--primary",
            "hgnc_id",
            "--hidden",
            "location",
            "--yes",
        )
        assert result.exit_code == 0, result.output
        fields = Database.open(target).fields
        assert fields["hgnc_id"].visibility == "primary"
        assert fields["hgnc_location"].visibility == "hidden"

    def test_drop_removes_the_column_entirely(self, run, tmp_path):
        target = tmp_path / "built"
        assert (
            run("--output", str(target), "--drop", "mane_select", "--yes").exit_code
            == 0
        )
        assert "mane_select" not in Database.open(target).fields

    def test_dropping_the_primary_key_fails_before_anything_is_written(
        self,
        run,
        tmp_path,
    ):
        target = tmp_path / "built"
        result = run("--output", str(target), "--drop", "hgnc_id", "--yes")
        assert result.exit_code == 1
        assert "must keep 'hgnc_id'" in result.output
        assert not (target / paths.LAYOUT["parquet"]).exists()

    def test_conflicting_flags_are_refused(self, run, tmp_path):
        result = run(
            "--output",
            str(tmp_path / "b"),
            "--primary",
            "symbol",
            "--hidden",
            "symbol",
            "--dry-run",
        )
        assert result.exit_code == 1
        assert "pick one" in result.output

    def test_no_ncbi_leaves_the_fallback_coordinates_null(self, run, tmp_path):
        target = tmp_path / "built"
        assert run("--output", str(target), "--no-ncbi", "--yes").exit_code == 0
        frame = Database.open(target).frame
        assert frame["grch38_source"].to_list().count("ncbi") == 0

    def test_a_pinned_gencode_release_is_honoured(self, run, tmp_path, monkeypatch):
        monkeypatch.setattr(
            init_cmds, "HttpFetcher", lambda **_: full_fetcher(release=44)
        )
        target = tmp_path / "built"
        assert (
            run("--output", str(target), "--gencode-release", "44", "--yes").exit_code
            == 0
        )
        assert "GENCODE v44" in Database.open(target).fields["grch38_chr"].source


class TestReplacingTheLiveTree:
    def test_it_refuses_without_a_confirmation(self, run, source):
        before = (source.root / paths.LAYOUT["parquet"]).read_bytes()
        result = run()
        assert result.exit_code == 1
        assert "aborted" in result.output
        assert (source.root / paths.LAYOUT["parquet"]).read_bytes() == before

    def test_the_prompt_names_what_would_be_lost(self, run):
        output = run().output
        assert "about to REPLACE" in output
        assert "current: 15 fields, 20 genes" in output
        assert "discards" in output

    def test_confirming_replaces_the_database_and_keeps_a_backup(self, run, source):
        result = run("--yes")
        assert result.exit_code == 0, result.output
        database = Database.open(source.root)
        assert database.n_rows == 5
        assert database.primary_key == "hgnc_id"
        backups = sorted((source.root / "processed_data" / ".backups").iterdir())
        assert len(backups) == 1
        # Wider than a normal save: init rewrites the webapp config too.
        assert {p.name for p in backups[0].iterdir()} == {
            "genetrek.parquet",
            "fields_description.yaml",
            "external_links.yaml",
            "badges.yaml",
        }
        assert str(backups[0]) in result.output

    def test_the_replacement_validates_against_the_rewritten_config(self, run, source):
        assert run("--yes").exit_code == 0
        report = Database.open(source.root).validate()
        assert not report.errors, report.describe()

    def test_the_old_annotation_columns_are_gone(self, run, source):
        assert run("--yes").exit_code == 0
        fields = Database.open(source.root).fields
        assert "flag_a" not in fields
        assert "loeuf" not in fields


class TestFailures:
    def test_a_missing_build_config_is_reported(self, run, source, tmp_path):
        (source.root / init_cmds.BUILD_CONFIG).unlink()
        result = run("--output", str(tmp_path / "b"), "--dry-run")
        assert result.exit_code == 1
        assert "no build config" in result.output

    def test_an_explicit_config_path_is_honoured(self, run, tmp_path):
        path = tmp_path / "custom.yaml"
        spec = {**BUILD_CONFIG, "columns": {"hgnc_id": "primary", "symbol": "primary"}}
        path.write_text(yaml.safe_dump(spec), encoding="utf-8")
        result = run(
            "--config", str(path), "--output", str(tmp_path / "b"), "--dry-run"
        )
        assert result.exit_code == 0
        assert "keeping 2 HGNC column(s)" in result.output

    def test_an_unreadable_config_is_reported(self, run, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("columns:\n  symbol: featured\n", encoding="utf-8")
        result = run("--config", str(path), "--dry-run")
        assert result.exit_code == 1
        assert "expected one of" in result.output

    def test_an_upstream_failure_is_reported_not_traced(
        self, run, tmp_path, monkeypatch
    ):
        from fakes import FakeFetcher  # noqa: PLC0415

        monkeypatch.setattr(init_cmds, "HttpFetcher", lambda **_: FakeFetcher())
        result = run("--output", str(tmp_path / "b"), "--dry-run")
        assert result.exit_code == 1
        assert "not in the fixture" in result.output

    def test_a_non_terminal_run_without_yes_refuses(self, run, source):
        """Piped input must neither hang nor proceed unconfirmed."""
        before = (source.root / paths.LAYOUT["parquet"]).read_bytes()
        result = run()
        assert result.exit_code == 1
        assert (source.root / paths.LAYOUT["parquet"]).read_bytes() == before


def test_json_output_summarises_the_build(run, tmp_path):
    target = tmp_path / "built"
    result = run("--output", str(target), "--yes", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") :])
    assert payload["n_genes"] == 5
    assert payload["n_fields"] == 20
    assert payload["hgnc_release"] == "20260828"
    assert payload["gencode_release"] == 50
    assert payload["backup"] is None
