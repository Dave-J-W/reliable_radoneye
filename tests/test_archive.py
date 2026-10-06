"""core/archive.py: per-window count CSVs round-trip exactly, with hourly totals."""

import glob
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye"))
from core import archive as A  # noqa: E402
from core import windows as W  # noqa: E402

T0 = datetime(2026, 10, 1, 23, 31, 30, tzinfo=timezone.utc)


class Archive(unittest.TestCase):
    def test_round_trip_across_midnight(self):
        boot = datetime(2026, 9, 28, 12, 3, tzinfo=timezone.utc)
        ws = [W.captured_window(T0 + timedelta(minutes=10 * i), 5001 + 10 * i, i % 4) for i in range(6)]
        with tempfile.TemporaryDirectory() as d:
            for w in ws:
                A.append_window(d, "XX01RE000001", "0001", w, boot)
            files = sorted(glob.glob(os.path.join(d, "counts_XX01RE000001_0001_*.csv")))
            self.assertEqual([os.path.basename(f)[-14:] for f in files], ["2026-10-01.csv", "2026-10-02.csv"])
            rows = A.read_counts(files)
        self.assertEqual([int(r["count"]) for r in rows], [w.count for w in ws])
        self.assertEqual(rows[0]["boot_utc"], boot.isoformat())
        hour = datetime(2026, 10, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(W.hour_totals(ws, hour), (sum(w.count for w in ws[3:]), 3))


if __name__ == "__main__":
    unittest.main()
