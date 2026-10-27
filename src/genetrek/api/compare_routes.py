"""Gene-set comparison over HTTP: ``POST /api/compare``, ``GET /api/backgrounds``.

The same statistics as the Compare view -- every number comes from
:mod:`genetrek.services.compare` -- so a comparison made in the browser can be
reproduced exactly from a script, which is what the ``request.json`` in every
Compare download is for.

**My lists, without storage.** The API keeps no state: a set is a database gene
list by name, a list of identifiers, a My lists export file, or a My lists
share token, and all of it arrives in the request **body**. Bodies are not
written to access logs (URLs are), and nothing here stores or logs a gene. A
visitor's list is therefore exactly as private here as in their browser.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
from fastapi import APIRouter, HTTPException

from genetrek.models.schemas import (
    AdjustedPairResult,
    BackgroundInfo,
    BackgroundsResponse,
    ComparePairResult,
    CompareRegionResult,
    CompareRequest,
    CompareResponse,
    CompareSet,
    CompareSetResult,
    CovariateInfo,
    Ratio,
)
from genetrek.services import compare as cmp
from genetrek.services import compare_export as export
from genetrek.services import lookup
from genetrek.services import user_lists as ul

if TYPE_CHECKING:
    from genetrek.backgrounds import Background, Covariate
    from genetrek.config.loader import AppConfig
    from genetrek.db.gene_index import GeneIndex


def _ratio(value: float) -> Ratio:
    if value != value:  # noqa: PLR0124 -- NaN
        return None
    if value in (float("inf"), float("-inf")):
        return "Inf" if value > 0 else "-Inf"
    return value


def _number(value: float) -> float | None:
    return None if value != value or value in (float("inf"), float("-inf")) else value  # noqa: PLR0124


def register_compare_routes(
    router: APIRouter,
    config: AppConfig,
    resolver: tuple[GeneIndex | None, str],
) -> None:
    """Register the comparison endpoints onto the ``/api`` router."""
    gene_index, index_problem = resolver
    key = config.roles.primary_key
    universes: dict[str, frozenset[str]] = {}
    measured_cache: dict[str, frozenset[str] | None] = {}
    #: Keyed by background, covariate and the partial-coverage columns in the
    #: comparison: the strata are computed within the analysed universe, which
    #: those columns shrink.
    strata_cache: dict[tuple[str, str, tuple[str, ...]], dict[str, str]] = {}
    database_keys = cmp.keys_where(config.df[key]) if key is not None else frozenset()

    def _universe(background: Background) -> frozenset[str]:
        if background.name not in universes and key is not None:
            universes[background.name] = cmp.keys_where(
                config.df.filter(background.expr())[key],
            )
        return universes[background.name]

    def _measured(column: str) -> frozenset[str] | None:
        """The genes a column measured, or ``None`` when it has no nulls."""
        if column not in measured_cache:
            assert key is not None  # noqa: S101 -- checked by the caller
            measured_cache[column] = cmp.measured_keys(config.df, column, key)
        return measured_cache[column]

    def _strata(
        background: Background,
        covariate: Covariate,
        sets: list[cmp.GeneSet],
    ) -> dict[str, str]:
        partial = tuple(
            sorted(s.column for s in sets if s.measured is not None and s.column)
        )
        cache_key = (background.name, covariate.name, partial)
        if cache_key not in strata_cache and key is not None:
            analysed = cmp.effective_universe(sets, _universe(background))
            strata_cache[cache_key] = cmp.strata_for(
                config.df.filter(pl.col(key).cast(pl.String).is_in(list(analysed))),
                key,
                covariate.column,
                covariate.bins,
            )
        return strata_cache[cache_key]

    def _background_info(background: Background) -> BackgroundInfo:
        return BackgroundInfo(
            name=background.name,
            definition=background.describe(),
            description=background.description,
            size=len(_universe(background)),
        )

    @router.get("/backgrounds", response_model=BackgroundsResponse)
    def backgrounds() -> BackgroundsResponse:
        """The universes and covariates ``POST /api/compare`` accepts."""
        return BackgroundsResponse(
            backgrounds=[_background_info(b) for b in config.backgrounds],
            covariates=[
                CovariateInfo(
                    name=c.name,
                    column=c.column,
                    bins=c.bins,
                    description=c.description,
                    definition=c.describe(),
                )
                for c in config.covariates
            ],
        )

    def _from_field(
        spec: CompareSet, index: int
    ) -> tuple[cmp.GeneSet, CompareSetResult]:
        assert spec.field is not None  # noqa: S101 -- guaranteed by the model
        try:
            column = lookup.resolve_field_name(
                spec.field,
                config.fields,
                config.field_aliases,
            )
        except lookup.FieldNotFoundError as exc:
            raise HTTPException(
                status_code=422, detail=f"set {index + 1}: {exc}"
            ) from exc
        if column not in config.all_boolean_columns:
            raise HTTPException(
                status_code=422,
                detail=f"set {index + 1}: {column!r} is not a gene list (a Boolean column)",
            )
        assert key is not None  # noqa: S101 -- checked by the caller
        # A filter keeps only True rows: null ("not measured") is not membership,
        # and the set's measured genes restrict the comparison's universe.
        genes = cmp.keys_where(config.df.filter(pl.col(column))[key])
        meta = config.fields[column]
        gene_set = cmp.GeneSet(
            id=f"db:{column}",
            label=spec.label or meta.fullname,
            name=meta.fullname,
            kind="database",
            genes=genes,
            column=column,
            details={
                "update": meta.update,
                "doi": meta.doi,
                "description": meta.description,
                "universe": meta.universe,
            },
            measured=_measured(column),
        )
        return gene_set, CompareSetResult(
            label=gene_set.label,
            name=meta.fullname,
            source="database",
            field=column,
            n_input=len(genes),
            n_genes=len(genes),
            n_in_background=0,
            n_outside_background=0,
        )

    def _from_genes(
        spec: CompareSet, index: int
    ) -> tuple[cmp.GeneSet, CompareSetResult]:
        if gene_index is None:
            raise HTTPException(
                status_code=503,
                detail=f"gene resolution unavailable: {index_problem}",
            )
        values = [v.strip() for v in spec.genes or [] if v and v.strip()]
        if not values:
            raise HTTPException(
                status_code=422, detail=f"set {index + 1}: no genes given"
            )
        try:
            report = gene_index.resolve(values, spec.id_type)
        except (ValueError, LookupError) as exc:
            raise HTTPException(
                status_code=422, detail=f"set {index + 1}: {exc}"
            ) from exc
        genes = cmp.keys_where(report.resolved()[report.key])
        label = spec.label or f"Set {index + 1}"
        gene_set = cmp.GeneSet(f"genes:{index}", label, label, "my_list", genes)
        return gene_set, CompareSetResult(
            label=label,
            name=label,
            source="genes",
            n_input=len(values),
            n_genes=len(genes),
            n_in_background=0,
            n_outside_background=0,
            unmatched=report.unmatched,
            ambiguous=report.ambiguous,
        )

    def _from_list(
        spec: CompareSet, index: int
    ) -> tuple[cmp.GeneSet, CompareSetResult]:
        assert key is not None  # noqa: S101 -- checked by the caller
        list_key = key
        try:
            if spec.share is not None:
                shared = ul.decode_share(spec.share)
                list_key = shared.key
                lists = list(shared.lists)
                source = "share"
            else:
                lists = ul.from_export_json(
                    json.dumps(spec.lists_file),
                    key=key,
                    release=config.release,
                    now=datetime.now(UTC),
                )
                source = "lists_file"
        except ul.UserListError as exc:
            raise HTTPException(
                status_code=422, detail=f"set {index + 1}: {exc}"
            ) from exc
        if list_key != key:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"set {index + 1}: the link was made for a database keyed on "
                    f"{list_key!r}, not {key!r}"
                ),
            )
        if spec.list_index >= len(lists):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"set {index + 1}: list_index {spec.list_index}, but it holds "
                    f"{len(lists)} list(s)"
                ),
            )
        chosen = lists[spec.list_index]
        label = spec.label or chosen.name
        missing = ul.missing(chosen, database_keys)
        gene_set = cmp.GeneSet(
            f"list:{index}",
            label,
            chosen.name,
            "my_list",
            chosen.genes,
            details={"created": chosen.created},
        )
        return gene_set, CompareSetResult(
            label=label,
            name=chosen.name,
            source=source,
            n_input=len(chosen.genes),
            n_genes=len(chosen.genes),
            n_in_background=0,
            n_outside_background=0,
            n_not_in_database=missing,
        )

    @router.post("/compare", response_model=CompareResponse)
    def compare(body: CompareRequest) -> CompareResponse:
        """Compare 2 to 12 gene sets: the Compare view's statistics, over HTTP."""
        if key is None:
            raise HTTPException(
                status_code=503,
                detail="this database has no primary key; sets cannot be compared",
            )
        backgrounds = {b.name: b for b in config.backgrounds}
        name = body.background or config.backgrounds[0].name
        if name not in backgrounds:
            raise HTTPException(
                status_code=422,
                detail=f"unknown background {name!r}; available: {list(backgrounds)}",
            )
        background = backgrounds[name]
        covariates = {c.name: c for c in config.covariates}
        wanted = (
            list(covariates)[:1] if body.adjust_for is None else list(body.adjust_for)
        )
        if unknown := [w for w in wanted if w not in covariates]:
            raise HTTPException(
                status_code=422,
                detail=f"unknown covariate(s) {unknown}; available: {list(covariates)}",
            )
        covariate = covariates[wanted[0]] if wanted else None

        built = []
        for index, spec in enumerate(body.sets):
            if spec.field is not None:
                built.append(_from_field(spec, index))
            elif spec.genes is not None:
                built.append(_from_genes(spec, index))
            else:
                built.append(_from_list(spec, index))

        labels = export.unique_headers([s.label for s, _ in built], [])
        sets = [
            replace(s, label=label) for (s, _), label in zip(built, labels, strict=True)
        ]
        comparison = cmp.compare(
            sets,
            _universe(background),
            background=background.name,
            construction=cmp.construction_reasons(
                sets, config.fields, config.field_aliases
            ),
            strata=_strata(background, covariate, sets) if covariate else None,
            covariate=covariate.name if covariate else "",
        )

        set_results = [
            result.model_copy(
                update={
                    "label": label,
                    "n_in_background": len(restricted.genes),
                    "n_outside_background": outside,
                    "n_not_measured": unmeasured,
                },
            )
            for (_, result), label, restricted, outside, unmeasured in zip(
                built,
                labels,
                comparison.sets,
                comparison.outside_background,
                comparison.not_measured,
                strict=True,
            )
        ]
        pairs = [
            ComparePairResult(
                set_a=labels[p.a],
                set_b=labels[p.b],
                n_a=p.n_a,
                n_b=p.n_b,
                overlap=p.overlap,
                expected_overlap=_number(p.expected),
                fold_enrichment=_number(p.fold),
                odds_ratio=_ratio(p.odds_ratio),
                ci95=[_ratio(p.ci_lower), _ratio(p.ci_upper)],
                p_value=_number(p.p_value),
                fdr=_number(p.fdr),
                jaccard=_number(p.jaccard),
                overlap_coefficient=_number(p.overlap_coefficient),
                overlap_by_construction=p.construction,
                adjusted=(
                    AdjustedPairResult(
                        adjusted_for=comparison.covariate,
                        odds_ratio=_ratio(p.adjusted.odds_ratio),
                        ci95=[_ratio(p.adjusted.ci_lower), _ratio(p.adjusted.ci_upper)],
                        p_value=_number(p.adjusted.p_value),
                        fdr=_number(p.adjusted.fdr),
                        strata_used=p.adjusted.strata,
                    )
                    if p.adjusted is not None
                    else None
                ),
            )
            for p in comparison.pairs
        ]
        regions = [
            CompareRegionResult(
                sets=[labels[i] for i in r.members],
                size=r.size,
                genes=sorted(r.genes) if body.include_genes else None,
            )
            for r in comparison.regions
        ]
        return CompareResponse(
            release=config.release,
            key=key,
            background=_background_info(background).model_copy(
                update={
                    "effective_size": comparison.universe_size,
                    "restricted_by": list(comparison.restricted_by),
                },
            ),
            warnings=(
                [comparison.coverage_warning] if comparison.coverage_warning else []
            ),
            adjusted_for=[comparison.covariate] if comparison.covariate else [],
            sets=set_results,
            pairs=pairs,
            regions=regions,
            methods=export.methods_text(
                comparison,
                release=config.release,
                background_definition=background.describe(),
                today=date.today(),  # noqa: DTZ011
                covariate_definition=covariate.describe() if covariate else "",
            ),
        )
