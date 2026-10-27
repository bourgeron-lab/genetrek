from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import polars as pl
from scipy import stats

from genetrek.services.odds import conditional_odds_ratio

if TYPE_CHECKING:
    from genetrek.backgrounds import Background
    from genetrek.schema import Roles


@dataclass(frozen=True)
class EnrichmentResult:
    a: int  # in search AND in annotation
    b: int  # in search AND NOT in annotation
    c: int  # NOT in search AND in annotation
    d: int  # NOT in search AND NOT in annotation
    odds_ratio: float
    p_value: float
    ci_lower: float
    ci_upper: float
    #: Genes left out because the annotation did not measure them (null).
    n_not_measured: int = 0


def _gene_key(roles: Roles, df: pl.DataFrame) -> str | None:
    """The column to identify genes by: the primary key, then ENSG, then symbol.

    Both sides of the contingency table must use the *same* column, or every
    intersection is empty and the odds ratio silently collapses to zero.
    """
    for candidate in (roles.primary_key, roles.ensg, roles.symbol):
        if candidate is not None and candidate in df.columns:
            return candidate
    return None


@dataclass(frozen=True)
class FisherResult:
    """Two-sided Fisher's exact test on one 2x2 table, with its exact 95% CI.

    ``odds_ratio`` is the conditional maximum-likelihood estimate and the
    interval the exact conditional one -- what R's ``fisher.test`` reports --
    so the interval excludes 1 exactly when the test is significant at 5%, and
    a zero cell still gives a bound (one end at 0 or infinity).
    """

    odds_ratio: float
    p_value: float
    ci_lower: float
    ci_upper: float


@lru_cache(maxsize=4096)
def fisher_2x2(a: int, b: int, c: int, d: int) -> FisherResult:
    """Fisher's exact test on ``[[a, b], [c, d]]``, with the conditional OR and CI.

    The one implementation behind the drawer's enrichment, the Compare view
    and ``POST /api/compare``, so none of them can disagree about the same
    table. Cached: relabelling or re-sorting a comparison recomputes nothing.
    """
    p_value = float(stats.fisher_exact([[a, b], [c, d]], alternative="two-sided")[1])
    ratio = conditional_odds_ratio(a, b, c, d)
    return FisherResult(ratio.estimate, p_value, ratio.ci_lower, ratio.ci_upper)


def compute_enrichment(
    df: pl.DataFrame,
    filtered_df: pl.DataFrame,
    annotation_column: str,
    roles: Roles,
    background: Background | None = None,
) -> EnrichmentResult:
    """Compute Fisher exact test for enrichment of annotation in search results.

    ``background`` restricts the universe -- and both sides with it -- before
    anything is counted. Without one it is every row of ``df``.

    A gene the annotation did not measure (null) is left out of the universe
    too: counting it as "not in the set" would treat unknown as no.
    """
    if background is not None:
        keep = background.expr()
        df = df.filter(keep)
        filtered_df = filtered_df.filter(keep)
    n_not_measured = 0
    if annotation_column in df.columns and df[annotation_column].null_count():
        n_not_measured = df[annotation_column].null_count()
        measured = pl.col(annotation_column).is_not_null()
        df = df.filter(measured)
        filtered_df = filtered_df.filter(measured)
    total_genes = len(df)

    key = _gene_key(roles, df)
    if key is None or key not in filtered_df.columns:
        search_gene_ids: set[object] = set()
        annotation_genes: set[object] = set()
    else:
        search_gene_ids = set(filtered_df[key].to_list())
        annotation_genes = set(df.filter(pl.col(annotation_column))[key].to_list())

    a = len(search_gene_ids & annotation_genes)
    b = len(search_gene_ids - annotation_genes)
    c = len(annotation_genes - search_gene_ids)
    d = total_genes - a - b - c

    fisher = fisher_2x2(a, b, c, d)
    return EnrichmentResult(
        a=a,
        b=b,
        c=c,
        d=d,
        odds_ratio=fisher.odds_ratio,
        p_value=fisher.p_value,
        ci_lower=fisher.ci_lower,
        ci_upper=fisher.ci_upper,
        n_not_measured=n_not_measured,
    )
