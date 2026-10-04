"""Counts-based radon (spec "Counts-based radon"). Pure: stdlib + core.windows only.

k = counts per hour per Bq/m3 (per monitor; default 1.27 from the trial). For N counts in captured
windows totalling T hours: radon = (N / T) / k Bq/m3, / 37 for pCi/L. The 68 % interval is the exact
Poisson (Garwood) interval on N, scaled the same way.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass

from .windows import WINDOW_MIN

BQ_PER_PCI_L = 37.0
ONE_SIGMA_TAIL = (1 - 0.6826894921370859) / 2   # 0.158655...


def poisson_cdf(n: int, mu: float) -> float:
    """P(X <= n) for X ~ Poisson(mu), in log space: the largest term in [0, n] is computed from its log,
    the others relative to it by the term ratio, stopping once a term is below double precision."""
    if n < 0:
        return 0.0
    if mu <= 0:
        return 1.0
    m = min(n, int(mu))                                    # the largest term with index <= n
    log_top = -mu + m * math.log(mu) - math.lgamma(m + 1)
    total = t = 1.0
    for i in range(m, 0, -1):                              # downwards: t_{i-1} = t_i * i / mu
        t *= i / mu
        total += t
        if t < 1e-17 * total:
            break
    t = 1.0
    for i in range(m + 1, n + 1):                          # upwards: t_i = t_{i-1} * mu / i
        t *= mu / i
        total += t
        if t < 1e-17 * total:
            break
    return min(1.0, math.exp(log_top) * total)


def _root(f, lo: float, hi: float) -> float:
    """Bisection for f(lo) and f(hi) of opposite sign: at most 60 halvings (float resolution), stopping
    early once the bracket is below 1e-12 relative (far below the 3 decimals shown)."""
    flo = f(lo)
    for _ in range(60):
        if hi - lo <= 1e-12 * max(1.0, hi):
            break
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


@functools.lru_cache(maxsize=4096)
def garwood(n: int) -> tuple[float, float]:
    """Exact central 68.27 % Poisson interval for an observed count n (cached: called on the event loop)."""
    top = n + 20 + 10 * math.sqrt(n + 1)
    upper = _root(lambda mu: poisson_cdf(n, mu) - ONE_SIGMA_TAIL, 0.0, top)
    lower = 0.0 if n == 0 else _root(lambda mu: (1 - poisson_cdf(n - 1, mu)) - ONE_SIGMA_TAIL, 0.0, top)
    return lower, upper


@dataclass(frozen=True)
class Derived:
    value_pci: float
    lower_pci: float
    upper_pci: float
    n: int
    windows: int
    coverage: float


def derive(counts: list[int], expected: int, minimum: int, k: float) -> Derived | None:
    """Radon from the counts of the windows CAPTURED in a period (missing windows shrink T)."""
    if len(counts) < minimum or k <= 0:
        return None
    n = sum(counts)
    hours = len(counts) * WINDOW_MIN / 60
    scale = 1 / (hours * k * BQ_PER_PCI_L)
    lo, hi = garwood(n)
    return Derived(n * scale, lo * scale, hi * scale, n, len(counts), len(counts) / expected)


def counts_from_hourly_mean(mean_pci: float, k: float) -> float:
    """Counts in an hour whose six 10-min values (pCi/L) average mean_pci: 6 x v x 37 x k / 6."""
    return mean_pci * BQ_PER_PCI_L * k
