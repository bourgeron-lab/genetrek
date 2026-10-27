"""Guard tests against the checked-in database rather than a fixture.

These are the regression net for the invariants the tooling relies on: that the
YAML validates, that its declared types still describe the parquet, and that the
identifier column the tooling keys on stays a usable primary key. They skip
rather than fail when the resources tree is unavailable, so a checkout without
data (or a wheel install) does not break the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest
import yaml

from genetrek import paths, schema
from genetrek.db.licences import licences_markdown
from genetrek.paths import ResourcesNotFoundError
from genetrek.services.versions import version_families


@pytest.fixture(scope="module")
def real_resources():
    try:
        root = paths.resources_dir()
    except ResourcesNotFoundError:  # pragma: no cover - depends on checkout
        pytest.skip("no resources directory available")
    if not paths.parquet_path(root).is_file():  # pragma: no cover
        pytest.skip("no parquet available")
    return root


@pytest.fixture(scope="module")
def real_fields(real_resources):
    return schema.load_fields_yaml(paths.fields_yaml_path(real_resources))


@pytest.fixture(scope="module")
def real_categories(real_resources, real_fields):
    """The declared category order, ranked against the real fields.

    Every ordering assertion below has to pass this. ``order_fields`` defaults
    the argument, so omitting it does not fail -- it silently asserts against
    the pre-categories order instead, which is worse than failing.
    """
    return schema.load_categories_yaml(
        paths.categories_path(real_resources),
        real_fields,
    )


@pytest.fixture(scope="module")
def real_schema(real_resources):
    return pl.read_parquet_schema(paths.parquet_path(real_resources))


def test_yaml_validates(real_fields):
    """Every entry parses through FieldMeta, including the extended keys."""
    assert len(real_fields) > 0


def test_yaml_and_parquet_have_the_same_columns(real_fields, real_schema):
    assert set(real_fields) == set(real_schema)


def test_key_order_matches_column_order(real_fields, real_schema):
    """Kept in lockstep so metadata diffs stay readable."""
    assert list(real_fields) == list(real_schema)


def test_declared_value_types_match_the_parquet(real_fields, real_schema):
    mismatched = {
        name: (str(real_schema[name]), meta.value_type)
        for name, meta in real_fields.items()
        if not schema.value_type_matches(real_schema[name], meta.value_type)
    }
    assert mismatched == {}


def test_every_field_declares_a_value_type(real_fields):
    assert [n for n, m in real_fields.items() if m.value_type is None] == []


def test_rewriting_the_real_yaml_is_byte_stable(real_resources, real_fields):
    """The writer must reproduce the curated file exactly, so saves stay clean."""
    path = paths.fields_yaml_path(real_resources)
    assert schema.dump_fields_yaml(real_fields) == path.read_text(encoding="utf-8")


def test_the_primary_key_is_usable(real_resources, real_fields):
    """The identifier the tooling resolves onto must be complete and unique.

    Found by role, not by name: this database was keyed on ``ncbi_id`` and is now
    keyed on ``hgnc_id``, and the invariant is the same either way.
    """
    key = schema.resolve_roles(real_fields).primary_key
    assert key is not None, "no field is marked primary_key"
    frame = pl.read_parquet(paths.parquet_path(real_resources), columns=[key])
    assert frame[key].null_count() == 0, f"{key} has nulls"
    assert frame[key].n_unique() == frame.height, f"{key} is not unique"


def test_external_link_placeholders_are_real_columns(real_resources, real_fields):
    config = yaml.safe_load(
        paths.external_links_path(real_resources).read_text(encoding="utf-8"),
    )
    referenced = {
        placeholder
        for link in config.get("links", [])
        for placeholder in re.findall(r"\{(\w+)\}", link.get("url", ""))
    }
    assert referenced <= set(real_fields)


def test_every_primary_column_declares_a_priority(real_fields):
    """What makes the default view's order deterministic rather than incidental."""
    missing = [
        name
        for name, meta in real_fields.items()
        if meta.visibility == "primary" and meta.priority is None
    ]
    assert not missing, f"primary without a priority: {missing}"


def test_the_display_order_is_deterministic(real_fields, real_categories):
    """What actually has to hold: the same metadata always yields the same order.

    Grouping is ``category`` then ``subcategory``; ``priority`` orders columns
    *within* one subcategory, so duplicate priorities across categories are
    expected rather than a slip. The five ``hc_ndd`` versions and the PanelApp
    panels are arranged that way. So the invariant is determinism, and it
    holds because ``fullname`` breaks every remaining tie.
    """
    order = schema.order_fields(real_fields, real_categories)
    assert set(order) == set(real_fields)
    # Independent of insertion order: with ties, a stable sort would otherwise
    # let the YAML's key order leak into the display order.
    reversed_fields = dict(reversed(list(real_fields.items())))
    assert order == schema.order_fields(reversed_fields, real_categories)
    # And every tie is broken, so no two fields share a sort key.
    keys = [schema.field_sort_key(real_fields[n], real_categories) for n in order]
    assert len(set(keys)) == len(keys), "two fields share a group, priority and label"


def test_hidden_columns_declare_no_priority(real_fields):
    """A hidden column never reaches the table, so a priority on it is dead metadata."""
    stray = [
        name
        for name, meta in real_fields.items()
        if meta.visibility == "hidden" and meta.priority is not None
    ]
    assert not stray, stray


def test_the_first_visible_column_is_the_symbol_column(real_fields, real_categories):
    """`styles.py` styles the sticky, right-aligned column positionally
    (`th:first-child`), so whatever sorts first silently inherits it. Expressed
    through roles so a rename cannot quietly invalidate it.

    Now depends on the category order as well as on priority, which is why the
    categories are passed: without them this asserts against an order the app
    does not use, and would pass while the real first column was something
    else entirely.
    """
    visible = {n: m for n, m in real_fields.items() if m.visibility != "hidden"}
    first = schema.order_fields(visible, real_categories)[0]
    assert first == schema.resolve_roles(real_fields).symbol


def test_every_declared_category_is_used(real_categories):
    """A category no field uses renders as an empty node, so it is dead config."""
    assert not real_categories.unused, f"declared but unused: {real_categories.unused}"


def test_every_used_category_is_declared(real_categories):
    """An undeclared category still sorts, but last and unordered."""
    assert not real_categories.undeclared, (
        f"used but not in categories.yaml: {real_categories.undeclared}"
    )


def test_the_grouping_flattens_to_the_display_order(real_fields, real_categories):
    """The tree the selector renders and the order the table uses are one thing.

    The only place the seeded metadata, `categories.yaml` and the sort key all
    meet, so the only place this can be checked for real rather than against a
    fixture built to satisfy it.
    """
    visible = {n: m for n, m in real_fields.items() if m.visibility != "hidden"}
    groups = schema.group_fields(visible, real_categories)
    flattened = [col for group in groups for col in group.all_columns]
    assert flattened == schema.order_fields(visible, real_categories)


def test_no_group_is_empty(real_fields, real_categories):
    """An empty node is a checkbox that ticks nothing, so grouping drops them."""
    visible = {n: m for n, m in real_fields.items() if m.visibility != "hidden"}
    for group in schema.group_fields(visible, real_categories):
        assert group.all_columns, f"empty category: {group.name}"
        for sub in group.subgroups:
            assert sub.columns, f"empty subcategory: {group.name}/{sub.name}"


def test_a_subcategory_always_has_a_category(real_fields):
    """A subcategory of nothing has no group to sort into."""
    stray = [
        name
        for name, meta in real_fields.items()
        if meta.subcategory and not meta.category
    ]
    assert not stray, stray


def test_the_shipped_alias_index_matches_the_shipped_parquet(real_resources):
    """CI is where a stale alias index has to be caught.

    The index is committed and copied into the image, and its exact-symbol
    layer describes the gene set it was built from. Rebuild the parquet with a
    different one and the two drift, which resolution cannot notice on its own
    -- a symbol that has gone looks exactly like a symbol that was retired.
    This test is the gate, for the same reason the parquet is checked against
    the metadata here rather than in the cluster.
    """
    path = paths.aliases_path(real_resources)
    assert path.is_file(), (
        f"no alias index at {path}; run `uv run genetrek-db build-aliases`"
    )

    meta = pl.read_parquet_metadata(path)
    frame = pl.read_parquet(real_resources / "processed_data" / "genetrek.parquet")
    fields = schema.load_fields_yaml(paths.fields_yaml_path(real_resources))
    key = schema.resolve_roles(fields).primary_key

    assert meta["key_column"] == key, "rebuild it: the primary key changed"
    assert meta["frame_rows"] == str(frame.height), (
        "rebuild it: the parquet's gene count changed"
    )
    assert key in pl.read_parquet_schema(path)


def test_the_version_comparison_matches_the_shipped_lists(real_resources):
    """Pin the figures the changelog publishes.

    These numbers were hand-written into `fields_description.yaml` once and two
    of that file's five numeric claims have since drifted, which is why the
    changelog computes them instead. This is the guard that the computation
    keeps agreeing with the data it is computed from.
    """
    frame = pl.read_parquet(paths.parquet_path(real_resources))
    fields = schema.load_fields_yaml(paths.fields_yaml_path(real_resources))
    aliases = {alias: name for name, meta in fields.items() for alias in meta.aliases}

    families = {family.alias: family for family in version_families(frame, aliases)}
    assert "hc_ndd" in families, "hc_ndd should still have older versions in the table"
    family = families["hc_ndd"]
    assert family.current == "hc_ndd_v7"
    assert not family.skipped

    by_older = {c.older: c for c in family.comparisons}
    assert set(by_older) == {"hc_ndd_v1", "hc_ndd_v2", "hc_ndd_v3", "hc_ndd_v6"}

    # (n_older, in_both, added, dropped, jaccard) against hc_ndd_v7's 2,200.
    for older, expected in {
        "hc_ndd_v1": (1594, 1517, 683, 77, 0.666227),
        "hc_ndd_v2": (1732, 1649, 551, 83, 0.722295),
        "hc_ndd_v3": (1834, 1763, 437, 71, 0.776310),
        "hc_ndd_v6": (2064, 2061, 139, 3, 0.935542),
    }.items():
        comparison = by_older[older]
        assert comparison.n_current == 2200, older
        assert (
            comparison.n_older,
            comparison.in_both,
            comparison.added,
            comparison.dropped,
        ) == expected[:4], older
        assert comparison.jaccard == pytest.approx(expected[4], abs=1e-6), older


def test_the_candidate_ndd_family_is_comparable_too(real_resources):
    """The second versioned family, restored from the 2024 export.

    Its v1 and v2 predate a large revision -- barely half of either survives
    into v5 -- which is exactly the kind of claim the changelog should compute
    rather than repeat from a hand-written description.
    """
    frame = pl.read_parquet(paths.parquet_path(real_resources))
    fields = schema.load_fields_yaml(paths.fields_yaml_path(real_resources))
    aliases = {alias: name for name, meta in fields.items() for alias in meta.aliases}

    families = {family.alias: family for family in version_families(frame, aliases)}
    assert "candidate_ndd" in families
    family = families["candidate_ndd"]
    assert family.current == "candidate_ndd_v5"
    assert not family.skipped

    by_older = {c.older: c for c in family.comparisons}
    assert set(by_older) == {
        "candidate_ndd_v1",
        "candidate_ndd_v2",
        "candidate_ndd_v3",
        "candidate_ndd_v4",
    }
    for older, expected in {
        "candidate_ndd_v1": (6556, 5002, 2314, 1554, 0.563923),
        "candidate_ndd_v2": (6492, 5045, 2271, 1447, 0.575716),
        "candidate_ndd_v3": (5773, 5395, 1921, 378, 0.701196),
        "candidate_ndd_v4": (5710, 5389, 1927, 321, 0.705644),
    }.items():
        comparison = by_older[older]
        assert comparison.n_current == 7316, older
        assert (
            comparison.n_older,
            comparison.in_both,
            comparison.added,
            comparison.dropped,
        ) == expected[:4], older
        assert comparison.jaccard == pytest.approx(expected[4], abs=1e-6), older


def test_data_licences_file_matches_the_metadata(real_fields, real_categories):
    """DATA_LICENCES.md is generated; a stale copy would misstate the terms."""
    committed = Path(__file__).resolve().parents[1] / "DATA_LICENCES.md"
    if not committed.is_file():  # pragma: no cover - depends on checkout
        pytest.skip("no DATA_LICENCES.md in this checkout")
    expected = licences_markdown(real_fields, real_categories)
    assert committed.read_text(encoding="utf-8") == expected, (
        "DATA_LICENCES.md is stale: run `uv run genetrek-db licences --write "
        "DATA_LICENCES.md`"
    )


def test_every_column_declares_a_licence(real_fields):
    """No column ships without stating the terms it is redistributed under."""
    missing = [name for name, meta in real_fields.items() if not meta.licence]
    assert not missing, missing
