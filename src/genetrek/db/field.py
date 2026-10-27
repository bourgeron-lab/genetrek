"""Field objects: metadata on its own, and metadata bound to its column.

``Field`` is standalone and serialisable -- safe to keep, export, or pass around.
``BoundField`` adds a reference to the owning :class:`~genetrek.db.database.Database`
so the values are reachable and metadata edits are staged on the database itself.
Neither holds a copy of the column, so a field object can never go stale relative
to the table it describes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any

import polars as pl

from genetrek.schema import MISSING_MARKER, FieldMeta

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from genetrek.db.database import Database

#: How many distinct values ``stats()`` reports for string and categorical fields.
TOP_VALUES = 10


@dataclass(frozen=True)
class FieldStats:
    """A type-appropriate summary of one column's values.

    Only the block matching the field's value type is populated; the rest stay
    ``None``. Use :meth:`rows` to render whatever applies.
    """

    name: str
    value_type: str | None
    dtype: str
    n_rows: int
    n_null: int
    n_unique: int

    # Boolean fields.
    n_true: int | None = None
    n_false: int | None = None

    # Integer and float fields.
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None

    # String and categorical fields.
    top_values: list[tuple[str, int]] | None = None
    n_missing_marker: int | None = None

    @property
    def n_measured(self) -> int:
        """Rows with a value: the column's coverage universe."""
        return self.n_rows - self.n_null

    @property
    def pct_null(self) -> float:
        """Null share as a percentage, or ``0.0`` for an empty table."""
        return 100.0 * self.n_null / self.n_rows if self.n_rows else 0.0

    def rows(self) -> list[tuple[str, str]]:
        """Return the populated statistics as label/value pairs, ready to print."""
        out: list[tuple[str, str]] = [
            ("dtype", self.dtype),
            ("rows", f"{self.n_rows:,}"),
            ("nulls", f"{self.n_null:,} ({self.pct_null:.1f}%)"),
            ("measured", f"{self.n_measured:,}"),
            ("distinct", f"{self.n_unique:,}"),
        ]
        if self.n_true is not None and self.n_false is not None:
            share = 100.0 * self.n_true / self.n_rows if self.n_rows else 0.0
            out.append(("true", f"{self.n_true:,} ({share:.2f}%)"))
            out.append(("false", f"{self.n_false:,}"))
        for label, value in (
            ("min", self.minimum),
            ("max", self.maximum),
            ("mean", self.mean),
            ("median", self.median),
            ("std", self.std),
        ):
            if value is not None:
                out.append((label, f"{value:,.4g}"))
        if self.n_missing_marker:
            share = 100.0 * self.n_missing_marker / self.n_rows if self.n_rows else 0.0
            out.append(
                (
                    f'literal "{MISSING_MARKER}"',
                    f"{self.n_missing_marker:,} ({share:.1f}%)",
                ),
            )
        if self.top_values:
            out.extend((f"  {name}", f"{count:,}") for name, count in self.top_values)
        return out


@dataclass
class Field:
    """A database column's metadata, independent of the data itself."""

    name: str
    meta: FieldMeta = dataclass_field(repr=False)

    def touch(self) -> None:
        """Set the metadata ``update`` date to today."""
        self.meta.touch()

    def apply(self, changes: Mapping[str, Any]) -> None:
        """Update several metadata keys at once, validating the result.

        Raises:
            ValueError: If a key is unknown or a value is invalid, in which case
                nothing is changed.
        """
        self.meta.apply(changes)

    def to_yaml(self) -> str:
        """Render as the YAML fragment used in ``fields_description.yaml``."""
        return self.meta.to_yaml(self.name)

    def to_json(self, *, indent: int = 2) -> str:
        """Render the metadata as JSON, keyed by field name."""
        return json.dumps({self.name: self.meta.model_dump()}, indent=indent)

    def save_metadata(self, path: Path) -> None:
        """Write this field's metadata to a file, as YAML or JSON by suffix."""
        text = self.to_json() if path.suffix.lower() == ".json" else self.to_yaml()
        path.write_text(text, encoding="utf-8")

    def describe(self) -> str:
        """Return a human-readable multi-line metadata summary."""
        payload = self.meta.model_dump()
        width = max(len(key) for key in payload)
        lines = [f"{self.name}"]
        lines.extend(
            f"  {key:<{width}}  {'' if value is None else value}"
            for key, value in payload.items()
        )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.describe()

    def _repr_html_(self) -> str:
        """Render as a table in Jupyter."""
        rows = "".join(
            f"<tr><th style='text-align:left'>{key}</th>"
            f"<td>{'' if value is None else value}</td></tr>"
            for key, value in self.meta.model_dump().items()
        )
        return (
            f"<b><code>{self.name}</code></b>"
            f"<table style='font-size:90%'>{rows}</table>"
        )


class BoundField(Field):
    """A field attached to its database, giving access to the column values.

    Metadata edits go straight through to the database's staged state, so::

        db["score"].meta.visibility = "primary"

    is a pending change that ``db.save()`` will write.
    """

    def __init__(self, name: str, meta: FieldMeta, database: Database) -> None:
        super().__init__(name=name, meta=meta)
        self._database = database

    @property
    def values(self) -> pl.Series:
        """The column, as a Polars Series."""
        return self._database.frame[self.name]

    @property
    def dtype(self) -> pl.DataType:
        """The column's Polars dtype."""
        return self._database.frame.schema[self.name]

    def estimated_size(self) -> int:
        """In-memory size of the column in bytes."""
        return int(self.values.estimated_size())

    def to_frame(self, id_col: str | None = None) -> pl.DataFrame:
        """Return a two-column frame of an identifier plus this field.

        Args:
            id_col: Identifier column to pair the values with. Defaults to
                whichever column fills the ``symbol`` role, falling back to the
                primary key -- not the literal name ``symbol``, which a database
                built by ``genetrek-db init`` does not have.

        Raises:
            KeyError: If ``id_col`` is not a column of the database.
        """
        roles = self._database.roles
        column = id_col or roles.symbol or self._database.primary_key
        if column not in self._database.frame.columns:
            msg = f"unknown identifier column: {column!r}"
            raise KeyError(msg)
        return self._database.frame.select(column, self.name)

    def to_tsv(
        self,
        path: Path,
        *,
        id_col: str | None = None,
        drop_nulls: bool = False,
    ) -> int:
        """Write an identifier/value TSV and return the number of rows written.

        Args:
            path: Destination file.
            id_col: Identifier column to pair the values with. Defaults to the
                ``symbol`` role, then the primary key.
            drop_nulls: Omit rows where this field is null. For boolean fields,
                also omit rows that are ``False`` -- the useful export of a gene
                list is its members.
        """
        frame = self.to_frame(id_col)
        if drop_nulls:
            frame = frame.filter(pl.col(self.name).is_not_null())
            if self.dtype == pl.Boolean:
                frame = frame.filter(pl.col(self.name))
        # "NA", not an empty cell: a null is "not measured", and an empty cell
        # reads as "no value" to most tools that later load the file.
        frame.write_csv(path, separator="\t", null_value="NA")
        return frame.height

    def stats(self) -> FieldStats:
        """Compute a summary appropriate to this field's type."""
        return field_stats(self.values, self.name, self.meta)

    def __str__(self) -> str:
        stats = "\n".join(
            f"  {label:<20}  {value}" for label, value in self.stats().rows()
        )
        return f"{self.describe()}\n{stats}"

    def _repr_html_(self) -> str:
        rows = "".join(
            f"<tr><th style='text-align:left'>{label}</th><td>{value}</td></tr>"
            for label, value in self.stats().rows()
        )
        return super()._repr_html_() + f"<table style='font-size:90%'>{rows}</table>"


def field_stats(series: pl.Series, name: str, meta: FieldMeta) -> FieldStats:
    """Summarise one column, choosing the block that fits its type.

    Takes the Series rather than a :class:`BoundField` so the webapp can call it
    with a column of ``AppConfig.df``; opening a :class:`~genetrek.db.database.Database`
    in a request handler would re-read the parquet and drag the whole mutation
    surface along with it.
    """
    dtype = series.dtype
    n_rows = series.len()
    n_null = series.null_count()
    common: dict[str, Any] = {
        "name": name,
        "value_type": meta.value_type,
        "dtype": str(dtype),
        "n_rows": n_rows,
        "n_null": n_null,
        "n_unique": series.n_unique(),
    }

    if dtype == pl.Boolean:
        n_true = int(series.sum() or 0)
        return FieldStats(**common, n_true=n_true, n_false=n_rows - n_null - n_true)

    if dtype.is_numeric():
        return FieldStats(
            **common,
            minimum=_as_float(series.min()),
            maximum=_as_float(series.max()),
            mean=_as_float(series.mean()),
            median=_as_float(series.median()),
            std=_as_float(series.std()),
        )

    return FieldStats(
        **common,
        top_values=_top_values(series),
        n_missing_marker=_count_missing_marker(series),
    )


def _as_float(value: object) -> float | None:
    """Coerce an aggregate to float, tolerating nulls and non-numeric results."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _top_values(series: pl.Series) -> list[tuple[str, int]]:
    counts = series.value_counts(sort=True).head(TOP_VALUES)
    value_col, count_col = counts.columns[0], counts.columns[1]
    return [
        (
            "<null>" if row[value_col] is None else str(row[value_col]),
            int(row[count_col]),
        )
        for row in counts.iter_rows(named=True)
    ]


def _count_missing_marker(series: pl.Series) -> int:
    """Count values equal to the literal ``"None"`` string marker."""
    if series.dtype == pl.Categorical:
        series = series.cast(pl.String)
    if series.dtype != pl.String:
        return 0
    return int((series == MISSING_MARKER).sum())
