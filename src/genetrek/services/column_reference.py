"""The column reference: every column's metadata as one table.

Backs the ``/columns`` page and its TSV download. Built from the loaded field
metadata, category order and parquet on every request rather than written by
hand, so it lists exactly what the database holds -- including the hidden
columns that only the API exposes, which the column selector never shows.

No NiceGUI here, for the reason :mod:`genetrek.services.versions` has none:
the rows are plain data, and the page is only one way of showing them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from genetrek import schema
from genetrek.services.lookup import value_counts

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from genetrek.schema import CategoryOrder, FieldMeta


def format_date(value: str) -> str:
    """Render a ``YYYYMMDD`` metadata date as ``YYYY-MM-DD``.

    Anything not of that shape is passed through: the field is free text, and a
    date nobody can parse is still better shown than hidden.
    """
    if len(value) == 8 and value.isdigit():  # noqa: PLR2004
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def doi_url(value: str) -> str:
    """The address a DOI resolves at. A DOI already written as a URL is kept."""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://doi.org/{value}"


def _type_label(meta: FieldMeta) -> str:
    """Value type, qualified by genome assembly for coordinate columns."""
    value_type = meta.value_type or ""
    if value_type and meta.assembly:
        return f"{value_type} ({meta.assembly})"
    return value_type or meta.assembly


def column_reference_rows(
    fields: Mapping[str, FieldMeta],
    categories: CategoryOrder,
    df: pl.DataFrame,
) -> list[dict[str, Any]]:
    """One row per column, in the order the table shows them.

    Hidden columns are included and flagged ``api_only``: they are queryable
    through ``/api/genes/{gene}?columns=``, so the reference is where a reader
    finds out they exist.

    Args:
        fields: Every column's metadata -- ``AppConfig.fields``, hidden ones
            included.
        categories: The declared category order.
        df: The table, for the counts.

    Returns:
        JSON-ready dicts. ``n_genes`` counts members for a gene list and
        non-empty values otherwise (:func:`~genetrek.services.lookup.value_counts`,
        as ``/api/fields?include_counts=true``). ``n_measured`` is the non-null
        rows, set only when that is fewer than every gene -- the same rule the
        column information dialog uses -- and ``None`` otherwise.
    """
    ordered = [name for name in schema.order_fields(fields, categories) if name in df]
    counts = value_counts(df, ordered)
    non_null = (
        df.select(pl.col(name).is_not_null().sum() for name in ordered).row(
            0, named=True
        )
        if ordered
        else {}
    )
    rows: list[dict[str, Any]] = []
    for rank, name in enumerate(ordered):
        meta = fields[name]
        measured = int(non_null[name])
        rows.append(
            {
                "rank": rank,
                "key": name,
                "label": meta.fullname,
                "category": meta.category or categories.uncategorized_label,
                "subcategory": meta.subcategory,
                "description": meta.description,
                "type": _type_label(meta),
                "source": meta.source,
                "doi": meta.doi,
                "doi_url": doi_url(meta.doi) if meta.doi else "",
                "licence": meta.licence,
                "updated": format_date(meta.update),
                "universe": meta.universe,
                "n_genes": counts[name],
                "n_measured": measured if measured < df.height else None,
                "api_only": meta.visibility == "hidden",
            },
        )
    return rows


#: TSV column -> row key, in file order.
_TSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("column", "key"),
    ("label", "label"),
    ("category", "category"),
    ("subcategory", "subcategory"),
    ("description", "description"),
    ("type", "type"),
    ("source", "source"),
    ("doi", "doi"),
    ("licence", "licence"),
    ("updated", "updated"),
    ("universe", "universe"),
    ("n_genes", "n_genes"),
    ("n_measured", "n_measured"),
    ("api_only", "api_only"),
)


def _tsv_cell(value: object) -> str:
    """One TSV cell: ``NA`` for missing, and no tab or newline from prose."""
    if value is None:
        return "NA"
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).split())


def column_reference_tsv(rows: Sequence[Mapping[str, Any]]) -> str:
    """The reference as a TSV file, one line per column plus a header."""
    lines = ["\t".join(header for header, _ in _TSV_COLUMNS)]
    lines += ["\t".join(_tsv_cell(row[key]) for _, key in _TSV_COLUMNS) for row in rows]
    return "\n".join(lines) + "\n"
