"""Tests for derived fields: boolean columns computed from other columns.

The point of recording a rule rather than just importing the roll-up is that
drift becomes detectable. A union that has quietly stopped matching its inputs
is otherwise invisible -- the column is an ordinary boolean, and nothing about
it says what it was supposed to be. So the drift check gets the most attention
here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from genetrek import schema
from genetrek.db import Database, DatabaseError, ValidationFailedError
from genetrek.schema import DerivationError, FieldMeta

if TYPE_CHECKING:
    from tests.conftest import SyntheticResources


def _overwrite(database: Database, name: str, values: pl.Series) -> None:
    """Replace a column's values, simulating an out-of-band upstream change.

    Reaches past the public API deliberately. ``Database`` offers no way to
    rewrite a column's values -- which is precisely why a derived column can
    drift without anyone noticing: the change arrives through a re-import or a
    notebook, not through this class. So the tests have to fake that route.
    """
    database._frame = database.frame.with_columns(values.alias(name))


def _open(resources: SyntheticResources) -> Database:
    return Database.open(resources.root)


def _meta(**kwargs: object) -> FieldMeta:
    return FieldMeta(fullname="X", **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------- metadata


class TestTheMetadataKeys:
    def test_sources_without_an_operation_default_to_union(self):
        meta = _meta(derived_from=["a", "b"])
        assert meta.derived_op == "union"
        assert meta.is_derived

    def test_an_operation_without_sources_is_refused(self):
        """A half-written rule must not look like a valid one."""
        with pytest.raises(ValueError, match="needs at least one derived_from"):
            _meta(derived_op="union")

    def test_a_plain_field_is_not_derived(self):
        meta = _meta()
        assert meta.derived_op is None
        assert meta.derived_from == []
        assert not meta.is_derived

    @pytest.mark.parametrize("bad", ["Not A Key", "", "_leading"])
    def test_a_source_must_be_a_valid_field_key(self, bad):
        with pytest.raises(ValueError, match="not a valid field key"):
            _meta(derived_from=[bad])

    def test_duplicate_sources_are_refused(self):
        with pytest.raises(ValueError, match="duplicate derived_from"):
            _meta(derived_from=["a", "a"])

    def test_the_keys_survive_a_yaml_round_trip(self, tmp_path):
        fields = {
            "a": _meta(value_type="boolean"),
            "b": _meta(value_type="boolean"),
            "ab": _meta(
                value_type="boolean",
                derived_from=["a", "b"],
                derived_op="intersection",
            ),
        }
        path = tmp_path / "fields.yaml"
        schema.write_fields_yaml(path, fields)
        reloaded = schema.load_fields_yaml(path)
        assert reloaded["ab"].derived_from == ["a", "b"]
        assert reloaded["ab"].derived_op == "intersection"
        assert reloaded["a"].derived_op is None
        # Rewriting what was just written must be a no-op.
        assert schema.dump_fields_yaml(reloaded) == path.read_text(encoding="utf-8")

    def test_the_sources_render_as_a_flow_list(self, tmp_path):
        """Reusing the aliases renderer, so the file stays readable."""
        path = tmp_path / "fields.yaml"
        schema.write_fields_yaml(path, {"ab": _meta(derived_from=["a", "b"])})
        assert "derived_from: [a, b]" in path.read_text(encoding="utf-8")


# ------------------------------------------------------------- evaluation order


class TestDerivedOrder:
    def test_a_rollup_of_a_rollup_comes_after_its_input(self):
        fields = {
            "a": _meta(),
            "b": _meta(),
            "c": _meta(),
            "abc": _meta(derived_from=["ab", "c"]),
            "ab": _meta(derived_from=["a", "b"]),
        }
        order = schema.derived_order(fields)
        assert order.index("ab") < order.index("abc")

    def test_plain_sources_are_not_listed(self):
        fields = {"a": _meta(), "b": _meta(), "ab": _meta(derived_from=["a", "b"])}
        assert schema.derived_order(fields) == ["ab"]

    def test_no_derived_fields_gives_an_empty_order(self):
        assert schema.derived_order({"a": _meta()}) == []

    def test_a_cycle_is_refused_and_named(self):
        fields = {"x": _meta(derived_from=["y"]), "y": _meta(derived_from=["x"])}
        with pytest.raises(DerivationError, match=r"cycle: (x -> y -> x|y -> x -> y)"):
            schema.derived_order(fields)

    def test_a_self_reference_is_a_cycle(self):
        with pytest.raises(DerivationError, match="cycle: x -> x"):
            schema.derived_order({"x": _meta(derived_from=["x"])})


# ------------------------------------------------------------ add_derived_field


class TestAddDerivedField:
    def test_a_union_is_the_or_of_its_sources(self, resources):
        database = _open(resources)
        field = database.add_derived_field("either", ["flag_a", "flag_b"])
        expected = database.frame["flag_a"] | database.frame["flag_b"]
        assert field.values.equals(expected.rename("either"))
        assert field.meta.value_type == "boolean"
        assert field.meta.derived_op == "union"
        assert field.meta.derived_from == ["flag_a", "flag_b"]

    def test_an_intersection_is_the_and_of_its_sources(self, resources):
        database = _open(resources)
        field = database.add_derived_field(
            "both",
            ["flag_a", "flag_b"],
            op="intersection",
        )
        expected = database.frame["flag_a"] & database.frame["flag_b"]
        assert field.values.equals(expected.rename("both"))

    def test_one_source_is_a_copy(self, resources):
        database = _open(resources)
        field = database.add_derived_field("copy_of_a", ["flag_a"])
        assert field.values.to_list() == resources.frame["flag_a"].to_list()

    def test_the_rule_is_recorded_in_the_history(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.save(backup=False)
        record = database.history()[-1]
        assert record["op"] == "add_derived_field"
        assert record["derived_from"] == ["flag_a", "flag_b"]

    def test_metadata_overrides_are_honoured(self, resources):
        database = _open(resources)
        field = database.add_derived_field(
            "either",
            ["flag_a", "flag_b"],
            meta={"fullname": "Either flag", "visibility": "primary"},
        )
        assert field.meta.fullname == "Either flag"
        assert field.meta.visibility == "primary"

    def test_a_missing_source_is_refused(self, resources):
        database = _open(resources)
        with pytest.raises(DerivationError, match="'nope' is not a column"):
            database.add_derived_field("either", ["flag_a", "nope"])
        assert "either" not in database.fields

    def test_a_non_boolean_source_is_refused(self, resources):
        database = _open(resources)
        with pytest.raises(DerivationError, match="'score' is Float32, not boolean"):
            database.add_derived_field("either", ["flag_a", "score"])

    @pytest.mark.parametrize(
        ("op", "a", "b", "expected"),
        [
            # Kleene: a null ("not measured") input decides nothing on its own.
            ("union", True, None, True),
            ("union", False, None, None),
            ("union", None, None, None),
            ("union", False, False, False),
            ("intersection", False, None, False),
            ("intersection", True, None, None),
            ("intersection", None, None, None),
            ("intersection", True, True, True),
        ],
    )
    def test_a_nullable_source_follows_kleene_logic(
        self, resources, op, a, b, expected
    ):
        """Not measured must never be quietly turned into "no"."""
        database = _open(resources)
        _overwrite(database, "flag_a", database.frame["flag_a"].scatter(0, a))
        _overwrite(database, "flag_b", database.frame["flag_b"].scatter(0, b))
        field = database.add_derived_field("rolled", ["flag_a", "flag_b"], op=op)
        assert field.values[0] is expected

    def test_external_is_not_an_evaluable_operation(self, resources):
        database = _open(resources)
        with pytest.raises(DerivationError, match="not an evaluable operation"):
            database.add_derived_field("either", ["flag_a"], op="external")

    def test_every_problem_is_reported_at_once(self, resources):
        database = _open(resources)
        with pytest.raises(DerivationError) as excinfo:
            database.add_derived_field("either", ["nope", "score"])
        message = str(excinfo.value)
        assert "'nope' is not a column" in message
        assert "not boolean" in message

    def test_no_sources_is_refused(self, resources):
        database = _open(resources)
        with pytest.raises(DerivationError, match="at least one source"):
            database.add_derived_field("either", [])

    def test_a_field_cannot_derive_from_itself(self, resources):
        database = _open(resources)
        with pytest.raises(DerivationError, match="is the field itself"):
            database.add_derived_field("flag_c", ["flag_c", "flag_a"])

    def test_an_existing_name_is_refused(self, resources):
        database = _open(resources)
        with pytest.raises(DatabaseError, match="already exists"):
            database.add_derived_field("flag_a", ["flag_b"])

    def test_an_alias_cannot_be_shadowed(self, resources):
        database = _open(resources)
        database.set_alias("shorthand", "flag_a")
        with pytest.raises(DatabaseError, match="already an alias"):
            database.add_derived_field("shorthand", ["flag_a", "flag_b"])

    def test_a_rollup_of_a_rollup_works(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        field = database.add_derived_field("either_or_more", ["either", "flag_b"])
        assert field.values.equals(database.frame["either"].rename("either_or_more"))


# ------------------------------------------------------------ the drift check


class TestValidateDetectsDrift:
    def test_a_clean_derivation_validates(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        assert database.validate().ok

    def test_changing_a_source_is_an_error(self, resources):
        """The whole reason for recording the rule."""
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        _overwrite(database, "flag_a", ~database.frame["flag_a"])

        report = database.validate()
        assert not report.ok
        assert any(
            "either: stored values disagree" in error and "row(s)" in error
            for error in report.errors
        ), report.errors

    def test_the_error_names_the_rule_and_the_remedy(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        _overwrite(database, "flag_a", ~database.frame["flag_a"])
        error = next(e for e in database.validate().errors if e.startswith("either:"))
        assert "union of ['flag_a', 'flag_b']" in error
        assert "recompute_derived" in error

    def test_a_nullable_derivation_validates(self, resources):
        """A row null on both sides agrees; it is not drift."""
        database = _open(resources)
        _overwrite(database, "flag_a", database.frame["flag_a"].scatter(0, None))
        _overwrite(database, "flag_b", database.frame["flag_b"].scatter(0, None))
        database.add_derived_field("either", ["flag_a", "flag_b"])
        assert database.frame["either"][0] is None
        assert database.validate().ok

    def test_drift_counts_null_versus_value(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        _overwrite(database, "either", database.frame["either"].scatter(0, None))
        error = next(e for e in database.validate().errors if e.startswith("either:"))
        assert "on 1 row(s)" in error

    def test_a_source_removed_from_under_a_rule_is_an_error(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.remove_field("flag_b", force=True)
        report = database.validate()
        assert not report.ok
        assert any("flag_b" in error for error in report.errors), report.errors

    def test_a_cycle_in_stored_metadata_is_an_error(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        # Only reachable by hand-editing the YAML, which is exactly why the
        # check exists rather than relying on add_derived_field's guard.
        database.fields["either"].derived_from = ["either"]
        assert any("cycle" in error for error in database.validate().errors)

    def test_a_save_refuses_a_drifted_derivation(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.save(backup=False)

        reopened = _open(resources)
        _overwrite(reopened, "flag_a", ~reopened.frame["flag_a"])
        with pytest.raises(ValidationFailedError) as excinfo:
            reopened.save(backup=False)
        assert any(
            "stored values disagree" in error for error in excinfo.value.report.errors
        )


# ------------------------------------------------------------ recompute_derived


class TestRecomputeDerived:
    def test_nothing_to_do_returns_empty(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        assert database.recompute_derived() == []

    def test_a_changed_source_is_refreshed(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        _overwrite(database, "flag_a", ~database.frame["flag_a"])
        assert database.recompute_derived() == ["either"]
        assert database.validate().ok

    def test_only_the_changed_fields_are_returned(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.add_derived_field("both", ["flag_a", "flag_b"], op="intersection")
        # flag_b is all-False except the first three rows; making it all True
        # changes the intersection and leaves the union alone.
        _overwrite(
            database, "flag_b", pl.repeat(value=True, n=database.n_rows, eager=True)
        )
        changed = database.recompute_derived()
        assert "both" in changed

    def test_it_follows_the_dependency_order(self, resources):
        """A roll-up of a roll-up must see its input already refreshed."""
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.add_derived_field("nested", ["either"])
        _overwrite(
            database, "flag_a", pl.repeat(value=True, n=database.n_rows, eager=True)
        )
        changed = database.recompute_derived()
        assert changed.index("either") < changed.index("nested")
        assert database.frame["nested"].all()
        assert database.validate().ok

    def test_a_subset_can_be_named(self, resources):
        """Naming a field limits the work to it, leaving the others stale."""
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.add_derived_field("both", ["flag_a", "flag_b"], op="intersection")
        _overwrite(
            database, "flag_b", pl.repeat(value=True, n=database.n_rows, eager=True)
        )

        assert database.recompute_derived(names=["either"]) == ["either"]
        assert database.recompute_derived(names=["either"]) == []
        # `both` was deliberately not refreshed, so the drift check still fires.
        assert any("both:" in e for e in database.validate().errors)

        assert database.recompute_derived(names=["both"]) == ["both"]
        assert database.validate().ok

    def test_a_recompute_is_logged(self, resources):
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.save(backup=False)
        _overwrite(database, "flag_a", ~database.frame["flag_a"])
        database.recompute_derived()
        database.save(backup=False, validate=False)
        assert any(r["op"] == "recompute_derived" for r in database.history())

    def test_save_does_not_recompute_on_its_own(self, resources):
        """Silently rewriting a column during a save is the failure mode avoided."""
        database = _open(resources)
        database.add_derived_field("either", ["flag_a", "flag_b"])
        database.save(backup=False)

        reopened = _open(resources)
        _overwrite(reopened, "flag_a", ~reopened.frame["flag_a"])
        # validate=False so the refusal under test is not the drift check.
        reopened.save(backup=False, validate=False)
        again = _open(resources)
        assert not again.validate().ok, "the drift was silently repaired"


# ------------------------------------------------------------- external op


class TestExternalDerivation:
    """Provenance only: a column computed outside GeneTrek from its parents."""

    def _stage(self, database: Database) -> None:
        database.set_meta(
            "flag_b",
            derived_op="external",
            derived_from=["score"],
        )

    def test_it_is_derived_but_not_a_rule(self):
        meta = _meta(derived_op="external", derived_from=["score"])
        assert meta.is_derived
        assert not meta.is_rule

    def test_it_is_never_evaluated_or_drift_checked(self, resources):
        database = _open(resources)
        self._stage(database)
        assert database.validate().ok  # a Float32 parent would fail evaluation
        assert database.recompute_derived() == []

    def test_a_missing_parent_is_an_error(self, resources):
        database = _open(resources)
        self._stage(database)
        database.remove_field("score", force=True)
        errors = database.validate().errors
        assert any(e.startswith("flag_b: computed from ['score']") for e in errors), (
            errors
        )

    def test_it_is_left_out_of_the_evaluation_order(self):
        fields = {
            "score": _meta(),
            "flag": _meta(derived_op="external", derived_from=["score"]),
        }
        assert schema.derived_order(fields) == []
