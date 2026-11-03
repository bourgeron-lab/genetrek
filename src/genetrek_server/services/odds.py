"""Odds ratios for 2x2 tables: exact conditional, and Mantel-Haenszel stratified.

**Conditional (exact).** For one table ``[[a, b], [c, d]]`` this reports what R's
``fisher.test`` reports: the conditional maximum-likelihood odds ratio and the
exact ("Cornfield") 95% interval, both from the noncentral hypergeometric
distribution of ``a`` given the margins. Unlike the sample odds ratio with a
Woolf interval, the interval inverts the same distribution Fisher's test uses,
so the two cannot disagree about significance, and it still gives a bound when
a cell is zero.

``scipy.stats.contingency.odds_ratio(kind="conditional")`` computes the same
thing, but takes up to a second per table at the sizes gene lists reach -- a
comparison of 12 sets is 66 tables. Here the distribution is evaluated once per
table in log space and each candidate odds ratio costs one vectorised
log-sum-exp, which is what R does. ``tests/test_odds.py`` pins the result to
scipy's.

**Mantel-Haenszel (stratified).** For tables split by a covariate -- LOEUF
decile, so that two lists both rich in constrained genes are not called
associated for that alone -- the common odds ratio, its Robins-Breslow-
Greenland 95% interval, and the Cochran-Mantel-Haenszel test with continuity
correction. These match ``statsmodels``' ``StratifiedTable``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from scipy import optimize, special

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: Two-sided 95%: each tail of the interval holds 2.5%.
_ALPHA = 0.025
#: Search range for log(odds ratio). e^+-60 is far past any finite estimate a
#: gene-sized table can produce, and well inside floating-point range.
_LOG_BOUND = 60.0
_Z = float(special.ndtri(1 - _ALPHA))  # 1.959964...


@dataclass(frozen=True)
class OddsRatio:
    """An odds ratio with a 95% interval. ``nan`` when the table has no answer."""

    estimate: float
    ci_lower: float
    ci_upper: float


class _Hypergeometric:
    """The noncentral hypergeometric law of cell ``a`` given a table's margins."""

    def __init__(self, a: int, b: int, c: int, d: int) -> None:
        self.row = a + b  # first row total
        self.col = a + c  # first column total
        other = b + d
        self.low = max(0, self.row - other)
        self.high = min(self.row, self.col)
        self.support = np.arange(self.low, self.high + 1, dtype=float)
        x = self.support
        # log C(col, x) + log C(other, row - x): the psi-free part of the pmf.
        self.base = (
            special.gammaln(self.col + 1)
            - special.gammaln(x + 1)
            - special.gammaln(self.col - x + 1)
            + special.gammaln(other + 1)
            - special.gammaln(self.row - x + 1)
            - special.gammaln(other - self.row + x + 1)
        )

    def pmf(self, log_psi: float) -> np.ndarray:
        logits = self.base + self.support * log_psi
        result: np.ndarray = np.exp(logits - special.logsumexp(logits))
        return result

    def mean(self, log_psi: float) -> float:
        return float(np.dot(self.support, self.pmf(log_psi)))

    def lower_tail(self, x: int, log_psi: float) -> float:
        """P(A <= x)."""
        return float(self.pmf(log_psi)[self.support <= x].sum())

    def upper_tail(self, x: int, log_psi: float) -> float:
        """P(A >= x)."""
        return float(self.pmf(log_psi)[self.support >= x].sum())


def _solve(func: Callable[[float], float]) -> float:
    """Root of a monotone function of log(psi) on the search range, as psi."""
    lo, hi = -_LOG_BOUND, _LOG_BOUND
    f_lo, f_hi = func(lo), func(hi)
    if f_lo == 0:
        return math.exp(lo)
    if f_lo * f_hi > 0:
        # No crossing inside e^+-60: the answer is at the edge it tends to.
        return 0.0 if abs(f_lo) < abs(f_hi) else math.inf
    return math.exp(optimize.brentq(func, lo, hi, xtol=1e-10, rtol=1e-12))


def conditional_odds_ratio(a: int, b: int, c: int, d: int) -> OddsRatio:
    """Conditional MLE odds ratio and exact 95% interval, as R's ``fisher.test``."""
    if min(a + b, c + d, a + c, b + d) == 0:
        # An empty row or column: the table says nothing about association.
        return OddsRatio(math.nan, 0.0, math.inf)
    law = _Hypergeometric(a, b, c, d)
    if law.low == law.high:
        return OddsRatio(math.nan, 0.0, math.inf)

    if a == law.low:
        estimate = 0.0
    elif a == law.high:
        estimate = math.inf
    else:
        estimate = _solve(lambda t: law.mean(t) - a)

    # Lower limit: the psi at which P(A >= a) = 2.5%; upper: P(A <= a) = 2.5%.
    lower = 0.0 if a == law.low else _solve(lambda t: law.upper_tail(a, t) - _ALPHA)
    upper = (
        math.inf if a == law.high else _solve(lambda t: law.lower_tail(a, t) - _ALPHA)
    )
    return OddsRatio(estimate, lower, upper)


@dataclass(frozen=True)
class StratifiedResult:
    """Mantel-Haenszel common odds ratio and the CMH test over strata."""

    odds_ratio: OddsRatio
    p_value: float
    #: Strata that contributed: those with at least two genes.
    strata: int


def mantel_haenszel(tables: Sequence[tuple[int, int, int, int]]) -> StratifiedResult:
    """Common odds ratio (RGB interval) and CMH test across ``(a, b, c, d)`` tables.

    The test carries the usual 0.5 continuity correction. A stratum of fewer
    than two genes adds nothing to either sum and is skipped.
    """
    used = [t for t in tables if sum(t) >= 2]  # noqa: PLR2004
    if not used:
        return StratifiedResult(OddsRatio(math.nan, math.nan, math.nan), math.nan, 0)
    arr = np.array(used, dtype=float)
    a, b, c, d = arr.T
    n = a + b + c + d
    r = a * d / n
    s = b * c / n
    big_r, big_s = float(r.sum()), float(s.sum())

    if big_r == 0 and big_s == 0:
        ratio = OddsRatio(math.nan, math.nan, math.nan)
    elif big_s == 0:
        ratio = OddsRatio(math.inf, math.nan, math.nan)
    elif big_r == 0:
        ratio = OddsRatio(0.0, math.nan, math.nan)
    else:
        estimate = big_r / big_s
        p = (a + d) / n
        q = (b + c) / n
        variance = (
            (p * r).sum() / (2 * big_r**2)
            + (p * s + q * r).sum() / (2 * big_r * big_s)
            + (q * s).sum() / (2 * big_s**2)
        )
        half = _Z * math.sqrt(variance)
        log_or = math.log(estimate)
        ratio = OddsRatio(estimate, math.exp(log_or - half), math.exp(log_or + half))

    expected = (a + b) * (a + c) / n
    var_a = (a + b) * (c + d) * (a + c) * (b + d) / (n**2 * (n - 1))
    total_var = var_a.sum()
    if total_var <= 0:
        p_value = math.nan
    else:
        statistic = (abs(a.sum() - expected.sum()) - 0.5) ** 2 / total_var
        p_value = float(special.chdtrc(1, statistic))  # chi-square(1) upper tail
    return StratifiedResult(ratio, p_value, len(used))
