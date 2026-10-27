"""Tests for the read-only Database surface, including validation."""

from __future__ import annotations

import polars as pl
import pytest
import yaml

from genetrek import schema
from genetrek.db import Database, DatabaseError, ValidationFailedError
from genetrek.db.database import LEGACY_PRIMARY_KEY as PRIMARY_KEY


@pytest.fixture
def db(resources):
    return Database.open(resources.root)


def rewrite_yaml(resources, mutate):
    """Load the fixture metadata, apply ``mutate``, and write it back."""
    fields = schema.load_fields_yaml(resources.fields_yaml)
    fields = mutate(fields) or fields
    schema.write_fields_yaml(resources.fields_yaml, fields)


class TestOpen:
    def test_open_loads_data_and_metadata(self, db, resources):
        assert db.n_rows == 20
        assert len(db) == resources.frame.width
        assert list(db.fields) == resources.frame.columns

    def test_open_is_clean(self, db):
        assert not db.is_dirty

    def test_missing_parquet_raises(self, resources):
        resources.parquet.unlink()
        with pytest.raises(DatabaseError, match="required file not found"):
            Database.open(resources.root)

    def test_missing_yaml_raises(self, resources):
        resources.fields_yaml.unlink()
        with pytest.raises(DatabaseError, match="required file not found"):
            Database.open(resources.root)

    def test_open_succeeds_despite_drift(self, resources):
        """A drifted database must still open so it can be repaired."""
        pl.read_parquet(resources.parquet).drop("score").write_parquet(
            resources.parquet
        )
        db = Database.open(resources.root)
        assert not db.validate().ok


class TestMappingProtocol:
    def test_len_iter_contains(self, db, resources):
        assert len(db) == resources.frame.width
        assert list(db) == resources.frame.columns
        assert "score" in db
        assert "nope" not in db

    def test_getitem_returns_a_bound_field(self, db):
        assert db["score"].name == "score"
        assert db["score"].values.len() == 20

    def test_unknown_field_raises_keyerror(self, db):
        with pytest.raises(KeyError, match="unknown field"):
            db.get_field("nope")


class TestFieldNames:
    def test_unfiltered_is_column_order(self, db, resources):
        assert db.field_names() == resources.frame.columns

    def test_filter_by_visibility(self, db):
        assert db.field_names(visibility="primary") == ["symbol", "flag_a"]

    def test_filter_by_value_type(self, db):
        assert db.field_names(value_type="boolean") == ["flag_a", "flag_b"]

    def test_filters_combine(self, db):
        assert db.field_names(visibility="primary", value_type="boolean") == ["flag_a"]

    def test_no_match_is_empty(self, db):
        assert db.field_names(visibility="primary", value_type="categorical") == []


class TestListFields:
    def test_one_row_per_field(self, db, resources):
        listing = db.list_fields()
        assert listing.height == resources.frame.width
        assert listing["field"].to_list() == resources.frame.columns

    def test_percentages_total_one_hundred(self, db):
        assert db.list_fields()["pct_memory"].sum() == pytest.approx(100.0, abs=0.05)

    def test_null_and_unique_counts_are_right(self, db):
        row = db.list_fields().filter(pl.col("field") == "ensg").row(0, named=True)
        assert row["n_null"] == 6
        assert row["n_unique"] == 15  # 14 distinct ENSGs plus the null group

    def test_filtered_listing_keeps_the_schema(self, db):
        full = db.list_fields()
        filtered = db.list_fields(visibility="primary")
        assert filtered.height == 2
        assert filtered.schema == full.schema

    def test_empty_listing_keeps_the_schema(self, db):
        empty = db.list_fields(visibility="primary", value_type="categorical")
        assert empty.height == 0
        assert empty.schema == db.list_fields().schema


class TestExport:
    def test_export_writes_every_column(self, db, tmp_path, resources):
        path = tmp_path / "all.tsv"
        assert db.export(path) == 20
        assert pl.read_csv(path, separator="\t").columns == resources.frame.columns

    def test_export_selected_columns(self, db, tmp_path):
        path = tmp_path / "some.tsv"
        db.export(path, columns=["symbol", "score"])
        assert pl.read_csv(path, separator="\t").columns == ["symbol", "score"]

    def test_export_by_visibility(self, db, tmp_path):
        path = tmp_path / "primary.tsv"
        db.export(path, visibility="primary")
        assert pl.read_csv(path, separator="\t").columns == ["symbol", "flag_a"]

    def test_export_rejects_both_selectors(self, db, tmp_path):
        with pytest.raises(ValueError, match="not both"):
            db.export(tmp_path / "x.tsv", columns=["symbol"], visibility="primary")

    def test_export_rejects_unknown_column(self, db, tmp_path):
        with pytest.raises(KeyError, match="unknown field"):
            db.export(tmp_path / "x.tsv", columns=["symbol", "nope"])

    def test_export_separator(self, db, tmp_path):
        path = tmp_path / "c.csv"
        db.export(path, columns=["symbol"], separator=",")
        assert path.read_text(encoding="utf-8").startswith("symbol\n")


class TestValidateHealthy:
    def test_fixture_has_no_errors(self, db):
        assert db.validate().ok

    def test_literal_marker_is_reported_as_a_note(self, db):
        notes = "\n".join(db.validate().notes)
        assert "missing marker" in notes
        assert "note" in notes

    def test_notes_do_not_make_it_invalid(self, db):
        report = db.validate()
        assert report.notes
        assert report.ok

    def test_raise_if_failed_is_silent_when_ok(self, db):
        db.validate().raise_if_failed()


class TestValidateColumnDrift:
    def test_column_missing_from_metadata(self, resources):
        rewrite_yaml(resources, lambda f: f.pop("score") and None)
        report = Database.open(resources.root).validate()
        assert not report.ok
        assert any("not in the metadata" in e and "score" in e for e in report.errors)

    def test_field_missing_from_parquet(self, resources):
        pl.read_parquet(resources.parquet).drop("score").write_parquet(
            resources.parquet
        )
        report = Database.open(resources.root).validate()
        assert any("not in the parquet" in e and "score" in e for e in report.errors)

    def test_key_order_mismatch_is_only_a_warning(self, resources):
        def reorder(fields):
            return dict(reversed(list(fields.items())))

        rewrite_yaml(resources, reorder)
        report = Database.open(resources.root).validate()
        assert report.ok
        assert any("key order" in w for w in report.warnings)


class TestValidateTypes:
    def test_declared_type_contradicting_the_parquet_is_an_error(self, resources):
        def break_type(fields):
            fields["score"].value_type = "boolean"

        rewrite_yaml(resources, break_type)
        report = Database.open(resources.root).validate()
        assert not report.ok
        assert any("score" in e and "value_type" in e for e in report.errors)

    def test_undeclared_type_is_only_a_warning(self, resources):
        def clear_type(fields):
            fields["score"].value_type = None

        rewrite_yaml(resources, clear_type)
        report = Database.open(resources.root).validate()
        assert report.ok
        assert any("without a declared value_type" in w for w in report.warnings)

    def test_integer_width_change_is_accepted(self, resources):
        """Int8 and Int32 both satisfy "integer"; narrowing must not fail."""
        frame = pl.read_parquet(resources.parquet).with_columns(
            pl.col("small_count").cast(pl.Int64),
        )
        frame.write_parquet(resources.parquet)
        assert Database.open(resources.root).validate().ok


class TestValidateMetadata:
    def test_empty_fullname_is_an_error(self, resources):
        def blank(fields):
            fields["score"].fullname = "   "

        rewrite_yaml(resources, blank)
        report = Database.open(resources.root).validate()
        assert any("fullname is empty" in e for e in report.errors)


class TestValidatePrimaryKey:
    def test_duplicate_primary_key_is_an_error(self, resources):
        frame = pl.read_parquet(resources.parquet)
        frame = frame.with_columns(pl.lit(1, dtype=pl.Int32).alias(PRIMARY_KEY))
        frame.write_parquet(resources.parquet)
        report = Database.open(resources.root).validate()
        assert any("not unique" in e for e in report.errors)

    def test_null_primary_key_is_an_error(self, resources):
        frame = pl.read_parquet(resources.parquet)
        frame = frame.with_columns(
            pl.when(pl.col(PRIMARY_KEY) > 10)
            .then(None)
            .otherwise(pl.col(PRIMARY_KEY))
            .cast(pl.Int32)
            .alias(PRIMARY_KEY),
        )
        frame.write_parquet(resources.parquet)
        report = Database.open(resources.root).validate()
        assert any("null value" in e for e in report.errors)

    def test_absent_primary_key_is_an_error(self, resources):
        pl.read_parquet(resources.parquet).drop(PRIMARY_KEY).write_parquet(
            resources.parquet,
        )
        rewrite_yaml(resources, lambda f: f.pop(PRIMARY_KEY) and None)
        report = Database.open(resources.root).validate()
        assert any("primary key" in e and "missing" in e for e in report.errors)


class TestValidateUiConfig:
    def test_badge_for_unknown_field_is_an_error(self, resources):
        path = resources.root / "config" / "badges.yaml"
        path.write_text(yaml.safe_dump({"no_such_field": {}}), encoding="utf-8")
        report = Database.open(resources.root).validate()
        assert any("badges.yaml" in e and "no_such_field" in e for e in report.errors)

    def test_badge_the_table_cannot_render_is_a_warning(self, resources):
        """The renderer keys off a fixed list, so config alone is not enough."""
        path = resources.root / "config" / "badges.yaml"
        path.write_text(yaml.safe_dump({"note": {}}), encoding="utf-8")
        report = Database.open(resources.root).validate()
        assert report.ok
        assert any("does not render" in w and "note" in w for w in report.warnings)

    def test_link_placeholder_for_unknown_field_is_an_error(self, resources):
        path = resources.root / "config" / "external_links.yaml"
        path.write_text(
            yaml.safe_dump({"links": [{"name": "X", "url": "http://x/{bogus}"}]}),
            encoding="utf-8",
        )
        report = Database.open(resources.root).validate()
        assert any("external_links.yaml" in e and "bogus" in e for e in report.errors)

    def test_a_colour_scale_needs_no_entry_in_the_render_list(self, resources):
        """Unlike a badge, a scale is driven entirely from its own config.

        `score` is a float in the fixture tree, so this is the shape the real
        `loeuf` uses.
        """
        path = resources.root / "config" / "badges.yaml"
        path.write_text(
            yaml.safe_dump(
                {"score": {"scale": [{"below": 0.45, "color": "#c53030"}]}},
            ),
            encoding="utf-8",
        )
        report = Database.open(resources.root).validate()
        assert report.ok
        assert not any("does not render" in w for w in report.warnings)

    def test_a_colour_scale_on_a_non_numeric_column_is_an_error(self, resources):
        path = resources.root / "config" / "badges.yaml"
        path.write_text(
            yaml.safe_dump({"note": {"scale": [{"color": "#c53030"}]}}),
            encoding="utf-8",
        )
        report = Database.open(resources.root).validate()
        assert any("colour scale" in e and "note" in e for e in report.errors), (
            report.errors
        )

    def test_a_malformed_scale_rule_is_an_error(self, resources):
        path = resources.root / "config" / "badges.yaml"
        path.write_text(
            yaml.safe_dump({"score": {"scale": [{"below": 0.45}]}}),
            encoding="utf-8",
        )
        report = Database.open(resources.root).validate()
        assert any("no colour" in e for e in report.errors), report.errors

    def test_badges_and_scales_can_share_the_file(self, resources):
        path = resources.root / "config" / "badges.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "note": {"hello": {"label": "H", "color": "#000"}},
                    "score": {"scale": [{"color": "#c53030"}]},
                },
            ),
            encoding="utf-8",
        )
        report = Database.open(resources.root).validate()
        assert report.ok
        # The badge map is still held to the render list; the scale is not.
        assert any("does not render" in w and "note" in w for w in report.warnings)

    def test_a_badge_may_be_keyed_on_an_alias(self, resources):
        """So the badge follows the alias from one version to the next."""
        path = resources.root / "config" / "badges.yaml"
        path.write_text(
            yaml.safe_dump({"inheritance": {"x": {"label": "X", "color": "#000"}}}),
            encoding="utf-8",
        )
        db = Database.open(resources.root)
        db.set_alias("inheritance", "note")
        report = db.validate()
        assert report.ok, report.errors
        assert not any("does not render" in w for w in report.warnings)
        assert not any("inheritance" in n for n in report.notes)

    def test_missing_ui_config_files_are_tolerated(self, resources):
        (resources.root / "config" / "badges.yaml").unlink()
        (resources.root / "config" / "external_links.yaml").unlink()
        assert Database.open(resources.root).validate().ok


class TestValidationReport:
    def test_raise_if_failed_carries_the_report(self, resources):
        def break_type(fields):
            fields["score"].value_type = "boolean"

        rewrite_yaml(resources, break_type)
        report = Database.open(resources.root).validate()
        with pytest.raises(ValidationFailedError) as excinfo:
            report.raise_if_failed()
        assert excinfo.value.report is report
        assert "ERROR" in str(excinfo.value)

    def test_describe_labels_each_severity(self, resources):
        def mixed(fields):
            fields["score"].value_type = "boolean"
            fields["flag_b"].value_type = None

        rewrite_yaml(resources, mixed)
        described = Database.open(resources.root).validate().describe()
        assert "ERROR:" in described
        assert "WARNING:" in described
        assert "NOTE:" in described


class TestRepr:
    def test_repr_reports_shape_and_state(self, db):
        assert "20 genes" in repr(db)
        assert "clean" in repr(db)

    def test_repr_flags_modification(self, db):
        db["score"].meta.visibility = "primary"
        assert "modified" in repr(db)

    def test_html_repr_is_a_table(self, db):
        html = db._repr_html_()
        assert "<table" in html
        assert "GeneTrek database" in html
