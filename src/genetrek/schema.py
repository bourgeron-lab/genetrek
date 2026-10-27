"""The field-metadata contract shared by the webapp and the maintenance tooling.

``resources/config/fields_description.yaml`` describes every column of
``resources/processed_data/genetrek.parquet``. This module is the single
definition of that pairing: the metadata model, the mapping between declared
value types and Polars dtypes, and a style-preserving YAML reader/writer so
routine edits produce small diffs.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from typing import TYPE_CHECKING, Any, Literal, Self, get_args

import polars as pl
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

VISIBILITY = Literal["primary", "secondary", "hidden"]
VALUE_TYPE = Literal["boolean", "integer", "float", "string", "categorical"]
#: What a column *is*, so code can find it without knowing its name.
#:
#: The identifier types name the identifier a column carries. The ``locus_*``
#: types, together with ``FieldMeta.assembly``, describe one coordinate column
#: each. Which column is the *resolution target* is a separate, orthogonal fact:
#: see ``FieldMeta.primary_key`` -- ``ncbi_id`` is both "the NCBI ID" and "the
#: key", and one enum cannot say both.
#:
#: ``locus`` is the unspecific predecessor of the ``locus_*`` types. It is still
#: accepted so an unmigrated file loads, but it resolves to nothing -- see
#: :func:`resolve_roles`.
ID_TYPE = Literal[
    "symbol",
    "ensg",
    "ncbi",
    "hgnc",
    "omim",
    "locus_chr",
    "locus_start",
    "locus_end",
    "locus_strand",
    "locus",
]

#: Identifier roles a database may fill, in the order reports list them.
IDENTIFIER_ROLES: tuple[str, ...] = ("symbol", "ensg", "ncbi", "hgnc", "omim")

#: Coordinate roles, each needing an ``assembly`` alongside it.
LOCUS_ROLES: tuple[str, ...] = ("locus_chr", "locus_start", "locus_end", "locus_strand")

#: Deprecated, unspecific coordinate marker.
LEGACY_LOCUS = "locus"

#: How a derived column is computed from its sources.
#:
#: ``union`` and ``intersection`` are boolean set algebra, evaluated with
#: Kleene three-valued logic so a null ("not measured") input stays honest: a
#: union is True if any input is True, False only when every input is measured
#: False, and null otherwise; an intersection is False if any input is False.
#: Filling nulls with False first would quietly turn "not measured" into "no".
#: A roll-up whose nulls do not follow from its inputs cannot be expressed --
#: ``Constraint_combined_woPLI_20260612`` matches the union of two constraint
#: flags on TRUE membership but has *fewer* non-null rows than either input, so
#: it is data, not a rule. Numeric roll-ups are out of scope entirely: this is
#: not an expression language.
#:
#: ``external`` records provenance only: the column was computed outside
#: GeneTrek from ``derived_from`` (a threshold on a numeric parent, say, whose
#: rule involves values the database does not store), so it is never evaluated
#: or drift-checked -- only its parents' existence is.
DERIVED_OP = Literal["union", "intersection", "external"]

VISIBILITIES: tuple[VISIBILITY, ...] = get_args(VISIBILITY)
VALUE_TYPES: tuple[VALUE_TYPE, ...] = get_args(VALUE_TYPE)
ID_TYPES: tuple[ID_TYPE, ...] = get_args(ID_TYPE)
DERIVED_OPS: tuple[DERIVED_OP, ...] = get_args(DERIVED_OP)
#: The operations :class:`~genetrek.db.Database` can evaluate.
EVALUATED_OPS: tuple[DERIVED_OP, ...] = ("union", "intersection")

#: Keys emitted for every field, in order. The original six come first so that
#: diffs against the pre-existing file stay confined to the appended keys.
FIELD_KEY_ORDER: tuple[str, ...] = (
    "fullname",
    "description",
    "update",
    "visibility",
    "priority",
    "category",
    "subcategory",
    "source",
    "doi",
    "licence",
    "universe",
    "value_type",
    "id_type",
    "assembly",
    "primary_key",
    "added",
    "source_file",
    "derived_op",
    "derived_from",
    "aliases",
)

#: Keys holding a list of strings rather than a scalar.
_LIST_KEYS = frozenset({"aliases", "derived_from"})

#: Columns the gene table renders as badges rather than raw values.
#:
#: The client-side renderer keys off this list, so adding an entry to
#: ``badges.yaml`` has no effect until the column appears here too. Defined in
#: this module rather than in ``ui/`` so the maintenance tooling can check the
#: coupling without importing NiceGUI.
#:
#: A name here may be an **alias** rather than a column (``inheritance`` is the
#: pointer to the current ``inheritance_v*``), and so may a ``badges.yaml`` key:
#: :func:`badge_key` resolves a column to whichever of its names is configured,
#: so moving the alias to a new version moves the badge with it.
BADGE_RENDERED_COLUMNS: tuple[str, ...] = ("inheritance", "eagle_score")

#: Marker key for a threshold colour scale in ``badges.yaml``. An entry under a
#: column name is a scale when it carries this key and a badge map otherwise, so
#: one file covers both ways of colouring a cell by its value: an exact match
#: for a categorical column, a range for a numeric one.
#:
#: Unlike badges, a scale needs no allow-list in code. The badge renderer has a
#: hardcoded cell type per column, which is why :data:`BADGE_RENDERED_COLUMNS`
#: exists; a scale is driven entirely from its own config, so configuring one is
#: enough to make it render.
SCALE_KEY = "scale"

#: Keys a single scale rule may carry. ``below``/``above`` are the bounds, and a
#: rule with neither is the fallback every remaining value falls into.
_SCALE_RULE_KEYS = frozenset({"below", "above", "color"})


def badge_key(
    column: str,
    aliases: Iterable[str],
    badges: Mapping[str, object],
) -> str:
    """The name a column's badge or scale is configured under.

    The column's own name wins, then the first of its aliases that
    ``badges.yaml`` configures or :data:`BADGE_RENDERED_COLUMNS` lists. A column
    with neither keeps its own name, which then matches nothing.
    """
    names = (column, *aliases)
    for name in names:
        if name in badges:
            return name
    return next((n for n in names if n in BADGE_RENDERED_COLUMNS), column)


def is_scale_config(entry: object) -> bool:
    """Whether a ``badges.yaml`` entry is a threshold scale, not a badge map."""
    return isinstance(entry, Mapping) and isinstance(entry.get(SCALE_KEY), list)


def scale_problems(column: str, entry: Mapping[str, Any]) -> list[str]:
    """Everything wrong with one column's scale config, or an empty list.

    Collected rather than raised one at a time: this runs inside
    ``Database.validate``, which reports every problem it can find in one pass.
    """
    problems: list[str] = []
    rules = entry[SCALE_KEY]
    if not rules:
        problems.append(f"{column}: {SCALE_KEY!r} is empty, so nothing is coloured")
    for index, rule in enumerate(rules):
        where = f"{column}.{SCALE_KEY}[{index}]"
        if not isinstance(rule, Mapping):
            problems.append(f"{where}: expected a mapping, got {type(rule).__name__}")
            continue
        if unknown := sorted(set(rule) - _SCALE_RULE_KEYS):
            problems.append(f"{where}: unknown key(s) {unknown}")
        if not str(rule.get("color") or "").strip():
            problems.append(f"{where}: no colour")
        problems += [
            f"{where}: {bound!r} must be a number"
            for bound in ("below", "above")
            if bound in rule and not isinstance(rule[bound], (int, float))
        ]
        # A rule with no bound catches everything, so anything after it is dead.
        unbounded = "below" not in rule and "above" not in rule
        if unbounded and index != len(rules) - 1:
            problems.append(
                f"{where}: has no bound, so it matches every value and the "
                f"{len(rules) - index - 1} rule(s) after it can never apply",
            )
    return problems


#: String columns conventionally use this literal as a missing-value marker
#: instead of a true null, which is worth surfacing but is not an error.
MISSING_MARKER = "None"

#: Columns whose *names* appear in code, mapped to where.
#:
#: Renaming or removing one of these silently changes behaviour: a plot degrades
#: to a "Missing columns" label, or a badge renders as raw text. Nothing else
#: would catch it, so ``rename_field`` refuses these by default and ``validate``
#: notes when one disappears.
#:
#: The identifier and coordinate columns are deliberately **absent**: they are
#: found through :class:`Roles`, so renaming one carries its ``id_type`` along
#: and search, enrichment and resolution follow automatically. What remains here
#: is the genuinely name-bound residue -- columns a UI component asks for by
#: literal name because they are specific measurements, not roles.
#:
#: Keep this in step with the code. It is the only inventory of the coupling.
CODE_DEPENDENT_COLUMNS: dict[str, str] = {
    # An alias, not a column: it points at the current inheritance_v*.
    "inheritance": "schema.BADGE_RENDERED_COLUMNS, ui/js/gene_table.js",
    "eagle_score": "schema.BADGE_RENDERED_COLUMNS, ui/js/gene_table.js",
    "shet_mean": "ui/components/stats_drawer.py constraint plot (x axis)",
    "loeuf": "ui/components/stats_drawer.py constraint plot",
    "moeuf": "ui/components/stats_drawer.py constraint plot",
    # Optional: the plot colours its points by this when present, and draws in
    # one colour when it is not.
    "is_shet_constrained": "ui/components/stats_drawer.py constraint plot (colour)",
    # The x axis prefers shet_mean and falls back to this, so a database built
    # before s-het arrived still plots. Removable once nothing carries pli.
    "pli": "ui/components/stats_drawer.py constraint plot (x axis fallback)",
    "37365347_number_of_carriers_among_autistic_individuals": (
        "ui/components/stats_drawer.py autism odds-ratio plot"
    ),
    "37365347_autism_or": "ui/components/stats_drawer.py autism odds-ratio plot",
    "37365347_p_value": "ui/components/stats_drawer.py autism odds-ratio plot",
    # Optional: the only locus column with no role, because it is one column for
    # both assemblies -- a gene does not change strand between them -- so
    # LocusColumns.strand is never filled and Roles cannot find it.
    "strand": "ui/components/gene_info_dialog.py coordinates block",
    # Optional: HGNC's curated band, which its own description calls "not
    # derived from the coordinates in this database", so it is no coordinate
    # role either.
    "hgnc_location": "ui/components/gene_info_dialog.py identifiers block",
}

#: Code-dependent columns a database can do without.
#:
#: The code reads these by name but degrades correctly and visibly without them:
#: ``is_shet_constrained`` is a marker colour the constraint plot omits, and
#: ``pli`` is the x-axis fallback for databases predating ``shet_mean``. So
#: ``validate`` does not report them as making a feature "unavailable" -- with
#: the required columns present, nothing is. Renaming or removing one is still
#: guarded, because with the newer column *also* absent the feature does break.
OPTIONAL_CODE_COLUMNS: frozenset[str] = frozenset(
    {"pli", "is_shet_constrained", "strand", "hgnc_location"},
)

#: Code-dependent columns that stand in for one another, newest first.
#:
#: A required column with a present stand-in is not reported absent, because the
#: feature works. Without this the note would tell a database still carrying
#: ``pli`` that its constraint plot is unavailable while the plot draws perfectly
#: well -- and a note that cries wolf is a note nobody reads.
CODE_COLUMN_FALLBACKS: dict[str, tuple[str, ...]] = {"shet_mean": ("pli",)}

#: A valid field key: lower-case, digits and underscores, not starting with an
#: underscore. Leading digits are allowed -- several real fields are PMID-prefixed.
FIELD_NAME_RE = re.compile(r"^[0-9a-z][0-9a-z_]*$")


def is_valid_field_name(name: str) -> bool:
    """Whether a string is usable as a field key."""
    return bool(FIELD_NAME_RE.match(name))


#: Keys always emitted double-quoted, matching the existing file's style.
_ALWAYS_QUOTED = frozenset(
    {
        "description",
        "update",
        "category",
        "subcategory",
        "source",
        "doi",
        "universe",
        "added",
        "source_file",
    },
)

#: What the bucket for fields with no ``category`` is called when
#: ``categories.yaml`` declares no ``uncategorized`` entry of its own.
_DEFAULT_UNCATEGORIZED = "Other"

#: Separator in a subcategory's tree node id, hence forbidden in a name.
_NAME_SEPARATOR = "/"

_DATE_STAMP_RE = re.compile(r"^\d{8}$")

# Below this code point a character is a C0 control and cannot appear in a
# double-quoted YAML scalar without escaping.
_MIN_PRINTABLE = 0x20


class CategoriesYamlError(ValueError):
    """``config/categories.yaml`` is malformed.

    Collects every problem, as :class:`FieldsYamlError` does and for the same
    reason: a bad file should be fixable in one pass.
    """

    def __init__(self, path: Path | None, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        location = f" in {path}" if path is not None else ""
        detail = "\n".join(f"  - {p}" for p in problems)
        super().__init__(
            f"{len(problems)} problem{'' if len(problems) == 1 else 's'}"
            f"{location}:\n{detail}",
        )


class FieldsYamlError(ValueError):
    """One or more field entries failed validation.

    Carries every problem found rather than only the first, so a bad file can be
    fixed in a single pass.
    """

    def __init__(self, path: Path | None, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        location = f" in {path}" if path is not None else ""
        detail = "\n".join(f"  - {p}" for p in problems)
        super().__init__(
            f"{len(problems)} invalid field entr"
            f"{'y' if len(problems) == 1 else 'ies'}{location}:\n{detail}",
        )


def today_stamp() -> str:
    """Return today's local date as a ``YYYYMMDD`` stamp."""
    return dt.datetime.now(tz=dt.UTC).astimezone().strftime("%Y%m%d")


class FieldMeta(BaseModel):
    """Metadata for a single database column.

    The first six attributes are the historical schema. ``value_type`` and
    ``id_type`` make the dtype contract explicit so drift between the YAML and
    the parquet becomes detectable; ``added`` and ``source_file`` record
    provenance for fields introduced through the tooling.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fullname: str
    description: str = ""
    update: str = ""
    visibility: VISIBILITY = "hidden"
    #: Display order among non-hidden columns, smaller first; unset sorts
    #: last. A *value*, deliberately not a position in this file --
    #: ``Database.save()`` rewrites the YAML in parquet column order on
    #: every save (``_normalise_order``), so reordering entries cannot
    #: express display order. Unconstrained on purpose: a negative value
    #: is the escape hatch for "before everything already numbered".
    priority: int | None = None
    #: Top-level display group. Empty means uncategorised, which is a group of
    #: its own rather than an absence -- see :class:`CategoryOrder`. The
    #: vocabulary lives in ``config/categories.yaml``, which also fixes the
    #: order the groups appear in.
    category: str = ""
    #: Second-level group within ``category``. Subcategories are alphabetical
    #: unless their category declares an explicit order. Empty sorts *first*,
    #: so a category's headline columns sit above its subgroups.
    #:
    #: Deliberately not validated against ``category`` here: a raising
    #: validator would make a file carrying the slip unloadable, and
    #: ``load_config`` exits on a bad file, so the webapp would refuse to start
    #: over cosmetic metadata. ``Database.validate`` warns instead.
    subcategory: str = ""
    source: str = ""
    doi: str = ""
    #: The terms the column's values are redistributed under, as an SPDX id
    #: where one exists (``CC0-1.0``, ``CC-BY-4.0``) and a short name where it
    #: does not ("GTEx Portal Data License"). Per column because the sources'
    #: terms differ: no single licence covers the database. ``DATA_LICENCES.md``
    #: is generated from it (``genetrek-db licences``).
    licence: str = ""
    #: Which genes were measured, in words -- the coverage definition of a
    #: column computed from a measurement ("proteins quantified in brain,
    #: Jiang 2020"). Its rows are null ("not measured") outside that universe
    #: and False/0 only where measured. The count is never stored: it is the
    #: column's non-null rows, so it cannot drift from the data.
    universe: str = ""
    value_type: VALUE_TYPE | None = None
    id_type: ID_TYPE | None = None
    assembly: str = ""
    primary_key: bool = False
    added: str = ""
    source_file: str = ""
    #: How this column is computed, when it is computed rather than imported.
    #: ``None`` for an ordinary column. See :data:`DERIVED_OP`.
    derived_op: DERIVED_OP | None = None
    #: Fields this column is derived from. Non-empty exactly when
    #: ``derived_op`` is set; the pair is validated together below.
    derived_from: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)

    @field_validator("derived_from")
    @classmethod
    def _check_derived_from(cls, value: list[str]) -> list[str]:
        """Require valid, non-duplicated, non-empty source field keys."""
        for name in value:
            if not is_valid_field_name(name):
                msg = f"derived_from entry {name!r} is not a valid field key"
                raise ValueError(msg)
        if len(set(value)) != len(value):
            msg = f"duplicate derived_from entries: {value}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _check_derivation(self) -> Self:
        """Keep ``derived_op`` and ``derived_from`` consistent with each other.

        Setting sources without an operation is the common case and defaults to
        a union; an operation with no sources is meaningless and is refused, so
        a half-written rule cannot look like a valid one.
        """
        if self.derived_from and self.derived_op is None:
            # Assignment would re-enter validation; set the attribute directly.
            object.__setattr__(self, "derived_op", "union")
        elif not self.derived_from and self.derived_op is not None:
            msg = (
                f"derived_op {self.derived_op!r} needs at least one derived_from entry"
            )
            raise ValueError(msg)
        return self

    @property
    def is_derived(self) -> bool:
        """Whether this column is computed from other columns."""
        return bool(self.derived_from)

    @property
    def is_rule(self) -> bool:
        """Whether this column is a rule GeneTrek evaluates and drift-checks.

        False for an ``external`` derivation, which only records its parents.
        """
        return self.is_derived and self.derived_op in EVALUATED_OPS

    @field_validator("aliases")
    @classmethod
    def _check_aliases(cls, value: list[str]) -> list[str]:
        """Require valid, non-duplicated field-key-shaped aliases."""
        for alias in value:
            if not is_valid_field_name(alias):
                msg = (
                    f"alias {alias!r} is not a valid field key: use lower-case "
                    f"letters, digits and underscores"
                )
                raise ValueError(msg)
        if len(set(value)) != len(value):
            msg = f"duplicate aliases: {value}"
            raise ValueError(msg)
        return value

    @field_validator("update", "added")
    @classmethod
    def _check_date_stamp(cls, value: str) -> str:
        """Require an empty string or a real ``YYYYMMDD`` calendar date."""
        if value == "":
            return value
        if not _DATE_STAMP_RE.match(value):
            msg = f"expected an empty string or YYYYMMDD, got {value!r}"
            raise ValueError(msg)
        try:
            dt.date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError as exc:
            msg = f"{value!r} is not a valid calendar date"
            raise ValueError(msg) from exc
        return value

    def touch(self) -> None:
        """Set ``update`` to today's date."""
        self.update = today_stamp()

    def apply(self, changes: Mapping[str, Any]) -> None:
        """Update several keys at once, validating the result.

        Args:
            changes: Attribute names mapped to new values.

        Raises:
            ValueError: If a key is unknown or a value fails validation. The
                instance is left unmodified in that case.
        """
        unknown = set(changes) - set(type(self).model_fields)
        if unknown:
            msg = (
                f"unknown metadata key(s): {sorted(unknown)}; "
                f"allowed: {sorted(type(self).model_fields)}"
            )
            raise ValueError(msg)
        # Validate against a copy first so a rejected batch changes nothing.
        candidate = self.model_copy(update=dict(changes))
        validated = FieldMeta.model_validate(candidate.model_dump())
        # Adopted in one step, rather than assigned key by key. With
        # ``validate_assignment`` the model-level validators run on *every*
        # assignment, so a batch applied one key at a time is visible to them
        # half-done -- and a half-done batch can be invalid even when the whole
        # is fine. Assigning ``derived_from`` and ``derived_op`` together shows
        # both failure modes, depending on which lands first: clearing a rule
        # (``derived_from=[], derived_op=None``) gets refused outright, and
        # setting one gets refused after the first key has already stuck,
        # leaving behind exactly the invalid state this method promises not to
        # produce. Taking the validated candidate's fields wholesale means no
        # intermediate state exists to be validated. Ordinary single-attribute
        # assignment still goes through ``validate_assignment`` as before.
        self.__dict__.update(validated.__dict__)
        self.__pydantic_fields_set__.update(changes)

    def to_yaml(self, name: str) -> str:
        """Render this field as the YAML fragment used in the config file."""
        return _dump_entry(name, self)


# --------------------------------------------------------------------------- #
# Display order: a declared position rather than an inherited one
# --------------------------------------------------------------------------- #

#: Sort position of a field that declares no ``priority``: after every field
#: that does. A sentinel rather than ``None`` so a sort key stays a comparable
#: ``tuple[int, str]`` -- a ``None`` mid-key is a TypeError waiting for the
#: first tie, and mypy rejects it as a sort key at all.
_UNSET_PRIORITY: int = sys.maxsize


#: Sort position of a subcategory that is not declared in its category's
#: explicit order: after every declared one, then alphabetically. Paired with
#: the name in the key, because a bare integer cannot separate two undeclared
#: subcategories -- see :meth:`CategoryOrder.sub_rank`.
_UNDECLARED_SUB: int = sys.maxsize


@dataclass(frozen=True)
class Category:
    """One declared category, as read from ``config/categories.yaml``."""

    name: str
    description: str = ""
    #: Explicit subcategory order. Empty means alphabetical.
    subcategories: tuple[str, ...] = ()
    #: Whether this entry is where fields with no ``category`` sort, and the
    #: label they are shown under. At most one category may claim it.
    uncategorized: bool = False


@dataclass(frozen=True)
class CategoryOrder:
    """The order the column categories appear in, and where each one sits.

    Every rank is assigned at construction rather than derived per call, for
    the reason ``column_order`` in ``config/loader.py`` is a precomputed map
    and not a live comparator: a rank has to be a single comparable integer,
    and a function of the category name alone cannot give two *undeclared*
    categories different ranks. So :meth:`of` takes the fields as well as the
    declaration and ranks what it actually finds.

    ``EMPTY_CATEGORIES`` ranks every category equal, which makes
    :func:`field_sort_key` degenerate to ``(priority, fullname)`` -- the order
    this module had before categories existed. That is deliberately *not* the
    same as ranking undeclared categories alphabetically: a tree whose fields
    carry categories but whose ``categories.yaml`` is missing would otherwise
    reorder the table by category name, and the first visible column is
    load-bearing (``ui/styles.py`` styles ``th:first-child`` positionally).
    """

    categories: tuple[Category, ...] = ()
    #: Category name -> rank. Includes undeclared names seen on fields.
    _ranks: dict[str, int] = dataclasses_field(default_factory=dict)
    #: Rank of the uncategorised bucket, i.e. of ``category: ""``.
    _uncategorized_rank: int = 0
    #: Names seen on a field but absent from the declaration.
    undeclared: tuple[str, ...] = ()
    #: Declared names that no field uses.
    unused: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        categories: Iterable[Category],
        fields: Mapping[str, FieldMeta] | None = None,
    ) -> Self:
        """Rank a declaration against the categories the fields actually use."""
        declared = tuple(categories)
        if not declared:
            return cls()

        ranks = {entry.name: rank for rank, entry in enumerate(declared)}
        bucket = next(
            (rank for rank, entry in enumerate(declared) if entry.uncategorized),
            len(declared),
        )

        used = {meta.category for meta in (fields or {}).values() if meta.category}
        # Ranked here rather than in the sort key: two undeclared categories
        # need two different integers, so they have to be numbered against
        # each other, which only this vantage point can do.
        extra = sorted(used - set(ranks), key=str.casefold)
        for offset, name in enumerate(extra):
            ranks[name] = len(declared) + offset

        return cls(
            categories=declared,
            _ranks=ranks,
            _uncategorized_rank=bucket,
            undeclared=tuple(extra),
            unused=tuple(
                entry.name
                for entry in declared
                if not entry.uncategorized and entry.name not in used
            ),
        )

    def __bool__(self) -> bool:
        """Whether anything is declared. Falsy is the pre-categories order."""
        return bool(self.categories)

    def get(self, name: str) -> Category | None:
        """Return the declared category of that name, if there is one."""
        return next((c for c in self.categories if c.name == name), None)

    @property
    def uncategorized_label(self) -> str:
        """What to call the bucket for fields with no category."""
        entry = next((c for c in self.categories if c.uncategorized), None)
        return entry.name if entry is not None else _DEFAULT_UNCATEGORIZED

    def rank(self, category: str) -> int:
        """Return a category's sort position. Unknown names sort last."""
        if not category:
            return self._uncategorized_rank
        return self._ranks.get(category, len(self._ranks) + len(self.categories))

    def sub_rank(self, category: str, subcategory: str) -> tuple[int, str]:
        """Return a subcategory's sort position within its category.

        Empty sorts first, so a category's headline columns lead it. A
        subcategory named in the category's explicit order sorts by that
        position; anything else follows, alphabetically.
        """
        if not subcategory:
            return (-1, "")
        entry = self.get(category)
        declared = entry.subcategories if entry is not None else ()
        if subcategory in declared:
            return (declared.index(subcategory), "")
        return (_UNDECLARED_SUB, subcategory.casefold())


#: Ranks every category equal. See :class:`CategoryOrder`.
EMPTY_CATEGORIES = CategoryOrder()


def field_sort_key(
    meta: FieldMeta,
    categories: CategoryOrder | None = None,
) -> tuple[int, int, str, int, str]:
    """Return one field's display sort key: group, then subgroup, then label.

    Category first, then subcategory, then ``priority`` ascending, then
    ``fullname`` case-insensitively. A field without a ``priority`` sorts after
    every field that has one *within its own subcategory* -- priority is a
    position inside a group, not across the whole table. Ties break on the
    label, so the order is total and never depends on the parquet's column
    order.

    With no ``categories``, or an empty one, this is ``(priority, fullname)``:
    the order this module had before categories existed.
    """
    cats = categories if categories is not None else EMPTY_CATEGORIES
    sub_index, sub_name = cats.sub_rank(meta.category, meta.subcategory)
    priority = meta.priority
    return (
        cats.rank(meta.category),
        sub_index,
        sub_name,
        _UNSET_PRIORITY if priority is None else priority,
        meta.fullname.casefold(),
    )


def order_fields(
    fields: Mapping[str, FieldMeta],
    categories: CategoryOrder | None = None,
) -> list[str]:
    """Return field names in display order. See :func:`field_sort_key`."""
    return sorted(fields, key=lambda name: field_sort_key(fields[name], categories))


def in_display_order(columns: Iterable[str], order: Mapping[str, int]) -> list[str]:
    """Sort an arbitrary column subset into display order.

    Args:
        columns: Any subset of the ordered columns, in any order.
        order: Column name mapped to its rank, as built from
            :func:`order_fields`.

    Returns:
        The subset in display order. A column absent from ``order`` goes last,
        keeping its input-relative position -- a stale selection has to
        degrade, not raise.
    """
    fallback = len(order)
    return sorted(columns, key=lambda column: order.get(column, fallback))


@dataclass(frozen=True)
class SubGroup:
    """One subcategory's columns, in display order."""

    name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ColumnGroup:
    """One category's columns, in display order.

    ``columns`` are the category's own -- those declaring no subcategory --
    and they come first, which is what makes a group's full contents the plain
    concatenation ``columns`` then each subgroup's.
    """

    name: str
    description: str
    columns: tuple[str, ...]
    subgroups: tuple[SubGroup, ...]

    @property
    def all_columns(self) -> tuple[str, ...]:
        """Every column in this group, in display order."""
        return self.columns + tuple(c for s in self.subgroups for c in s.columns)


def group_fields(
    fields: Mapping[str, FieldMeta],
    categories: CategoryOrder | None = None,
) -> list[ColumnGroup]:
    """Group fields into their categories and subcategories, in display order.

    Built by cutting :func:`order_fields` at each group boundary rather than by
    sorting again, so the flattened result *is* the display order rather than
    merely agreeing with it.

    Groups holding no columns are dropped: an empty node renders as a checkbox
    that ticks nothing, because a tree only treats a node with children as a
    parent.
    """
    cats = categories if categories is not None else EMPTY_CATEGORIES
    ordered = order_fields(fields, cats)

    groups: list[ColumnGroup] = []
    for name, members in _runs(ordered, lambda n: fields[n].category):
        own = tuple(n for n in members if not fields[n].subcategory)
        subgroups = tuple(
            SubGroup(name=sub, columns=tuple(cols))
            for sub, cols in _runs(
                [n for n in members if fields[n].subcategory],
                lambda n: fields[n].subcategory,
            )
        )
        declared = cats.get(name)
        groups.append(
            ColumnGroup(
                name=name or cats.uncategorized_label,
                description=declared.description if declared is not None else "",
                columns=own,
                subgroups=subgroups,
            ),
        )
    return groups


def _runs(
    names: list[str],
    key: Any,  # noqa: ANN401 - a Callable[[str], str], but see the TYPE_CHECKING import
) -> list[tuple[str, list[str]]]:
    """Cut an ordered list into consecutive runs sharing a key.

    Not ``itertools.groupby``, only because the caller wants the members as a
    list it can walk twice.
    """
    runs: list[tuple[str, list[str]]] = []
    for name in names:
        value = key(name)
        if runs and runs[-1][0] == value:
            runs[-1][1].append(name)
        else:
            runs.append((value, [name]))
    return runs


# --------------------------------------------------------------------------- #
# Roles: finding a column by what it means, not by what it is called
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LocusColumns:
    """The coordinate columns of one assembly."""

    assembly: str
    chrom: str
    start: str
    end: str
    strand: str | None = None

    @property
    def columns(self) -> tuple[str, ...]:
        """Every column this assembly contributes, strand included when present."""
        base = (self.chrom, self.start, self.end)
        return base if self.strand is None else (*base, self.strand)


@dataclass(frozen=True)
class Roles:
    """Which column fills each semantic role, derived from the metadata.

    Code asks for ``roles.symbol`` rather than hardcoding ``"symbol"``, so a
    database using ``hgnc_symbol`` works without a code change. A role no
    database fills is ``None``, and callers must degrade explicitly rather than
    silently doing nothing -- the failure this whole indirection exists to stop.
    """

    primary_key: str | None = None
    symbol: str | None = None
    ensg: str | None = None
    ncbi: str | None = None
    hgnc: str | None = None
    omim: str | None = None
    assemblies: dict[str, LocusColumns] = dataclasses_field(default_factory=dict)
    #: Ambiguities and omissions found while resolving. Surfaced by ``validate``.
    problems: list[str] = dataclasses_field(default_factory=list)

    @property
    def identifiers(self) -> dict[str, str]:
        """Filled identifier roles, mapped to their column."""
        return {
            role: column
            for role in IDENTIFIER_ROLES
            if (column := getattr(self, role)) is not None
        }

    @property
    def searchable(self) -> bool:
        """Whether any role a text search could use is filled.

        False means a search cannot be performed at all, which callers must
        report instead of returning every row.
        """
        return any((self.symbol, self.ensg, self.ncbi)) or bool(self.assemblies)

    def describe(self) -> str:
        """Return a printable summary of the resolved roles."""
        lines = [f"primary_key: {self.primary_key or '<unset>'}"]
        lines += [f"{r}: {c}" for r, c in self.identifiers.items()]
        lines += [
            f"{name}: {loc.chrom} / {loc.start} / {loc.end}"
            + (f" / {loc.strand}" if loc.strand else "")
            for name, loc in sorted(self.assemblies.items())
        ]
        lines += [f"problem: {p}" for p in self.problems]
        return "\n".join(lines)


#: ``id_type`` -> the ``LocusColumns`` attribute it supplies.
_LOCUS_ATTR = {
    "locus_chr": "chrom",
    "locus_start": "start",
    "locus_end": "end",
    "locus_strand": "strand",
}


class DerivationError(ValueError):
    """A derived-field rule cannot be evaluated."""


def derived_order(fields: Mapping[str, FieldMeta]) -> list[str]:
    """Derived field names in an order safe to evaluate them in.

    A roll-up may itself be a source for another roll-up, so evaluation has to
    follow the dependency graph rather than the file order.

    Returns:
        The derived field names, each appearing after every derived field it
        depends on. Non-derived sources are not listed.

    Raises:
        DerivationError: If the rules contain a cycle, naming the fields in it.
    """
    derived = {name: meta.derived_from for name, meta in fields.items() if meta.is_rule}
    ordered: list[str] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = [*trail[trail.index(name) :], name]
            msg = f"derived fields form a cycle: {' -> '.join(cycle)}"
            raise DerivationError(msg)
        visiting.add(name)
        for source in derived[name]:
            if source in derived:
                visit(source, (*trail, name))
        visiting.discard(name)
        done.add(name)
        ordered.append(name)

    for name in derived:
        visit(name, ())
    return ordered


def resolve_roles(fields: Mapping[str, FieldMeta]) -> Roles:
    """Derive the role map from field metadata.

    Args:
        fields: Field keys mapped to their metadata, in column order.

    Returns:
        A :class:`Roles`. Anything ambiguous or incomplete is recorded in
        ``problems`` rather than raised, so a half-migrated database still opens
        and can be inspected.
    """
    problems: list[str] = []

    keys = [name for name, meta in fields.items() if meta.primary_key]
    if len(keys) > 1:
        problems.append(f"several fields claim primary_key: {keys}; using {keys[0]!r}")
    primary_key = keys[0] if keys else None
    if primary_key is None:
        problems.append("no field is marked primary_key")

    identifiers: dict[str, str] = {}
    for role in IDENTIFIER_ROLES:
        matches = [n for n, m in fields.items() if m.id_type == role]
        if len(matches) > 1:
            problems.append(
                f"several fields claim id_type {role!r}: {matches}; using {matches[0]!r}",
            )
        if matches:
            identifiers[role] = matches[0]

    if legacy := [n for n, m in fields.items() if m.id_type == LEGACY_LOCUS]:
        problems.append(
            f"{len(legacy)} field(s) still use the unspecific id_type 'locus' and "
            f"contribute no coordinates: {legacy}. Use locus_chr / locus_start / "
            f"locus_end with an assembly.",
        )

    parts: dict[str, dict[str, str]] = {}
    for name, meta in fields.items():
        attr = _LOCUS_ATTR.get(meta.id_type or "")
        if attr is None:
            continue
        if not meta.assembly:
            problems.append(f"{name}: id_type {meta.id_type!r} needs an assembly")
            continue
        slot = parts.setdefault(meta.assembly, {})
        if attr in slot:
            problems.append(
                f"assembly {meta.assembly!r} has two {meta.id_type!r} columns "
                f"({slot[attr]!r} and {name!r}); using {slot[attr]!r}",
            )
            continue
        slot[attr] = name

    assemblies: dict[str, LocusColumns] = {}
    for assembly, slot in parts.items():
        if missing := [a for a in ("chrom", "start", "end") if a not in slot]:
            problems.append(
                f"assembly {assembly!r} is incomplete (no {', '.join(missing)}); "
                f"locus search will skip it",
            )
            continue
        assemblies[assembly] = LocusColumns(assembly=assembly, **slot)

    return Roles(
        primary_key=primary_key,
        assemblies=assemblies,
        problems=problems,
        **identifiers,
    )


# --------------------------------------------------------------------------- #
# Value types vs. Polars dtypes
# --------------------------------------------------------------------------- #


def value_type_for_dtype(dtype: pl.DataType) -> VALUE_TYPE | None:
    """Return the declared value type a Polars dtype corresponds to.

    Returns:
        The matching value type, or ``None`` for dtypes outside the supported
        set (temporal, nested, binary, ...).
    """
    if dtype == pl.Boolean:
        return "boolean"
    if dtype == pl.String:
        return "string"
    if dtype == pl.Categorical or isinstance(dtype, pl.Enum):
        return "categorical"
    if dtype.is_integer():
        return "integer"
    if dtype.is_float():
        return "float"
    return None


def value_type_matches(dtype: pl.DataType, declared: VALUE_TYPE | None) -> bool:
    """Whether a Polars dtype satisfies a declared value type.

    An undeclared (``None``) value type matches anything, so the check stays
    opt-in per field.
    """
    if declared is None:
        return True
    return value_type_for_dtype(dtype) == declared


# --------------------------------------------------------------------------- #
# Field naming
# --------------------------------------------------------------------------- #


def slugify_field_name(header: str) -> str:
    """Derive a snake_case field key from a source column header.

    Differs from the transform historically used in ``create_dataset.ipynb`` by
    collapsing runs of underscores and stripping them from the edges, so
    ``"hg19 - chromosome"`` yields ``hg19_chromosome`` rather than
    ``hg19___chromosome``. Existing keys are never re-derived, so this only
    affects newly added fields.

    Raises:
        ValueError: If the header contains no usable characters.
    """
    slug = re.sub(r"[^0-9a-z]+", "_", header.strip().lower()).strip("_")
    if not slug:
        msg = f"cannot derive a field name from {header!r}"
        raise ValueError(msg)
    return slug


# --------------------------------------------------------------------------- #
# YAML reading and writing
# --------------------------------------------------------------------------- #


def load_fields_yaml(path: Path) -> dict[str, FieldMeta]:
    """Read and validate ``fields_description.yaml``.

    Returns:
        Field keys mapped to validated metadata, in file order.

    Raises:
        FieldsYamlError: If the document is not a mapping, or if any entry fails
            validation. All failures are reported together.
    """
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problem = f"top level must be a mapping, got {type(raw).__name__}"
        raise FieldsYamlError(path, [problem])

    fields: dict[str, FieldMeta] = {}
    problems: list[str] = []
    for name, entry in raw.items():
        if not isinstance(name, str):
            problems.append(f"field key {name!r} is not a string")
            continue
        if not isinstance(entry, dict):
            kind = type(entry).__name__
            problems.append(f"{name}: entry must be a mapping, got {kind}")
            continue
        try:
            fields[name] = FieldMeta.model_validate(entry)
        except ValidationError as exc:
            problems.extend(f"{name}: {_format_error(err)}" for err in exc.errors())

    if problems:
        raise FieldsYamlError(path, problems)
    return fields


def _format_error(error: Any) -> str:  # noqa: ANN401 - pydantic error dict
    location = ".".join(str(part) for part in error["loc"]) or "<entry>"
    return f"{location}: {error['msg']}"


def dump_fields_yaml(fields: Mapping[str, FieldMeta]) -> str:
    """Render fields as YAML in the config file's existing style.

    Emits block mappings with two-space indentation, the historical six keys
    first, and the same quoting convention as the hand-maintained file so that
    rewriting an unchanged file is a no-op.
    """
    return "".join(_dump_entry(name, meta) for name, meta in fields.items())


def _dump_entry(name: str, meta: FieldMeta) -> str:
    lines = [f"{_scalar(name)}:"]
    payload = meta.model_dump()
    for key in FIELD_KEY_ORDER:
        value = payload[key]
        lines.append(f"  {key}: {_render(key, value)}")
    return "\n".join(lines) + "\n"


def _render(key: str, value: object) -> str:
    if isinstance(value, bool):
        # Checked before the numeric/str paths: bool is a subclass of int.
        return "true" if value else "false"
    if isinstance(value, int):
        # Emitted bare, bypassing `_scalar`: an int key has to reload as an int,
        # and `_is_plain_safe` quotes anything numeric-looking (and anything
        # starting with `-`, which a negative priority does).
        return str(value)
    if key in _LIST_KEYS:
        items = value if isinstance(value, list) else []
        # Flow style: these lists are short, and one line per alias would triple
        # the size of the file for no gain in readability.
        return "[" + ", ".join(_scalar(str(item)) for item in items) + "]"
    if value is None:
        return "null"
    text = str(value)
    if key in _ALWAYS_QUOTED:
        return _quoted(text)
    return _scalar(text)


# YAML indicator characters that cannot start a plain scalar.
_LEADING_INDICATORS = frozenset("-?:,[]{}#&*!|>'\"%@`")
# Plain scalars that YAML would resolve to a non-string type.
_RESERVED_PLAIN = frozenset(
    {
        "true",
        "false",
        "null",
        "yes",
        "no",
        "on",
        "off",
        "y",
        "n",
        "~",
        "none",
    },
)


def _scalar(text: str) -> str:
    """Render a string as a plain scalar when safe, otherwise double-quoted."""
    return text if _is_plain_safe(text) else _quoted(text)


def _is_plain_safe(text: str) -> bool:
    if text == "" or text != text.strip():
        return False
    if text[0] in _LEADING_INDICATORS:
        return False
    if ": " in text or text.endswith(":") or " #" in text:
        return False
    if any(ord(ch) < _MIN_PRINTABLE for ch in text):
        return False
    if text.lower() in _RESERVED_PLAIN:
        return False
    return not _looks_numeric(text)


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _quoted(text: str) -> str:
    if any(ord(ch) < _MIN_PRINTABLE for ch in text):
        msg = f"control characters are not supported in field metadata: {text!r}"
        raise ValueError(msg)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load_categories_yaml(
    path: Path,
    fields: Mapping[str, FieldMeta] | None = None,
) -> CategoryOrder:
    """Read the declared category order, ranked against ``fields``.

    A **missing file yields an empty** :class:`CategoryOrder`, which orders
    columns exactly as this module did before categories existed. The file is
    optional on purpose: a staged tree or an older deployment will not have
    one, and refusing to start over its absence would be worse than ordering
    by priority alone.

    Raises:
        CategoriesYamlError: The file exists but is malformed.
    """
    if not path.is_file():
        return CategoryOrder()

    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if raw is None:
        return CategoryOrder()

    problems: list[str] = []
    if not isinstance(raw, dict):
        raise CategoriesYamlError(
            path,
            [f"top level must be a mapping, got {type(raw).__name__}"],
        )
    entries = raw.get("categories")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise CategoriesYamlError(
            path,
            [f"'categories' must be a list, got {type(entries).__name__}"],
        )

    categories: list[Category] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        parsed = _parse_category(entry, index, problems, seen)
        if parsed is not None:
            categories.append(parsed)
            seen.add(parsed.name)

    buckets = [c.name for c in categories if c.uncategorized]
    if len(buckets) > 1:
        problems.append(
            "several categories claim 'uncategorized': "
            f"{', '.join(repr(n) for n in buckets)}; at most one may",
        )

    # A bucket sharing its name with a category a field declares would collapse
    # to one tree node, and a tree keys its nodes by id, so one would silently
    # shadow the other.
    used = {meta.category for meta in (fields or {}).values() if meta.category}
    problems.extend(
        f"category {name!r} is both the uncategorized bucket and a "
        "category fields declare; give the bucket another name"
        for name in buckets
        if name in used
    )

    if problems:
        raise CategoriesYamlError(path, problems)
    return CategoryOrder.of(categories, fields)


def _parse_category(
    entry: object,
    index: int,
    problems: list[str],
    seen: set[str],
) -> Category | None:
    """Validate one ``categories`` list entry. Appends to ``problems``."""
    where = f"categories[{index}]"
    if not isinstance(entry, dict):
        problems.append(f"{where}: must be a mapping, got {type(entry).__name__}")
        return None

    unknown = set(entry) - {"name", "description", "subcategories", "uncategorized"}
    if unknown:
        problems.append(f"{where}: unknown key(s) {sorted(unknown)}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append(f"{where}: 'name' must be a non-empty string")
        return None
    name = name.strip()
    if _NAME_SEPARATOR in name:
        problems.append(f"{where}: name {name!r} may not contain {_NAME_SEPARATOR!r}")
        return None
    if name in seen:
        problems.append(f"{where}: duplicate category name {name!r}")
        return None

    subcategories = entry.get("subcategories") or []
    if not isinstance(subcategories, list):
        problems.append(f"{where}: 'subcategories' must be a list")
        subcategories = []
    subs: list[str] = []
    for sub in subcategories:
        if not isinstance(sub, str) or not sub.strip():
            problems.append(f"{where}: subcategory names must be non-empty strings")
        elif _NAME_SEPARATOR in sub:
            problems.append(
                f"{where}: subcategory {sub!r} may not contain {_NAME_SEPARATOR!r}",
            )
        elif sub.strip() in subs:
            problems.append(f"{where}: duplicate subcategory {sub!r}")
        else:
            subs.append(sub.strip())

    return Category(
        name=name,
        description=str(entry.get("description") or ""),
        subcategories=tuple(subs),
        uncategorized=bool(entry.get("uncategorized", False)),
    )


def write_fields_yaml(path: Path, fields: Mapping[str, FieldMeta]) -> None:
    """Write ``fields_description.yaml``, overwriting it in place.

    Callers that need crash safety should go through ``Database.save()``, which
    stages both this file and the parquet before swapping them into place.
    """
    path.write_text(dump_fields_yaml(fields), encoding="utf-8")
