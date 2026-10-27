"""Tests for the ``genetrek-db`` command line."""

from __future__ import annotations

import json
from typing import Any

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from genetrek import paths, schema
from genetrek.cli import app
from genetrek.db import Database
from genetrek.schema import today_stamp

runner = CliRunner()


@pytest.fixture
def run(resources):
    """Invoke the CLI against the fixture resources tree."""

    def invoke(*args: str, **kwargs: Any):
        return runner.invoke(app, ["--resources", str(resources.root), *args], **kwargs)

    return invoke


@pytest.fixture
def gene_list(tmp_path):
    path = tmp_path / "genes.txt"
    path.write_text("GENE02\nGENE03\n", encoding="utf-8")
    return path


class TestTopLevel:
    def test_no_arguments_shows_help(self):
        result = runner.invoke(app, [])
        assert "Inspect and maintain" in result.output

    def test_every_command_is_registered(self):
        result = runner.invoke(app, ["--help"])
        for command in (
            "list",
            "categories",
            "show",
            "get",
            "export",
            "resolve",
            "validate",
            "history",
            "add",
            "remove",
            "set",
            "sync",
            "diff",
        ):
            assert command in result.output

    def test_a_missing_resources_directory_is_a_clean_error(self, tmp_path):
        result = runner.invoke(app, ["--resources", str(tmp_path / "nope"), "validate"])
        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_invalid_metadata_is_a_clean_error(self, resources):
        resources.fields_yaml.write_text(
            "score:\n  visibility: bogus\n", encoding="utf-8"
        )
        result = runner.invoke(app, ["--resources", str(resources.root), "list"])
        assert result.exit_code == 1
        assert "invalid field" in result.output


class TestList:
    def test_lists_every_field(self, run, resources):
        result = run("list")
        assert result.exit_code == 0
        assert f"{resources.frame.width} field(s)" in result.output
        assert "symbol" in result.output

    def test_sizes_adds_a_memory_column(self, run):
        assert "in_memory" in run("list", "--sizes").output

    def test_sizes_are_hidden_by_default(self, run):
        assert "in_memory" not in run("list").output

    def test_visibility_filter(self, run):
        assert "2 field(s)" in run("list", "--visibility", "primary").output

    def test_type_filter(self, run):
        assert "2 field(s)" in run("list", "--type", "boolean").output

    def test_json_after_the_subcommand(self, run):
        """A global-only flag would have to precede the subcommand; nobody does that."""
        payload = json.loads(run("list", "--json").output)
        assert {row["field"] for row in payload} >= {"symbol", "score"}

    def test_json_before_the_subcommand_also_works(self, resources):
        result = runner.invoke(
            app,
            ["--resources", str(resources.root), "--json", "list"],
        )
        assert isinstance(json.loads(result.output), list)

    def test_on_disk_needs_pyarrow_or_works(self, run):
        """pyarrow is an optional extra, so either outcome is acceptable."""
        result = run("list", "--on-disk")
        if result.exit_code == 0:
            assert "on_disk" in result.output
        else:
            assert "needs pyarrow" in result.output


class TestShow:
    def test_boolean_field(self, run):
        result = run("show", "flag_a")
        assert "true" in result.output
        assert "Boolean" in result.output

    def test_numeric_field(self, run):
        result = run("show", "score")
        assert "median" in result.output

    def test_unknown_field(self, run):
        result = run("show", "nope")
        assert result.exit_code == 1
        assert "unknown field" in result.output

    def test_json_output(self, run):
        payload = json.loads(run("show", "flag_a", "--json").output)
        assert payload["field"] == "flag_a"
        assert payload["stats"]["n_true"] == 10


class TestGet:
    def test_writes_tsv_to_stdout(self, run):
        result = run("get", "score")
        assert result.output.splitlines()[0] == "symbol\tscore"
        assert len(result.output.strip().splitlines()) == 21

    def test_id_col_option(self, run):
        assert run("get", "score", "--id-col", "ncbi_id").output.startswith(
            "ncbi_id\tscore",
        )

    def test_drop_nulls_for_boolean_keeps_members(self, run):
        lines = run("get", "flag_b", "--drop-nulls").output.strip().splitlines()
        assert len(lines) == 4  # header plus three members

    def test_output_file(self, run, tmp_path):
        target = tmp_path / "out.tsv"
        result = run("get", "score", "-o", str(target))
        assert "wrote 20 row(s)" in result.output
        assert pl.read_csv(target, separator="\t").height == 20

    def test_unknown_id_col(self, run):
        result = run("get", "score", "--id-col", "nope")
        assert result.exit_code == 1
        assert "unknown identifier column" in result.output


class TestExport:
    def test_exports_everything(self, run, tmp_path, resources):
        target = tmp_path / "all.tsv"
        run("export", str(target))
        assert pl.read_csv(target, separator="\t").width == resources.frame.width

    def test_columns_option(self, run, tmp_path):
        target = tmp_path / "some.tsv"
        run("export", str(target), "--columns", "symbol,score")
        assert pl.read_csv(target, separator="\t").columns == ["symbol", "score"]

    def test_unknown_column_is_an_error(self, run, tmp_path):
        result = run("export", str(tmp_path / "x.tsv"), "--columns", "nope")
        assert result.exit_code == 1
        assert "unknown field" in result.output

    def test_gzip(self, run, tmp_path):
        target = tmp_path / "all.tsv"
        run("export", str(target), "--gzip")
        assert target.with_suffix(".tsv.gz").is_file()


class TestResolve:
    def test_resolves_a_file(self, run, gene_list):
        result = run("resolve", str(gene_list), "--id-type", "symbol")
        assert result.exit_code == 0
        assert "2/2 resolved" in result.output

    def test_exits_non_zero_when_something_fails(self, run, tmp_path):
        path = tmp_path / "g.txt"
        path.write_text("GENE02\nNOPE\n", encoding="utf-8")
        result = run("resolve", str(path), "--id-type", "symbol")
        assert result.exit_code == 1
        assert "which unmatched: NOPE" in result.output

    def test_unmatched_only_is_pipeable(self, run, tmp_path):
        path = tmp_path / "g.txt"
        path.write_text("GENE02\nNOPE\n", encoding="utf-8")
        result = run("resolve", str(path), "--id-type", "symbol", "--unmatched-only")
        assert result.output.strip() == "NOPE"

    def test_reads_stdin(self, run):
        result = run("resolve", "-", "--id-type", "symbol", input="GENE02\nGENE03\n")
        assert "2/2 resolved" in result.output

    def test_comma_separated_input(self, run, tmp_path):
        path = tmp_path / "g.txt"
        path.write_text("GENE02, GENE03\n", encoding="utf-8")
        assert "2/2 resolved" in run("resolve", str(path), "--id-type", "symbol").output

    def test_empty_input_is_an_error(self, run, tmp_path):
        path = tmp_path / "g.txt"
        path.write_text("\n\n", encoding="utf-8")
        result = run("resolve", str(path))
        assert result.exit_code == 1
        assert "no identifiers" in result.output

    def test_bad_id_type(self, run, gene_list):
        result = run("resolve", str(gene_list), "--id-type", "nonsense")
        assert result.exit_code == 1
        assert "unknown id_type" in result.output

    def test_json_output(self, run, gene_list):
        payload = json.loads(run("resolve", str(gene_list), "--json").output)
        assert payload["n_resolved"] == 2
        assert len(payload["rows"]) == 2


class TestValidate:
    def test_healthy_database(self, run):
        result = run("validate")
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_broken_database_exits_non_zero(self, run, resources):
        fields = schema.load_fields_yaml(resources.fields_yaml)
        fields["score"].value_type = "boolean"
        schema.write_fields_yaml(resources.fields_yaml, fields)
        result = run("validate")
        assert result.exit_code == 1
        assert "value_type" in result.output

    def test_json_output(self, run):
        payload = json.loads(run("validate", "--json").output)
        assert payload["ok"] is True
        assert payload["errors"] == []


class TestAdd:
    def test_adds_a_gene_list(self, run, gene_list, resources):
        result = run("add", str(gene_list), "--name", "mine", "--yes")
        assert result.exit_code == 0, result.output
        assert "saved" in result.output
        assert "mine" in Database.open(resources.root)

    def test_dry_run_writes_nothing(self, run, gene_list, resources):
        result = run("add", str(gene_list), "--name", "mine", "--dry-run")
        assert "--dry-run" in result.output
        assert "mine" not in Database.open(resources.root)

    def test_metadata_options_are_applied(self, run, gene_list, resources):
        run(
            "add",
            str(gene_list),
            "--name",
            "mine",
            "--fullname",
            "My List",
            "--visibility",
            "secondary",
            "--priority",
            "25",
            "--doi",
            "10.1/x",
            "--yes",
        )
        meta = Database.open(resources.root).fields["mine"]
        assert meta.fullname == "My List"
        assert meta.visibility == "secondary"
        assert meta.priority == 25
        assert meta.doi == "10.1/x"

    def test_unmatched_blocks_and_names_the_flag(self, run, tmp_path, resources):
        path = tmp_path / "g.txt"
        path.write_text("GENE02\nNOPE\n", encoding="utf-8")
        result = run("add", str(path), "--name", "mine", "--yes")
        assert result.exit_code == 1
        assert "--allow-unmatched" in result.output
        assert "--allow-ambiguous" not in result.output
        assert "mine" not in Database.open(resources.root)

    def test_allow_unmatched_proceeds(self, run, tmp_path, resources):
        path = tmp_path / "g.txt"
        path.write_text("GENE02\nNOPE\n", encoding="utf-8")
        result = run("add", str(path), "--name", "mine", "--allow-unmatched", "--yes")
        assert result.exit_code == 0, result.output
        assert int(Database.open(resources.root)["mine"].values.sum()) == 1

    def test_ambiguous_blocks_separately(self, run, tmp_path):
        path = tmp_path / "g.txt"
        path.write_text("GENE01\n", encoding="utf-8")
        result = run("add", str(path), "--name", "mine", "--yes")
        assert result.exit_code == 1
        assert "--allow-ambiguous" in result.output
        assert "--allow-unmatched" not in result.output

    def test_bad_missing_policy(self, run, gene_list):
        result = run("add", str(gene_list), "--name", "mine", "--missing", "nonsense")
        assert result.exit_code == 1
        assert "--missing must be" in result.output

    def test_fill_without_a_value(self, run, gene_list):
        result = run("add", str(gene_list), "--name", "mine", "--missing", "fill")
        assert result.exit_code == 1
        assert "needs --fill" in result.output

    def test_duplicate_name(self, run, gene_list):
        result = run("add", str(gene_list), "--name", "score", "--yes")
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_records_the_actor_and_note(self, run, gene_list, resources):
        run(
            "add",
            str(gene_list),
            "--name",
            "mine",
            "--yes",
            "--actor",
            "me",
            "--note",
            "why",
        )
        record = Database.open(resources.root).history()[-1]
        assert record["actor"] == "me"
        assert record["note"] == "why"

    def test_a_non_tty_without_yes_refuses(self, run, gene_list, resources):
        """Better to refuse than to hang or to write unasked in a pipeline."""
        result = run("add", str(gene_list), "--name", "mine")
        assert result.exit_code == 1
        assert "pass --yes" in result.output
        assert "mine" not in Database.open(resources.root)


class TestRemove:
    def test_removes_a_field(self, run, resources):
        result = run("remove", "small_count", "--yes")
        assert result.exit_code == 0
        assert "small_count" not in Database.open(resources.root)

    def test_unknown_field(self, run):
        result = run("remove", "nope", "--yes")
        assert result.exit_code == 1
        assert "unknown field" in result.output

    def test_referenced_field_is_protected(self, run, resources):
        """small_count is not code-dependent, so only the link reference blocks it."""
        (resources.root / "config" / "external_links.yaml").write_text(
            yaml.safe_dump(
                {"links": [{"name": "N", "url": "https://x/{small_count}"}]},
            ),
            encoding="utf-8",
        )
        result = run("remove", "small_count", "--yes")
        assert result.exit_code == 1
        assert "external_links.yaml" in result.output
        assert "small_count" in Database.open(resources.root)

    def test_force_still_refuses_to_write_a_broken_database(self, run, resources):
        """--force stages the removal, but saving would leave a dangling link."""
        result = run("remove", "ensg", "--yes", "--force")
        assert result.exit_code == 1
        assert "external_links.yaml uses placeholder" in result.output
        assert "nothing was written" in result.output
        assert "ensg" in Database.open(resources.root)

    def test_force_succeeds_once_the_link_config_is_updated(self, run, resources):
        (resources.root / "config" / "external_links.yaml").write_text(
            yaml.safe_dump({"links": []}),
            encoding="utf-8",
        )
        result = run("remove", "ensg", "--yes", "--force")
        assert result.exit_code == 0, result.output
        assert "ensg" not in Database.open(resources.root)


class TestRename:
    def test_renames_a_field(self, run, resources):
        result = run("rename", "score", "constraint_score", "--yes")
        assert result.exit_code == 0, result.output
        reopened = Database.open(resources.root)
        assert "constraint_score" in reopened
        assert "score" not in reopened

    def test_code_dependent_column_is_refused_with_the_location(self, run, resources):
        result = run("rename", "loeuf", "loeuf_v4", "--yes")
        assert result.exit_code == 1
        assert "referenced by name in code" in result.output
        assert "stats_drawer" in result.output
        assert "loeuf" in Database.open(resources.root)

    def test_force_allows_it(self, run, resources):
        result = run("rename", "loeuf", "loeuf_v4", "--yes", "--force")
        assert result.exit_code == 0, result.output
        assert "loeuf_v4" in Database.open(resources.root)

    def test_config_edits_are_reported(self, run):
        result = run("rename", "ensg", "ensembl_gene_id", "--yes", "--force")
        assert "external_links.yaml" in result.output

    def test_keep_config_makes_the_save_fail(self, run, resources):
        result = run(
            "rename",
            "ensg",
            "ensembl_gene_id",
            "--yes",
            "--force",
            "--keep-config",
        )
        assert result.exit_code == 1
        assert "nothing was written" in result.output
        assert "ensg" in Database.open(resources.root)

    def test_unknown_field(self, run):
        result = run("rename", "nope", "whatever", "--yes")
        assert result.exit_code == 1
        assert "unknown field" in result.output

    def test_invalid_new_name(self, run):
        result = run("rename", "score", "Bad Name", "--yes")
        assert result.exit_code == 1
        assert "not a valid field key" in result.output

    def test_hints_at_updating_the_label(self, run):
        result = run("rename", "score", "constraint_score", "--yes")
        assert "fullname" in result.output


class TestSet:
    def test_updates_metadata(self, run, resources):
        result = run("set", "score", "--visibility", "primary")
        assert result.exit_code == 0
        assert Database.open(resources.root).fields["score"].visibility == "primary"

    def test_touch_sets_today(self, run, resources):
        run("set", "score", "--touch")
        assert Database.open(resources.root).fields["score"].update == today_stamp()

    def test_nothing_to_change(self, run):
        result = run("set", "score")
        assert result.exit_code == 1
        assert "nothing to change" in result.output

    def test_invalid_visibility(self, run):
        result = run("set", "score", "--visibility", "nonsense")
        assert result.exit_code != 0

    def test_unknown_field(self, run):
        result = run("set", "nope", "--visibility", "primary")
        assert result.exit_code == 1
        assert "unknown field" in result.output

    def test_sets_priority(self, run, resources):
        result = run("set", "score", "--priority", "5")
        assert result.exit_code == 0
        assert Database.open(resources.root).fields["score"].priority == 5

    def test_sets_a_negative_priority(self, run, resources):
        run("set", "score", "--priority", "-3")
        assert Database.open(resources.root).fields["score"].priority == -3

    def test_sets_priority_zero(self, run, resources):
        """0 is falsy; the option must not treat it as absent."""
        run("set", "score", "--priority", "0")
        assert Database.open(resources.root).fields["score"].priority == 0

    def test_clears_priority(self, run, resources):
        run("set", "symbol", "--priority", "")
        assert Database.open(resources.root).fields["symbol"].priority is None

    def test_rejects_a_non_integer_priority(self, run, resources):
        result = run("set", "score", "--priority", "high")
        assert result.exit_code == 1
        assert "must be an integer" in result.output
        assert Database.open(resources.root).fields["score"].priority is None


class TestSyncAndDiff:
    def test_sync_is_a_no_op_on_a_healthy_database(self, run):
        result = run("sync")
        assert result.exit_code == 0
        assert "already matches" in result.output

    def test_sync_adds_a_stub_for_a_new_column(self, run, resources):
        frame = pl.read_parquet(resources.parquet)
        frame.with_columns(pl.lit(1).alias("surprise")).write_parquet(resources.parquet)
        result = run("sync", "--yes")
        assert "surprise" in result.output
        assert "surprise" in Database.open(resources.root)

    def test_sync_reports_orphans_without_removing(self, run, resources):
        pl.read_parquet(resources.parquet).drop("note").write_parquet(resources.parquet)
        result = run("sync", "--yes")
        assert "orphan" in result.output
        assert "note" in Database.open(resources.root).fields

    def test_sync_prune_removes_orphans(self, run, resources):
        pl.read_parquet(resources.parquet).drop("note").write_parquet(resources.parquet)
        run("sync", "--prune", "--yes")
        assert "note" not in Database.open(resources.root).fields

    def test_sync_json_is_a_single_object(self, run, resources):
        frame = pl.read_parquet(resources.parquet)
        frame.with_columns(pl.lit(1).alias("surprise")).write_parquet(resources.parquet)
        payload = json.loads(run("sync", "--json", "--yes").output)
        assert payload["added"] == ["surprise"]
        assert payload["saved"] is True

    def test_sync_json_no_op(self, run):
        payload = json.loads(run("sync", "--json").output)
        assert payload["added"] == []
        assert payload["saved"] is False

    def test_diff_is_empty_for_a_fresh_database(self, run):
        assert "No staged changes" in run("diff").output


class TestHistory:
    def test_empty_history(self, run):
        assert "No history yet" in run("history").output

    def test_shows_entries_after_a_change(self, run, gene_list):
        run("add", str(gene_list), "--name", "mine", "--yes")
        result = run("history")
        assert "add_field" in result.output
        assert "mine" in result.output

    def test_json_output(self, run, gene_list):
        run("add", str(gene_list), "--name", "mine", "--yes")
        payload = json.loads(run("history", "--json").output)
        assert payload[-1]["op"] == "add_field"


class TestInterruptedSaveWarning:
    def test_a_stray_marker_is_surfaced(self, run, resources):
        """The one case where the two files may genuinely disagree."""
        marker = resources.root / "processed_data" / ".save-in-progress"
        marker.write_text("2026-01-01", encoding="utf-8")
        result = run("validate")
        assert "interrupted" in result.output


class TestAlias:
    def test_no_aliases_initially(self, run):
        assert "No aliases defined" in run("alias").output

    def test_set_and_list(self, run):
        result = run("alias", "flag", "flag_b")
        assert result.exit_code == 0, result.output
        assert "-> flag_b" in result.output
        assert "flag" in run("alias").output

    def test_retarget_shows_the_move(self, run):
        run("alias", "flag", "flag_b")
        result = run("alias", "flag", "flag_a")
        assert "flag_b -> flag_a" in result.output

    def test_show_resolves_and_says_so(self, run):
        run("alias", "flag", "flag_b")
        result = run("show", "flag")
        assert "flag -> flag_b" in result.output
        assert "(alias)" in result.output

    def test_get_headers_name_the_real_column(self, run):
        """Downloaded data must identify its own provenance, not the alias."""
        run("alias", "flag", "flag_b")
        header = run("get", "flag").output.splitlines()[0]
        assert header == "symbol\tflag_b"

    def test_remove(self, run):
        run("alias", "flag", "flag_b")
        result = run("alias", "flag", "--remove")
        assert "removed alias" in result.output
        assert "No aliases defined" in run("alias").output

    def test_setting_without_a_target_is_an_error(self, run):
        result = run("alias", "flag")
        assert result.exit_code == 1
        assert "give a field" in result.output

    def test_shadowing_a_field_is_refused(self, run):
        result = run("alias", "score", "flag_b")
        assert result.exit_code == 1
        assert "already a real field" in result.output

    def test_unknown_target(self, run):
        result = run("alias", "flag", "nope")
        assert result.exit_code == 1
        assert "unknown field" in result.output

    def test_json_listing(self, run):
        run("alias", "flag", "flag_b")
        assert json.loads(run("alias", "--json").output) == {"flag": "flag_b"}

    def test_remove_refuses_an_alias(self, run, resources):
        """An alias must never be what gets deleted."""
        run("alias", "flag", "flag_b")
        result = run("remove", "flag", "--yes")
        assert result.exit_code == 1
        assert "needs the real field name" in result.output
        assert "flag_b" in Database.open(resources.root)

    def test_rename_refuses_an_alias(self, run):
        run("alias", "flag", "flag_b")
        result = run("rename", "flag", "whatever", "--yes")
        assert result.exit_code == 1
        assert "needs the real field name" in result.output

    def test_list_shows_the_alias_column(self, run):
        run("alias", "flag", "flag_b")
        assert "aliases" in run("list").output


class TestAddMissingPolicy:
    def test_keep_is_accepted(self, run, tmp_path, resources):
        """A derived flag needs null, not False, for genes with no upstream data."""
        path = tmp_path / "flags.tsv"
        pl.DataFrame({"symbol": ["GENE02", "GENE03"], "flag": [True, False]}).write_csv(
            path,
            separator="\t",
        )
        result = run(
            "add", str(path), "--name", "derived", "--missing", "keep", "--yes"
        )
        assert result.exit_code == 0, result.output
        field = Database.open(resources.root)["derived"]
        assert field.values.null_count() == 18
        assert field.values.to_list()[1:3] == [True, False]

    def test_an_unknown_policy_lists_the_valid_ones(self, run, gene_list):
        result = run("add", str(gene_list), "--name", "x", "--missing", "nonsense")
        assert result.exit_code == 1
        assert "auto, keep, fill, or error" in result.output


class TestBuildAliases:
    """``genetrek-db build-aliases``: what a deployment's alias coverage is."""

    def test_it_writes_an_index_and_says_where_from(self, run, resources, hgnc_file):
        result = run("build-aliases")
        assert result.exit_code == 0
        # Which source file was used is the single most useful thing it prints:
        # several hgnc_complete_set* files can sit in the tree at once and the
        # newest by mtime need not be the newest release.
        assert hgnc_file.name in result.output
        assert paths.aliases_path(resources.root).is_file()

    @pytest.mark.usefixtures("hgnc_file")
    def test_dry_run_writes_nothing(self, run, resources):
        result = run("build-aliases", "--dry-run")
        assert result.exit_code == 0
        assert "nothing written" in result.output
        assert not paths.aliases_path(resources.root).is_file()

    @pytest.mark.usefixtures("hgnc_file")
    def test_it_reports_the_layers_it_found(self, run):
        payload = json.loads(run("build-aliases", "--json").output)
        assert payload["key_column"] == "ncbi_id"
        assert payload["frame_rows"] == 20
        # 19 distinct symbols over 20 rows, and GENE01 covers two genes -- so
        # it is stored once as ambiguous rather than resolving to either.
        assert payload["counts"]["symbol"] == 18
        assert payload["counts"]["ambiguous"] == 1
        assert payload["counts"]["alias_symbol"] == 2
        assert payload["counts"]["prev_symbol"] == 1

    def test_without_a_source_it_fails_rather_than_writing_an_empty_index(
        self,
        run,
        resources,
    ):
        """No HGNC file in this tree.

        An index of exact symbols only would look like coverage while
        providing none.
        """
        result = run("build-aliases")
        assert result.exit_code != 0
        assert not paths.aliases_path(resources.root).is_file()

    def test_resolve_then_uses_it(self, run, hgnc_file, tmp_path):
        """The CLI and the deployed webapp must answer the same way."""
        run("build-aliases")
        hgnc_file.unlink()  # as in the container, where only the index ships
        listing = tmp_path / "retired.txt"
        listing.write_text("OLDALIAS\n", encoding="utf-8")
        result = run("resolve", str(listing), "--json")
        assert json.loads(result.output)["rows"][0]["matched_by"] == "alias_symbol"


class TestValidateChecksTheAliasIndex:
    def test_a_missing_index_is_a_warning_not_an_error(self, run):
        result = run("validate")
        assert result.exit_code == 0
        assert "alias index" in result.output

    @pytest.mark.usefixtures("hgnc_file")
    def test_a_stale_index_is_reported(self, run, resources):
        """Guards the drift resolution cannot notice.

        A symbol that has gone from the table looks exactly like a symbol that
        was retired upstream, so only the recorded gene count can tell them
        apart.
        """
        run("build-aliases")
        resources.frame.head(10).write_parquet(resources.parquet)
        result = run("validate")
        assert "20-gene table but this one has 10" in result.output
