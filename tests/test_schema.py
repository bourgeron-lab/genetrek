"""Tests for the field-metadata contract."""

from __future__ import annotations

from typing import ClassVar

import polars as pl
import pytest
from pydantic import ValidationError

from genetrek import schema
from genetrek.schema import FieldMeta, FieldsYamlError


class TestFieldMeta:
    def test_defaults_leave_optional_keys_empty(self):
        meta = FieldMeta(fullname="Gene")
        assert meta.description == ""
        assert meta.visibility == "hidden"
        assert meta.value_type is None
        assert meta.id_type is None
        assert meta.priority is None

    def test_unknown_key_is_rejected(self):
        with pytest.raises(ValidationError):
            FieldMeta(fullname="Gene", colour="blue")

    @pytest.mark.parametrize("bad", ["primaryy", "PRIMARY", "visible", ""])
    def test_visibility_is_restricted(self, bad):
        with pytest.raises(ValidationError):
            FieldMeta(fullname="Gene", visibility=bad)

    @pytest.mark.parametrize("stamp", ["", "20240116", "20000229"])
    def test_valid_date_stamps(self, stamp):
        assert FieldMeta(fullname="Gene", update=stamp).update == stamp

    @pytest.mark.parametrize(
        "bad",
        ["2024-01-16", "202401", "20240132", "20240230", "abcdefgh"],
    )
    def test_invalid_date_stamps(self, bad):
        with pytest.raises(ValidationError):
            FieldMeta(fullname="Gene", update=bad)

    def test_assignment_is_validated(self):
        meta = FieldMeta(fullname="Gene")
        with pytest.raises(ValidationError):
            meta.visibility = "nonsense"

    def test_touch_sets_today(self):
        meta = FieldMeta(fullname="Gene")
        meta.touch()
        assert meta.update == schema.today_stamp()

    def test_apply_updates_several_keys(self):
        meta = FieldMeta(fullname="Gene")
        meta.apply({"visibility": "primary", "description": "hello"})
        assert meta.visibility == "primary"
        assert meta.description == "hello"

    def test_apply_rejects_unknown_key(self):
        meta = FieldMeta(fullname="Gene")
        with pytest.raises(ValueError, match="unknown metadata key"):
            meta.apply({"colour": "blue"})

    def test_apply_is_atomic(self):
        """A rejected batch must leave every key untouched."""
        meta = FieldMeta(fullname="Gene", visibility="hidden")
        with pytest.raises(ValidationError):
            meta.apply({"description": "kept?", "visibility": "nonsense"})
        assert meta.visibility == "hidden"
        assert meta.description == ""

    def test_apply_is_atomic_against_the_model_validator(self):
        """A batch refused by a *cross-field* rule also changes nothing.

        Held by the pre-validation rather than by the assignment being one
        step: the end state is what gets validated, so an invalid one is
        refused before anything is written. Pinned because it is the half of
        the contract the tests below cannot reach -- once assignment is a
        single step, no batch can be rejected partway through.
        """
        meta = FieldMeta(fullname="Gene")
        before = meta.model_dump()
        with pytest.raises(ValidationError, match="needs at least one"):
            meta.apply({"derived_op": "union", "derived_from": []})
        assert meta.model_dump() == before

    @pytest.mark.parametrize(
        "changes",
        [
            {"derived_from": [], "derived_op": None},
            {"derived_op": None, "derived_from": []},
        ],
        ids=["sources-first", "op-first"],
    )
    def test_apply_clears_a_derivation_whatever_the_key_order(self, changes):
        """Valid as a whole, invalid halfway: key order must not decide.

        Applied one key at a time this was refused outright whenever
        `derived_from` landed first, because the model still carried the
        operation at that moment.
        """
        meta = FieldMeta(fullname="Gene", derived_op="union", derived_from=["a"])
        meta.apply(changes)
        assert meta.derived_op is None
        assert meta.derived_from == []

    def test_apply_sets_a_derivation_in_one_batch(self):
        """The reported defect, in the direction that left invalid state.

        Applied key by key with `derived_op` first, the model briefly carried
        an operation with no sources; the validator refused that, *after* the
        operation had already stuck -- so the call raised and left behind the
        `union` with no sources `_check_derivation` exists to forbid.
        """
        meta = FieldMeta(fullname="Gene")
        meta.apply({"derived_op": "intersection", "derived_from": ["a", "b"]})
        assert meta.derived_op == "intersection"
        assert meta.derived_from == ["a", "b"]

    def test_apply_keeps_the_benign_normalisation(self):
        """Sources without an operation still default to a union."""
        meta = FieldMeta(fullname="Gene")
        meta.apply({"derived_from": ["a"]})
        assert meta.derived_op == "union"

    def test_apply_leaves_the_model_reusable(self):
        """Bulk assignment must not leave pydantic's own bookkeeping behind.

        Applying fields wholesale bypasses `validate_assignment`, so this pins
        that ordinary validation, dumping and round-tripping still work after
        one -- otherwise the escape hatch would quietly disable the contract.
        """
        meta = FieldMeta(fullname="Gene")
        meta.apply({"category": "Constraint", "subcategory": "gnomAD"})
        assert "category" in meta.__pydantic_fields_set__
        assert FieldMeta.model_validate(meta.model_dump()).category == "Constraint"
        with pytest.raises(ValidationError):
            meta.visibility = "nonsense"


class TestValueTypes:
    @pytest.mark.parametrize(
        ("dtype", "expected"),
        [
            (pl.Boolean, "boolean"),
            (pl.Int8, "integer"),
            (pl.Int32, "integer"),
            (pl.UInt64, "integer"),
            (pl.Float32, "float"),
            (pl.Float64, "float"),
            (pl.String, "string"),
            (pl.Categorical, "categorical"),
            (pl.Enum(["a", "b"]), "categorical"),
        ],
    )
    def test_dtype_maps_to_value_type(self, dtype, expected):
        assert schema.value_type_for_dtype(dtype) == expected

    def test_unsupported_dtype_maps_to_none(self):
        assert schema.value_type_for_dtype(pl.Date) is None

    def test_matches_accepts_dtype_family(self):
        assert schema.value_type_matches(pl.Int8, "integer")
        assert schema.value_type_matches(pl.Int64, "integer")

    def test_matches_rejects_wrong_family(self):
        assert not schema.value_type_matches(pl.String, "boolean")
        assert not schema.value_type_matches(pl.Float32, "integer")

    def test_undeclared_matches_anything(self):
        assert schema.value_type_matches(pl.String, None)


class TestSlugify:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("High_Confidence_NDD_genes_v7", "high_confidence_ndd_genes_v7"),
            ("hg19 - chromosome", "hg19_chromosome"),
            ("p-value", "p_value"),
            (
                "gnomad.v4.0.constraint_metrics - lof.oe_ci.upper < 0.6",
                "gnomad_v4_0_constraint_metrics_lof_oe_ci_upper_0_6",
            ),
            (
                "SPARK genes (updated september 2022",
                "spark_genes_updated_september_2022",
            ),
            ("AUTISM|DD categories", "autism_dd_categories"),
            ("  padded  ", "padded"),
        ],
    )
    def test_slugify(self, header, expected):
        assert schema.slugify_field_name(header) == expected

    @pytest.mark.parametrize("header", ["", "   ", "///", "!!!"])
    def test_unusable_header_raises(self, header):
        with pytest.raises(ValueError, match="cannot derive a field name"):
            schema.slugify_field_name(header)


class TestYamlRoundTrip:
    def test_round_trip_is_lossless(self, resources):
        fields = schema.load_fields_yaml(resources.fields_yaml)
        rewritten = resources.root / "again.yaml"
        schema.write_fields_yaml(rewritten, fields)
        assert schema.load_fields_yaml(rewritten) == fields

    def test_rewriting_is_byte_stable(self, resources):
        """Writing an unmodified file back must not churn the diff."""
        before = resources.fields_yaml.read_text(encoding="utf-8")
        fields = schema.load_fields_yaml(resources.fields_yaml)
        schema.write_fields_yaml(resources.fields_yaml, fields)
        assert resources.fields_yaml.read_text(encoding="utf-8") == before

    def test_key_order_is_stable(self, resources):
        fields = schema.load_fields_yaml(resources.fields_yaml)
        rendered = schema.dump_fields_yaml(fields)
        entry = rendered.split("ncbi_id:\n", 1)[1].split("ensg:", 1)[0]
        emitted = [line.split(":")[0].strip() for line in entry.strip().splitlines()]
        assert emitted == list(schema.FIELD_KEY_ORDER)

    def test_none_renders_as_null_and_reloads(self, resources):
        fields = schema.load_fields_yaml(resources.fields_yaml)
        assert "  id_type: null\n" in schema.dump_fields_yaml(fields)
        assert fields["flag_a"].id_type is None

    @pytest.mark.parametrize(
        "fullname",
        [
            "AUTISM|DD categories from Fu et al. (PMID: 35982160)",
            "value: with colon",
            "true",
            "123",
            "-leading dash",
            "trailing space ",
            '"already quoted"',
            "back\\slash",
            "# hash start",
            "null",
        ],
    )
    def test_awkward_scalars_survive_a_round_trip(self, tmp_path, fullname):
        original = {"f": FieldMeta(fullname=fullname)}
        path = tmp_path / "f.yaml"
        schema.write_fields_yaml(path, original)
        assert schema.load_fields_yaml(path)["f"].fullname == fullname

    def test_awkward_field_key_survives(self, tmp_path):
        original = {"35982160_autism_dd": FieldMeta(fullname="Fu et al.")}
        path = tmp_path / "f.yaml"
        schema.write_fields_yaml(path, original)
        assert list(schema.load_fields_yaml(path)) == ["35982160_autism_dd"]


class TestYamlErrors:
    def test_empty_file_loads_as_empty(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert schema.load_fields_yaml(path) == {}

    def test_non_mapping_top_level_raises(self, tmp_path):
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(FieldsYamlError, match="top level must be a mapping"):
            schema.load_fields_yaml(path)

    def test_all_problems_are_reported_together(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            "a:\n  fullname: A\n  visibility: nonsense\n"
            "b:\n  description: no fullname\n"
            "c:\n  fullname: C\n  update: not-a-date\n",
            encoding="utf-8",
        )
        with pytest.raises(FieldsYamlError) as excinfo:
            schema.load_fields_yaml(path)
        assert len(excinfo.value.problems) == 3
        joined = "\n".join(excinfo.value.problems)
        assert "a: visibility" in joined
        assert "b: fullname" in joined
        assert "c: update" in joined

    def test_entry_that_is_not_a_mapping_is_reported(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("a: just a string\n", encoding="utf-8")
        with pytest.raises(FieldsYamlError, match="entry must be a mapping"):
            schema.load_fields_yaml(path)


class TestEveryKeyIsPersisted:
    """A key on FieldMeta that FIELD_KEY_ORDER omits is dropped on save.

    ``_dump_entry`` iterates FIELD_KEY_ORDER, so a newly added attribute
    round-trips in memory, passes every other test in this file, and vanishes
    the first time a database is saved -- silently, with no error and nothing in
    ``validate`` to notice. ``test_key_order_is_stable`` compares the emitted
    keys against the same constant, so it cannot catch this.
    """

    def test_field_key_order_covers_every_model_field(self):
        assert set(schema.FIELD_KEY_ORDER) == set(FieldMeta.model_fields)

    def test_no_key_is_listed_twice(self):
        assert len(schema.FIELD_KEY_ORDER) == len(set(schema.FIELD_KEY_ORDER))

    def test_the_style_sets_name_real_keys(self):
        """A typo in either set fails silently: wrong quoting, no error."""
        keys = set(schema.FIELD_KEY_ORDER)
        assert keys >= schema._ALWAYS_QUOTED
        assert keys >= schema._LIST_KEYS


def _named(*specs: tuple[str, str, int | None]) -> dict[str, FieldMeta]:
    """Build fields from ``(key, fullname, priority)`` triples."""
    return {
        key: FieldMeta(fullname=fullname, priority=priority)
        for key, fullname, priority in specs
    }


class TestDisplayOrder:
    def test_smaller_priority_comes_first(self):
        fields = _named(("b", "Bee", 20), ("a", "Ay", 10))
        assert schema.order_fields(fields) == ["a", "b"]

    def test_unset_priority_sorts_last(self):
        fields = _named(("free", "Aaa", None), ("pinned", "Zzz", 99))
        assert schema.order_fields(fields) == ["pinned", "free"]

    def test_all_unset_falls_back_to_the_label(self):
        fields = _named(("c", "Gamma", None), ("a", "Alpha", None), ("b", "Beta", None))
        assert schema.order_fields(fields) == ["a", "b", "c"]

    def test_ties_break_case_insensitively(self):
        fields = _named(("a", "beta", 1), ("b", "Alpha", 1))
        assert schema.order_fields(fields) == ["b", "a"]

    def test_negative_priority_outranks_zero(self):
        fields = _named(("zero", "Zero", 0), ("neg", "Neg", -5))
        assert schema.order_fields(fields) == ["neg", "zero"]

    def test_order_is_independent_of_input_order(self):
        """The whole point: the parquet's column order stops mattering."""
        specs = (("a", "Ay", 10), ("b", "Bee", None), ("c", "Cee", 5))
        forward = schema.order_fields(_named(*specs))
        backward = schema.order_fields(_named(*reversed(specs)))
        assert forward == backward == ["c", "a", "b"]

    def test_an_empty_mapping_orders_to_nothing(self):
        assert schema.order_fields({}) == []


class TestInDisplayOrder:
    ORDER: ClassVar[dict[str, int]] = {"first": 0, "second": 1, "third": 2}

    def test_a_subset_is_reordered(self):
        assert schema.in_display_order(["third", "first"], self.ORDER) == [
            "first",
            "third",
        ]

    def test_a_set_is_accepted(self):
        assert schema.in_display_order({"third", "second"}, self.ORDER) == [
            "second",
            "third",
        ]

    def test_unknown_columns_go_last_in_input_order(self):
        """A stale selection has to degrade, not raise."""
        columns = ["ghost", "third", "spectre"]
        assert schema.in_display_order(columns, self.ORDER) == [
            "third",
            "ghost",
            "spectre",
        ]

    def test_it_is_idempotent(self):
        once = schema.in_display_order(["third", "first", "second"], self.ORDER)
        assert schema.in_display_order(once, self.ORDER) == once

    def test_an_empty_order_keeps_the_input_order(self):
        assert schema.in_display_order(["b", "a"], {}) == ["b", "a"]


class TestPriorityRendering:
    def test_it_renders_bare_and_reloads_as_an_int(self, tmp_path):
        """Quoted `priority: "10"` would reload only by pydantic's coercion."""
        path = tmp_path / "f.yaml"
        schema.write_fields_yaml(path, {"f": FieldMeta(fullname="F", priority=10)})
        assert "  priority: 10\n" in path.read_text(encoding="utf-8")
        reloaded = schema.load_fields_yaml(path)["f"].priority
        assert reloaded == 10
        assert isinstance(reloaded, int)

    def test_a_negative_priority_renders_bare(self, tmp_path):
        """`-` leads a YAML indicator, which is why the int branch skips _scalar."""
        path = tmp_path / "f.yaml"
        schema.write_fields_yaml(path, {"f": FieldMeta(fullname="F", priority=-5)})
        assert "  priority: -5\n" in path.read_text(encoding="utf-8")
        assert schema.load_fields_yaml(path)["f"].priority == -5

    def test_an_unset_priority_renders_as_null(self, tmp_path):
        path = tmp_path / "f.yaml"
        schema.write_fields_yaml(path, {"f": FieldMeta(fullname="F")})
        assert "  priority: null\n" in path.read_text(encoding="utf-8")
        assert schema.load_fields_yaml(path)["f"].priority is None

    def test_the_boolean_key_still_renders_as_a_boolean(self):
        """bool subclasses int, so the int branch must stay below the bool one."""
        rendered = schema.dump_fields_yaml(
            {"f": FieldMeta(fullname="F", primary_key=True)},
        )
        assert "  primary_key: true\n" in rendered

    def test_a_non_numeric_priority_is_rejected(self):
        """Assert on a non-numeric string: pydantic coerces "5" to 5."""
        with pytest.raises(ValidationError):
            FieldMeta(fullname="F", priority="high")


def _grouped(*specs: tuple[str, str, str, str, int | None]) -> dict[str, FieldMeta]:
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


def _declare(*specs: tuple[str, ...]) -> schema.CategoryOrder:
    """Rank ``(name, subcategories...)`` tuples, with no field set."""
    return schema.CategoryOrder.of(
        [
            schema.Category(name=spec[0], subcategories=tuple(spec[1:]))
            for spec in specs
        ],
    )


class TestLoadCategoriesYaml:
    def test_a_missing_file_declares_nothing(self, tmp_path):
        """The file is optional: older trees and staged ones have none."""
        order = schema.load_categories_yaml(tmp_path / "absent.yaml")
        assert not order
        assert order.categories == ()

    def test_an_empty_file_declares_nothing(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("", encoding="utf-8")
        assert not schema.load_categories_yaml(path)

    def test_it_reads_names_descriptions_and_subcategories(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text(
            "categories:\n"
            "  - name: Second\n"
            "    description: The other one\n"
            "    subcategories: [Beta, Alpha]\n"
            "  - name: First\n",
            encoding="utf-8",
        )
        order = schema.load_categories_yaml(path)
        assert [c.name for c in order.categories] == ["Second", "First"]
        assert order.get("Second").description == "The other one"
        assert order.get("Second").subcategories == ("Beta", "Alpha")

    def test_every_problem_is_reported_at_once(self, tmp_path):
        """A bad file should be fixable in one pass, as for fields_description."""
        path = tmp_path / "c.yaml"
        path.write_text(
            "categories:\n"
            "  - name: ''\n"
            "  - []\n"
            "  - name: Dup\n"
            "  - name: Dup\n"
            "  - name: A/B\n"
            "  - name: Ok\n"
            "    bogus: 1\n",
            encoding="utf-8",
        )
        with pytest.raises(schema.CategoriesYamlError) as excinfo:
            schema.load_categories_yaml(path)
        assert len(excinfo.value.problems) == 5

    def test_a_name_may_not_contain_a_slash(self, tmp_path):
        """Subcategory node ids are ``sub:<category>/<name>``."""
        path = tmp_path / "c.yaml"
        path.write_text("categories:\n  - name: A\n    subcategories: [x/y]\n")
        with pytest.raises(schema.CategoriesYamlError, match="may not contain"):
            schema.load_categories_yaml(path)

    def test_only_one_category_may_be_the_bucket(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text(
            "categories:\n"
            "  - name: A\n    uncategorized: true\n"
            "  - name: B\n    uncategorized: true\n",
        )
        with pytest.raises(schema.CategoriesYamlError, match="at most one"):
            schema.load_categories_yaml(path)

    def test_the_bucket_may_not_shadow_a_real_category(self, tmp_path):
        """Both would key the same tree node, so one would silently win."""
        path = tmp_path / "c.yaml"
        path.write_text("categories:\n  - name: Other\n    uncategorized: true\n")
        fields = _grouped(("a", "Ay", "Other", "", None))
        with pytest.raises(schema.CategoriesYamlError, match="another name"):
            schema.load_categories_yaml(path, fields)

    def test_a_top_level_list_is_rejected(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("- name: A\n")
        with pytest.raises(schema.CategoriesYamlError, match="must be a mapping"):
            schema.load_categories_yaml(path)


class TestCategoryOrder:
    def test_declared_categories_rank_by_position(self):
        order = _declare(("Zebra",), ("Aardvark",))
        assert order.rank("Zebra") < order.rank("Aardvark")

    def test_an_undeclared_category_sorts_after_every_declared_one(self):
        fields = _grouped(("a", "Ay", "Ghost", "", None))
        order = schema.CategoryOrder.of([schema.Category("Known")], fields)
        assert order.rank("Ghost") > order.rank("Known")
        assert order.undeclared == ("Ghost",)

    def test_two_undeclared_categories_do_not_share_a_rank(self):
        """Otherwise their fields interleave through the later key components."""
        fields = _grouped(
            ("z", "Zed", "Zebra", "", None),
            ("a", "Ay", "Aardvark", "", None),
        )
        order = schema.CategoryOrder.of([schema.Category("Known")], fields)
        assert order.rank("Aardvark") < order.rank("Zebra")

    def test_the_bucket_sits_where_it_is_declared(self):
        order = schema.CategoryOrder.of(
            [
                schema.Category("First"),
                schema.Category("Other", uncategorized=True),
                schema.Category("Last"),
            ],
        )
        assert order.rank("First") < order.rank("") < order.rank("Last")
        assert order.uncategorized_label == "Other"

    def test_without_a_bucket_uncategorised_sorts_last(self):
        order = _declare(("First",), ("Second",))
        assert order.rank("") > order.rank("Second")
        assert order.uncategorized_label == "Other"

    def test_a_declared_category_no_field_uses_is_reported(self):
        fields = _grouped(("a", "Ay", "Used", "", None))
        order = schema.CategoryOrder.of(
            [schema.Category("Used"), schema.Category("Idle")],
            fields,
        )
        assert order.unused == ("Idle",)

    def test_an_empty_subcategory_sorts_first(self):
        """So a category's headline columns lead it."""
        order = _declare(("A", "Sub"))
        assert order.sub_rank("A", "") < order.sub_rank("A", "Sub")

    def test_a_declared_subcategory_order_beats_alphabetical(self):
        order = _declare(("A", "Nomenclature", "Cross-references"))
        assert order.sub_rank("A", "Nomenclature") < order.sub_rank(
            "A",
            "Cross-references",
        )

    def test_undeclared_subcategories_follow_declared_ones_alphabetically(self):
        order = _declare(("A", "Listed"))
        assert order.sub_rank("A", "Listed") < order.sub_rank("A", "Zebra")
        assert order.sub_rank("A", "Aardvark") < order.sub_rank("A", "Zebra")


class TestOrderFieldsWithCategories:
    FIELDS: ClassVar[dict[str, FieldMeta]] = _grouped(
        ("late_first", "Aaa", "Late", "", None),
        ("early_last", "Zzz", "Early", "", None),
    )

    def test_category_beats_the_label(self):
        order = _declare(("Early",), ("Late",))
        assert schema.order_fields(self.FIELDS, order) == ["early_last", "late_first"]

    def test_category_beats_priority(self):
        fields = _grouped(
            ("late_urgent", "Aaa", "Late", "", 1),
            ("early_idle", "Zzz", "Early", "", 99),
        )
        order = _declare(("Early",), ("Late",))
        assert schema.order_fields(fields, order) == ["early_idle", "late_urgent"]

    def test_subcategory_beats_priority(self):
        fields = _grouped(
            ("b_urgent", "Aaa", "C", "Beta", 1),
            ("a_idle", "Zzz", "C", "Alpha", 99),
        )
        order = _declare(("C",))
        assert schema.order_fields(fields, order) == ["a_idle", "b_urgent"]

    def test_priority_beats_the_label_within_a_subcategory(self):
        fields = _grouped(
            ("second", "Aaa", "C", "S", 20),
            ("first", "Zzz", "C", "S", 10),
        )
        assert schema.order_fields(fields, _declare(("C",))) == ["first", "second"]

    def test_no_categories_reproduces_the_priority_order(self):
        """The pre-categories order, so an absent categories.yaml is inert.

        Deliberately *not* "undeclared categories sort alphabetically": that
        would reorder a whole table by category name whenever the file went
        missing, and the first visible column is styled positionally.
        """
        fields = _grouped(
            ("c", "Gamma", "Zebra", "", None),
            ("a", "Alpha", "Aardvark", "", None),
            ("b", "Beta", "", "", 5),
        )
        assert schema.order_fields(fields) == ["b", "a", "c"]
        assert schema.order_fields(fields, schema.EMPTY_CATEGORIES) == ["b", "a", "c"]


class TestFieldNamesCannotCollideWithTreeNodes:
    def test_a_colon_is_not_a_valid_field_name(self):
        """What makes the selector's ``cat:``/``sub:`` node ids safe."""
        assert not schema.is_valid_field_name("cat:Gene identity")
        assert not schema.is_valid_field_name("a:b")


class TestScaleConfig:
    """`badges.yaml` carries two shapes; these tell them apart and check one."""

    SCALE: ClassVar[dict] = {
        "scale": [{"below": 0.45, "color": "#c53030"}, {"color": "#2b6cb0"}],
    }

    def test_a_scale_is_recognised(self):
        assert schema.is_scale_config(self.SCALE)

    def test_a_badge_map_is_not_a_scale(self):
        assert not schema.is_scale_config({"AD": {"label": "AD", "color": "#000"}})

    def test_a_non_mapping_is_not_a_scale(self):
        assert not schema.is_scale_config(["scale"])
        assert not schema.is_scale_config(None)

    def test_a_scale_key_that_is_not_a_list_is_not_a_scale(self):
        """A column whose values happen to include the word is still a badge map."""
        assert not schema.is_scale_config({"scale": {"label": "S", "color": "#000"}})

    def test_a_well_formed_scale_has_no_problems(self):
        assert schema.scale_problems("loeuf", self.SCALE) == []

    def test_an_empty_scale_is_reported(self):
        problems = schema.scale_problems("loeuf", {"scale": []})
        assert any("empty" in p for p in problems)

    def test_a_rule_without_a_colour_is_reported(self):
        problems = schema.scale_problems("loeuf", {"scale": [{"below": 0.45}]})
        assert any("no colour" in p for p in problems)

    def test_an_unknown_rule_key_is_reported(self):
        problems = schema.scale_problems(
            "loeuf",
            {"scale": [{"below": 0.45, "color": "#000", "colour": "#fff"}]},
        )
        assert any("unknown key" in p and "colour" in p for p in problems)

    def test_a_non_numeric_bound_is_reported(self):
        problems = schema.scale_problems(
            "loeuf",
            {"scale": [{"below": "0.45", "color": "#000"}]},
        )
        assert any("must be a number" in p for p in problems)

    def test_a_rule_after_an_unbounded_one_can_never_apply(self):
        """The fallback catches everything, so anything behind it is dead."""
        problems = schema.scale_problems(
            "loeuf",
            {"scale": [{"color": "#2b6cb0"}, {"below": 0.45, "color": "#c53030"}]},
        )
        assert any("never apply" in p for p in problems)

    def test_the_last_rule_may_be_unbounded(self):
        assert schema.scale_problems("loeuf", {"scale": [{"color": "#2b6cb0"}]}) == []

    def test_every_problem_is_collected_not_just_the_first(self):
        problems = schema.scale_problems(
            "loeuf",
            {"scale": [{"below": "x"}, {"nope": 1, "color": "#000"}]},
        )
        assert len(problems) >= 3, problems


class TestBadgeKey:
    def test_the_column_name_wins(self):
        badges = {"inheritance_v7": {}, "inheritance": {}}
        assert schema.badge_key("inheritance_v7", ["inheritance"], badges) == (
            "inheritance_v7"
        )

    def test_an_alias_configured_in_the_file(self):
        assert schema.badge_key(
            "inheritance_v7", ["inheritance"], {"inheritance": {}}
        ) == ("inheritance")

    def test_an_alias_in_the_render_list_without_config(self):
        assert schema.badge_key("inheritance_v7", ["inheritance"], {}) == "inheritance"

    def test_an_unconfigured_column_keeps_its_name(self):
        assert schema.badge_key("note", ["memo"], {}) == "note"
