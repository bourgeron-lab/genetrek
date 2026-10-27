"""The column reference: every column as one table, built from the metadata.

The page is only a rendering of :func:`column_reference_rows`, so these pin the
rows -- order, hidden columns, counts -- and the TSV made from them.
"""

from __future__ import annotations

import polars as pl
import pytest

from genetrek import schema
from genetrek.config.loader import load_config
from genetrek.schema import EMPTY_CATEGORIES, FieldMeta
from genetrek.services.column_reference import (
    column_reference_rows,
    column_reference_tsv,
    doi_url,
    format_date,
)
from genetrek.services.lookup import value_counts


class TestOverTheSyntheticTree:
    @pytest.fixture
    def config(self, resources):
        return load_config(resources.root)

    @pytest.fixture
    def rows(self, config):
        return column_reference_rows(config.fields, config.categories, config.df)

    def test_every_column_in_display_order_hidden_ones_included(self, config, rows):
        assert [row["key"] for row in rows] == schema.order_fields(
            config.fields, config.categories
        )
        assert [row["rank"] for row in rows] == list(range(len(rows)))

    def test_hidden_columns_are_marked_api_only(self, config, rows):
        flagged = {row["key"] for row in rows if row["api_only"]}
        hidden = {n for n, m in config.fields.items() if m.visibility == "hidden"}
        assert hidden
        assert flagged == hidden

    def test_genes_counts_as_the_api_does(self, config, rows):
        """Members for a boolean, non-empty for a string: `include_counts`."""
        expected = value_counts(config.df, [row["key"] for row in rows])
        assert {row["key"]: row["n_genes"] for row in rows} == expected

    def test_measured_only_for_columns_that_missed_genes(self, config, rows):
        by_key = {row["key"]: row for row in rows}
        # `ensg` is null on every third gene; `symbol` is never null.
        assert by_key["ensg"]["n_measured"] == config.df["ensg"].drop_nulls().len()
        assert by_key["ensg"]["n_measured"] < config.df.height
        assert by_key["symbol"]["n_measured"] is None

    def test_the_tsv_has_a_header_and_a_line_per_column(self, rows):
        lines = column_reference_tsv(rows).splitlines()
        assert lines[0].split("\t")[0] == "column"
        assert len(lines) == len(rows) + 1
        width = len(lines[0].split("\t"))
        assert all(len(line.split("\t")) == width for line in lines)


class TestRows:
    def test_metadata_is_formatted_for_reading(self):
        fields = {
            "pos": FieldMeta(
                fullname="Start",
                value_type="integer",
                assembly="GRCh38",
                update="20260918",
                doi="10.1038/abc",
            ),
            "plain": FieldMeta(fullname="Plain"),
        }
        frame = pl.DataFrame({"pos": [1, 2], "plain": ["a", "b"]})
        rows = {
            row["key"]: row
            for row in column_reference_rows(fields, EMPTY_CATEGORIES, frame)
        }
        assert rows["pos"]["type"] == "integer (GRCh38)"
        assert rows["pos"]["updated"] == "2026-09-18"
        assert rows["pos"]["doi_url"] == "https://doi.org/10.1038/abc"
        assert rows["plain"]["doi_url"] == ""
        assert rows["plain"]["category"] == EMPTY_CATEGORIES.uncategorized_label

    def test_a_field_missing_from_the_parquet_is_left_out(self):
        fields = {"here": FieldMeta(fullname="Here"), "gone": FieldMeta(fullname="G")}
        frame = pl.DataFrame({"here": [1]})
        rows = column_reference_rows(fields, EMPTY_CATEGORIES, frame)
        assert [row["key"] for row in rows] == ["here"]

    def test_a_boolean_counts_members_and_measured_separately(self):
        fields = {"flag": FieldMeta(fullname="Flag", value_type="boolean")}
        frame = pl.DataFrame({"flag": [True, False, None, True]})
        (row,) = column_reference_rows(fields, EMPTY_CATEGORIES, frame)
        assert row["n_genes"] == 2
        assert row["n_measured"] == 3


class TestTsv:
    def test_prose_cannot_break_the_layout(self):
        fields = {
            "x": FieldMeta(fullname="X", description="one\ttwo\nthree"),
        }
        frame = pl.DataFrame({"x": [1]})
        text = column_reference_tsv(
            column_reference_rows(fields, EMPTY_CATEGORIES, frame)
        )
        header, line = text.splitlines()
        assert "one two three" in line.split("\t")
        assert len(line.split("\t")) == len(header.split("\t"))

    def test_a_missing_count_is_na_and_a_flag_is_lower_case(self):
        fields = {"x": FieldMeta(fullname="X", visibility="hidden")}
        frame = pl.DataFrame({"x": [1]})
        text = column_reference_tsv(
            column_reference_rows(fields, EMPTY_CATEGORIES, frame)
        )
        record = dict(
            zip(*(line.split("\t") for line in text.splitlines()), strict=True)
        )
        assert record["n_measured"] == "NA"
        assert record["api_only"] == "true"


class TestFormatters:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("20260918", "2026-09-18"), ("", ""), ("2026", "2026"), ("soon", "soon")],
    )
    def test_format_date(self, value, expected):
        assert format_date(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("10.1038/abc", "https://doi.org/10.1038/abc"),
            ("https://doi.org/10.1038/abc", "https://doi.org/10.1038/abc"),
            ("http://example.org/x", "http://example.org/x"),
        ],
    )
    def test_doi_url(self, value, expected):
        assert doi_url(value) == expected
