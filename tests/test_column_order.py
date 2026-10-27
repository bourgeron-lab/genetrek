"""Tests for the webapp's column display order.

Order is a declared metadata value (``priority``), not the parquet's column
order. These tests pin that distinction: ``visible_columns`` follows priority,
``common_columns`` stays in parquet order because that is the *data* order both
download paths and every ``df.select`` depend on.
"""

from __future__ import annotations

from genetrek import schema
from genetrek.config.loader import load_config
from genetrek.db import Database

#: What the synthetic fixture's priorities (symbol 10, flag_a 20, flag_b 30)
#: plus the alphabetical tail come to.
EXPECTED = [
    "symbol",
    "flag_a",
    "flag_b",
    "gene_type",
    "note",
    "score",
    "small_count",
]


class TestColumnOrder:
    def test_visible_columns_follow_priority_then_label(self, resources):
        assert load_config(resources.root).visible_columns == EXPECTED

    def test_hidden_columns_are_absent(self, resources):
        config = load_config(resources.root)
        hidden = {
            name
            for name, meta in schema.load_fields_yaml(resources.fields_yaml).items()
            if meta.visibility == "hidden"
        }
        assert hidden
        assert not hidden & set(config.visible_columns)

    def test_primary_columns_are_a_subsequence_of_visible_columns(self, resources):
        """The default view has to read in the same order as the picker."""
        config = load_config(resources.root)
        wanted = set(config.primary_columns)
        assert [c for c in config.visible_columns if c in wanted] == (
            config.primary_columns
        )

    def test_secondary_columns_are_a_subsequence_too(self, resources):
        config = load_config(resources.root)
        wanted = set(config.secondary_columns)
        assert [c for c in config.visible_columns if c in wanted] == (
            config.secondary_columns
        )

    def test_column_order_indexes_every_visible_column(self, resources):
        config = load_config(resources.root)
        assert set(config.column_order) == set(config.visible_columns)
        assert sorted(config.column_order.values()) == list(
            range(len(config.visible_columns)),
        )

    def test_column_order_agrees_with_visible_columns(self, resources):
        config = load_config(resources.root)
        assert (
            sorted(config.visible_columns, key=config.column_order.__getitem__)
            == config.visible_columns
        )

    def test_common_columns_stay_in_parquet_order(self, resources):
        """Priority must not leak into the data order.

        `common_columns` drives `df.select` and both download paths, and
        `Database.save` normalises the YAML's key order to match it.
        """
        config = load_config(resources.root)
        assert config.common_columns == list(config.df.columns)

    def test_labels_and_boolean_columns_follow_visible_order(self, resources):
        config = load_config(resources.root)
        assert list(config.column_names) == config.visible_columns
        wanted = set(config.boolean_columns)
        assert [c for c in config.visible_columns if c in wanted] == (
            config.boolean_columns
        )


class TestPriorityChangesTheOrder:
    def test_raising_a_secondary_column_promotes_it(self, resources):
        """A secondary column can outrank a primary one -- that is the design."""
        database = Database.open(resources.root)
        database.set_meta("score", priority=15)
        database.save()

        config = load_config(resources.root)
        # Priority 15 lands it *between* the two primaries (10 and 20).
        assert config.visible_columns[:3] == ["symbol", "score", "flag_a"]
        # Promotion changes position, never whether it shows by default.
        assert "score" not in config.primary_columns
        assert config.primary_columns == ["symbol", "flag_a"]

    def test_clearing_a_priority_drops_it_into_the_alphabetical_tail(self, resources):
        database = Database.open(resources.root)
        database.set_meta("symbol", priority=None)
        database.save()

        config = load_config(resources.root)
        assert config.visible_columns[0] == "flag_a"
        # "Symbol" now sorts alphabetically among the unset columns.
        assert config.visible_columns[1:] == [
            "flag_b",
            "gene_type",
            "note",
            "score",
            "small_count",
            "symbol",
        ]

    def test_a_negative_priority_leads(self, resources):
        database = Database.open(resources.root)
        database.set_meta("note", priority=-1)
        database.save()
        assert load_config(resources.root).visible_columns[0] == "note"
