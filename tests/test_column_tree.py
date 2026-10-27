"""Tests for grouping columns into the selector's category tree.

``group_fields`` is a pure function, so it is tested directly rather than
through NiceGUI -- the same reasoning as ``test_column_info``.
"""

from __future__ import annotations

import pytest

from genetrek import schema
from genetrek.config.loader import load_config
from genetrek.schema import Category, CategoryOrder, FieldMeta
from genetrek.ui.components.column_selector import (
    MY_LISTS_GROUP,
    _tree_nodes,
    _with_locked,
    _with_user_lists,
)


def _fields(*specs: tuple[str, str, str, str, int | None]) -> dict[str, FieldMeta]:
    """Build fields from ``(key, fullname, category, subcategory, priority)``."""
    return {
        key: FieldMeta(
            fullname=fullname,
            category=category,
            subcategory=subcategory,
            priority=priority,
        )
        for key, fullname, category, subcategory, priority in specs
    }


FIELDS = _fields(
    ("sym", "Gene", "Identity", "", 10),
    ("hid", "HGNC ID", "Identity", "Nomenclature", None),
    ("ncbi", "NCBI ID", "Identity", "Cross-references", None),
    ("c38", "GRCh38 chromosome", "Coordinates", "GRCh38", None),
    ("c37", "GRCh37 chromosome", "Coordinates", "GRCh37", None),
    ("loose", "Loose", "", "", None),
)

ORDER = CategoryOrder.of(
    [
        Category("Identity", "Who", ("Nomenclature", "Cross-references")),
        Category("Coordinates", "Where", ("GRCh38", "GRCh37")),
        Category("Other", uncategorized=True),
    ],
    FIELDS,
)


class TestGroupFields:
    def test_groups_follow_the_declared_category_order(self):
        groups = schema.group_fields(FIELDS, ORDER)
        assert [g.name for g in groups] == ["Identity", "Coordinates", "Other"]

    def test_a_group_carries_its_declared_description(self):
        groups = schema.group_fields(FIELDS, ORDER)
        assert groups[0].description == "Who"

    def test_columns_without_a_subcategory_lead_their_category(self):
        """So a category's headline columns sit above its subgroups."""
        identity = schema.group_fields(FIELDS, ORDER)[0]
        assert identity.columns == ("sym",)
        assert identity.all_columns[0] == "sym"

    def test_a_declared_subcategory_order_is_honoured(self):
        """ "Cross-references" sorts before "Nomenclature" alphabetically."""
        identity = schema.group_fields(FIELDS, ORDER)[0]
        assert [s.name for s in identity.subgroups] == [
            "Nomenclature",
            "Cross-references",
        ]

    def test_subcategories_are_alphabetical_without_an_override(self):
        fields = _fields(
            ("z", "Zed", "C", "Zebra", None),
            ("a", "Ay", "C", "Aardvark", None),
        )
        order = CategoryOrder.of([Category("C")], fields)
        groups = schema.group_fields(fields, order)
        assert [s.name for s in groups[0].subgroups] == ["Aardvark", "Zebra"]

    def test_uncategorised_columns_land_in_the_declared_bucket(self):
        bucket = schema.group_fields(FIELDS, ORDER)[-1]
        assert bucket.name == "Other"
        assert bucket.columns == ("loose",)

    def test_an_empty_group_is_dropped(self):
        """A node with no children ticks nothing, so it must not be rendered."""
        fields = _fields(("a", "Ay", "Used", "", None))
        order = CategoryOrder.of(
            [Category("Used"), Category("Idle", subcategories=("Ghost",))],
            fields,
        )
        groups = schema.group_fields(fields, order)
        assert [g.name for g in groups] == ["Used"]
        assert all(g.all_columns for g in groups)
        assert all(s.columns for g in groups for s in g.subgroups)

    def test_nothing_groups_to_nothing(self):
        assert schema.group_fields({}, ORDER) == []

    def test_without_categories_everything_lands_in_one_bucket(self):
        fields = _fields(("b", "Bee", "", "", None), ("a", "Ay", "", "", None))
        groups = schema.group_fields(fields)
        assert len(groups) == 1
        assert groups[0].all_columns == ("a", "b")

    @pytest.mark.parametrize(
        "order",
        [ORDER, schema.EMPTY_CATEGORIES],
        ids=["declared", "undeclared"],
    )
    def test_the_grouping_flattens_to_the_display_order(self, order):
        """The tree the selector renders and the order the table uses are one.

        True by construction -- ``group_fields`` cuts ``order_fields`` rather
        than sorting again -- and pinned here so it stays that way.
        """
        groups = schema.group_fields(FIELDS, order)
        flattened = [col for group in groups for col in group.all_columns]
        assert flattened == schema.order_fields(FIELDS, order)

    def test_it_does_not_depend_on_the_input_order(self):
        reversed_fields = dict(reversed(list(FIELDS.items())))
        assert schema.group_fields(reversed_fields, ORDER) == schema.group_fields(
            FIELDS,
            ORDER,
        )


class TestConfigColumnGroups:
    def test_a_tree_without_categories_still_groups(self, resources):
        """The default fixture writes no categories.yaml, which is the point."""
        config = load_config(resources.root)
        assert not config.categories
        assert len(config.column_groups) == 1
        assert config.column_groups[0].all_columns == tuple(config.visible_columns)

    def test_declared_categories_group_the_visible_columns(
        self,
        categorised_resources,
    ):
        """ "Coordinates" is absent because the fixture hides all three of its
        columns, which is the empty-group rule working through `load_config`:
        the tree only ever shows non-hidden columns."""
        config = load_config(categorised_resources.root)
        assert [g.name for g in config.column_groups] == [
            "Identity",
            "Annotations",
            "Unsorted",
        ]

    def test_the_groups_flatten_to_visible_columns(self, categorised_resources):
        config = load_config(categorised_resources.root)
        flattened = [col for g in config.column_groups for col in g.all_columns]
        assert flattened == config.visible_columns

    def test_the_symbol_still_leads(self, categorised_resources):
        """`styles.py` styles the first column positionally."""
        config = load_config(categorised_resources.root)
        assert config.visible_columns[0] == config.roles.symbol

    def test_no_group_is_empty(self, categorised_resources):
        config = load_config(categorised_resources.root)
        for group in config.column_groups:
            assert group.all_columns
            for sub in group.subgroups:
                assert sub.columns


class TestLockedSymbolColumn:
    """The symbol column is never offered and never lost.

    ``_tree_nodes`` and ``_with_locked`` are pure, so they are tested directly
    -- the dialog around them needs a NiceGUI client.
    """

    def test_the_locked_column_is_not_offered(self):
        nodes = _tree_nodes(schema.group_fields(FIELDS, ORDER), {}, "sym")
        identity = next(n for n in nodes if n["id"] == "cat:Identity")
        assert "sym" not in [child["id"] for child in identity["children"]]

    def test_the_locked_column_is_not_counted(self):
        """A count that still said 3 would promise a checkbox that is not there."""
        groups = schema.group_fields(FIELDS, ORDER)
        assert _tree_nodes(groups, {}, None)[0]["label"] == "Identity (3)"
        assert _tree_nodes(groups, {}, "sym")[0]["label"] == "Identity (2)"

    def test_a_group_the_lock_empties_is_dropped(self):
        fields = _fields(("only", "Only", "Identity", "", 10))
        nodes = _tree_nodes(schema.group_fields(fields, ORDER), {}, "only")
        assert nodes == []

    def test_a_subgroup_the_lock_empties_is_dropped(self):
        fields = _fields(
            ("sym", "Gene", "Identity", "", 10),
            ("hid", "HGNC ID", "Identity", "Nomenclature", None),
        )
        nodes = _tree_nodes(schema.group_fields(fields, ORDER), {}, "hid")
        assert [child["id"] for child in nodes[0]["children"]] == ["sym"]

    def test_without_a_symbol_role_nothing_is_locked(self):
        """`roles.symbol` may be None, and then this behaves as it always did."""
        groups = schema.group_fields(FIELDS, ORDER)
        assert _tree_nodes(groups, {}, None) == _tree_nodes(groups, {})

    def test_an_empty_selection_still_carries_the_locked_column(self):
        """The "None" button empties the tree; it must not empty the table."""
        assert _with_locked([], "sym", {"sym": 0, "hid": 1}) == ["sym"]

    def test_a_tick_payload_without_the_symbol_still_gets_it_first(self):
        order = {"sym": 0, "hid": 1, "ncbi": 2}
        assert _with_locked(["ncbi", "hid"], "sym", order) == ["sym", "hid", "ncbi"]

    def test_locking_nothing_changes_nothing(self):
        order = {"sym": 0, "hid": 1}
        assert _with_locked(["hid"], None, order) == ["hid"]

    def test_the_locked_column_is_not_duplicated(self):
        order = {"sym": 0, "hid": 1}
        assert _with_locked(["hid", "sym"], "sym", order) == ["sym", "hid"]

    def test_it_holds_over_the_real_taxonomy(self, categorised_resources):
        """End to end on the fixture tree: what the selector can offer plus the
        lock is exactly what the table shows, in the table's own order."""
        config = load_config(categorised_resources.root)
        locked = config.roles.symbol
        offered = [c for c in config.visible_columns if c != locked]
        nodes = _tree_nodes(config.column_groups, config.column_names, locked)
        in_tree = _leaf_ids(nodes)
        assert in_tree == offered
        assert (
            _with_locked(offered, locked, config.column_order) == config.visible_columns
        )
        assert _with_locked((), locked, config.column_order) == [locked]


def _leaf_ids(nodes: list[dict]) -> list[str]:
    """Every column id in a tree, depth first, in the order it is rendered."""
    found: list[str] = []
    for node in nodes:
        if children := node.get("children"):
            found += _leaf_ids(children)
        else:
            found.append(node["id"])
    return found


class TestUserListGroup:
    """The visitor's lists join the tree as one last group, never a category."""

    def test_lists_come_after_every_database_group(self, categorised_resources):
        config = load_config(categorised_resources.root)
        lists = ["mylist:aaaaaaaaaaaa", "mylist:bbbbbbbbbbbb"]
        groups = _with_user_lists(config.column_groups, lists)
        assert groups[-1].name == MY_LISTS_GROUP
        nodes = _tree_nodes(groups, {**config.column_names}, config.roles.symbol)
        assert _leaf_ids(nodes)[-2:] == lists
        assert nodes[-1]["label"] == f"{MY_LISTS_GROUP} (2)"

    def test_no_lists_no_group(self, categorised_resources):
        config = load_config(categorised_resources.root)
        assert _with_user_lists(config.column_groups, []) == config.column_groups

    def test_the_symbol_stays_first_with_lists_selected(self, categorised_resources):
        config = load_config(categorised_resources.root)
        locked = config.roles.symbol
        picked = ["mylist:aaaaaaaaaaaa", *config.visible_columns[1:3]]
        out = _with_locked(picked, locked, config.column_order)
        assert out[0] == locked
        assert out[-1] == "mylist:aaaaaaaaaaaa"
