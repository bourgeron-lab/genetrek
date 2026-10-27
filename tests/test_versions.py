"""Comparing coexisting versions of a gene list.

The point of computing these numbers is that the hand-written ones drifted, so
the arithmetic is pinned here and against the real database in
`test_real_database.py`. The detection rules matter as much as the arithmetic:
seeding from the aliases is what keeps a Categorical or a nullable column out
of a set operation.
"""

from __future__ import annotations

import polars as pl
import pytest

from genetrek.services.versions import (
    compare_boolean_columns,
    version_families,
    version_table_markdown,
)


@pytest.fixture
def frame() -> pl.DataFrame:
    """Two versions of a list, plus the shapes that must not be compared."""
    return pl.DataFrame(
        {
            "hc_v1": [True, True, True, False, False],
            "hc_v2": [True, True, False, True, False],
            # Boolean but nullable: neither in nor out of the set.
            "hc_v0": [True, None, True, False, False],
            # A different type entirely; `A & B` on this raises.
            "hc_text": pl.Series(["a", "b", "a", "b", "a"], dtype=pl.Categorical),
            "unrelated": [True, False, True, False, True],
        },
    )


class TestComparison:
    def test_the_arithmetic(self, frame):
        result = compare_boolean_columns(frame, "hc_v1", "hc_v2")
        assert (result.n_older, result.n_current) == (3, 3)
        assert result.in_both == 2
        assert result.added == 1
        assert result.dropped == 1
        # 2 shared over a union of 4.
        assert result.jaccard == pytest.approx(0.5)

    def test_identical_lists_are_one(self, frame):
        assert compare_boolean_columns(frame, "hc_v1", "hc_v1").jaccard == 1.0

    def test_two_empty_lists_do_not_divide_by_zero(self):
        frame = pl.DataFrame({"a": [False, False], "b": [False, False]})
        assert compare_boolean_columns(frame, "a", "b").jaccard == 0.0


class TestFamilyDetection:
    def test_a_family_is_seeded_from_its_alias(self, frame):
        families = version_families(frame, {"hc": "hc_v2"})
        assert len(families) == 1
        assert families[0].current == "hc_v2"
        assert [c.older for c in families[0].comparisons] == ["hc_v1"]

    def test_the_unrelated_column_is_not_a_sibling(self, frame):
        families = version_families(frame, {"hc": "hc_v2"})
        assert "unrelated" not in {c.older for c in families[0].comparisons}

    def test_a_nullable_sibling_is_skipped_with_a_reason(self, frame):
        """Set arithmetic over nulls would make up both halves of the answer."""
        family = version_families(frame, {"hc": "hc_v2"})[0]
        assert ("hc_v0", "1 genes have no value") in family.skipped

    def test_a_non_boolean_sibling_is_skipped_rather_than_raising(self, frame):
        """This is the crash a version regex over column names would cause."""
        family = version_families(frame, {"hc": "hc_v2"})[0]
        skipped = dict(family.skipped)
        assert "hc_text" in skipped
        assert "not a gene list" in skipped["hc_text"]

    def test_a_list_with_no_older_version_is_omitted(self, frame):
        assert version_families(frame, {"unrelated": "unrelated"}) == []

    def test_an_unusable_current_version_omits_the_family(self, frame):
        assert version_families(frame, {"hc": "hc_text"}) == []

    def test_an_alias_pointing_at_a_missing_column_is_ignored(self, frame):
        assert version_families(frame, {"hc": "absent"}) == []


class TestTable:
    def test_it_names_the_family_by_its_alias(self, frame):
        table = version_table_markdown(frame, {"hc": "hc_v2"})
        assert "| `hc` | `hc_v1` |" in table
        assert "**`hc_v2`** (current)" in table

    def test_a_skipped_sibling_says_why_in_the_table(self, frame):
        table = version_table_markdown(frame, {"hc": "hc_v2"})
        assert "not comparable" in table

    def test_nothing_to_compare_gives_an_empty_string(self, frame):
        """So the page can omit the section instead of showing a bare header."""
        assert version_table_markdown(frame, {"unrelated": "unrelated"}) == ""
