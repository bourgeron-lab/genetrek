"""The lookup endpoints: fields, genes, gene lists and identifier conversion.

Split out of :mod:`genetrek.api.routes` for size alone -- they register onto
that module's router and share its ``/api`` prefix, so nothing here is a
separate surface. The router must still be included before ``ui.run_with``
mounts NiceGUI at ``/``; see :mod:`genetrek.app`.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from genetrek import schema
from genetrek.db.field import field_stats
from genetrek.db.gene_index import GeneIndex, GeneIndexError
from genetrek.models.schemas import (
    ConversionResult,
    ConvertRequest,
    ConvertResponse,
    FieldCoverage,
    FieldDetailResponse,
    FieldGenesResponse,
    FieldListResponse,
    FieldSummary,
    FieldUniverseResponse,
    GeneGenesetsResponse,
    GeneMatch,
    GenePair,
    GeneResponse,
)
from genetrek.services import lookup

if TYPE_CHECKING:
    from genetrek.config.loader import AppConfig
    from genetrek.db.gene_index import ResolutionReport


#: The most identifiers one ``/api/convert`` call will resolve. A longer list
#: belongs in a file, and ``/api/download/database`` already serves the whole
#: table -- which is a smaller transfer than a 45,000-item query string.
MAX_CONVERT_IDS = 1000


def _conversion_results(
    df: pl.DataFrame,
    report: ResolutionReport,
    id_columns: dict[str, str],
) -> list[ConversionResult]:
    """Attach the requested identifier columns to a resolution report.

    A left join is used rather than a lookup per input, and an explicit row
    index carries the input order through it: the report is in the order the
    caller supplied, and a conversion that silently reorders its output is
    worse than one that fails.
    """
    key = report.key
    frame = report.frame.with_row_index("_pos")
    columns = list(dict.fromkeys([key, *id_columns.values()]))
    frame = frame.join(
        df.select(columns),
        on=key,
        how="left",
    ).sort("_pos")
    return [
        ConversionResult(
            input=row["input"],
            matched_by=row["matched_by"],
            note=row["note"] or "",
            ids={role: row.get(column) for role, column in id_columns.items()},
        )
        for row in frame.iter_rows(named=True)
    ]


def register_lookup_routes(
    router: APIRouter,
    config: AppConfig,
    resolver: tuple[GeneIndex | None, str] | None = None,
) -> None:
    """Register the lookup endpoints onto an existing ``/api`` router.

    ``resolver`` is :func:`lookup.build_gene_index`'s result, passed in when the
    application shares one resolver between the API and the UI; built here
    otherwise, which is what a test client wants.
    """
    # -- lookup ----------------------------------------------------------
    #
    # Built once, not on ``AppConfig``: the caller runs this once at startup,
    # so the resolver is created once and closed over exactly as ``config`` is.
    gene_index, index_problem = resolver or lookup.build_gene_index(config)

    def _require_index() -> GeneIndex:
        if gene_index is None:
            raise HTTPException(
                status_code=503,
                detail=f"gene resolution unavailable: {index_problem}",
            )
        return gene_index

    def _field(name: str) -> tuple[str, schema.FieldMeta]:
        """Resolve a field name or alias, or raise a 404.

        Unlike ``/api/data``, the lookup endpoints accept an alias: the short
        names all point at versioned gene lists, and a caller should not have
        to track ``_20260331`` suffixes across releases. The condition set when
        that was deferred was that the response must echo which real column it
        resolved to, which every model here does.
        """
        try:
            real = lookup.resolve_field_name(name, config.fields, config.field_aliases)
        except lookup.FieldNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return real, config.fields[real]

    def _id_column(id_type: str) -> str:
        try:
            return lookup.identifier_column(config.roles, id_type)
        except lookup.IdentifierRoleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _summary(name: str, *, n_genes: int | None = None) -> FieldSummary:
        return FieldSummary(
            **lookup.field_summary(name, config.fields[name]),
            n_genes=n_genes,
        )

    def _ambiguous_keys(query: str) -> list[str]:
        """Every gene an ambiguous identifier could have meant."""
        key_column = config.roles.primary_key
        if key_column is None:
            return []
        keys: list[str] = []
        for column in config.roles.identifiers.values():
            if column not in config.df.columns:
                continue
            matched = config.df.filter(
                pl.col(column).cast(pl.String).str.to_uppercase() == query.upper(),
            )
            keys.extend(str(value) for value in matched[key_column])
        return sorted(set(keys))

    def _match(query: str) -> GeneMatch:
        """Resolve one user-supplied identifier onto one gene."""
        report = _require_index().resolve([query])
        if report.frame.height == 0:
            raise HTTPException(status_code=404, detail=f"no gene matches {query!r}")
        row = report.frame.row(0, named=True)
        if row["matched_by"] == "ambiguous":
            candidates = ", ".join(_ambiguous_keys(query)) or "several genes"
            raise HTTPException(
                status_code=409,
                detail=f"{query!r} is ambiguous; it matches {candidates}",
            )
        if (key := row[report.key]) is None:
            raise HTTPException(status_code=404, detail=f"no gene matches {query!r}")
        return GeneMatch(
            input=row["input"],
            matched_by=row["matched_by"],
            note=row["note"] or "",
            key=str(key),
            key_column=report.key,
        )

    def _row(match: GeneMatch) -> dict[str, Any]:
        try:
            row = lookup.gene_row(config.df, config.roles, match.key)
        except lookup.AmbiguousGeneError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"no gene matches {match.input!r}",
            )
        return row

    @router.get("/fields", response_model=FieldListResponse)
    def list_fields(
        category: str | None = Query(None),
        subcategory: str | None = Query(None),
        value_type: str | None = Query(None),
        visibility: str | None = Query(None),
        include_hidden: bool = Query(
            default=False,
            description="List hidden columns too. They are always reachable by name.",
        ),
        include_counts: bool = Query(
            default=False,
            description="Add n_genes per field; costs one pass over the table.",
        ),
    ) -> FieldListResponse:
        """List the columns, in the order the table shows them."""
        selected = [
            name
            for name, meta in config.fields.items()
            if (include_hidden or meta.visibility != "hidden")
            and (visibility is None or meta.visibility == visibility)
            and (category is None or meta.category == category)
            and (subcategory is None or meta.subcategory == subcategory)
            and (value_type is None or meta.value_type == value_type)
        ]
        # Hidden columns have no rank -- `in_display_order` puts anything it
        # does not know last, keeping its relative order, which is parquet
        # order here because `config.fields` is keyed on `common_columns`.
        ordered = schema.in_display_order(selected, config.column_order)
        counts = lookup.value_counts(config.df, ordered) if include_counts else {}
        return FieldListResponse(
            total=len(ordered),
            fields=[_summary(name, n_genes=counts.get(name)) for name in ordered],
        )

    @router.get("/fields/{field}", response_model=FieldDetailResponse)
    def get_field(field: str) -> FieldDetailResponse:
        """One column's metadata and how much of the table it covers."""
        real, meta = _field(field)
        stats = field_stats(config.df[real], real, meta)
        return FieldDetailResponse(
            field=real,
            requested_as=field,
            metadata=_summary(real),
            coverage=FieldCoverage(
                dtype=stats.dtype,
                n_rows=stats.n_rows,
                n_null=stats.n_null,
                n_measured=stats.n_measured,
                n_unique=stats.n_unique,
                n_true=stats.n_true,
                n_false=stats.n_false,
                minimum=stats.minimum,
                maximum=stats.maximum,
                mean=stats.mean,
                median=stats.median,
                std=stats.std,
                top_values=stats.top_values or None,
                n_missing_marker=stats.n_missing_marker,
            ),
        )

    def _genes_frame(
        field: str,
        id_type: str,
        *,
        drop_empty: bool,
        limit: int | None,
        offset: int,
    ) -> tuple[str, schema.FieldMeta, str, pl.DataFrame, int, int]:
        """Shared body of the two gene-pair endpoints."""
        real, meta = _field(field)
        id_column = _id_column(id_type)
        pairs, n_without_identifier = lookup.column_pairs(
            config.df,
            real,
            id_column,
            drop_empty=drop_empty,
        )
        total = pairs.height
        if offset or limit is not None:
            pairs = pairs.slice(offset, limit)
        return real, meta, id_column, pairs, total, n_without_identifier

    @router.get("/fields/{field}/genes", response_model=FieldGenesResponse)
    def get_field_genes(
        field: str,
        id_type: str = Query(
            "symbol",
            description="Identifier role: symbol, hgnc, ensg, ncbi or omim.",
        ),
        drop_empty: bool = Query(
            default=False,
            description=(
                "Omit genes with no value. For a boolean column that omits "
                "False as well, so the result is the gene list's members."
            ),
        ),
        limit: int | None = Query(None, ge=1),
        offset: int = Query(0, ge=0),
    ) -> FieldGenesResponse:
        """One column as gene/value pairs.

        With ``drop_empty=true`` on a boolean column this is the membership of
        a gene list, which is the question most callers are really asking.
        """
        real, meta, id_column, pairs, total, unnamed = _genes_frame(
            field,
            id_type,
            drop_empty=drop_empty,
            limit=limit,
            offset=offset,
        )
        return FieldGenesResponse(
            field=real,
            requested_as=field,
            id_type=id_type,
            id_column=id_column,
            value_type=meta.value_type,
            drop_empty=drop_empty,
            total=total,
            n_without_identifier=unnamed,
            genes=[GenePair(**pair) for pair in pairs.to_dicts()],
        )

    @router.get("/fields/{field}/genes/tsv")
    def get_field_genes_tsv(
        field: str,
        id_type: str = Query("symbol"),
        drop_empty: bool = Query(default=False),
    ) -> StreamingResponse:
        """The same pairs as a TSV, which is what a gene list is usually for."""
        real, _meta, id_column, pairs, _total, _unnamed = _genes_frame(
            field,
            id_type,
            drop_empty=drop_empty,
            limit=None,
            offset=0,
        )
        buffer = io.BytesIO()
        # A null is "not measured": written as NA so it cannot be read as an
        # empty string or, for a boolean, confused with false.
        pairs.rename({"gene": id_column, "value": real}).write_csv(
            buffer,
            separator="\t",
            null_value="NA",
        )
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="text/tab-separated-values",
            headers={
                "Content-Disposition": f"attachment; filename=genetrek-{real}.tsv",
            },
        )

    def _universe(field: str, id_type: str) -> FieldUniverseResponse:
        real, meta = _field(field)
        id_column = _id_column(id_type)
        genes, n_not_measured, n_unnamed = lookup.measured_genes(
            config.df,
            real,
            id_column,
        )
        return FieldUniverseResponse(
            field=real,
            requested_as=field,
            universe=meta.universe,
            id_type=id_type,
            id_column=id_column,
            total=len(genes),
            n_not_measured=n_not_measured,
            n_without_identifier=n_unnamed,
            genes=genes,
        )

    @router.get("/fields/{field}/universe", response_model=FieldUniverseResponse)
    def get_field_universe(
        field: str,
        id_type: str = Query(
            "symbol",
            description="Identifier role: symbol, hgnc, ensg, ncbi or omim.",
        ),
    ) -> FieldUniverseResponse:
        """The genes this column measured -- its non-null rows.

        The background to test a gene list against elsewhere: a measured
        ``False`` is in it, a gene the source never measured is not.
        """
        return _universe(field, id_type)

    @router.get("/fields/{field}/universe/tsv")
    def get_field_universe_tsv(
        field: str,
        id_type: str = Query("symbol"),
    ) -> StreamingResponse:
        """The same universe, one identifier per line under a header."""
        result = _universe(field, id_type)
        body = "\n".join([result.id_column, *result.genes]) + "\n"
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")),
            media_type="text/tab-separated-values",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=genetrek-{result.field}-universe.tsv"
                ),
            },
        )

    @router.get("/genes/{gene}", response_model=GeneResponse)
    def get_gene(
        gene: str,
        columns: str | None = Query(
            None,
            description=(
                "Comma-separated columns. Aliases are resolved, and a hidden "
                "column is reachable here by name."
            ),
        ),
        include_hidden: bool = Query(default=False),
    ) -> GeneResponse:
        """One gene, addressed by any identifier it carries."""
        match = _match(gene)
        row = _row(match)

        if columns:
            requested = [name.strip() for name in columns.split(",") if name.strip()]
            selected = [_field(name)[0] for name in requested]
        else:
            selected = [
                name
                for name, meta in config.fields.items()
                if include_hidden or meta.visibility != "hidden"
            ]
        ordered = schema.in_display_order(selected, config.column_order)

        id_columns = config.roles.identifiers
        return GeneResponse(
            gene=match,
            id_columns=id_columns,
            identifiers={role: row.get(col) for role, col in id_columns.items()},
            values={name: row.get(name) for name in ordered},
        )

    @router.get("/genes/{gene}/genesets", response_model=GeneGenesetsResponse)
    def get_gene_genesets(
        gene: str,
        category: str | None = Query(None),
    ) -> GeneGenesetsResponse:
        """Every boolean column that is True for this gene.

        Every boolean column counts, so curated gene lists arrive alongside
        derived constraint flags -- each entry carries its category, and its
        ``derived_from`` when it is a roll-up of its own siblings, so a caller
        can filter or collapse. Note that versions of one list coexist: a gene
        on the current high-confidence NDD list is usually on the two before
        it as well, and an alias always names the current one.
        """
        match = _match(gene)
        row = _row(match)
        members = lookup.memberships(row, config.all_boolean_columns)
        if category is not None:
            members = [
                name for name in members if config.fields[name].category == category
            ]
        ordered = schema.in_display_order(members, config.column_order)
        counts = lookup.value_counts(config.df, ordered)
        return GeneGenesetsResponse(
            gene=match,
            total=len(ordered),
            genesets=[_summary(name, n_genes=counts.get(name)) for name in ordered],
        )

    @router.get("/convert", response_model=ConvertResponse)
    def convert(
        ids: str = Query(
            ...,
            description="Identifiers, separated by commas, whitespace or newlines.",
        ),
        from_id_type: str = Query(
            "auto",
            alias="from",
            description="Identifier role of the input, or 'auto' to detect it.",
        ),
        to: str | None = Query(
            None,
            description="Identifier roles to return. Defaults to every role.",
        ),
    ) -> ConvertResponse:
        """Convert identifiers onto the roles this database carries.

        The identifiers travel in the URL, so they reach the access logs. For a
        list that should not -- a visitor's own -- use ``POST /api/convert``.
        """
        values = [token for token in re.split(r"[,;\s]+", ids) if token]
        if not values:
            raise HTTPException(status_code=400, detail="no identifiers given")
        if len(values) > MAX_CONVERT_IDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(values)} identifiers exceeds the {MAX_CONVERT_IDS} limit; "
                    f"use POST /api/convert, or /api/download/database for a "
                    f"whole-table join"
                ),
            )
        wanted = (
            [role.strip() for role in to.split(",") if role.strip()] if to else None
        )
        return _convert(values, from_id_type, wanted)

    @router.post("/convert", response_model=ConvertResponse)
    def convert_posted(body: ConvertRequest) -> ConvertResponse:
        """``GET /api/convert`` with the identifiers in the body, not the URL.

        A request body is not written to access logs, so this is the form for a
        list that should leave no trace; it also takes up to 50,000 ids.
        """
        values = [v.strip() for v in body.ids if v.strip()]
        if not values:
            raise HTTPException(status_code=400, detail="no identifiers given")
        return _convert(values, body.from_id_type, body.to)

    def _convert(
        values: list[str],
        from_id_type: str,
        wanted_roles: list[str] | None,
    ) -> ConvertResponse:
        available = config.roles.identifiers
        wanted = wanted_roles or list(available)
        id_columns = {role: _id_column(role) for role in wanted}

        index = _require_index()
        try:
            report = index.resolve(values, from_id_type)
        except (GeneIndexError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return ConvertResponse(
            requested_id_type=from_id_type,
            id_type=report.id_type,
            detected=report.detected,
            n_input=report.n_input,
            n_resolved=report.n_resolved,
            counts=report.counts,
            symbol_coverage=index.symbol_coverage,
            warnings=list(report.warnings),
            id_columns=id_columns,
            results=_conversion_results(config.df, report, id_columns),
        )
