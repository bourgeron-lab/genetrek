"""Tests for Field and BoundField."""

from __future__ import annotations

import json

import polars as pl
import pytest
from pydantic import ValidationError

from genetrek import schema
from genetrek.db import Database, Field
from genetrek.schema import FieldMeta, today_stamp


@pytest.fixture
def db(resources):
    return Database.open(resources.root)


class TestField:
    def test_to_yaml_reparses(self, resources):
        field = Field(name="thing", meta=FieldMeta(fullname="A Thing"))
        path = resources.root / "one.yaml"
        path.write_text(field.to_yaml(), encoding="utf-8")
        assert schema.load_fields_yaml(path)["thing"].fullname == "A Thing"

    def test_to_json_is_keyed_by_name(self):
        field = Field(name="thing", meta=FieldMeta(fullname="A Thing"))
        payload = json.loads(field.to_json())
        assert payload["thing"]["fullname"] == "A Thing"
        assert payload["thing"]["visibility"] == "hidden"

    @pytest.mark.parametrize("suffix", [".yaml", ".json"])
    def test_save_metadata_picks_format_by_suffix(self, tmp_path, suffix):
        field = Field(name="thing", meta=FieldMeta(fullname="A Thing"))
        path = tmp_path / f"out{suffix}"
        field.save_metadata(path)
        text = path.read_text(encoding="utf-8")
        assert text.lstrip().startswith("{") is (suffix == ".json")

    def test_describe_lists_every_key(self):
        field = Field(name="thing", meta=FieldMeta(fullname="A Thing"))
        described = field.describe()
        assert described.startswith("thing")
        for key in FieldMeta.model_fields:
            assert key in described

    def test_touch_and_apply_delegate_to_meta(self):
        field = Field(name="thing", meta=FieldMeta(fullname="A Thing"))
        field.touch()
        assert field.meta.update == today_stamp()
        field.apply({"visibility": "primary"})
        assert field.meta.visibility == "primary"


class TestBoundFieldAccess:
    def test_values_are_the_column(self, db, resources):
        assert db["score"].values.to_list() == resources.frame["score"].to_list()

    def test_dtype(self, db):
        assert db["score"].dtype == pl.Float32
        assert db["flag_a"].dtype == pl.Boolean

    def test_estimated_size_is_positive(self, db):
        assert db["symbol"].estimated_size() > 0

    def test_to_frame_pairs_with_an_identifier(self, db):
        frame = db["score"].to_frame("ncbi_id")
        assert frame.columns == ["ncbi_id", "score"]
        assert frame.height == 20

    def test_to_frame_rejects_unknown_id_column(self, db):
        with pytest.raises(KeyError, match="unknown identifier column"):
            db["score"].to_frame("nope")

    def test_to_tsv_writes_every_row(self, db, tmp_path):
        path = tmp_path / "out.tsv"
        assert db["score"].to_tsv(path) == 20
        assert pl.read_csv(path, separator="\t").height == 20

    def test_to_tsv_drop_nulls_for_boolean_keeps_only_members(self, db, tmp_path):
        """A gene list's useful export is its members, not its False rows."""
        path = tmp_path / "flags.tsv"
        written = db["flag_b"].to_tsv(path, drop_nulls=True)
        assert written == 3
        assert pl.read_csv(path, separator="\t")["flag_b"].all()

    def test_to_tsv_drop_nulls_for_sparse_column(self, db, tmp_path):
        path = tmp_path / "omim.tsv"
        written = db["omim_id"].to_tsv(path, drop_nulls=True)
        assert written == 4  # rows where i % 5 == 0


class TestBoundFieldStats:
    def test_boolean_counts(self, db):
        stats = db["flag_a"].stats()
        assert stats.value_type == "boolean"
        assert stats.n_true == 10
        assert stats.n_false == 10
        assert stats.n_null == 0
        assert stats.minimum is None

    def test_numeric_summary(self, db):
        stats = db["score"].stats()
        assert stats.value_type == "float"
        assert stats.minimum == pytest.approx(0.25)
        assert stats.maximum == pytest.approx(5.0)
        assert stats.median == pytest.approx(2.625)
        assert stats.n_true is None

    def test_integer_summary(self, db):
        stats = db["small_count"].stats()
        assert stats.value_type == "integer"
        assert stats.minimum == 0
        assert stats.maximum == 6

    def test_categorical_top_values(self, db):
        stats = db["gene_type"].stats()
        assert stats.value_type == "categorical"
        assert stats.n_unique == 2
        assert dict(stats.top_values or []) == {"protein-coding": 10, "ncRNA": 10}

    def test_string_nulls_are_counted(self, db):
        stats = db["ensg"].stats()
        assert stats.n_null == 6  # rows where i % 3 == 0
        assert stats.pct_null == pytest.approx(30.0)

    def test_literal_none_marker_is_counted_separately(self, db):
        """A string "None" is data, not a null, and must be visible as such."""
        stats = db["note"].stats()
        assert stats.n_null == 0
        assert stats.n_missing_marker == 15  # rows where i % 4 != 0

    def test_marker_count_is_zero_for_clean_columns(self, db):
        assert db["symbol"].stats().n_missing_marker == 0

    def test_marker_is_not_reported_for_numeric(self, db):
        assert db["score"].stats().n_missing_marker is None

    def test_rows_render_the_applicable_block_only(self, db):
        labels = [label for label, _ in db["flag_a"].stats().rows()]
        assert "true" in labels
        assert "median" not in labels

        labels = [label for label, _ in db["score"].stats().rows()]
        assert "median" in labels
        assert "true" not in labels


class TestWriteThrough:
    def test_metadata_edit_stages_on_the_database(self, db):
        assert not db.is_dirty
        db["score"].meta.visibility = "primary"
        assert db.is_dirty
        assert db.fields["score"].visibility == "primary"

    def test_edit_is_visible_through_a_fresh_lookup(self, db):
        db["score"].meta.description = "constraint"
        assert db["score"].meta.description == "constraint"

    def test_apply_through_bound_field(self, db):
        db["score"].apply({"visibility": "primary", "source": "gnomAD"})
        assert db.fields["score"].visibility == "primary"
        assert db.fields["score"].source == "gnomAD"

    def test_rejected_edit_leaves_database_clean(self, db):
        with pytest.raises(ValidationError):
            db["score"].meta.visibility = "nonsense"
        assert not db.is_dirty
