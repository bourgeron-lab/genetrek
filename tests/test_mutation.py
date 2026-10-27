"""Tests for staging changes and writing them to disk."""

from __future__ import annotations

import warnings as warnings_module

import polars as pl
import pytest
import yaml

from genetrek import schema
from genetrek.db import (
    Database,
    DatabaseError,
    ResolutionFailedError,
    ValidationFailedError,
)
from genetrek.db.database import LEGACY_PRIMARY_KEY as PRIMARY_KEY
from genetrek.schema import FieldsYamlError


@pytest.fixture
def db(resources):
    return Database.open(resources.root)


class TestAddFromGeneList:
    def test_bare_list_becomes_a_boolean_membership_column(self, db):
        field = db.add_field(["GENE02", "GENE03"], "my_list", id_type="symbol")
        assert field.dtype == pl.Boolean
        assert field.values.to_list()[:4] == [False, True, True, False]

    def test_absent_genes_become_false_not_null(self, db):
        """Absent from a gene list means not in the list, not unknown."""
        field = db.add_field(["GENE02"], "my_list", id_type="symbol")
        assert field.values.null_count() == 0
        assert int(field.values.sum()) == 1

    def test_metadata_defaults_are_filled_in(self, db):
        field = db.add_field(["GENE02"], "my_list", id_type="symbol")
        assert field.meta.value_type == "boolean"
        assert field.meta.added == schema.today_stamp()
        assert field.meta.visibility == "hidden"

    def test_metadata_overrides_are_applied(self, db):
        field = db.add_field(
            ["GENE02"],
            "my_list",
            id_type="symbol",
            meta={"fullname": "My List", "visibility": "secondary", "doi": "10.1/x"},
        )
        assert field.meta.fullname == "My List"
        assert field.meta.visibility == "secondary"
        assert field.meta.doi == "10.1/x"

    def test_accepts_a_series(self, db):
        field = db.add_field(pl.Series(["GENE02"]), "my_list", id_type="symbol")
        assert int(field.values.sum()) == 1

    def test_duplicate_identifiers_in_the_input_are_collapsed(self, db):
        field = db.add_field(["GENE02", "GENE02"], "my_list", id_type="symbol")
        assert int(field.values.sum()) == 1

    def test_a_bare_string_is_rejected(self, db):
        """Iterating a string would silently treat it as a list of characters."""
        with pytest.raises(DatabaseError, match="neither an existing file"):
            db.add_field("GENE02", "my_list", id_type="symbol")

    def test_the_field_lands_last_in_column_order(self, db):
        db.add_field(["GENE02"], "my_list", id_type="symbol")
        assert db.frame.columns[-1] == "my_list"
        assert list(db.fields)[-1] == "my_list"


class TestAddFromFrame:
    def test_two_column_frame_infers_the_columns(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02", "GENE03"], "score2": [1.5, 2.5]})
        field = db.add_field(incoming, id_type="symbol")
        assert field.name == "score2"
        assert field.meta.value_type == "float"
        assert field.values.to_list()[1:3] == [1.5, 2.5]

    def test_name_derives_from_the_value_column(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "High Confidence v7": [True]})
        assert db.add_field(incoming, id_type="symbol").name == "high_confidence_v7"

    def test_explicit_name_wins(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "raw": [True]})
        assert db.add_field(incoming, "chosen", id_type="symbol").name == "chosen"

    def test_wide_frame_requires_an_explicit_value_column(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "a": [1], "b": [2]})
        with pytest.raises(DatabaseError, match="pass value_col"):
            db.add_field(incoming, id_type="symbol")

    def test_wide_frame_with_explicit_columns(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "a": [1], "b": [2]})
        field = db.add_field(incoming, id_col="symbol", value_col="b", id_type="symbol")
        assert field.name == "b"
        assert field.values.to_list()[1] == 2

    def test_unknown_id_column_is_rejected(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1]})
        with pytest.raises(DatabaseError, match="id column 'nope' not in the input"):
            db.add_field(incoming, id_col="nope", id_type="symbol")

    def test_unknown_value_column_is_rejected(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1]})
        with pytest.raises(DatabaseError, match="value column 'nope' not in the input"):
            db.add_field(incoming, value_col="nope", id_type="symbol")

    def test_single_column_frame_is_rejected(self, db):
        with pytest.raises(DatabaseError, match="at least two columns"):
            db.add_field(pl.DataFrame({"symbol": ["GENE02"]}), "x", id_type="symbol")

    def test_resolution_by_a_non_symbol_key(self, db):
        incoming = pl.DataFrame({"hgnc_id": ["HGNC:30"], "flag": [True]})
        field = db.add_field(incoming, "by_hgnc", id_type="hgnc")
        assert field.values.to_list()[2] is True


class TestAddFromFile:
    def test_two_column_tsv(self, db, tmp_path):
        path = tmp_path / "list.tsv"
        pl.DataFrame({"symbol": ["GENE02"], "in_list": [True]}).write_csv(
            path,
            separator="\t",
        )
        field = db.add_field(path, id_type="symbol")
        assert field.name == "in_list"
        assert field.meta.source_file == "list.tsv"

    def test_single_column_file_is_a_gene_list(self, db, tmp_path):
        path = tmp_path / "genes.txt"
        path.write_text("symbol\nGENE02\nGENE03\n", encoding="utf-8")
        field = db.add_field(path, "from_file", id_type="symbol")
        assert field.dtype == pl.Boolean
        assert int(field.values.sum()) == 2

    def test_headerless_gene_list_keeps_its_first_gene(self, db, tmp_path):
        """Gene lists in the wild often have no header; the first line is a gene."""
        path = tmp_path / "genes.txt"
        path.write_text("GENE02\nGENE03\n", encoding="utf-8")
        field = db.add_field(path, "from_file", id_type="symbol")
        assert int(field.values.sum()) == 2
        assert field.values.to_list()[1:3] == [True, True]

    def test_a_one_gene_headerless_list_works(self, db, tmp_path):
        path = tmp_path / "genes.txt"
        path.write_text("GENE02\n", encoding="utf-8")
        assert int(db.add_field(path, "one", id_type="symbol").values.sum()) == 1

    @pytest.mark.parametrize("header", ["symbol", "Gene", "HGNC_ID", "ensg", "id"])
    def test_recognised_headers_are_still_dropped(self, db, tmp_path, header):
        path = tmp_path / "genes.txt"
        path.write_text(f"{header}\nGENE02\n", encoding="utf-8")
        assert int(db.add_field(path, "x", id_type="symbol").values.sum()) == 1

    def test_an_empty_file_produces_an_all_false_column(self, db, tmp_path):
        """Must not blow up on a Null-typed join key."""
        path = tmp_path / "genes.txt"
        path.write_text("symbol\n", encoding="utf-8")
        field = db.add_field(path, "empty", id_type="symbol")
        assert int(field.values.sum()) == 0
        assert field.values.null_count() == 0

    def test_csv_separator_is_inferred(self, db, tmp_path):
        path = tmp_path / "list.csv"
        path.write_text("symbol,score9\nGENE02,3.5\n", encoding="utf-8")
        assert db.add_field(path, id_type="symbol").values.to_list()[1] == 3.5

    def test_missing_file_is_reported(self, db, tmp_path):
        with pytest.raises(DatabaseError, match="input file not found"):
            db.add_field(tmp_path / "absent.tsv", "x", id_type="symbol")

    def test_string_path_is_accepted(self, db, tmp_path):
        path = tmp_path / "genes.tsv"
        pl.DataFrame({"symbol": ["GENE02"], "flag": [True]}).write_csv(
            path,
            separator="\t",
        )
        assert db.add_field(str(path), id_type="symbol").name == "flag"


class TestMissingPolicy:
    def test_auto_leaves_non_boolean_null(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1.5]})
        field = db.add_field(incoming, id_type="symbol")
        assert field.values.null_count() == db.n_rows - 1

    def test_auto_fills_boolean_false(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [True]})
        assert db.add_field(incoming, id_type="symbol").values.null_count() == 0

    def test_fill_uses_the_given_value(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1.5]})
        field = db.add_field(incoming, id_type="symbol", missing="fill", fill_value=0.0)
        assert field.values.null_count() == 0
        assert field.values.to_list()[0] == 0.0

    def test_error_refuses_partial_coverage(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1.5]})
        with pytest.raises(DatabaseError, match="not covered by the input"):
            db.add_field(incoming, id_type="symbol", missing="error")

    def test_keep_leaves_boolean_nulls_alone(self, db):
        """A derived flag with no upstream data is unknown, not False.

        Several existing fields are that shape: gnomad_v4_is_loeuf has 43,925
        nulls, so the library has to be able to reproduce them.
        """
        incoming = pl.DataFrame(
            {"symbol": ["GENE02", "GENE03"], "flag": [True, False]},
        )
        field = db.add_field(incoming, "derived", id_type="symbol", missing="keep")
        assert field.dtype == pl.Boolean
        assert field.values.null_count() == db.n_rows - 2
        assert field.values.to_list()[1:3] == [True, False]

    def test_keep_also_leaves_numeric_nulls(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1.5]})
        field = db.add_field(incoming, "x", id_type="symbol", missing="keep")
        assert field.values.null_count() == db.n_rows - 1

    def test_auto_would_have_filled_that_flag_false(self, db):
        """Contrast: auto is right for a curated list, wrong for a derived flag."""
        incoming = pl.DataFrame(
            {"symbol": ["GENE02", "GENE03"], "flag": [True, False]},
        )
        field = db.add_field(incoming, "derived", id_type="symbol", missing="auto")
        assert field.values.null_count() == 0

    def test_fill_without_a_value_points_at_keep(self, db):
        """fill_null(None) used to crash with a bare polars ValueError."""
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1.5]})
        with pytest.raises(DatabaseError, match="missing='keep'"):
            db.add_field(incoming, "x", id_type="symbol", missing="fill")

    def test_error_mentions_keep(self, db):
        incoming = pl.DataFrame({"symbol": ["GENE02"], "v": [1.5]})
        with pytest.raises(DatabaseError, match="'keep'"):
            db.add_field(incoming, "x", id_type="symbol", missing="error")

    def test_error_accepts_full_coverage(self, db):
        incoming = pl.DataFrame(
            {"ncbi_id": [str(i) for i in range(1, 21)], "v": list(range(20))},
        )
        field = db.add_field(incoming, id_type="ncbi", missing="error")
        assert field.values.null_count() == 0


class TestAddResolutionFailures:
    def test_unmatched_identifier_refuses_by_default(self, db):
        with pytest.raises(ResolutionFailedError) as excinfo:
            db.add_field(["GENE02", "NOPE"], "x", id_type="symbol")
        assert excinfo.value.report.unmatched == ["NOPE"]

    def test_nothing_is_staged_after_a_refusal(self, db):
        with pytest.raises(ResolutionFailedError):
            db.add_field(["GENE02", "NOPE"], "x", id_type="symbol")
        assert "x" not in db
        assert not db.is_dirty

    def test_force_adds_what_resolved(self, db):
        field = db.add_field(["GENE02", "NOPE"], "x", id_type="symbol", force=True)
        assert int(field.values.sum()) == 1

    def test_ambiguous_identifier_refuses(self, db):
        """GENE01 is used by two rows in the fixture."""
        with pytest.raises(ResolutionFailedError) as excinfo:
            db.add_field(["GENE01"], "x", id_type="symbol")
        assert excinfo.value.report.ambiguous == ["GENE01"]

    def test_existing_name_is_refused(self, db):
        with pytest.raises(DatabaseError, match="already exists"):
            db.add_field(["GENE02"], "score", id_type="symbol")

    def test_two_inputs_resolving_to_one_gene_warn(self, db):
        """GENE05 and its ncbi_id are the same gene; the value cannot be both."""
        incoming = pl.DataFrame({"id": ["GENE05", "gene05"], "v": [1.0, 2.0]})
        with pytest.warns(UserWarning, match="already covered"):
            field = db.add_field(incoming, id_type="symbol")
        assert field.values.to_list()[4] == 1.0


class TestRemoveField:
    def test_removes_from_both_data_and_metadata(self, db):
        db.remove_field("note")
        assert "note" not in db
        assert "note" not in db.frame.columns

    def test_unknown_field_raises(self, db):
        with pytest.raises(KeyError, match="unknown field"):
            db.remove_field("nope")

    def test_primary_key_is_protected(self, db):
        with pytest.raises(DatabaseError, match="primary key"):
            db.remove_field(PRIMARY_KEY)

    def test_all_objections_are_reported_at_once(self, resources):
        """A field that is both code-dependent and link-referenced says both."""
        (resources.root / "config" / "external_links.yaml").write_text(
            yaml.safe_dump({"links": [{"name": "L", "url": "https://x/{loeuf}"}]}),
            encoding="utf-8",
        )
        fresh = Database.open(resources.root)
        with pytest.raises(DatabaseError) as excinfo:
            fresh.remove_field("loeuf")
        message = str(excinfo.value)
        assert "referenced by name in code" in message
        assert "external_links.yaml" in message

    def test_primary_key_can_be_forced(self, db):
        db.remove_field(PRIMARY_KEY, force=True)
        assert PRIMARY_KEY not in db

    def test_badge_reference_is_protected(self, db):
        with pytest.raises(DatabaseError, match=r"badges\.yaml"):
            db.remove_field("gene_type")

    def test_a_code_dependent_alias_protects_its_column(self, db):
        """Removing the column would take the alias the badge is keyed on."""
        db.set_alias("inheritance", "note")
        with pytest.raises(DatabaseError, match="'inheritance'"):
            db.remove_field("note")

    def test_link_reference_is_protected(self, resources):
        """Guard a field that code does *not* depend on, so only the link blocks it."""
        (resources.root / "config" / "external_links.yaml").write_text(
            yaml.safe_dump(
                {"links": [{"name": "Note", "url": "https://x/{small_count}"}]},
            ),
            encoding="utf-8",
        )
        fresh = Database.open(resources.root)
        with pytest.raises(DatabaseError, match=r"external_links\.yaml"):
            fresh.remove_field("small_count")

    def test_referenced_field_can_be_forced(self, db):
        db.remove_field("ensg", force=True)
        assert "ensg" not in db

    def test_unreferenced_field_needs_no_force(self, db):
        db.remove_field("small_count")
        assert "small_count" not in db


class TestRenameField:
    def test_renames_data_and_metadata_together(self, db):
        db.rename_field("score", "constraint_score")
        assert "score" not in db
        assert "score" not in db.frame.columns
        assert "constraint_score" in db
        assert "constraint_score" in db.frame.columns

    def test_position_is_preserved(self, db):
        """A rename must not reorder anything, or every future diff is noisy."""
        before = db.frame.columns.index("score")
        db.rename_field("score", "constraint_score")
        assert db.frame.columns.index("constraint_score") == before
        assert list(db.fields).index("constraint_score") == before
        assert list(db.fields) == db.frame.columns

    def test_metadata_is_carried_over_intact(self, db):
        db.set_meta("score", description="kept", doi="10.1/x", visibility="primary")
        db.rename_field("score", "constraint_score")
        meta = db.fields["constraint_score"]
        assert meta.description == "kept"
        assert meta.doi == "10.1/x"
        assert meta.visibility == "primary"

    def test_values_are_unchanged(self, db, resources):
        db.rename_field("score", "constraint_score")
        assert (
            db["constraint_score"].values.to_list()
            == resources.frame["score"].to_list()
        )

    def test_unknown_field_raises(self, db):
        with pytest.raises(KeyError, match="unknown field"):
            db.rename_field("nope", "whatever")

    def test_renaming_to_an_existing_name_raises(self, db):
        with pytest.raises(DatabaseError, match="already exists"):
            db.rename_field("score", "note")

    def test_renaming_to_the_same_name_raises(self, db):
        with pytest.raises(DatabaseError, match="already the field's name"):
            db.rename_field("score", "score")

    @pytest.mark.parametrize(
        "bad", ["Score", "my score", "my-score", "_score", "", "é"]
    )
    def test_invalid_names_are_rejected(self, db, bad):
        with pytest.raises(DatabaseError, match="not a valid field key"):
            db.rename_field("score", bad)

    def test_a_leading_digit_is_allowed(self, db):
        """Several real fields are PMID-prefixed, e.g. 37365347_p_value."""
        db.rename_field("score", "12345_score")
        assert "12345_score" in db

    def test_code_dependent_column_is_refused(self, db):
        """Renaming these would silently degrade a plot to a label."""
        with pytest.raises(DatabaseError, match="referenced by name in code"):
            db.rename_field("loeuf", "loeuf_v4")
        assert "loeuf" in db

    def test_the_refusal_names_where_the_dependency_is(self, db):
        with pytest.raises(DatabaseError, match="stats_drawer"):
            db.rename_field("loeuf", "loeuf_v4")

    def test_code_dependent_column_can_be_forced(self, db):
        db.rename_field("loeuf", "loeuf_v4", force=True)
        assert "loeuf_v4" in db

    def test_a_role_column_needs_no_force(self, db):
        """Identifiers are found by role, so renaming one is not name-bound."""
        db.rename_field("symbol", "hgnc_symbol")
        assert db.roles.symbol == "hgnc_symbol"

    def test_a_renamed_coordinate_keeps_its_assembly(self, db):
        db.rename_field("hg38_chr", "grch38_chr")
        assert db.roles.assemblies["hg38"].chrom == "grch38_chr"

    def test_badge_reference_is_rewritten(self, db, resources):
        db.rename_field("gene_type", "biotype", force=True)
        badges = yaml.safe_load(
            (resources.root / "config" / "badges.yaml").read_text(encoding="utf-8"),
        )
        assert "biotype" in badges
        assert "gene_type" not in badges

    def test_link_placeholder_is_rewritten(self, db, resources):
        db.rename_field("ensg", "ensembl_gene_id", force=True)
        text = (resources.root / "config" / "external_links.yaml").read_text(
            encoding="utf-8",
        )
        assert "{ensembl_gene_id}" in text
        assert "{ensg}" not in text

    def test_rewritten_config_keeps_the_database_valid(self, db):
        """The point of rewriting: a stale placeholder would fail validation."""
        db.rename_field("ensg", "ensembl_gene_id", force=True)
        assert db.validate().ok

    def test_keep_config_leaves_references_stale(self, db):
        db.rename_field("ensg", "ensembl_gene_id", force=True, update_config=False)
        report = db.validate()
        assert not report.ok
        assert any("external_links.yaml" in e for e in report.errors)

    def test_config_edits_are_reported(self, db):
        db.rename_field("ensg", "ensembl_gene_id", force=True)
        edits = "\n".join(db.pending_config_edits())
        assert "external_links.yaml" in edits

    def test_history_records_the_old_name(self, db):
        db.rename_field("score", "constraint_score")
        db.save()
        record = db.history()[-1]
        assert record["op"] == "rename_field"
        assert record["field"] == "constraint_score"
        assert record["renamed_from"] == "score"

    def test_survives_a_save_and_reopen(self, db, resources):
        db.rename_field("score", "constraint_score")
        db.save()
        reopened = Database.open(resources.root)
        assert "constraint_score" in reopened
        assert "score" not in reopened
        assert list(reopened.fields) == reopened.frame.columns

    def test_the_yaml_diff_is_one_key(self, db, resources):
        before = resources.fields_yaml.read_text(encoding="utf-8").splitlines()
        db.rename_field("score", "constraint_score")
        db.save()
        after = resources.fields_yaml.read_text(encoding="utf-8").splitlines()
        differing = [
            i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b
        ]
        assert len(differing) == 1
        assert before[differing[0]] == "score:"
        assert after[differing[0]] == "constraint_score:"


class TestCodeDependencyCheck:
    def test_removing_one_is_refused(self, db):
        with pytest.raises(DatabaseError, match="referenced by name in code"):
            db.remove_field("loeuf")
        assert "loeuf" in db

    def test_the_refusal_names_the_location(self, db):
        with pytest.raises(DatabaseError, match="stats_drawer"):
            db.remove_field("loeuf")

    def test_removing_one_can_be_forced(self, db):
        db.remove_field("loeuf", force=True)
        assert "loeuf" not in db

    def test_validate_notes_the_absence(self, db):
        db.rename_field("loeuf", "loeuf_v4", force=True)
        notes = "\n".join(db.validate().notes)
        assert "loeuf" in notes
        assert "features are unavailable" in notes

    def test_the_note_does_not_block_a_save(self, db):
        """These features degrade gracefully, so absence is not an error."""
        db.rename_field("loeuf", "loeuf_v4", force=True)
        db.save()
        assert db.validate().ok


class TestSetMeta:
    def test_updates_several_keys(self, db):
        db.set_meta("score", visibility="primary", description="constraint")
        assert db.fields["score"].visibility == "primary"
        assert db.fields["score"].description == "constraint"

    def test_unknown_key_is_rejected(self, db):
        with pytest.raises(ValueError, match="unknown metadata key"):
            db.set_meta("score", colour="blue")

    def test_unknown_field_raises(self, db):
        with pytest.raises(KeyError, match="unknown field"):
            db.set_meta("nope", visibility="primary")

    def test_touch_sets_today(self, db):
        db.touch("score")
        assert db.fields["score"].update == schema.today_stamp()


class TestSyncFieldsDescription:
    def test_new_column_gets_a_stub(self, db):
        db._frame = db.frame.with_columns(pl.lit(1).alias("surprise"))
        report = db.sync_fields_description()
        assert report.added == ["surprise"]
        assert db.fields["surprise"].value_type == "integer"
        assert db.fields["surprise"].added == schema.today_stamp()

    def test_curated_metadata_is_never_blanked(self, db):
        """The whole point: syncing must not undo hand-written metadata."""
        db.set_meta(
            "score",
            description="carefully written",
            visibility="primary",
            doi="10.1/x",
            update="20240116",
        )
        db.sync_fields_description()
        assert db.fields["score"].description == "carefully written"
        assert db.fields["score"].visibility == "primary"
        assert db.fields["score"].doi == "10.1/x"
        assert db.fields["score"].update == "20240116"

    def test_all_visibilities_survive(self, db):
        before = {n: m.visibility for n, m in db.fields.items()}
        db.sync_fields_description()
        assert {n: m.visibility for n, m in db.fields.items()} == before

    def test_orphans_are_reported_but_kept(self, db):
        db._frame = db.frame.drop("note")
        report = db.sync_fields_description()
        assert report.orphans == ["note"]
        assert report.removed == []
        assert "note" in db.fields

    def test_orphans_are_removed_when_pruning(self, db):
        db._frame = db.frame.drop("note")
        report = db.sync_fields_description(prune=True)
        assert report.removed == ["note"]
        assert "note" not in db.fields

    def test_order_is_normalised_to_column_order(self, db):
        db._fields = dict(reversed(list(db.fields.items())))
        report = db.sync_fields_description()
        assert report.reordered
        assert list(db.fields) == db.frame.columns

    def test_no_op_on_a_healthy_database(self, db):
        report = db.sync_fields_description()
        assert not report.changed
        assert "already matches" in report.describe()


class TestDiff:
    def test_clean_database_has_no_diff(self, db):
        assert db.diff().empty

    def test_added_field(self, db):
        db.add_field(["GENE02"], "x", id_type="symbol")
        assert db.diff().added_fields == ["x"]

    def test_removed_field(self, db):
        db.remove_field("note")
        assert db.diff().removed_fields == ["note"]

    def test_metadata_change_records_before_and_after(self, db):
        db.set_meta("score", visibility="primary")
        assert db.diff().metadata_changes == {
            "score": {"visibility": ("secondary", "primary")},
        }

    def test_describe_renders_each_kind(self, db):
        db.add_field(["GENE02"], "x", id_type="symbol")
        db.remove_field("note")
        db.set_meta("score", visibility="primary")
        described = db.diff().describe()
        assert "+ x" in described
        assert "- note" in described
        assert "~ score.visibility" in described


class TestSave:
    def test_save_persists_both_files(self, db, resources):
        db.add_field(["GENE02"], "x", id_type="symbol")
        db.save()
        reopened = Database.open(resources.root)
        assert "x" in reopened
        assert "x" in reopened.frame.columns

    def test_save_clears_the_dirty_flag(self, db):
        db.set_meta("score", visibility="primary")
        db.save()
        assert not db.is_dirty
        assert db.diff().empty

    def test_save_refuses_an_invalid_database(self, db):
        db._fields["score"].value_type = "boolean"
        with pytest.raises(ValidationFailedError):
            db.save()

    def test_save_can_be_forced_past_validation(self, db, resources):
        db._fields["score"].value_type = "boolean"
        db.save(validate=False)
        assert not Database.open(resources.root).validate().ok

    def test_save_normalises_metadata_order(self, db, resources):
        db._fields = dict(reversed(list(db.fields.items())))
        db.save()
        reopened = Database.open(resources.root)
        assert list(reopened.fields) == reopened.frame.columns

    def test_save_makes_a_backup(self, db):
        db.set_meta("score", visibility="primary")
        result = db.save()
        assert result.backup is not None
        assert (result.backup / "genetrek.parquet").is_file()
        assert (result.backup / "fields_description.yaml").is_file()

    def test_backup_holds_the_previous_content(self, db):
        db.remove_field("note")
        result = db.save()
        assert result.backup is not None
        restored = yaml.safe_load(
            (result.backup / "fields_description.yaml").read_text(encoding="utf-8"),
        )
        assert "note" in restored

    def test_backup_can_be_skipped(self, db):
        db.set_meta("score", visibility="primary")
        assert db.save(backup=False).backup is None

    def test_backups_are_pruned(self, db, resources):
        for i in range(4):
            db.set_meta("score", description=f"v{i}")
            db.save(keep_backups=2)
        kept = list((resources.root / "processed_data" / ".backups").iterdir())
        assert len(kept) == 2

    def test_history_records_the_operations(self, db):
        db.add_field(["GENE02"], "x", id_type="symbol")
        db.remove_field("note")
        db.save(actor="me", note="housekeeping")
        records = db.history()
        assert [r["op"] for r in records] == ["add_field", "remove_field"]
        assert all(r["actor"] == "me" for r in records)
        assert all(r["note"] == "housekeeping" for r in records)

    def test_history_captures_resolution_counts(self, db):
        db.add_field(["GENE02", "GENE03"], "x", id_type="symbol")
        db.save()
        record = db.history()[-1]
        assert record["rows_matched"] == 2
        assert record["rows_input"] == 2
        assert record["field"] == "x"

    def test_a_bare_save_still_logs(self, db):
        db.save()
        assert [r["op"] for r in db.history()] == ["save"]

    def test_no_marker_survives_a_save(self, db, resources):
        db.save()
        assert not (resources.root / "processed_data" / ".save-in-progress").exists()

    def test_metadata_stays_byte_stable_across_a_no_op_save(self, db, resources):
        before = resources.fields_yaml.read_text(encoding="utf-8")
        db.save()
        assert resources.fields_yaml.read_text(encoding="utf-8") == before

    def test_add_then_remove_returns_the_file_to_its_original_bytes(
        self, db, resources
    ):
        """The clearest proof that saving does not churn the metadata file."""
        before = resources.fields_yaml.read_text(encoding="utf-8")
        db.add_field(["GENE02"], "temporary", id_type="symbol")
        db.save()
        assert resources.fields_yaml.read_text(encoding="utf-8") != before
        db.remove_field("temporary")
        db.save()
        assert resources.fields_yaml.read_text(encoding="utf-8") == before


class TestContextManager:
    def test_clean_exit_is_silent(self, resources):
        with warnings_module.catch_warnings():
            warnings_module.simplefilter("error")
            with Database.open(resources.root) as db:
                _ = db.list_fields()

    def test_leaving_dirty_warns_and_does_not_save(self, resources):
        with (
            pytest.warns(UserWarning, match="unsaved changes"),
            Database.open(
                resources.root,
            ) as db,
        ):
            db.set_meta("score", visibility="primary")
        assert Database.open(resources.root).fields["score"].visibility == "secondary"

    def test_saving_inside_the_block_is_silent(self, resources):
        with warnings_module.catch_warnings():
            warnings_module.simplefilter("error")
            with Database.open(resources.root) as db:
                db.set_meta("score", visibility="primary")
                db.save()

    def test_an_exception_does_not_add_a_warning(self, resources):
        """An error on the way out must not be masked by an unsaved-changes warning."""

        def blow_up() -> None:
            with Database.open(resources.root) as db:
                db.set_meta("score", visibility="primary")
                msg = "boom"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            blow_up()


class TestAliases:
    def test_no_aliases_by_default(self, db):
        assert db.aliases == {}

    def test_set_and_resolve(self, db):
        db.set_alias("flag", "flag_b")
        assert db.aliases == {"flag": "flag_b"}
        assert db.resolve_name("flag") == "flag_b"

    def test_stored_on_the_target_field(self, db):
        db.set_alias("flag", "flag_b")
        assert db.fields["flag_b"].aliases == ["flag"]

    def test_getitem_resolves_an_alias(self, db):
        db.set_alias("flag", "flag_b")
        assert db["flag"].name == "flag_b"
        assert db["flag"].values.to_list() == db["flag_b"].values.to_list()

    def test_contains_sees_an_alias(self, db):
        db.set_alias("flag", "flag_b")
        assert "flag" in db

    def test_fields_does_not_include_aliases(self, db):
        """The real field map stays authoritative for iteration and sync."""
        db.set_alias("flag", "flag_b")
        assert "flag" not in db.fields
        assert list(db.fields) == db.frame.columns

    def test_retargeting_moves_the_alias(self, db):
        db.set_alias("flag", "flag_b")
        previous = db.set_alias("flag", "flag_a")
        assert previous == "flag_b"
        assert db.aliases == {"flag": "flag_a"}
        assert db.fields["flag_b"].aliases == []
        assert db.fields["flag_a"].aliases == ["flag"]

    def test_setting_the_same_target_twice_is_a_no_op(self, db):
        db.set_alias("flag", "flag_b")
        db._pending.clear()
        assert db.set_alias("flag", "flag_b") == "flag_b"
        assert db._pending == []

    def test_several_aliases_on_one_field(self, db):
        db.set_alias("flag", "flag_b")
        db.set_alias("flagged", "flag_b")
        assert db.fields["flag_b"].aliases == ["flag", "flagged"]

    def test_unknown_target_raises(self, db):
        with pytest.raises(KeyError, match="unknown field"):
            db.set_alias("flag", "nope")

    def test_shadowing_a_real_field_is_refused(self, db):
        with pytest.raises(DatabaseError, match="already a real field"):
            db.set_alias("score", "flag_b")

    @pytest.mark.parametrize("bad", ["Flag", "my flag", "_flag", ""])
    def test_invalid_alias_names_are_refused(self, db, bad):
        with pytest.raises(DatabaseError, match="not a valid alias"):
            db.set_alias(bad, "flag_b")

    def test_remove_alias(self, db):
        db.set_alias("flag", "flag_b")
        assert db.remove_alias("flag") == "flag_b"
        assert db.aliases == {}

    def test_removing_an_unknown_alias_raises(self, db):
        with pytest.raises(KeyError, match="unknown alias"):
            db.remove_alias("nope")

    def test_resolve_name_rejects_the_unknown(self, db):
        with pytest.raises(KeyError, match="unknown field"):
            db.resolve_name("nope")

    def test_survives_a_save_and_reopen(self, db, resources):
        db.set_alias("flag", "flag_b")
        db.save()
        assert Database.open(resources.root).aliases == {"flag": "flag_b"}

    def test_history_records_the_retarget(self, db):
        db.set_alias("flag", "flag_b")
        db.save()
        db.set_alias("flag", "flag_a")
        db.save()
        record = db.history()[-1]
        assert record["op"] == "set_alias"
        assert record["alias"] == "flag"
        assert record["field"] == "flag_a"
        assert record["alias_was"] == "flag_b"

    def test_a_rule_follows_a_renamed_source(self, db):
        db.add_derived_field("either", ["flag_a", "flag_b"])
        db.rename_field("flag_b", "flag_beta")
        assert db.fields["either"].derived_from == ["flag_a", "flag_beta"]
        assert db.validate().ok

    def test_an_alias_follows_a_rename(self, db):
        db.set_alias("flag", "flag_b")
        db.rename_field("flag_b", "flag_beta")
        assert db.aliases == {"flag": "flag_beta"}

    def test_an_alias_dies_with_its_field(self, db):
        db.set_alias("cnt", "small_count")
        db.remove_field("small_count")
        assert db.aliases == {}

    def test_adding_a_field_named_like_an_alias_is_refused(self, db):
        db.set_alias("flag", "flag_b")
        with pytest.raises(DatabaseError, match="already an alias"):
            db.add_field(["GENE02"], "flag", id_type="symbol")

    def test_renaming_onto_an_alias_is_refused(self, db):
        db.set_alias("flag", "flag_b")
        with pytest.raises(DatabaseError, match="already an alias"):
            db.rename_field("score", "flag")

    def test_list_fields_shows_aliases(self, db):
        db.set_alias("flag", "flag_b")
        row = db.list_fields().filter(pl.col("field") == "flag_b").row(0, named=True)
        assert row["aliases"] == "flag"

    def test_nothing_resolves_automatically(self, db):
        """No 'latest version' inference: an unset alias resolves to nothing."""
        db.add_field(["GENE02"], "hc_ndd_v1", id_type="symbol")
        db.add_field(["GENE03"], "hc_ndd_v2", id_type="symbol")
        assert "hc_ndd" not in db
        with pytest.raises(KeyError):
            db.resolve_name("hc_ndd")


class TestAliasValidation:
    def test_a_healthy_alias_validates(self, db):
        db.set_alias("flag", "flag_b")
        assert db.validate().ok

    def test_shadowing_is_an_error(self, db):
        db._fields["flag_b"].aliases = ["score"]
        report = db.validate()
        assert not report.ok
        assert any("shadows a real field" in e for e in report.errors)

    def test_self_reference_is_an_error(self, db):
        db._fields["flag_b"].aliases = ["flag_b"]
        assert any("points at its own field" in e for e in db.validate().errors)

    def test_two_fields_claiming_one_alias_is_an_error(self, db):
        db._fields["flag_a"].aliases = ["flag"]
        db._fields["flag_b"].aliases = ["flag"]
        assert any("claimed by both" in e for e in db.validate().errors)

    def test_a_bad_alias_name_fails_at_load(self, resources):
        text = resources.fields_yaml.read_text(encoding="utf-8")
        resources.fields_yaml.write_text(
            text.replace("  aliases: []", "  aliases: [Bad Name]", 1),
            encoding="utf-8",
        )
        with pytest.raises(FieldsYamlError, match="not a valid field key"):
            Database.open(resources.root)

    def test_duplicate_aliases_on_one_field_fail_at_load(self, resources):
        text = resources.fields_yaml.read_text(encoding="utf-8")
        resources.fields_yaml.write_text(
            text.replace("  aliases: []", "  aliases: [a, a]", 1),
            encoding="utf-8",
        )
        with pytest.raises(FieldsYamlError, match="duplicate aliases"):
            Database.open(resources.root)
