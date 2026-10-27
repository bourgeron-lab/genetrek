"""Supplementary tables for a gene-set comparison, built in memory.

Four tables, meant to go into a paper's supplementary material as they are:

``genesets``
    One row per gene in the union of the compared sets (after the background
    restriction): its key and symbol, a 0/1 column per set, how many sets hold
    it, and its region (``A & C``). The table a reviewer asks for.
``sets``
    What each set is: where it came from, its version, size before and after
    the background restriction, and its provenance from the metadata.
``statistics``
    The pairwise table, every number the view shows.
``methods``
    A generated paragraph for the methods section.

Everything returns ``bytes`` and touches no disk: the files leave through
``ui.download.content``, which sends them over the page's websocket. That keeps
the My lists promise -- a visitor's list never lands on the server -- true for
anything derived from one.
"""

from __future__ import annotations

import io
import json
import math
import zipfile
from typing import TYPE_CHECKING

import polars as pl
import xlsxwriter

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from genetrek.services.compare import Comparison, PairStat

#: Header for the symbol column when the database has no symbol role. Not a
#: column read -- just a name for the empty column the tables still carry.
SYMBOL_FALLBACK = "gene_symbol"

_KIND_TEXT = {
    "results": "GeneTrek search results",
    "database": "GeneTrek database gene list",
    "my_list": "User gene list",
}


def unique_headers(labels: Sequence[str], taken: Sequence[str]) -> list[str]:
    """``labels`` made unique, and distinct from ``taken`` column names."""
    used = set(taken)
    out: list[str] = []
    for label in labels:
        name, n = label, 2
        while name in used:
            name = f"{label} ({n})"
            n += 1
        used.add(name)
        out.append(name)
    return out


def _symbols(df: pl.DataFrame, key: str, symbol: str | None, genes: set[str]) -> dict:
    if symbol is None or symbol not in df.columns:
        return {}
    frame = df.select(
        pl.col(key).cast(pl.String).alias("_k"),
        pl.col(symbol).cast(pl.String).alias("_s"),
    ).filter(pl.col("_k").is_in(list(genes)))
    return dict(zip(frame["_k"], frame["_s"], strict=True))


def genesets_table(
    comparison: Comparison,
    df: pl.DataFrame,
    key: str,
    symbol: str | None,
) -> pl.DataFrame:
    """Gene x set membership, sorted by region (largest first) then symbol."""
    union = comparison.union
    symbols = _symbols(df, key, symbol, set(union))
    symbol_header = symbol or SYMBOL_FALLBACK
    headers = unique_headers([s.label for s in comparison.sets], [key, symbol_header])
    order = {r.members: rank for rank, r in enumerate(comparison.regions)}

    rows = []
    for gene in union:
        members = tuple(i for i, s in enumerate(comparison.sets) if gene in s.genes)
        rows.append(
            {
                key: gene,
                symbol_header: symbols.get(gene),
                **{
                    header: int(gene in s.genes)
                    for header, s in zip(headers, comparison.sets, strict=True)
                },
                "n_sets": len(members),
                "region": " & ".join(comparison.sets[i].label for i in members),
                "_rank": order.get(members, len(order)),
            },
        )
    schema = {
        key: pl.String,
        symbol_header: pl.String,
        **dict.fromkeys(headers, pl.Int8),
        "n_sets": pl.Int8,
        "region": pl.String,
        "_rank": pl.Int32,
    }
    return (
        pl.DataFrame(rows, schema=schema)
        .sort(["_rank", symbol_header, key], nulls_last=True)
        .drop("_rank")
    )


def sets_table(comparison: Comparison, release: str) -> pl.DataFrame:
    """One row per compared set, with its provenance."""
    rows = []
    for s, before, outside, unmeasured in zip(
        comparison.sets,
        comparison.original_sizes,
        comparison.outside_background or (0,) * len(comparison.sets),
        comparison.not_measured or (0,) * len(comparison.sets),
        strict=True,
    ):
        rows.append(
            {
                "label": s.label,
                "name": s.name,
                "source": _KIND_TEXT.get(s.kind, s.kind),
                "column": s.column or "",
                "version_date": s.details.get("update", ""),
                "created": s.details.get("created", ""),
                "description": s.details.get("description", ""),
                "doi": s.details.get("doi", ""),
                "universe": s.details.get("universe", ""),
                "genes_total": before,
                "genes_outside_background": outside,
                "genes_not_measured": unmeasured,
                "genes_in_background": len(s.genes),
                "background": comparison.background,
                "database_release": release,
            },
        )
    return pl.DataFrame(rows)


def _number(value: float) -> float | None:
    return None if math.isnan(value) or math.isinf(value) else value


def _ratio(value: float) -> str:
    """An odds ratio as text: ``Inf`` when a zero cell makes it infinite."""
    if math.isnan(value):
        return "NA"
    if math.isinf(value):
        return "Inf"
    return f"{value:.4g}"


def _p(value: float) -> str:
    """A p-value as text. Fisher's test underflows to exactly 0 below ~1e-300,
    and a published "p = 0" is wrong, so that is written as a bound."""
    if math.isnan(value):
        return "NA"
    if value == 0:
        return "<1e-300"
    return f"{value:.3g}"


def statistics_table(comparison: Comparison) -> pl.DataFrame:
    """The pairwise statistics, one row per pair."""
    sets = comparison.sets
    rows = [
        {
            "set_a": sets[p.a].label,
            "set_b": sets[p.b].label,
            "n_a": p.n_a,
            "n_b": p.n_b,
            "overlap": p.overlap,
            "expected_overlap": _number(p.expected),
            "fold_enrichment": _number(p.fold),
            # Text, not floats: an infinite OR and an underflowed p-value have
            # no honest numeric cell, and the spreadsheet should not invent one.
            "odds_ratio": _ratio(p.odds_ratio),
            "or_ci95_lower": _ratio(p.ci_lower),
            "or_ci95_upper": _ratio(p.ci_upper),
            "p_value": _p(p.p_value),
            "fdr_bh": _p(p.fdr),
            "jaccard": _number(p.jaccard),
            "overlap_coefficient": _number(p.overlap_coefficient),
            "overlap_by_construction": p.construction,
            "background": comparison.background,
            "background_size": comparison.background_size or comparison.universe_size,
            # The N of every 2x2 table: the background, less any gene a set did
            # not measure. Equal to background_size when every set covers it.
            "universe_size": comparison.universe_size,
            "test": "Fisher's exact test, two-sided; conditional MLE odds ratio, "
            "exact 95% CI",
            **_adjusted_columns(comparison, p),
        }
        for p in comparison.pairs
    ]
    return pl.DataFrame(rows, infer_schema_length=None)


def _adjusted_columns(comparison: Comparison, pair: PairStat) -> dict[str, object]:
    """The stratified result, beside the crude one -- the gap is the point."""
    adj = pair.adjusted
    if adj is None or not comparison.covariate:
        return {}
    return {
        "adjusted_for": comparison.covariate,
        "mh_odds_ratio": _ratio(adj.odds_ratio),
        "mh_ci95_lower": _ratio(adj.ci_lower),
        "mh_ci95_upper": _ratio(adj.ci_upper),
        "cmh_p_value": _p(adj.p_value),
        "cmh_fdr_bh": _p(adj.fdr),
        "strata_used": adj.strata,
    }


def _universe_sentence(comparison: Comparison, background_definition: str) -> str:
    """Which genes were tested, and why that many."""
    background = (
        f"{comparison.background} ({background_definition})"
        if background_definition
        else comparison.background
    )
    if not comparison.restricted_by:
        return (
            f"All sets were restricted to a common background of "
            f"{comparison.universe_size:,} genes ({comparison.background}: "
            f"{background_definition}); genes outside it were excluded before "
            f"testing."
        )
    sources = ", ".join(comparison.restricted_by)
    return (
        f"All sets were restricted to a common universe of "
        f"{comparison.universe_size:,} genes: the {comparison.background_size:,} "
        f"genes of the background {background}, less the "
        f"{comparison.coverage_loss:,} that {sources} did not measure. A gene a "
        f"set did not measure is unknown, not absent, so it was excluded from "
        f"every pair rather than counted outside that set; one universe was used "
        f"for all pairs so that every count and test refers to the same genes."
    )


def methods_text(
    comparison: Comparison,
    *,
    release: str,
    background_definition: str,
    today: date,
    covariate_definition: str = "",
) -> str:
    """A methods paragraph naming every choice the numbers depend on."""
    k = len(comparison.pairs)
    names = "; ".join(
        (f"{s.label}, {s.name}" if s.name != s.label else s.label)
        + f" ({len(s.genes):,} genes)"
        for s in comparison.sets
    )
    text = [
        f"Gene-set overlaps were computed with GeneTrek (database release "
        f"{release or 'unversioned'}, accessed {today.isoformat()}). "
        f"{len(comparison.sets)} gene sets were compared: {names}.",
        _universe_sentence(comparison, background_definition),
        f"For each of the {k} pairs of sets, the overlap was tested with a "
        f"two-sided Fisher's exact test on the 2x2 table of membership within that "
        f"universe, reporting the conditional maximum-likelihood odds ratio with "
        f"its exact 95% confidence interval (as computed by R's fisher.test), the "
        f"fold enrichment over the expected overlap (|A| x |B| / N), the Jaccard "
        f"index and the overlap coefficient (|A n B| / min(|A|, |B|)). P-values "
        f"were adjusted for the {k} tests with the Benjamini-Hochberg procedure.",
    ]
    if comparison.covariate:
        text.append(
            f"Because two gene lists can overlap through a shared gene property "
            f"rather than a shared biology, each pair was also tested within strata "
            f"of {comparison.covariate} ({covariate_definition or 'see GeneTrek'}): "
            f"Cochran-Mantel-Haenszel test with continuity correction, reporting the "
            f"Mantel-Haenszel common odds ratio with a Robins-Breslow-Greenland 95% "
            f"confidence interval. These p-values were adjusted separately, with "
            f"the Benjamini-Hochberg procedure across the {k} pairs.",
        )
    flagged = [p for p in comparison.pairs if p.construction]
    if flagged:
        listed = "; ".join(
            f"{comparison.sets[p.a].label}-{comparison.sets[p.b].label} "
            f"({p.construction})"
            for p in flagged
        )
        text.append(
            f"The following pairs overlap by construction and their tests are "
            f"not evidence of association: {listed}.",
        )
    text.append(
        "The comparison can be reproduced exactly by sending the accompanying "
        "request.json to GeneTrek's API (POST /api/compare).",
    )
    return "\n\n".join(text) + "\n"


def request_body(comparison: Comparison) -> dict[str, object]:
    """The ``POST /api/compare`` body that reproduces ``comparison``.

    Database lists go by field name, so the request reads as what it is.
    Anything else -- the current search results, a visitor's list -- is written
    out as its gene keys, from before the background restriction: the server
    keeps no state, so a reproduction cannot refer to either.
    """
    sets: list[dict[str, object]] = []
    for gene_set in comparison.inputs or comparison.sets:
        if gene_set.kind == "database" and gene_set.column:
            sets.append({"field": gene_set.column, "label": gene_set.label})
        else:
            sets.append({"label": gene_set.label, "genes": sorted(gene_set.genes)})
    return {
        "sets": sets,
        "background": comparison.background,
        "adjust_for": [comparison.covariate] if comparison.covariate else [],
        "include_genes": False,
    }


def tsv_bytes(frame: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.write_csv(buffer, separator="\t")
    return buffer.getvalue()


def xlsx_bytes(
    tables: Mapping[str, pl.DataFrame],
    methods: str,
    *,
    alt_texts: Mapping[str, str] | None = None,
    request: Mapping[str, object] | None = None,
) -> bytes:
    """One workbook: a sheet per table, the methods text, figure alt texts, request."""
    buffer = io.BytesIO()
    with xlsxwriter.Workbook(buffer, {"in_memory": True}) as workbook:
        for name, frame in tables.items():
            frame.write_excel(
                workbook=workbook,
                worksheet=name[:31],
                autofit=True,
                float_precision=6,
            )
        wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
        sheet = workbook.add_worksheet("methods")
        sheet.set_column(0, 0, 110)
        for row, paragraph in enumerate(methods.strip().split("\n\n")):
            sheet.write(row, 0, paragraph, wrap)
        if alt_texts:
            sheet = workbook.add_worksheet("figures")
            sheet.set_column(0, 0, 18)
            sheet.set_column(1, 1, 110)
            sheet.write_row(0, 0, ["figure", "alt text"])
            for row, (name, text) in enumerate(alt_texts.items(), start=1):
                sheet.write(row, 0, name)
                sheet.write(row, 1, text, wrap)
        if request is not None:
            sheet = workbook.add_worksheet("request")
            sheet.set_column(0, 0, 110)
            sheet.write(0, 0, "POST this JSON to /api/compare to reproduce the tables:")
            sheet.write(1, 0, json.dumps(request), wrap)
    return buffer.getvalue()


def zip_bytes(
    tables: Mapping[str, pl.DataFrame],
    methods: str,
    *,
    alt_texts: Mapping[str, str] | None = None,
    request: Mapping[str, object] | None = None,
) -> bytes:
    """The TSVs, a README with the methods text, alt texts and request.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, frame in tables.items():
            archive.writestr(f"{name}.tsv", tsv_bytes(frame))
        archive.writestr("README.txt", methods)
        if alt_texts:
            archive.writestr(
                "figures.txt",
                "\n\n".join(f"{name}\n{text}" for name, text in alt_texts.items())
                + "\n",
            )
        if request is not None:
            archive.writestr("request.json", json.dumps(request, indent=1) + "\n")
    return buffer.getvalue()


def region_table(
    genes: frozenset[str],
    df: pl.DataFrame,
    key: str,
    symbol: str | None,
) -> pl.DataFrame:
    """One region's genes, key and symbol, sorted by symbol."""
    symbols = _symbols(df, key, symbol, set(genes))
    header = symbol or SYMBOL_FALLBACK
    return pl.DataFrame(
        [{key: g, header: symbols.get(g)} for g in genes],
        schema={key: pl.String, header: pl.String},
    ).sort([header, key], nulls_last=True)
