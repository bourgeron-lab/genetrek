"""Odds ratios: the exact conditional estimate, and Mantel-Haenszel stratified.

Both are reimplemented rather than called -- scipy's conditional interval takes
up to a second per table at gene-list sizes, and statsmodels is not a
dependency -- so both are pinned to the libraries they replace: scipy on
random tables, and statsmodels on values recorded from
``statsmodels.stats.contingency_tables.StratifiedTable`` (0.14).
"""

from __future__ import annotations

import math
import random

import pytest
from scipy.stats.contingency import odds_ratio

from genetrek.services.odds import conditional_odds_ratio, mantel_haenszel


class TestConditional:
    @pytest.mark.parametrize(
        "table",
        [
            (10, 10, 20, 60),
            (3, 7, 9, 81),
            (1, 2, 3, 4),
            (283, 1900, 1500, 15600),
            (1500, 700, 100, 17000),
            (3000, 2700, 2000, 11597),
        ],
    )
    def test_matches_scipy(self, table):
        a, b, c, d = table
        ours = conditional_odds_ratio(a, b, c, d)
        ref = odds_ratio([[a, b], [c, d]], kind="conditional")
        ci = ref.confidence_interval()
        assert ours.estimate == pytest.approx(ref.statistic, rel=1e-8)
        assert ours.ci_lower == pytest.approx(ci.low, rel=1e-8)
        assert ours.ci_upper == pytest.approx(ci.high, rel=1e-8)

    def test_matches_scipy_on_random_tables(self):
        rng = random.Random(7)  # noqa: S311 -- reproducible test tables
        for _ in range(150):
            a, b, c, d = (rng.randint(0, 40) for _ in range(4))
            ours = conditional_odds_ratio(a, b, c, d)
            ref = odds_ratio([[a, b], [c, d]], kind="conditional")
            ci = ref.confidence_interval()
            for got, want in (
                (ours.estimate, ref.statistic),
                (ours.ci_lower, ci.low),
                (ours.ci_upper, ci.high),
            ):
                if math.isnan(want):
                    assert math.isnan(got)
                elif math.isinf(want) or want == 0:
                    assert got == want
                else:
                    assert got == pytest.approx(want, rel=1e-7)

    def test_zero_cells_bound_one_side(self):
        high = conditional_odds_ratio(10, 0, 5, 85)
        assert math.isinf(high.estimate)
        assert high.ci_lower == pytest.approx(26.164, rel=1e-3)
        assert math.isinf(high.ci_upper)
        low = conditional_odds_ratio(0, 10, 5, 85)
        assert low.estimate == 0
        assert low.ci_lower == 0
        assert low.ci_upper == pytest.approx(10.611, rel=1e-3)

    def test_an_empty_margin_has_no_answer(self):
        result = conditional_odds_ratio(0, 0, 5, 85)
        assert math.isnan(result.estimate)
        assert (result.ci_lower, result.ci_upper) == (0.0, math.inf)


#: (strata, MH OR, RGB lower, RGB upper, CMH p with continuity correction), as
#: computed by statsmodels' StratifiedTable for the same tables.
STATSMODELS = [
    (
        [(10, 20, 30, 40), (5, 15, 25, 35), (12, 8, 6, 30)],
        1.092807,
        0.62258,
        1.918193,
        0.8662223644132264,
    ),
    (
        [(0, 5, 7, 20), (3, 9, 2, 30), (1, 1, 1, 1)],
        1.309562,
        0.337364,
        5.083396,
        0.9758223880614681,
    ),
]


class TestMantelHaenszel:
    @pytest.mark.parametrize(("tables", "est", "lo", "hi", "p"), STATSMODELS)
    def test_matches_statsmodels(self, tables, est, lo, hi, p):
        result = mantel_haenszel(tables)
        assert result.odds_ratio.estimate == pytest.approx(est, abs=1e-6)
        assert result.odds_ratio.ci_lower == pytest.approx(lo, abs=1e-6)
        assert result.odds_ratio.ci_upper == pytest.approx(hi, abs=1e-6)
        assert result.p_value == pytest.approx(p, rel=1e-9)

    def test_one_stratum_is_the_crude_sample_odds_ratio(self):
        result = mantel_haenszel([(10, 10, 20, 60)])
        assert result.odds_ratio.estimate == pytest.approx(10 * 60 / (10 * 20))

    def test_confounding_is_removed(self):
        """Two strata with no association inside either, but different base
        rates: the crude OR shows an association, the MH OR does not."""
        tables = [(80, 20, 20, 5), (5, 20, 20, 80)]
        crude_a = sum(t[0] for t in tables)
        crude_b = sum(t[1] for t in tables)
        crude_c = sum(t[2] for t in tables)
        crude_d = sum(t[3] for t in tables)
        assert crude_a * crude_d / (crude_b * crude_c) > 3
        result = mantel_haenszel(tables)
        assert result.odds_ratio.estimate == pytest.approx(1.0)
        assert result.p_value > 0.5

    def test_tiny_strata_are_skipped(self):
        result = mantel_haenszel([(1, 0, 0, 0), (10, 10, 20, 60)])
        assert result.strata == 1

    def test_no_usable_stratum(self):
        result = mantel_haenszel([(1, 0, 0, 0)])
        assert math.isnan(result.odds_ratio.estimate)
        assert math.isnan(result.p_value)
