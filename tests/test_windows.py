"""core/windows.py: capture, dedupe, reboots, validator, and a replay of the 2026-09-29/30 trial."""

import csv
import glob
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye"))
from core import windows as W  # noqa: E402

T0 = datetime(2026, 9, 29, 17, 0, 30, tzinfo=timezone.utc)
CUTOFF = "2026-10-01T03:00:00Z"   # the 33.5 h analysed in HANDOFF (2026-09-29 12:25 -> 09-30 22:00 CDT)


def trial_rows(label):
    rows = []
    for p in sorted(glob.glob(str(ROOT / f"tests/fixtures/pulse_counts/rd200_*_{label}_*.csv"))):
        with open(p, newline="", encoding="utf-8") as f:
            rows += [r for r in csv.DictReader(f) if r["ok"] == "1" and r["time_utc"] < CUTOFF]
    return rows


class Capture(unittest.TestCase):
    def test_previous_window_is_the_one_that_just_closed(self):
        w = W.captured_window(T0, 2734, 6)
        self.assertEqual(w.index, 272)                       # uptime 2730..2739 is window 273; 272 just closed
        self.assertEqual(w.end_utc, T0 - timedelta(minutes=4))
        self.assertEqual(w.count, 6)

    def test_no_window_before_the_first_rollover(self):
        self.assertIsNone(W.captured_window(T0, 7, 1))

    def test_round_trip(self):
        w = W.captured_window(T0, 2734, 6, device_bq=11)
        self.assertEqual(W.Window.from_dict(w.to_dict()), w)

    def test_compact_row_round_trip(self):                    # final review I2: the Store's compact form
        w = W.captured_window(T0, 236034, 6, device_bq=11)
        self.assertEqual(W.Window.from_row(w.to_row()), w)
        b = W.Window(T0, 7, 0, "backup", T0 + timedelta(seconds=210))
        self.assertEqual(W.Window.from_row(b.to_row()), b)
        frac = W.captured_window(T0 + timedelta(microseconds=600_000), 2734, 6)
        back = W.Window.from_row(frac.to_row())
        self.assertEqual(back.end_utc, T0 - timedelta(minutes=4) + timedelta(seconds=1))   # whole s, rounded
        self.assertEqual(back.end_utc.utcoffset(), timedelta(0))


class Log(unittest.TestCase):
    def test_two_reads_of_one_window_are_one_window(self):
        log = W.WindowLog()
        self.assertEqual(log.add(W.captured_window(T0, 2731, 3)), (True, None))
        self.assertEqual(log.add(W.captured_window(T0 + timedelta(minutes=5), 2736, 3)), (False, None))
        self.assertEqual(len(log), 1)

    def test_ha_copy_replaces_backup_copy_with_a_warning(self):
        log = W.WindowLog()
        log.add(W.captured_window(T0, 2731, 2, source="backup"))
        changed, warning = log.add(W.captured_window(T0 + timedelta(seconds=40), 2731, 3))
        self.assertTrue(changed)
        self.assertIn("kept ha", warning)
        self.assertEqual(log.all()[0].count, 3)

    def test_between_prune_hour_totals(self):
        log = W.WindowLog([W.captured_window(T0 + timedelta(minutes=10 * i), 2731 + 10 * i, i) for i in range(12)])
        self.assertEqual(len(log.between(T0, T0 + timedelta(minutes=60))), 6)
        hour = T0.replace(minute=0, second=0)          # 17:00; windows i=1..6 end 17:09:30 .. 17:59:30
        self.assertEqual(W.hour_totals(log.all(), hour), (1 + 2 + 3 + 4 + 5 + 6, 6))
        log.prune(T0 + timedelta(minutes=50))
        self.assertEqual(len(log), 6)


class Boot(unittest.TestCase):
    def test_reboot_detected_when_uptime_goes_back(self):
        b = W.BootTracker()
        self.assertFalse(b.observe(T0, 2734))
        first = b.boot_utc
        self.assertFalse(b.observe(T0 + timedelta(minutes=5), 2739))
        self.assertTrue(b.observe(T0 + timedelta(minutes=10), 3))
        self.assertNotEqual(b.boot_utc, first)

    def test_round_trip_detects_reboot_while_away_even_when_uptime_grew(self):
        b = W.BootTracker()
        b.observe(T0, 600)
        r = W.BootTracker(b.to_dict())
        self.assertEqual((r.boot_utc, r.last_uptime, r.last_read_utc), (b.boot_utc, 600, T0))
        self.assertTrue(r.observe(T0 + timedelta(hours=24), 700))     # 24 h away but only 100 min more uptime
        self.assertEqual(r.boot_utc, (T0 + timedelta(hours=24) - timedelta(minutes=700)).replace(second=0))

    def test_round_trip_no_false_reboot(self):
        b = W.BootTracker()
        b.observe(T0, 600)
        r = W.BootTracker(b.to_dict())
        self.assertFalse(r.observe(T0 + timedelta(hours=24, seconds=50), 600 + 24 * 60))   # floor + jitter
        self.assertFalse(r.observe(T0 + timedelta(hours=24, minutes=5, seconds=40), 600 + 24 * 60 + 5))


class Validator(unittest.TestCase):
    def test_ten_minute_windows_pass(self):
        v = W.WindowValidator()
        u = 1000
        for block in range(30):                       # aligned A (+1) and B (+6) reads, 2 counts per window
            v.observe(u + 10 * block + 1, 0, 2 + block % 2)
            v.observe(u + 10 * block + 6, 1, 2 + block % 2)
        self.assertEqual(v.status, "passed")

    def test_five_minute_windows_fail(self):
        v = W.WindowValidator()
        for i in range(60):                           # previous changes every 5 min, inside a 10-min block
            v.observe(1000 + 5 * i, 0, i % 7)
        self.assertEqual(v.status, "failed")

    def _fail(self, v, at):
        for i in range(60):                           # 5-min windows: violations
            v.observe(1000 + 5 * i, 0, i % 7, at)
        self.assertEqual(v.status, "failed")

    def _good(self, v, u, at, blocks=30):
        for block in range(blocks):
            v.observe(u + 10 * block + 1, 0, 2 + block % 2, at + timedelta(minutes=10 * block + 1))
            v.observe(u + 10 * block + 6, 1, 2 + block % 2, at + timedelta(minutes=10 * block + 6))

    def test_failed_retests_24h_later_and_can_pass(self):                 # final review I3
        v = W.WindowValidator()
        self._fail(v, T0)
        self.assertEqual(v.to_dict()["failed_at"], T0.isoformat())
        r = W.WindowValidator(v.to_dict())                                # persisted across a restart
        self._good(r, 5000, T0 + timedelta(hours=24))
        self.assertEqual(r.status, "passed")
        self.assertIsNone(r.failed_at)

    def test_failed_not_retested_before_24h(self):
        v = W.WindowValidator()
        self._fail(v, T0)
        frozen = v.to_dict()
        self._good(v, 5000, T0 + timedelta(hours=18))                    # ends 23 h 2 min after the fail
        self.assertEqual(v.status, "failed")
        self.assertEqual(v.to_dict(), frozen)                             # nothing counted, failed_at kept

    def test_old_failed_state_without_failed_at_starts_its_24h_clock(self):
        v = W.WindowValidator({"status": "failed", "same_ok": 0, "violations": 3, "crossings": 9, "silent": 0})
        v.observe(5001, 0, 2, T0)
        self.assertEqual(v.status, "failed")
        self.assertEqual(v.failed_at, T0)
        self._good(v, 6000, T0 + timedelta(hours=24))
        self.assertEqual(v.status, "passed")

    def test_only_informative_crossings_count(self):                      # the INFORMATIVE_MIN filter
        def crossing(old_prev, new_prev, old_cur=0, new_cur=0):
            v = W.WindowValidator()
            v.observe(1005, old_cur, old_prev)                            # block 100
            v.observe(1012, new_cur, new_prev)                            # block 101: a crossing
            return v.crossings, v.silent
        self.assertEqual(crossing(0, 0), (0, 0))                          # 0 -> 0: not counted
        self.assertEqual(crossing(0, 0, 3, 0), (0, 0))                    # not even with current falling
        self.assertEqual(crossing(0, 1), (1, 0))                          # 0 -> 1: counted, a rollover
        self.assertEqual(crossing(1, 0), (1, 0))                          # 1 -> 0: counted, a rollover
        self.assertEqual(crossing(1, 1), (1, 1))                          # 1 -> 1, current not falling: silent
        self.assertEqual(crossing(1, 1, 2, 2), (1, 1))
        self.assertEqual(crossing(1, 1, 2, 0), (1, 0))                    # 1 -> 1, current fell: a rollover

    def test_state_round_trip_keeps_status(self):
        v = W.WindowValidator({"status": "passed", "same_ok": 30, "violations": 0, "crossings": 30, "silent": 2})
        self.assertEqual(W.WindowValidator(v.to_dict()).status, "passed")


class Replay(unittest.TestCase):
    """The trial (HANDOFF 2026-09-30): 200/201 windows captured per monitor, one gap at 06:00."""

    def replay(self, label):
        log, v = W.WindowLog(), W.WindowValidator()
        for r in trial_rows(label):
            t = datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00"))
            u, cur, prev = int(r["uptime_minutes"]), int(r["counts_current"]), int(r["counts_previous"])
            v.observe(u, cur, prev)
            w = W.captured_window(t, u, prev)
            if w:
                log.add(w)
        idx = sorted(w.index for w in log.all())
        missing = [k for k in range(idx[0], idx[-1]) if k not in set(idx)]
        return idx, missing, v

    def test_unit_b(self):
        idx, missing, v = self.replay("0002")
        self.assertEqual(missing, [23603])            # window ending at uptime 236040 (06:00 quiet window)
        self.assertEqual(v.status, "passed")

    def test_unit_a(self):
        idx, missing, v = self.replay("0001")
        self.assertEqual(missing, [280])              # window ending at uptime 2810
        self.assertEqual(v.status, "passed")


if __name__ == "__main__":
    unittest.main()
