"""core/radon_math.py: exact Poisson intervals and counts-based radon."""

import math
import pathlib
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye"))
from core import radon_math as M  # noqa: E402

# Gehrels (1986) Table 1/2, 1-sigma (0.8413 one-sided) Poisson limits
GEHRELS = {0: (0.0, 1.841), 1: (0.173, 3.300), 2: (0.708, 4.638), 3: (1.367, 5.918), 10: (6.891, 14.27)}


class Garwood(unittest.TestCase):
    def test_against_published_table(self):
        for n, (lo, hi) in GEHRELS.items():
            got_lo, got_hi = M.garwood(n)
            self.assertAlmostEqual(got_lo, lo, delta=0.002 + 0.0005 * n, msg=f"lower n={n}")
            self.assertAlmostEqual(got_hi, hi, delta=0.002 + 0.0005 * n, msg=f"upper n={n}")

    def test_cdf_basics(self):
        self.assertAlmostEqual(M.poisson_cdf(0, 2.0), 0.1353352832, places=9)
        self.assertEqual(M.poisson_cdf(-1, 2.0), 0.0)
        self.assertEqual(M.poisson_cdf(3, 0.0), 1.0)

    def test_cdf_matches_direct_sum(self):
        for n, mu in ((0, 0.5), (5, 3.2), (13, 17.7), (300, 282.7), (300, 318.3), (40, 2.0), (2, 60.0), (3000, 3100.0)):
            direct = sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1)) for i in range(n + 1))
            self.assertAlmostEqual(M.poisson_cdf(n, mu), min(1.0, direct), delta=1e-12, msg=f"n={n} mu={mu}")


class Timing(unittest.TestCase):
    def test_1000_derives_over_typical_n_under_1s_cold_cache(self):     # final review I1: runs on the event loop
        if hasattr(M.garwood, "cache_clear"):
            M.garwood.cache_clear()                                    # honest: every n in 0..300 computed once
        t0 = time.perf_counter()
        for i in range(1000):
            n = (i * 11) % 301                                         # 11 is coprime to 301: all 301 values occur
            M.derive([n // 6] * 5 + [n - 5 * (n // 6)], expected=6, minimum=4, k=1.27)
        self.assertLess(time.perf_counter() - t0, 1.0)
        if hasattr(M.garwood, "cache_info"):
            self.assertEqual(M.garwood.cache_info().misses, 301)


class Derive(unittest.TestCase):
    def test_value_and_interval(self):
        d = M.derive([2, 2, 3, 2, 2, 2], expected=6, minimum=4, k=1.27)
        # 13 counts in 1 h at k = 1.27 -> 10.236 Bq/m3 -> 0.2767 pCi/L
        self.assertAlmostEqual(d.value_pci, 13 / 1.27 / 37, places=6)
        lo, hi = M.garwood(13)
        self.assertAlmostEqual(d.lower_pci, lo / 1.27 / 37, places=6)
        self.assertAlmostEqual(d.upper_pci, hi / 1.27 / 37, places=6)
        self.assertEqual((d.n, d.windows, d.coverage), (13, 6, 1.0))

    def test_missing_windows_shrink_time_not_count(self):
        full = M.derive([2] * 6, 6, 4, 1.27)
        partial = M.derive([2] * 4, 6, 4, 1.27)
        self.assertAlmostEqual(full.value_pci, partial.value_pci, places=9)
        self.assertAlmostEqual(partial.coverage, 4 / 6)

    def test_below_coverage_is_none(self):
        self.assertIsNone(M.derive([2] * 3, 6, 4, 1.27))

    def test_zero_counts_have_an_upper_limit(self):
        d = M.derive([0] * 6, 6, 4, 1.27)
        self.assertEqual(d.value_pci, 0.0)
        self.assertEqual(d.lower_pci, 0.0)
        self.assertGreater(d.upper_pci, 0.0)

    def test_counts_recoverable_from_hourly_mean_of_window_values(self):
        k = 1.27
        windows = [1, 3, 0, 2, 4, 2]
        per_window_pci = [n * 6 / k / 37 for n in windows]
        mean = sum(per_window_pci) / 6
        self.assertAlmostEqual(M.counts_from_hourly_mean(mean, k), sum(windows), places=9)


if __name__ == "__main__":
    unittest.main()
