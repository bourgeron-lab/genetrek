"""Gene and field lookups, shared by the HTTP API and the UI dialogs.

Like :mod:`genetrek.services.search`, every function here takes a frame and a
:class:`~genetrek.schema.Roles` rather than an ``AppConfig``, so nothing in this
module depends on how the application is composed and all of it is testable
without a browser or a client.

No function names a column literally. Which column carries the symbol, or the
primary key, is a role -- see :func:`genetrek.schema.resolve_roles` -- because
the real database keys on ``hgnc_id`` and a database built by ``genetrek-db
init`` keys on ``ncbi_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from genetrek import paths, schema
from genetrek.db.gene_index import GeneIndex, GeneIndexError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from genetrek.config.loader import AppConfig
    from genetrek.schema import FieldMeta, Roles


class FieldNotFoundError(LookupError):
    """No field or alias goes by the requested name."""


class IdentifierRoleError(LookupError):
    """The database has no column filling the requested identifier role."""


class AmbiguousGeneError(LookupError):
    """One identifier names several genes.

    Carries the candidate keys rather than picking one: on the real database
    both the symbol and the primary key are unique across all 45,045 rows, so
    reaching this means the data changed shape and the caller should be told,
    not handed an arbitrary row.
    """

    def __init__(self, query: str, keys: Sequence[str]) -> None:
        self.query = query
        self.keys = list(keys)
        super().__init__(
            f"{query!r} matches {len(self.keys)} genes: {', '.join(self.keys)}",
        )


def resolve_field_name(
    name: str,
    fields: Mapping[str, FieldMeta],
    aliases: Mapping[str, str],
) -> str:
    """Return the real field name for a field name or one of its aliases.

    Raises:
        FieldNotFoundError: If the name is neither a field nor an alias.
    """
    if name in fields:
        return name
    if (target := aliases.get(name)) is not None:
        return target
    msg = f"unknown field: {name!r}"
    raise FieldNotFoundError(msg)


def field_summary(name: str, meta: FieldMeta) -> dict[str, Any]:
    """One field's metadata as a JSON-ready dict.

    ``FieldMeta`` has no ``name`` key -- the column name is the mapping key in
    ``fields_description.yaml`` -- so it is injected here rather than left for
    every caller to remember.
    """
    return {
        "name": name,
        "fullname": meta.fullname,
        "description": meta.description,
        "category": meta.category,
        "subcategory": meta.subcategory,
        "visibility": meta.visibility,
        "value_type": meta.value_type,
        "id_type": meta.id_type,
        "assembly": meta.assembly,
        "source": meta.source,
        "doi": meta.doi,
        "licence": meta.licence,
        "universe": meta.universe,
        "update": meta.update,
        "aliases": list(meta.aliases),
        "derived_op": meta.derived_op,
        "derived_from": list(meta.derived_from),
    }


def identifier_column(roles: Roles, id_type: str) -> str:
    """The column filling an identifier role.

    Raises:
        IdentifierRoleError: If this database has no column for that role. The
            message lists the roles it does have, so a caller who guessed
            ``ensg`` against a database without one gets told what to ask for.
    """
    identifiers = roles.identifiers
    if (column := identifiers.get(id_type)) is not None:
        return column
    available = ", ".join(identifiers) or "none"
    msg = f"no {id_type!r} column in this database; available roles: {available}"
    raise IdentifierRoleError(msg)


def drop_empty_rows(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    """Drop the rows of ``frame`` that carry no value for ``column``.

    "No value" is type-dependent, and the two special cases are not
    embellishments:

    * For a **boolean** column, ``False`` is dropped along with null. The
      useful reading of a gene list is its members, which is the same rule
      :meth:`genetrek.db.field.BoundField.to_tsv` already applies -- 28 of the
      32 boolean columns on the real database have no nulls at all, so treating
      only null as missing would drop nothing.
    * For a **string** column, the literal ``"None"`` is dropped. It is a
      missing-value marker that arrived with the imported data, which is why
      :class:`~genetrek.db.field.FieldStats` counts it separately.
    """
    dtype = frame.schema[column]
    frame = frame.filter(pl.col(column).is_not_null())
    if dtype == pl.Boolean:
        return frame.filter(pl.col(column))
    if dtype == pl.String:
        return frame.filter(pl.col(column) != schema.MISSING_MARKER)
    return frame


def column_pairs(
    df: pl.DataFrame,
    column: str,
    id_column: str,
    *,
    drop_empty: bool = False,
) -> tuple[pl.DataFrame, int]:
    """Pair an identifier with one column's values, and count the unnameable.

    Returns the frame -- two columns, ``gene`` and ``value`` -- plus how many
    rows were dropped because the gene has no value for the chosen identifier.
    A pair whose ``gene`` is null names nothing, so it is never returned; on
    the real database 5,700-odd genes have no OMIM id, and asking for
    ``id_type=omim`` should not hand back thousands of anonymous values.

    The columns are renamed so the response shape does not change with the
    identifier asked for; which real column ``gene`` came from is reported
    alongside rather than inferred from the key. ``gene`` is cast to string
    because an identifier column may be an integer (``ncbi_id``) or a prefixed
    string (``hgnc_id``) depending on the database.
    """
    # Selecting the same column twice is a Polars error, and it is reachable:
    # `/api/fields/hgnc_symbol/genes?id_type=symbol` asks for exactly that.
    frame = (
        df.select(pl.col(column).cast(pl.String).alias("gene"), pl.col(column))
        if column == id_column
        else df.select(pl.col(id_column).cast(pl.String).alias("gene"), pl.col(column))
    )
    if drop_empty:
        frame = drop_empty_rows(frame, column)
    named = frame.filter(pl.col("gene").is_not_null())
    return named.rename({column: "value"}), frame.height - named.height


def measured_genes(
    df: pl.DataFrame,
    column: str,
    id_column: str,
) -> tuple[list[str], int, int]:
    """The genes ``column`` measured, named by ``id_column``.

    A gene is measured when its value is not null -- whatever the value, so a
    measured ``False`` or ``0`` is in, and a null ("not measured") is out. This
    is the universe a gene list must be tested against outside GeneTrek.

    Returns:
        The identifiers (unique, in table order), how many genes were not
        measured, and how many measured genes have no ``id_column`` value.
    """
    measured = df.filter(pl.col(column).is_not_null())
    named = measured[id_column].drop_nulls().cast(pl.String)
    genes = named.unique(maintain_order=True).to_list()
    n_unnamed = measured.height - named.len()
    return genes, df.height - measured.height, n_unnamed


def gene_keys(df: pl.DataFrame, roles: Roles, query: str) -> list[str]:
    """Every primary-key value whose row matches ``query`` on the key column."""
    key_column = roles.primary_key
    if key_column is None or key_column not in df.columns:
        return []
    matched = df.filter(df[key_column].cast(str) == query)
    return [str(value) for value in matched[key_column]]


def gene_row(df: pl.DataFrame, roles: Roles, key: str) -> dict[str, Any] | None:
    """The single row a primary-key value names, or ``None``.

    Raises:
        AmbiguousGeneError: If the key names more than one row, which means the
            primary key is not unique and nothing downstream can be trusted to
            have picked the right gene.
    """
    key_column = roles.primary_key
    if key_column is None or key_column not in df.columns:
        return None
    matched = df.filter(df[key_column].cast(str) == key)
    if matched.height == 0:
        return None
    if matched.height > 1:
        raise AmbiguousGeneError(key, [str(v) for v in matched[key_column]])
    return matched.row(0, named=True)


def memberships(row: Mapping[str, Any], boolean_columns: Sequence[str]) -> list[str]:
    """The boolean columns that are ``True`` for one gene.

    Order follows ``boolean_columns``, so the caller decides whether that is
    display order or parquet order.
    """
    return [column for column in boolean_columns if row.get(column) is True]


def value_counts(df: pl.DataFrame, columns: Sequence[str]) -> dict[str, int]:
    """How many genes carry a value for each column, in one pass.

    Uses the same definition of "carries a value" as :func:`drop_empty_rows`,
    so a gene list's count is its membership and not its row count.
    """
    if not columns:
        return {}
    expressions = [
        (
            pl.col(column).fill_null(value=False).sum()
            if df.schema[column] == pl.Boolean
            else (
                (
                    pl.col(column).is_not_null()
                    & (pl.col(column) != schema.MISSING_MARKER)
                ).sum()
                if df.schema[column] == pl.String
                else pl.col(column).is_not_null().sum()
            )
        ).alias(column)
        for column in columns
    ]
    counted = df.select(expressions).row(0, named=True)
    return {column: int(counted[column]) for column in columns}


def build_gene_index(config: AppConfig) -> tuple[GeneIndex | None, str]:
    """Build the resolver once, or explain why it could not be built.

    Shared by the lookup endpoints and the UI's list dialogs, so a pasted
    ``KIAA1650`` resolves to ``SHANK3`` there exactly as ``/api/convert`` does.

    ``GeneIndex`` refuses to build without a primary key, and unlike
    ``Database`` the webapp has no ``ncbi_id`` fallback to substitute. That is
    a half-migrated database, which must still serve pages -- so the failure is
    carried to the affected endpoints as a 503 rather than raised here, where
    it would stop the process from starting at all.
    """
    try:
        index = GeneIndex.from_frame(config.df, config.roles)
    except GeneIndexError as exc:
        print(f"Warning: gene resolution unavailable: {exc}")  # noqa: T201
        return None, str(exc)

    # Retired symbols only resolve with an alias source, and the container has
    # no HGNC complete set -- that is what the shipped index is for. Absent or
    # mismatched, it costs alias coverage and nothing else, so the failure is a
    # warning and `symbol_coverage` tells a caller what they actually got.
    alias_index = paths.aliases_path(config.resources)
    if alias_index.is_file():
        try:
            index = index.with_alias_index(alias_index)
        except GeneIndexError as exc:
            print(f"Warning: {exc}")  # noqa: T201
    else:
        print(  # noqa: T201
            f"Warning: no alias index at {alias_index}; retired gene symbols "
            f"will not resolve. Run `genetrek-db build-aliases`.",
        )

    # Each id type builds its lookup table on first use. Paying for all of
    # them here costs ~25 ms once, against the first request of each kind
    # paying it while a user waits.
    for id_type in index.available_id_types:
        index.resolve([], id_type)
    return index, ""


def symbol_resolver(
    index: GeneIndex | None,
) -> Callable[[Sequence[str]], list[str]] | None:
    """The search's symbol resolver: symbols -> primary keys, via ``index``.

    Gives the table search the alias and previous-symbol layers the list
    dialogs and ``/api/convert`` already use, so ``KIAA1109`` finds ``BLTP1``
    in all three. ``None`` without an index; the search then matches current
    symbols only.
    """
    if index is None:
        return None

    def resolve(symbols: Sequence[str]) -> list[str]:
        if not symbols:
            return []
        # Detected, as the list dialogs do: only symbol-shaped tokens get here.
        report = index.resolve(list(symbols))
        return report.resolved()[report.key].cast(pl.String).to_list()

    return resolve
