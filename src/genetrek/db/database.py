"""The parquet/YAML pair, managed as one object.

``resources/processed_data/genetrek.parquet`` and
``resources/config/fields_description.yaml`` only make sense together: the YAML
describes the parquet's columns, and the webapp fails or silently misbehaves when
they disagree. :class:`Database` loads both, keeps them consistent, and exposes
the checks that catch drift.

Every mutation is staged in memory. Nothing on disk changes until ``save()``,
which validates first, keeps a backup, and swaps both files into place together.
"""

from __future__ import annotations

import dataclasses
import json
import operator
import re
import warnings
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

import polars as pl
import yaml

from genetrek import paths, schema
from genetrek.backgrounds import load_backgrounds, load_covariates
from genetrek.content import CONTENT_PAGES
from genetrek.db import storage
from genetrek.db.field import BoundField
from genetrek.db.gene_index import GeneIndex, GeneIndexError, ResolutionReport
from genetrek.schema import BADGE_RENDERED_COLUMNS, MISSING_MARKER, FieldMeta

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from types import TracebackType

    from genetrek.schema import ID_TYPE, VALUE_TYPE, VISIBILITY

#: Primary key assumed when the metadata marks none. Retained so a database
#: predating the role layer still opens; new databases declare
#: ``primary_key: true`` on the field instead.
LEGACY_PRIMARY_KEY = "ncbi_id"

#: How many backups ``save()`` keeps by default.
DEFAULT_KEEP_BACKUPS = 5

#: What to do about database rows an incoming gene list does not mention.
#:
#: ``auto`` fills boolean fields with ``False`` -- absent from a curated gene list
#: means not on it -- and leaves every other type null. ``keep`` leaves them null
#: whatever the type, which is what a *derived* boolean needs: a threshold flag
#: computed from upstream data that a gene has none of is unknown, not False.
#: Several existing fields are that shape (``gnomad_v4_is_loeuf``,
#: ``is_haploinsufficient``, ``is_triplosensitive``).
MissingPolicy = Literal["auto", "keep", "fill", "error"]

#: Minimum columns an ``add_field`` input needs: identifiers plus values.
_MIN_INPUT_COLUMNS = 2

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

#: Field separator inferred from a source file's suffix.
_SEPARATORS = {".csv": ",", ".tsv": "\t", ".txt": "\t", ".tab": "\t"}


#: How many field names a validation message lists before eliding the rest.
_LISTED = 8


class DatabaseError(RuntimeError):
    """A database operation could not be completed."""


class ResolutionFailedError(DatabaseError):
    """Some identifiers in an incoming gene list could not be resolved.

    Carries the :class:`~genetrek.db.gene_index.ResolutionReport` so the caller
    can see exactly which inputs failed and decide whether to proceed. The two
    failure kinds are reported separately because they mean different things:
    unmatched identifiers are usually genes the database simply does not carry,
    while an ambiguous one would take an arbitrary gene's value.
    """

    def __init__(
        self,
        report: ResolutionReport,
        blocking: tuple[str, ...] = ("unmatched", "ambiguous"),
    ) -> None:
        self.report = report
        #: Which failure kinds actually stopped the operation, so callers only
        #: suggest the flags that are still missing.
        self.blocking = blocking
        remedies = []
        if "unmatched" in blocking:
            remedies.append(
                f"{len(report.unmatched)} unmatched (not in the database) -- "
                f"allow_unmatched=True to ignore them",
            )
        if "ambiguous" in blocking:
            remedies.append(
                f"{len(report.ambiguous)} ambiguous (match several genes) -- "
                f"allow_ambiguous=True to ignore them",
            )
        super().__init__(
            f"{report.n_input - report.n_resolved} of {report.n_input} "
            f"identifier(s) did not resolve to exactly one gene:\n"
            f"{report.describe()}\n" + "\n".join(remedies),
        )


class ValidationFailedError(DatabaseError):
    """Validation found errors."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(report.describe())


@dataclass(frozen=True)
class ValidationReport:
    """The outcome of checking the parquet against its metadata.

    Attributes:
        errors: Problems that make the pair inconsistent. ``save()`` refuses
            while any are present.
        warnings: Things worth fixing that do not break the webapp.
        notes: Observations about the data, expected to persist.
    """

    errors: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    notes: list[str] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether no errors were found."""
        return not self.errors

    def raise_if_failed(self) -> None:
        """Raise :class:`ValidationFailedError` when any error was found."""
        if not self.ok:
            raise ValidationFailedError(self)

    def describe(self) -> str:
        """Return a printable multi-line report."""
        if self.ok and not self.warnings and not self.notes:
            return "Database is valid."
        blocks: list[str] = []
        for label, items in (
            ("ERROR", self.errors),
            ("WARNING", self.warnings),
            ("NOTE", self.notes),
        ):
            blocks.extend(f"{label}: {item}" for item in items)
        return "\n".join(blocks)

    def __str__(self) -> str:
        return self.describe()


@dataclass(frozen=True)
class SyncReport:
    """What reconciling the metadata against the parquet columns changed.

    Attributes:
        added: Columns that gained a stub metadata entry.
        orphans: Metadata entries with no matching column.
        removed: Orphans actually deleted (only when pruning was requested).
        reordered: Whether metadata order was changed to match column order.
    """

    added: list[str] = dataclass_field(default_factory=list)
    orphans: list[str] = dataclass_field(default_factory=list)
    removed: list[str] = dataclass_field(default_factory=list)
    reordered: bool = False

    @property
    def changed(self) -> bool:
        """Whether anything was changed at all."""
        return bool(self.added or self.removed) or self.reordered

    def describe(self) -> str:
        """Return a printable summary."""
        if not self.changed and not self.orphans:
            return "Metadata already matches the parquet columns."
        lines = []
        if self.added:
            lines.append(
                f"added stubs for {len(self.added)} new column(s): {self.added}"
            )
        if self.removed:
            lines.append(
                f"removed {len(self.removed)} orphan entr(y/ies): {self.removed}"
            )
        elif self.orphans:
            lines.append(
                f"{len(self.orphans)} orphan entr(y/ies) left in place "
                f"(pass prune=True to remove): {self.orphans}",
            )
        if self.reordered:
            lines.append("reordered metadata to match column order")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.describe()


@dataclass(frozen=True)
class DiffReport:
    """Staged changes, relative to what is currently on disk.

    Attributes:
        added_fields: Fields staged for addition.
        removed_fields: Fields staged for removal.
        metadata_changes: Field name mapped to ``{key: (before, after)}``.
        reordered: Whether field order differs from the loaded order.
    """

    added_fields: list[str] = dataclass_field(default_factory=list)
    removed_fields: list[str] = dataclass_field(default_factory=list)
    metadata_changes: dict[str, dict[str, tuple[Any, Any]]] = dataclass_field(
        default_factory=dict,
    )
    reordered: bool = False

    @property
    def empty(self) -> bool:
        """Whether there is nothing staged."""
        return not (
            self.added_fields
            or self.removed_fields
            or self.metadata_changes
            or self.reordered
        )

    def describe(self) -> str:
        """Return a printable summary of the staged changes."""
        if self.empty:
            return "No staged changes."
        lines: list[str] = []
        lines.extend(f"+ {name}" for name in self.added_fields)
        lines.extend(f"- {name}" for name in self.removed_fields)
        for name, changes in self.metadata_changes.items():
            for key, (before, after) in changes.items():
                lines.append(f"~ {name}.{key}: {before!r} -> {after!r}")
        if self.reordered:
            lines.append("~ field order changed")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.describe()


class Database:
    """The GeneTrek database: a Polars frame plus its field metadata.

    Open it with :meth:`open`. Field metadata is mutable in place and staged
    until saved::

        db = Database.open()
        db["loeuf"].meta.visibility = "primary"
        db.save()

    Column values are read-only through this class; adding and removing columns
    goes through the dedicated methods so the metadata is updated in step.
    """

    def __init__(
        self,
        frame: pl.DataFrame,
        fields: dict[str, FieldMeta],
        *,
        resources: Path,
    ) -> None:
        self._frame = frame
        self._fields = fields
        self._resources = resources
        self._baseline_fields = {n: m.model_copy(deep=True) for n, m in fields.items()}
        self._baseline_columns = list(frame.columns)
        self._pending: list[dict[str, Any]] = []
        self._index: GeneIndex | None = None
        self._roles = schema.resolve_roles(fields)

    # ----------------------------------------------------------------- opening

    @classmethod
    def open(cls, resources: Path | str | None = None) -> Database:
        """Load the database from a resources directory.

        Args:
            resources: Resources directory. Defaults to the usual resolution
                order (``GENETREK_RESOURCES``, then discovery).

        Returns:
            The loaded database. Validation is *not* enforced here -- call
            :meth:`validate` to inspect it -- so a database with drift can still
            be opened and repaired.

        Raises:
            DatabaseError: If the parquet or the metadata file is missing.
            FieldsYamlError: If the metadata file has invalid entries.
        """
        root = paths.resources_dir(resources)
        parquet = paths.parquet_path(root)
        yaml_file = paths.fields_yaml_path(root)
        for required in (parquet, yaml_file):
            if not required.is_file():
                msg = f"required file not found: {required}"
                raise DatabaseError(msg)
        return cls(
            pl.read_parquet(parquet),
            schema.load_fields_yaml(yaml_file),
            resources=root,
        )

    # -------------------------------------------------------------- properties

    @property
    def frame(self) -> pl.DataFrame:
        """The data, as a Polars DataFrame."""
        return self._frame

    @property
    def resources(self) -> Path:
        """The resources directory this database was loaded from."""
        return self._resources

    @property
    def fields(self) -> Mapping[str, FieldMeta]:
        """Field metadata by name, in column order."""
        return self._fields

    @property
    def roles(self) -> schema.Roles:
        """Which column fills each semantic role, derived from the metadata."""
        return self._roles

    def _resolved_roles(self) -> schema.Roles:
        """The role map with the legacy primary-key fallback already applied.

        Downstream code should never have to know that an unmigrated database
        leaves ``primary_key`` unset.
        """
        if self._roles.primary_key is not None:
            return self._roles
        return dataclasses.replace(self._roles, primary_key=LEGACY_PRIMARY_KEY)

    @property
    def primary_key(self) -> str:
        """The column every identifier resolves onto.

        Falls back to :data:`LEGACY_PRIMARY_KEY` when the metadata marks none, so
        a database predating the role layer still works.
        """
        return self._roles.primary_key or LEGACY_PRIMARY_KEY

    @property
    def n_rows(self) -> int:
        """Number of genes in the database."""
        return self._frame.height

    @property
    def is_dirty(self) -> bool:
        """Whether there are staged changes not yet written to disk."""
        return (
            self._fields != self._baseline_fields
            or self._frame.columns != self._baseline_columns
        )

    def __len__(self) -> int:
        return len(self._fields)

    def __iter__(self) -> Iterator[str]:
        return iter(self._fields)

    def __contains__(self, name: object) -> bool:
        """Whether a field or one of its aliases is present."""
        return name in self._fields or name in self.aliases

    def __getitem__(self, name: str) -> BoundField:
        return self.get_field(name)

    def get_field(self, name: str) -> BoundField:
        """Return a field bound to this database, resolving aliases.

        Metadata changes made through the returned object are staged here.

        Raises:
            KeyError: If neither a field nor an alias by that name exists.
        """
        resolved = self.resolve_name(name)
        return BoundField(resolved, self._fields[resolved], self)

    # ------------------------------------------------------------------ aliases

    @property
    def aliases(self) -> dict[str, str]:
        """Alias names mapped to the field each one points at."""
        return {
            alias: name for name, meta in self._fields.items() for alias in meta.aliases
        }

    def resolve_name(self, name: str) -> str:
        """Return the real field name for a field name or an alias.

        Read operations accept either. Operations that change the shape of the
        database -- add, remove, rename -- deliberately require the real name, so
        an alias can never be the thing that gets deleted.

        Raises:
            KeyError: If ``name`` is neither a field nor an alias.
        """
        if name in self._fields:
            return name
        if (target := self.aliases.get(name)) is not None:
            return target
        msg = f"unknown field: {name!r}"
        raise KeyError(msg)

    def set_alias(self, alias: str, field: str) -> str | None:
        """Point an alias at a field, moving it if it already existed elsewhere.

        Aliases are explicit pointers, never computed. Nothing infers "the latest
        version" -- retargeting is a deliberate act, and it lands in the history
        log so a past analysis referring to the alias can be interpreted later.

        Args:
            alias: Short stable name, e.g. ``hc_ndd``.
            field: Real field the alias should point at.

        Returns:
            The field the alias previously pointed at, or ``None`` if it is new.

        Raises:
            KeyError: If ``field`` does not exist.
            DatabaseError: If ``alias`` is not a valid key, or collides with a
                real field name.
        """
        if field not in self._fields:
            msg = f"unknown field: {field!r}"
            raise KeyError(msg)
        if not schema.is_valid_field_name(alias):
            msg = (
                f"{alias!r} is not a valid alias: use lower-case letters, digits "
                f"and underscores, not starting with an underscore"
            )
            raise DatabaseError(msg)
        if alias in self._fields:
            msg = (
                f"{alias!r} is already a real field, so it cannot also be an "
                f"alias. Rename that field first if the name should be freed."
            )
            raise DatabaseError(msg)

        previous = self.aliases.get(alias)
        if previous == field:
            return previous
        if previous is not None:
            self._fields[previous].aliases = [
                a for a in self._fields[previous].aliases if a != alias
            ]
        self._fields[field].aliases = sorted({*self._fields[field].aliases, alias})
        self._pending.append(
            {
                "op": "set_alias",
                "field": field,
                "alias": alias,
                "alias_was": previous or "",
            },
        )
        return previous

    def remove_alias(self, alias: str) -> str:
        """Drop an alias.

        Returns:
            The field it pointed at.

        Raises:
            KeyError: If no such alias exists.
        """
        target = self.aliases.get(alias)
        if target is None:
            msg = f"unknown alias: {alias!r}"
            raise KeyError(msg)
        self._fields[target].aliases = [
            a for a in self._fields[target].aliases if a != alias
        ]
        self._pending.append(
            {"op": "remove_alias", "field": target, "alias": alias},
        )
        return target

    # ------------------------------------------------------------------ listing

    def field_names(
        self,
        *,
        visibility: VISIBILITY | None = None,
        value_type: VALUE_TYPE | None = None,
    ) -> list[str]:
        """Return field names, optionally filtered, in column order."""
        return [
            name
            for name, meta in self._fields.items()
            if (visibility is None or meta.visibility == visibility)
            and (value_type is None or meta.value_type == value_type)
        ]

    def list_fields(
        self,
        *,
        visibility: VISIBILITY | None = None,
        value_type: VALUE_TYPE | None = None,
    ) -> pl.DataFrame:
        """Summarise every field as a DataFrame, which renders well in Jupyter.

        Null and distinct counts are computed in a single pass over the frame.
        ``est_bytes`` is the in-memory footprint of the column and ``pct_memory``
        its share of the whole table, which is what identifies the columns worth
        dropping or narrowing.
        """
        names = self.field_names(visibility=visibility, value_type=value_type)
        if not names:
            return pl.DataFrame(schema=_LIST_SCHEMA)

        counts = self._frame.select(
            *(pl.col(n).null_count().alias(f"n:{n}") for n in names),
            *(pl.col(n).n_unique().alias(f"u:{n}") for n in names),
        ).row(0, named=True)

        sizes = {n: int(self._frame[n].estimated_size()) for n in names}
        total = sum(int(self._frame[c].estimated_size()) for c in self._frame.columns)

        return pl.DataFrame(
            {
                "field": names,
                "fullname": [self._fields[n].fullname for n in names],
                "value_type": [self._fields[n].value_type for n in names],
                "visibility": [self._fields[n].visibility for n in names],
                "category": [
                    "/".join(
                        p
                        for p in (
                            self._fields[n].category,
                            self._fields[n].subcategory,
                        )
                        if p
                    )
                    for n in names
                ],
                "priority": [self._fields[n].priority for n in names],
                "id_type": [self._fields[n].id_type for n in names],
                "aliases": [", ".join(self._fields[n].aliases) for n in names],
                "n_null": [counts[f"n:{n}"] for n in names],
                "n_unique": [counts[f"u:{n}"] for n in names],
                "est_bytes": [sizes[n] for n in names],
                "pct_memory": [
                    round(100.0 * sizes[n] / total, 3) if total else 0.0 for n in names
                ],
            },
            schema=_LIST_SCHEMA,
        )

    # ---------------------------------------------------------------- exporting

    def export(
        self,
        path: Path,
        *,
        columns: Sequence[str] | None = None,
        visibility: VISIBILITY | None = None,
        separator: str = "\t",
    ) -> int:
        """Write the database to a delimited text file.

        Args:
            path: Destination. A ``.gz`` suffix is *not* handled here; compress
                afterwards if needed.
            columns: Explicit columns to write. Mutually exclusive with
                ``visibility``.
            visibility: Write only fields with this visibility.
            separator: Field separator, tab by default.

        Returns:
            The number of rows written.

        Raises:
            KeyError: If a requested column does not exist.
            ValueError: If both ``columns`` and ``visibility`` are given.
        """
        if columns is not None and visibility is not None:
            msg = "pass either columns or visibility, not both"
            raise ValueError(msg)
        if columns is not None:
            unknown = [c for c in columns if c not in self._fields]
            if unknown:
                msg = f"unknown field(s): {unknown}"
                raise KeyError(msg)
            selected = list(columns)
        else:
            selected = self.field_names(visibility=visibility)

        self._frame.select(selected).write_csv(path, separator=separator)
        return self._frame.height

    # ----------------------------------------------------- identifier resolution

    @property
    def gene_index(self) -> GeneIndex:
        """A cached :class:`GeneIndex` over this database's identifier columns.

        An alias source is loaded on first use when one is available, so
        retired symbols resolve without the caller having to ask: the HGNC
        complete set when the tree has one, otherwise the precomputed alias
        index. Preferring the complete set keeps ``genetrek-db resolve``
        answering from the authority when it is present, and answering exactly
        as the deployed webapp does when it is not.

        A malformed or mismatched index is a warning rather than a failure --
        it costs alias coverage, and refusing to open the database over it
        would cost everything else too.
        """
        if self._index is None:
            index = GeneIndex.from_frame(self._frame, self._resolved_roles())
            hgnc = paths.latest_hgnc_file(self._resources)
            if hgnc is not None:
                index = index.with_hgnc(hgnc)
            elif paths.aliases_path(self._resources).is_file():
                try:
                    index = index.with_alias_index(
                        paths.aliases_path(self._resources),
                    )
                except GeneIndexError as exc:
                    print(f"Warning: {exc}")  # noqa: T201
            self._index = index
        return self._index

    def resolve(
        self,
        values: Sequence[str] | pl.Series,
        id_type: ID_TYPE | str = "auto",
    ) -> ResolutionReport:
        """Resolve identifiers against this database. See :meth:`GeneIndex.resolve`."""
        return self.gene_index.resolve(values, id_type)

    # ------------------------------------------------------------------ mutation

    def add_field(
        self,
        values: pl.DataFrame | pl.Series | Path | str | Iterable[str],
        name: str | None = None,
        *,
        id_col: str | None = None,
        value_col: str | None = None,
        meta: Mapping[str, Any] | None = None,
        missing: MissingPolicy = "auto",
        fill_value: Any = None,
        id_type: ID_TYPE | str = "auto",
        allow_unmatched: bool = False,
        allow_ambiguous: bool = False,
        force: bool = False,
    ) -> BoundField:
        """Stage a new field, resolving its identifiers onto the primary key.

        Args:
            values: A two-or-more column frame, a path to a delimited file, or a
                bare collection of gene identifiers. The bare form produces a
                boolean membership column -- the common "here is a gene list"
                case -- and then ``name`` is required.
            name: Field key. Defaults to ``slugify_field_name(value_col)``.
            id_col: Identifier column in ``values``. Defaults to the first column.
            value_col: Value column in ``values``. Defaults to the second column.
            meta: Metadata overrides, validated through :class:`FieldMeta`.
            missing: What to do about database rows the input does not mention.
                ``"auto"`` fills boolean fields with ``False`` -- absent from a
                curated gene list means not on it -- and leaves every other type
                null. ``"keep"`` leaves them null whatever the type, which is what
                a derived boolean flag needs (no upstream data means unknown, not
                False). ``"fill"`` uses ``fill_value``; ``"error"`` refuses unless
                every row is covered.
            fill_value: Value used when ``missing="fill"``.
            id_type: Identifier type of ``id_col``, or ``"auto"`` to detect.
            allow_unmatched: Proceed when some identifiers match no gene. Usual
                for an upstream file covering genes the database does not carry.
            allow_ambiguous: Proceed when some identifiers match several genes,
                dropping them. Those values cannot be assigned unarbitrarily.
            force: Shorthand for both ``allow_unmatched`` and ``allow_ambiguous``.

        Returns:
            The newly staged field, bound to this database.

        Raises:
            DatabaseError: If the field already exists, the input is unusable, or
                ``missing="error"`` and some rows are uncovered.
            ResolutionFailedError: If identifiers failed to resolve in a way that
                has not been explicitly allowed.
        """
        frame, source = _coerce_input(values)
        id_column, value_column = _pick_columns(
            frame, id_col=id_col, value_col=value_col
        )

        field_name = name or schema.slugify_field_name(value_column)
        if field_name in self._fields or field_name in self._frame.columns:
            msg = f"field {field_name!r} already exists; remove it first or pick another name"
            raise DatabaseError(msg)
        if (owner := self.aliases.get(field_name)) is not None:
            msg = (
                f"{field_name!r} is already an alias for {owner!r}; drop the alias "
                f"first or pick another name"
            )
            raise DatabaseError(msg)

        report = self.resolve(frame[id_column].cast(pl.String), id_type)
        blocking = tuple(
            kind
            for kind, present, allowed in (
                ("unmatched", report.unmatched, allow_unmatched or force),
                ("ambiguous", report.ambiguous, allow_ambiguous or force),
            )
            if present and not allowed
        )
        if blocking:
            raise ResolutionFailedError(report, blocking)

        column = self._build_column(
            frame,
            report,
            id_column=id_column,
            value_column=value_column,
            missing=missing,
            fill_value=fill_value,
            field_name=field_name,
        )

        overrides = dict(meta or {})
        overrides.setdefault("fullname", value_column)
        overrides.setdefault("value_type", schema.value_type_for_dtype(column.dtype))
        overrides.setdefault("added", schema.today_stamp())
        if source is not None:
            overrides.setdefault("source_file", source)
        field_meta = FieldMeta.model_validate(overrides)

        self._frame = self._frame.with_columns(column.alias(field_name))
        self._fields[field_name] = field_meta
        self._pending.append(
            {
                "op": "add_field",
                "field": field_name,
                "value_type": field_meta.value_type,
                "id_type": report.id_type,
                "source_file": source or "",
                "rows_matched": report.n_resolved,
                "rows_input": report.n_input,
                "rows_total": self.n_rows,
                "n_unmatched": len(report.unmatched),
                "n_ambiguous": len(report.ambiguous),
                "unmatched": report.unmatched[:20],
                "ambiguous": report.ambiguous[:20],
                "allow_unmatched": allow_unmatched or force,
                "allow_ambiguous": allow_ambiguous or force,
            },
        )
        return self.get_field(field_name)

    def _build_column(
        self,
        frame: pl.DataFrame,
        report: ResolutionReport,
        *,
        id_column: str,
        value_column: str,
        missing: MissingPolicy,
        fill_value: Any,
        field_name: str,
    ) -> pl.Series:
        """Map resolved identifiers to values and align them to the database rows."""
        incoming = frame.select(
            pl.col(id_column).cast(pl.String).alias("input"),
            pl.col(value_column).alias("_value"),
        )
        pairs = (
            report.frame.select("input", report.key)
            .filter(pl.col(report.key).is_not_null())
            .join(incoming, on="input", how="left")
            .select(report.key, "_value")
        )

        before = pairs.height
        pairs = pairs.unique(subset=[report.key], keep="first", maintain_order=True)
        if pairs.height < before:
            warnings.warn(
                f"{before - pairs.height} input row(s) resolved to a gene already "
                f"covered by another row; the first value was kept",
                stacklevel=3,
            )

        aligned = (
            self._frame.select(pl.col(report.key))
            .join(pairs, on=report.key, how="left")
            .get_column("_value")
        )

        n_missing = aligned.null_count()
        if missing == "error" and n_missing:
            msg = (
                f"{n_missing:,} of {self.n_rows:,} database rows are not covered by "
                f"the input for {field_name!r}; use missing='auto', 'keep' or 'fill'"
            )
            raise DatabaseError(msg)
        if missing == "fill":
            if fill_value is None:
                msg = (
                    f"missing='fill' needs a fill_value; to leave uncovered rows "
                    f"null use missing='keep' instead ({field_name!r})"
                )
                raise DatabaseError(msg)
            aligned = aligned.fill_null(fill_value)
        elif missing == "auto" and aligned.dtype == pl.Boolean:
            aligned = aligned.fill_null(value=False)

        return aligned

    def add_derived_field(
        self,
        name: str,
        sources: Sequence[str],
        *,
        op: schema.DERIVED_OP = "union",
        meta: Mapping[str, Any] | None = None,
    ) -> BoundField:
        """Stage a boolean column computed from other boolean columns.

        The rule is stored in the field's metadata, so a roll-up can be
        regenerated after one of its inputs is refreshed, and :meth:`validate`
        can detect that the stored values have drifted from the rule.

        Args:
            name: Field key for the new column.
            sources: Fields to combine. Each must already exist and be
                boolean. Nulls ("not measured") follow Kleene logic -- see
                :data:`genetrek.schema.DERIVED_OP`.
            op: ``"union"`` or ``"intersection"``. ``"external"`` is not a rule
                this method can evaluate; record it through ``meta`` on an
                ordinary :meth:`add_field` instead.
            meta: Metadata overrides, validated through
                :class:`~genetrek.schema.FieldMeta`. ``derived_from`` and
                ``derived_op`` are set from the arguments and cannot be
                overridden here.

        Returns:
            The newly staged field, bound to this database.

        Raises:
            DatabaseError: If the field already exists, or shadows an alias.
            DerivationError: If a source is missing or not boolean, or ``op``
                is not an evaluable operation.
        """
        if op not in schema.EVALUATED_OPS:
            msg = (
                f"{name}: {op!r} is not an evaluable operation; use one of "
                f"{', '.join(schema.EVALUATED_OPS)}"
            )
            raise schema.DerivationError(msg)
        if name in self._fields or name in self._frame.columns:
            msg = f"field {name!r} already exists; remove it first or pick another name"
            raise DatabaseError(msg)
        if (owner := self.aliases.get(name)) is not None:
            msg = f"{name!r} is already an alias for {owner!r}; drop the alias first"
            raise DatabaseError(msg)

        column = self._evaluate_derived(name, list(sources), op)

        overrides = dict(meta or {})
        overrides.setdefault("fullname", name.replace("_", " ").capitalize())
        overrides.setdefault("added", schema.today_stamp())
        overrides["value_type"] = "boolean"
        overrides["derived_from"] = list(sources)
        overrides["derived_op"] = op
        field_meta = FieldMeta.model_validate(overrides)

        self._frame = self._frame.with_columns(column.alias(name))
        self._fields[name] = field_meta
        self._pending.append(
            {
                "op": "add_derived_field",
                "field": name,
                "derived_op": op,
                "derived_from": list(sources),
                "n_true": int(column.sum()),
                "n_null": column.null_count(),
                "rows_total": self.n_rows,
            },
        )
        return self.get_field(name)

    def recompute_derived(self, *, names: Iterable[str] | None = None) -> list[str]:
        """Re-evaluate derived columns, returning the ones whose values changed.

        Evaluation follows the dependency graph, so a roll-up of a roll-up sees
        its input already refreshed.

        Not called by :meth:`save`. Recomputing on every write would silently
        overwrite a column whose upstream values had legitimately changed, which
        is exactly the drift :meth:`validate` exists to report.

        Args:
            names: Derived fields to refresh. Defaults to all of them.

        Returns:
            The fields whose stored values differed from the rule, in evaluation
            order. An empty list means everything already agreed.

        Raises:
            DerivationError: If a rule is unevaluable or the rules form a cycle.
        """
        wanted = set(names) if names is not None else None
        changed: list[str] = []
        for field_name in schema.derived_order(self._fields):
            if wanted is not None and field_name not in wanted:
                continue
            meta = self._fields[field_name]
            column = self._evaluate_derived(
                field_name,
                meta.derived_from,
                meta.derived_op or "union",
            )
            if field_name in self._frame.columns and self._frame[field_name].equals(
                column,
            ):
                continue
            self._frame = self._frame.with_columns(column.alias(field_name))
            changed.append(field_name)

        if changed:
            self._pending.append(
                {"op": "recompute_derived", "field": ", ".join(changed)},
            )
        return changed

    def _evaluate_derived(
        self,
        name: str,
        sources: Sequence[str],
        op: schema.DERIVED_OP,
    ) -> pl.Series:
        """Combine boolean source columns, refusing anything ill-defined."""
        if not sources:
            msg = f"{name}: a derived field needs at least one source"
            raise schema.DerivationError(msg)

        problems: list[str] = []
        for source in sources:
            if source == name:
                problems.append(f"{source!r} is the field itself")
                continue
            if source not in self._frame.columns:
                problems.append(f"{source!r} is not a column of this database")
                continue
            series = self._frame[source]
            if series.dtype != pl.Boolean:
                problems.append(f"{source!r} is {series.dtype}, not boolean")
        if problems:
            joined = "\n".join(f"  - {problem}" for problem in problems)
            msg = f"cannot derive {name!r} from {list(sources)}:\n{joined}"
            raise schema.DerivationError(msg)

        # Series ``|`` and ``&`` are Kleene: True | null is True, False | null
        # is null, False & null is False. That is the semantics wanted for
        # "not measured", so no fill_null belongs here.
        combine = operator.or_ if op == "union" else operator.and_
        result = self._frame[sources[0]]
        for source in sources[1:]:
            result = combine(result, self._frame[source])
        return result.rename(name)

    def remove_field(self, name: str, *, force: bool = False) -> None:
        """Stage the removal of a field.

        Args:
            name: Field to remove.
            force: Remove even when the webapp configuration references it.

        Raises:
            KeyError: If no such field exists.
            DatabaseError: If the field is the primary key, or is referenced by
                ``badges.yaml`` or ``external_links.yaml``, unless ``force``.
        """
        if name not in self._fields:
            msg = f"unknown field: {name!r}"
            raise KeyError(msg)

        if not force and (reasons := self._removal_objections(name)):
            joined = "\n".join(f"  - {reason}" for reason in reasons)
            msg = f"refusing to remove {name!r}:\n{joined}\nPass force=True to insist."
            raise DatabaseError(msg)

        if name in self._frame.columns:
            self._frame = self._frame.drop(name)
        del self._fields[name]
        self._pending.append({"op": "remove_field", "field": name, "forced": force})

    def _removal_objections(self, name: str) -> list[str]:
        """Every reason not to remove a field, so one call reports them all."""
        reasons: list[str] = []
        if name == self.primary_key:
            reasons.append(
                "it is the primary key every identifier resolves onto; "
                "removing it breaks resolution",
            )
        # Its aliases go with it, and code or config may name one of those
        # rather than the column (inheritance -> inheritance_v7).
        for known_as in (name, *self._fields[name].aliases):
            if where := schema.CODE_DEPENDENT_COLUMNS.get(known_as):
                reasons.append(
                    f"it is referenced by name in code as {known_as!r} ({where}), "
                    f"which will silently stop working",
                )
            reasons.extend(
                f"it is referenced by the webapp configuration in {reference}"
                for reference in self._references_to(known_as)
            )
        return reasons

    def _references_to(self, name: str) -> list[str]:
        """Return descriptions of every webapp config reference to a field."""
        found: list[str] = []
        badges = self._load_optional_yaml(paths.badges_path(self._resources))
        if isinstance(badges, dict) and name in badges:
            found.append("badges.yaml")

        links = self._load_optional_yaml(paths.external_links_path(self._resources))
        if isinstance(links, dict):
            for link in links.get("links") or []:
                if not isinstance(link, dict):
                    continue
                if name in _PLACEHOLDER_RE.findall(str(link.get("url", ""))):
                    found.append(f"external_links.yaml -> {link.get('name', '?')}")
        return found

    def rename_field(
        self,
        old: str,
        new: str,
        *,
        update_config: bool = True,
        force: bool = False,
    ) -> BoundField:
        """Rename a field, keeping every reference to it in step.

        A field key is not just metadata: it is the parquet column name, the
        metadata key, possibly a ``badges.yaml`` key, an ``external_links.yaml``
        placeholder, a ``column_mapping.json`` value, and possibly a name that
        appears in code. This updates the data, the metadata, and the YAML
        configuration together, and refuses names that code depends on.

        The column keeps its position, so the resulting diff is one renamed key
        rather than a reordering.

        Args:
            old: Current field key.
            new: New field key. Must be lower-case alphanumeric with underscores.
            update_config: Also rewrite references in ``badges.yaml``,
                ``external_links.yaml`` and ``column_mapping.json``. Leaving
                these stale would make ``save()`` fail validation.
            force: Rename even a column whose name appears in code. The code will
                not follow -- see :data:`~genetrek.schema.CODE_DEPENDENT_COLUMNS`.

        Returns:
            The renamed field, bound to this database.

        Raises:
            KeyError: If ``old`` does not exist.
            DatabaseError: If ``new`` is invalid or already taken, or if ``old``
                is depended on by code and ``force`` is not set.
        """
        if old not in self._fields:
            msg = f"unknown field: {old!r}"
            raise KeyError(msg)
        if new == old:
            msg = f"{new!r} is already the field's name"
            raise DatabaseError(msg)
        if not schema.is_valid_field_name(new):
            msg = (
                f"{new!r} is not a valid field key: use lower-case letters, "
                f"digits and underscores, not starting with an underscore"
            )
            raise DatabaseError(msg)
        if new in self._fields or new in self._frame.columns:
            msg = f"field {new!r} already exists"
            raise DatabaseError(msg)
        if (owner := self.aliases.get(new)) is not None:
            msg = (
                f"{new!r} is already an alias for {owner!r}; drop the alias first "
                f"or pick another name"
            )
            raise DatabaseError(msg)

        if not force and (where := schema.CODE_DEPENDENT_COLUMNS.get(old)):
            msg = (
                f"{old!r} is referenced by name in code ({where}), which will not "
                f"follow the rename -- the feature would silently stop working. "
                f"Update the code first, then pass force=True."
            )
            raise DatabaseError(msg)

        if old in self._frame.columns:
            self._frame = self._frame.rename({old: new})
        # Rebuild the metadata in place so the key keeps its position.
        self._fields = {
            (new if name == old else name): meta for name, meta in self._fields.items()
        }
        self._roles = schema.resolve_roles(self._fields)
        # A rule (or an external parent) names its sources by key, so it follows.
        for meta in self._fields.values():
            if old in meta.derived_from:
                meta.apply(
                    {
                        "derived_from": [
                            new if source == old else source
                            for source in meta.derived_from
                        ],
                    },
                )

        config_edits: list[str] = []
        if update_config:
            config_edits = self._rename_in_config(old, new)

        self._pending.append(
            {
                "op": "rename_field",
                "field": new,
                "renamed_from": old,
                "config_updated": config_edits,
                "forced": force,
            },
        )
        return self.get_field(new)

    def _rename_in_config(self, old: str, new: str) -> list[str]:
        """Rewrite references to a renamed field in the YAML/JSON configuration.

        Returns:
            A description of each file changed.
        """
        edits: list[str] = []

        badges_file = paths.badges_path(self._resources)
        badges = self._load_optional_yaml(badges_file)
        if isinstance(badges, dict) and old in badges:
            renamed = {(new if k == old else k): v for k, v in badges.items()}
            badges_file.write_text(
                yaml.safe_dump(renamed, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            edits.append(f"badges.yaml: {old} -> {new}")

        links_file = paths.external_links_path(self._resources)
        links = self._load_optional_yaml(links_file)
        if isinstance(links, dict):
            changed = 0
            for link in links.get("links") or []:
                if not isinstance(link, dict):
                    continue
                url = str(link.get("url", ""))
                if f"{{{old}}}" in url:
                    link["url"] = url.replace(f"{{{old}}}", f"{{{new}}}")
                    changed += 1
            if changed:
                links_file.write_text(
                    yaml.safe_dump(links, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
                edits.append(f"external_links.yaml: {changed} placeholder(s)")

        mapping_file = paths.column_mapping_path(self._resources)
        if mapping_file.is_file():
            mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
            if isinstance(mapping, dict) and old in mapping.values():
                updated = {
                    header: (new if key == old else key)
                    for header, key in mapping.items()
                }
                mapping_file.write_text(
                    json.dumps(updated, indent=2) + "\n",
                    encoding="utf-8",
                )
                edits.append(f"column_mapping.json: {old} -> {new}")

        return edits

    def set_meta(self, name: str, **changes: Any) -> BoundField:
        """Stage metadata changes for a field.

        Raises:
            KeyError: If no such field exists.
            ValueError: If a key is unknown or a value invalid, changing nothing.
        """
        field = self.get_field(name)
        field.apply(changes)
        self._pending.append(
            {"op": "set_meta", "field": name, "keys": sorted(changes)},
        )
        return field

    def touch(self, name: str) -> BoundField:
        """Set a field's ``update`` date to today."""
        return self.set_meta(name, update=schema.today_stamp())

    def sync_fields_description(self, *, prune: bool = False) -> SyncReport:
        """Reconcile the metadata with the parquet columns, preserving what exists.

        Existing entries are kept verbatim -- this never blanks a curated
        description, visibility, or DOI. New columns get a stub, and metadata
        order is normalised to column order. Orphaned entries are only reported
        unless ``prune`` is set.
        """
        added: list[str] = []
        for column in self._frame.columns:
            if column not in self._fields:
                self._fields[column] = FieldMeta(
                    fullname=column,
                    value_type=schema.value_type_for_dtype(self._frame.schema[column]),
                    added=schema.today_stamp(),
                )
                added.append(column)

        orphans = [name for name in self._fields if name not in self._frame.columns]
        removed: list[str] = []
        if prune:
            for name in orphans:
                del self._fields[name]
            removed = list(orphans)

        reordered = self._normalise_order()
        report = SyncReport(
            added=added,
            orphans=orphans,
            removed=removed,
            reordered=reordered,
        )
        if report.changed:
            self._pending.append(
                {
                    "op": "sync_fields_description",
                    "added": added,
                    "removed": removed,
                    "reordered": reordered,
                },
            )
        return report

    def _normalise_order(self) -> bool:
        """Reorder metadata to match column order. Returns whether it changed."""
        desired = [c for c in self._frame.columns if c in self._fields]
        desired += [n for n in self._fields if n not in self._frame.columns]
        if desired == list(self._fields):
            return False
        self._fields = {name: self._fields[name] for name in desired}
        return True

    # ------------------------------------------------------------------- diffing

    def diff(self) -> DiffReport:
        """Describe what is staged, relative to the files on disk."""
        current, baseline = set(self._fields), set(self._baseline_fields)
        changes: dict[str, dict[str, tuple[Any, Any]]] = {}
        for name in current & baseline:
            now = self._fields[name].model_dump()
            was = self._baseline_fields[name].model_dump()
            if per_key := {k: (was[k], now[k]) for k in now if was[k] != now[k]}:
                changes[name] = per_key

        shared_order_now = [n for n in self._fields if n in baseline]
        shared_order_was = [n for n in self._baseline_fields if n in current]
        return DiffReport(
            added_fields=[n for n in self._fields if n not in baseline],
            removed_fields=[n for n in self._baseline_fields if n not in current],
            metadata_changes=changes,
            reordered=shared_order_now != shared_order_was,
        )

    # -------------------------------------------------------------------- saving

    def save(
        self,
        *,
        backup: bool = True,
        keep_backups: int = DEFAULT_KEEP_BACKUPS,
        actor: str | None = None,
        note: str | None = None,
        validate: bool = True,
    ) -> storage.SaveResult:
        """Write both files to disk, together.

        Normalises metadata order to column order, validates, backs up the
        current files, then swaps the new ones into place. See
        :mod:`genetrek.db.storage` for exactly how far the atomicity goes.

        Args:
            backup: Copy the current files into ``processed_data/.backups/``
                before overwriting them.
            keep_backups: How many backup directories to retain.
            actor: Who made the change, recorded in the history log.
            note: Free-text reason, recorded in the history log.
            validate: Run :meth:`validate` and refuse on any error. Turning this
                off can write an inconsistent database and is only for repair.

        Returns:
            A :class:`~genetrek.db.storage.SaveResult`.

        Raises:
            ValidationFailedError: If validation found errors.
        """
        self._normalise_order()
        if validate:
            self.validate().raise_if_failed()

        parquet = paths.parquet_path(self._resources)
        yaml_file = paths.fields_yaml_path(self._resources)

        backup_dir = None
        pruned: list[Path] = []
        if backup:
            backup_dir = storage.make_backup(
                paths.backups_dir(self._resources),
                (parquet, yaml_file),
            )
            pruned = storage.prune_backups(
                paths.backups_dir(self._resources),
                keep_backups,
            )

        frame = self._frame
        fields = dict(self._fields)

        def write_parquet(path: Path) -> None:
            frame.write_parquet(path)

        def write_yaml(path: Path) -> None:
            path.write_text(schema.dump_fields_yaml(fields), encoding="utf-8")

        storage.write_pair(
            parquet.parent,
            [(parquet, write_parquet), (yaml_file, write_yaml)],
        )

        n_written = storage.append_history(
            paths.history_path(self._resources),
            [
                {
                    "ts": storage.utc_now(),
                    "actor": actor or "",
                    "note": note or "",
                    "n_fields": len(fields),
                    "n_rows": frame.height,
                    **record,
                }
                for record in (self._pending or [{"op": "save"}])
            ],
        )

        self._pending.clear()
        self._baseline_fields = {
            n: m.model_copy(deep=True) for n, m in self._fields.items()
        }
        self._baseline_columns = list(self._frame.columns)
        storage.clear_interrupted_marker(parquet.parent)
        return storage.SaveResult(
            backup=backup_dir,
            pruned=pruned,
            n_operations=n_written,
        )

    def history(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the mutation log, newest last."""
        return storage.read_history(paths.history_path(self._resources), limit)

    def pending_config_edits(self) -> list[str]:
        """Configuration files rewritten by staged operations, for reporting.

        Unlike the parquet and the metadata, these edits happen immediately --
        they are small YAML files, not part of the staged pair.
        """
        return [
            edit
            for record in self._pending
            for edit in record.get("config_updated") or []
        ]

    # --------------------------------------------------------- context behaviour

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Warn about unsaved changes; never save implicitly.

        A half-finished notebook cell must not be able to write the database, so
        leaving the block dirty is a warning rather than an automatic save.
        """
        if exc_type is None and self.is_dirty:
            warnings.warn(
                f"leaving a Database with unsaved changes:\n{self.diff().describe()}",
                stacklevel=2,
            )

    # --------------------------------------------------------------- validation

    def validate(self) -> ValidationReport:
        """Check the metadata against the data and the UI configuration.

        Errors are conditions that make the pair inconsistent -- a missing or
        extra field, a declared type that no longer matches the parquet, a broken
        external link, or a primary key that is not usable. Warnings are
        fixable-but-harmless, and notes describe expected data quirks.
        """
        report = ValidationReport()
        self._check_columns(report)
        self._check_types(report)
        self._check_metadata(report)
        self._check_primary_key(report)
        self._check_ui_config(report)
        self._check_roles(report)
        self._check_derived(report)
        self._check_code_dependencies(report)
        self._check_alias_index(report)
        self._check_content_pages(report)
        self._note_missing_markers(report)
        return report

    def _check_content_pages(self, report: ValidationReport) -> None:
        """Check every declared prose page has its markdown file.

        A warning, not an error, and the distinction is load-bearing:
        ``save()`` refuses on an error, and a missing prose page does not make
        the parquet/YAML pair wrong -- it makes the *webapp* unable to start.
        Failing ``save`` over it would stop ``genetrek-db`` being usable on a
        data-only tree, which is a legitimate thing to have.

        The two gates that matter are elsewhere: ``load_config`` refuses at
        import, so a deployment fails loudly rather than serving a blank page,
        and ``tests/test_content_pages.py`` checks the checked-in tree in CI.
        This is the third, for the case neither covers -- a tree from ``init
        --output`` or a volume mount -- and ``validate`` is what maintainers
        are told to run before publishing one.
        """
        missing = [
            page.slug
            for page in CONTENT_PAGES
            if not paths.content_path(page.slug, self._resources).is_file()
        ]
        if missing:
            report.warnings.append(
                f"prose page(s) with no markdown file in content/: {missing}. "
                f"The webapp will not start without them.",
            )

    def _check_alias_index(self, report: ValidationReport) -> None:
        """Check the shipped alias index still matches this database.

        A warning throughout, never an error: the index is derived, optional,
        and its absence costs retired-symbol resolution and nothing else. The
        application must still start and serve without it.

        The row count is the staleness signal. Rebuilding the parquet with a
        different gene set leaves the index's exact-symbol layer describing
        genes that are no longer there, which resolution cannot detect on its
        own because a stale symbol looks exactly like a retired one.
        """
        path = paths.aliases_path(self._resources)
        if not path.is_file():
            report.warnings.append(
                "no alias index at "
                f"{path.name}; retired gene symbols will not resolve in a "
                "deployment. Run `genetrek-db build-aliases`.",
            )
            return

        meta = pl.read_parquet_metadata(path)
        if (stored_key := meta.get("key_column")) and stored_key != self.primary_key:
            report.warnings.append(
                f"{path.name} was built against {stored_key!r} but the "
                f"primary key is now {self.primary_key!r}; rebuild it with "
                f"`genetrek-db build-aliases`",
            )
        if (rows := meta.get("frame_rows")) and rows != str(self.n_rows):
            report.warnings.append(
                f"{path.name} was built from a {rows}-gene table but this one "
                f"has {self.n_rows}; rebuild it with `genetrek-db build-aliases`",
            )

    def _check_columns(self, report: ValidationReport) -> None:
        in_data = set(self._frame.columns)
        in_config = set(self._fields)
        if missing_config := sorted(in_data - in_config):
            report.errors.append(
                f"columns in the parquet but not in the metadata: {missing_config}",
            )
        if missing_data := sorted(in_config - in_data):
            report.errors.append(
                f"fields in the metadata but not in the parquet: {missing_data}",
            )
        if in_data == in_config and list(self._fields) != self._frame.columns:
            report.warnings.append(
                "metadata key order differs from parquet column order; "
                "save() will normalise it",
            )

    def _check_types(self, report: ValidationReport) -> None:
        frame_schema = self._frame.schema
        undeclared: list[str] = []
        for name, meta in self._fields.items():
            dtype = frame_schema.get(name)
            if dtype is None:
                continue
            if meta.value_type is None:
                undeclared.append(name)
            elif not schema.value_type_matches(dtype, meta.value_type):
                actual = schema.value_type_for_dtype(dtype) or str(dtype)
                report.errors.append(
                    f"{name}: declared value_type {meta.value_type!r} but the "
                    f"parquet holds {dtype} ({actual})",
                )
        if undeclared:
            report.warnings.append(
                f"fields without a declared value_type: {undeclared}"
            )

    def _check_metadata(self, report: ValidationReport) -> None:
        for name, meta in self._fields.items():
            if not meta.fullname.strip():
                report.errors.append(f"{name}: fullname is empty")
            if meta.id_type is not None and meta.value_type is None:
                report.warnings.append(
                    f"{name}: declares id_type but no value_type",
                )
            if meta.visibility == "primary" and meta.priority is None:
                # A warning, never an error: `validate` runs on every save, and
                # a database straight from `init` declares no priorities.
                report.warnings.append(
                    f"{name}: primary but declares no priority, so its position "
                    f"in the default view is decided by its label",
                )
            if meta.subcategory and not meta.category:
                # Also a warning, and deliberately not a model validator: a
                # raising one would make the whole YAML unloadable, and
                # `load_config` exits on a bad file, so the webapp would refuse
                # to start over a stray key.
                report.warnings.append(
                    f"{name}: declares subcategory {meta.subcategory!r} but no "
                    f"category, so it has no group to sort into and the "
                    f"subcategory is ignored",
                )
        unlicensed = [name for name, meta in self._fields.items() if not meta.licence]
        if unlicensed:
            # A note, not a warning: a database fresh from `init` has none yet.
            # tests/test_real_database.py is what holds the published one to it.
            shown = ", ".join(unlicensed[:_LISTED]) + (
                ", ..." if len(unlicensed) > _LISTED else ""
            )
            report.notes.append(
                f"{len(unlicensed)} field(s) declare no licence, so DATA_LICENCES.md "
                f"cannot state their terms: {shown}",
            )
        self._check_aliases(report)
        self._check_display_order(report)

    def _check_display_order(self, report: ValidationReport) -> None:
        """Check the gene symbol still leads the table.

        ``ui/styles.py`` styles the sticky, right-aligned first column
        *positionally* (``th:first-child``), so whichever column sorts first
        inherits it. Since ``priority`` orders columns only within their own
        category, a single category change elsewhere can take that slot --
        which is why this is checked on every save rather than left to a test.
        """
        symbol = self._roles.symbol
        if symbol is None:
            return
        visible = {
            name: meta
            for name, meta in self._fields.items()
            if meta.visibility != "hidden"
        }
        if not visible:
            return
        first = schema.order_fields(visible, self._categories())[0]
        if first != symbol:
            report.warnings.append(
                f"the first visible column is {first!r}, not the gene symbol "
                f"{symbol!r}; the table styles its first column positionally, "
                f"so {first!r} would render as the sticky identifier column",
            )

    def _categories(self) -> schema.CategoryOrder:
        """The declared category order for this tree, or an empty one."""
        try:
            return schema.load_categories_yaml(
                paths.categories_path(self._resources),
                self._fields,
            )
        except schema.CategoriesYamlError:
            # Reported by _check_ui_config; ordering falls back to priority.
            return schema.CategoryOrder()

    def _check_aliases(self, report: ValidationReport) -> None:
        """Aliases must be unambiguous: no shadowing, no duplicates, no self-reference."""
        seen: dict[str, str] = {}
        for name, meta in self._fields.items():
            for alias in meta.aliases:
                if alias in self._fields:
                    report.errors.append(
                        f"{name}: alias {alias!r} shadows a real field of that name",
                    )
                if alias == name:
                    report.errors.append(
                        f"{name}: alias {alias!r} points at its own field",
                    )
                if (owner := seen.get(alias)) is not None:
                    report.errors.append(
                        f"alias {alias!r} is claimed by both {owner!r} and {name!r}",
                    )
                seen[alias] = name

    def _check_roles(self, report: ValidationReport) -> None:
        """Surface anything ambiguous or incomplete in the role map.

        A role the metadata does not fill is not an error in itself -- a database
        need not carry every identifier -- but an *ambiguous* one is, because the
        resolution target would then be arbitrary.
        """
        for problem in self._roles.problems:
            if "several fields claim" in problem:
                report.errors.append(problem)
            else:
                report.warnings.append(problem)

    def _check_derived(self, report: ValidationReport) -> None:
        """Check every derived column still matches the rule that produced it.

        This is the point of recording the rule. A roll-up drifting from its
        inputs is otherwise invisible: the column is a perfectly ordinary
        boolean, and nothing about it says it was supposed to be a union.
        """
        for name, meta in self._fields.items():
            if meta.is_derived and not meta.is_rule:
                missing = [p for p in meta.derived_from if p not in self._fields]
                if missing:
                    report.errors.append(
                        f"{name}: computed from {missing}, which "
                        f"{'is' if len(missing) == 1 else 'are'} not a field",
                    )

        derived = {n: m for n, m in self._fields.items() if m.is_rule}
        if not derived:
            return

        try:
            order = schema.derived_order(self._fields)
        except schema.DerivationError as exc:
            report.errors.append(str(exc))
            return

        for name in order:
            meta = derived[name]
            try:
                expected = self._evaluate_derived(
                    name,
                    meta.derived_from,
                    meta.derived_op or "union",
                )
            except schema.DerivationError as exc:
                report.errors.append(f"{name}: {exc}".replace("\n", " "))
                continue
            if name not in self._frame.columns:
                continue  # already reported by _check_columns
            stored = self._frame[name]
            if stored.equals(expected):
                continue
            # ne_missing: a row null on both sides agrees.
            differing = int(stored.ne_missing(expected).sum())
            report.errors.append(
                f"{name}: stored values disagree with its "
                f"{meta.derived_op} of {meta.derived_from} on {differing:,} row(s). "
                f"Run recompute_derived() if an input changed, or correct the rule.",
            )

    def _check_code_dependencies(self, report: ValidationReport) -> None:
        """Note which name-dependent features have no column to work with.

        A note rather than a warning: every one of these degrades gracefully (a
        plot renders a "Missing columns" label, the search stops matching a token
        type), and a database need not carry them all. Enforcement lives where it
        belongs -- ``rename_field`` and ``remove_field`` refuse them outright.

        Two exclusions keep the note trustworthy rather than merely complete.
        :data:`~genetrek.schema.OPTIONAL_CODE_COLUMNS` never appear: an absent
        marker colour or fallback breaks nothing. And a column with a present
        stand-in from :data:`~genetrek.schema.CODE_COLUMN_FALLBACKS` is skipped,
        so a database still keyed on ``pli`` is not told its constraint plot is
        dead while the plot draws. With *both* absent, the newer name is
        reported -- which is the case where the feature really has no data.
        """
        aliases = self.aliases
        absent = {
            column: where
            for column, where in schema.CODE_DEPENDENT_COLUMNS.items()
            if column not in self._fields
            and column not in aliases
            and column not in schema.OPTIONAL_CODE_COLUMNS
            and not any(
                stand_in in self._fields
                for stand_in in schema.CODE_COLUMN_FALLBACKS.get(column, ())
            )
        }
        if absent:
            report.notes.append(
                f"{len(absent)} column(s) referenced by name in code are absent, "
                f"so those features are unavailable: "
                + ", ".join(f"{c} ({w})" for c, w in absent.items()),
            )

    def _check_primary_key(self, report: ValidationReport) -> None:
        key = self.primary_key
        if key not in self._frame.columns:
            report.errors.append(f"primary key column {key!r} is missing")
            return
        series = self._frame[key]
        if (nulls := series.null_count()) > 0:
            report.errors.append(f"{key} has {nulls:,} null value(s)")
        if (unique := series.n_unique()) != series.len():
            report.errors.append(
                f"{key} is not unique: {unique:,} distinct "
                f"across {series.len():,} rows",
            )

    def _check_ui_config(self, report: ValidationReport) -> None:
        columns = set(self._fields)
        aliases = self.aliases

        badges = self._load_optional_yaml(paths.badges_path(self._resources))
        if isinstance(badges, dict):
            # A badge may be keyed on an alias, so that it follows the alias
            # from one version of a column to the next.
            if unknown := sorted(set(badges) - columns - set(aliases)):
                report.errors.append(
                    f"badges.yaml refers to unknown field(s): {unknown}",
                )
            # Two shapes share this file: a badge map keyed on exact values,
            # and a threshold scale for a numeric column. Only the first needs
            # an entry in BADGE_RENDERED_COLUMNS, because only the first has a
            # cell type hardcoded in the renderer.
            scales = {
                name: entry
                for name, entry in badges.items()
                if schema.is_scale_config(entry)
            }
            badge_maps = set(badges) - set(scales)
            if unreachable := sorted(badge_maps - set(BADGE_RENDERED_COLUMNS)):
                report.warnings.append(
                    f"badges.yaml configures {unreachable}, which the table does "
                    f"not render as badges (renders: {list(BADGE_RENDERED_COLUMNS)})",
                )
            for name, entry in scales.items():
                report.errors.extend(schema.scale_problems(name, entry))
                meta = self._fields.get(aliases.get(name, name))
                if meta is not None and meta.value_type not in ("float", "integer"):
                    report.errors.append(
                        f"badges.yaml gives {name!r} a colour scale, but it is "
                        f"{meta.value_type or 'untyped'} rather than numeric; a "
                        f"threshold has nothing to compare against",
                    )

        links = self._load_optional_yaml(paths.external_links_path(self._resources))
        if isinstance(links, dict):
            referenced = {
                placeholder
                for link in links.get("links") or []
                if isinstance(link, dict)
                for placeholder in _PLACEHOLDER_RE.findall(str(link.get("url", "")))
            }
            if unknown := sorted(referenced - columns):
                report.errors.append(
                    f"external_links.yaml uses placeholder(s) that are not "
                    f"fields: {unknown}",
                )

        # Warnings, not errors: the webapp drops a bad entry and still starts.
        _, background_problems = load_backgrounds(
            paths.backgrounds_path(self._resources),
            columns,
        )
        report.warnings.extend(f"backgrounds.yaml: {p}" for p in background_problems)
        _, covariate_problems = load_covariates(
            paths.backgrounds_path(self._resources),
            columns,
        )
        report.warnings.extend(f"backgrounds.yaml: {p}" for p in covariate_problems)

        try:
            categories = schema.load_categories_yaml(
                paths.categories_path(self._resources),
                self._fields,
            )
        except schema.CategoriesYamlError as exc:
            report.errors.extend(f"categories.yaml: {p}" for p in exc.problems)
        else:
            if categories.undeclared:
                report.warnings.append(
                    f"categories.yaml does not declare "
                    f"{list(categories.undeclared)}, so those columns sort "
                    f"after every declared category and are not grouped",
                )
            if categories.unused:
                report.notes.append(
                    f"categories.yaml declares {list(categories.unused)}, "
                    f"which no field uses",
                )

    def _note_missing_markers(self, report: ValidationReport) -> None:
        affected: list[str] = []
        for name, meta in self._fields.items():
            if meta.value_type not in ("string", "categorical"):
                continue
            series = self._frame[name]
            if series.dtype == pl.Categorical:
                series = series.cast(pl.String)
            if series.dtype == pl.String and (series == MISSING_MARKER).sum():
                affected.append(name)
        if affected:
            report.notes.append(
                f'{len(affected)} field(s) use the literal string "{MISSING_MARKER}" '
                f"as a missing marker rather than null: {affected}",
            )

    @staticmethod
    def _load_optional_yaml(path: Path) -> object:
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    # ------------------------------------------------------------------- repr

    def __repr__(self) -> str:
        state = "modified" if self.is_dirty else "clean"
        return (
            f"<Database {self.n_rows:,} genes x {len(self)} fields "
            f"({state}) at {self._resources}>"
        )

    def _repr_html_(self) -> str:
        by_visibility = _tally(m.visibility for m in self._fields.values())
        by_type = _tally(m.value_type or "?" for m in self._fields.values())
        state = (
            "<span style='color:#c0392b'>modified</span>"
            if self.is_dirty
            else "<span style='color:#27ae60'>clean</span>"
        )
        rows = "".join(
            f"<tr><th style='text-align:left'>{label}</th><td>{value}</td></tr>"
            for label, value in (
                ("genes", f"{self.n_rows:,}"),
                ("fields", str(len(self))),
                ("visibility", by_visibility),
                ("value types", by_type),
                ("resources", str(self._resources)),
                ("state", state),
            )
        )
        return f"<b>GeneTrek database</b><table style='font-size:90%'>{rows}</table>"


_LIST_SCHEMA: dict[str, pl.DataType] = {
    "field": pl.String(),
    "fullname": pl.String(),
    "value_type": pl.String(),
    "visibility": pl.String(),
    "category": pl.String(),
    "priority": pl.Int64(),
    "id_type": pl.String(),
    "aliases": pl.String(),
    "n_null": pl.UInt32(),
    "n_unique": pl.UInt32(),
    "est_bytes": pl.Int64(),
    "pct_memory": pl.Float64(),
}


def _tally(values: Iterable[str]) -> str:
    counts = Counter(values)
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))


#: Column names used when a bare gene list is turned into a membership column.
_LIST_ID_COLUMN = "gene"
_LIST_VALUE_COLUMN = "member"


def _coerce_input(
    values: pl.DataFrame | pl.Series | Path | str | Iterable[str],
) -> tuple[pl.DataFrame, str | None]:
    """Normalise ``add_field``'s input to a frame, plus the source file name.

    A bare collection of identifiers becomes a two-column membership frame, which
    is what makes ``add_field(["SHANK3", ...], "my_list")`` mean "these genes are
    in the list, the rest are not".
    """
    if isinstance(values, pl.DataFrame):
        return values, None
    if isinstance(values, pl.Series):
        return _membership_frame(values), None
    if isinstance(values, Path):
        return _read_input_file(values)
    if isinstance(values, str):
        candidate = Path(values)
        if candidate.is_file() or candidate.suffix.lower() in _SEPARATORS:
            return _read_input_file(candidate)
        # Iterating a string would yield characters, which is never intended.
        msg = (
            f"{values!r} is neither an existing file nor a collection of "
            f"identifiers; pass a list, a Series, a DataFrame, or a file path"
        )
        raise DatabaseError(msg)
    return _membership_frame(pl.Series(list(values))), None


#: Header labels recognised at the top of a bare gene list, so the first gene is
#: not mistaken for a column name. Gene lists in the wild often have no header at
#: all, and consuming a real symbol as the header silently loses a gene.
_GENE_LIST_HEADERS = frozenset(
    {
        "symbol",
        "symbols",
        "gene",
        "genes",
        "gene_symbol",
        "gene_name",
        "hgnc",
        "hgnc_id",
        "ensg",
        "ensembl",
        "ensembl_gene_id",
        "ncbi",
        "ncbi_id",
        "entrez",
        "entrez_id",
        "id",
        "ids",
    },
)


def _read_input_file(path: Path) -> tuple[pl.DataFrame, str]:
    """Read a delimited gene-list or annotation file."""
    if not path.is_file():
        msg = f"input file not found: {path}"
        raise DatabaseError(msg)
    separator = _SEPARATORS.get(path.suffix.lower(), "\t")

    probe = pl.read_csv(
        path,
        separator=separator,
        has_header=False,
        n_rows=1,
        infer_schema_length=0,
        truncate_ragged_lines=True,
    )
    if probe.width < _MIN_INPUT_COLUMNS:
        # A one-column file is a gene list, not an annotation table. Read it
        # without a header and drop the first row only if it is a column label.
        frame = pl.read_csv(
            path,
            separator=separator,
            has_header=False,
            infer_schema_length=0,
            truncate_ragged_lines=True,
            new_columns=[_LIST_ID_COLUMN],
        )
        series = frame.to_series()
        if series.len() and str(series[0]).strip().lower() in _GENE_LIST_HEADERS:
            series = series.slice(1)
        return _membership_frame(series), path.name

    frame = pl.read_csv(
        path,
        separator=separator,
        infer_schema_length=10_000,
        truncate_ragged_lines=True,
    )
    return frame, path.name


def _membership_frame(identifiers: pl.Series) -> pl.DataFrame:
    """Build a two-column frame marking every listed identifier as a member.

    Dtypes are pinned explicitly so an empty list still joins cleanly instead of
    producing a Null-typed key column.
    """
    unique = identifiers.cast(pl.String).drop_nulls().unique(maintain_order=True)
    return pl.DataFrame(
        {
            _LIST_ID_COLUMN: pl.Series(unique.to_list(), dtype=pl.String),
            _LIST_VALUE_COLUMN: pl.Series([True] * unique.len(), dtype=pl.Boolean),
        },
    )


def _pick_columns(
    frame: pl.DataFrame,
    *,
    id_col: str | None,
    value_col: str | None,
) -> tuple[str, str]:
    """Resolve which columns hold the identifiers and the values.

    Defaults to the first two columns, which is how gene-list exports are almost
    always shaped.
    """
    if frame.width < _MIN_INPUT_COLUMNS:
        msg = "input needs at least two columns: identifiers and values"
        raise DatabaseError(msg)

    identifier = id_col or frame.columns[0]
    if identifier not in frame.columns:
        msg = f"id column {identifier!r} not in the input: {frame.columns}"
        raise DatabaseError(msg)

    if value_col is not None:
        if value_col not in frame.columns:
            msg = f"value column {value_col!r} not in the input: {frame.columns}"
            raise DatabaseError(msg)
        value = value_col
    else:
        remaining = [c for c in frame.columns if c != identifier]
        if len(remaining) > 1:
            msg = (
                f"the input has {frame.width} columns, so which one holds the "
                f"values is ambiguous; pass value_col (candidates: {remaining})"
            )
            raise DatabaseError(msg)
        value = remaining[0]

    if identifier == value:
        msg = "the identifier and value columns must differ"
        raise DatabaseError(msg)
    return identifier, value
