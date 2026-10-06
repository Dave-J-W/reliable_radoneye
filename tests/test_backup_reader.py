"""extras/backup_reader/rd200_counts.py: self-aligned slots (+3:30/+8:30, never after +9:00) and the 24 h ring."""

import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye/core"))   # windows.py, as deployed
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye"))        # protocol.py
_spec = importlib.util.spec_from_file_location("rd200_counts", ROOT / "extras/backup_reader/rd200_counts.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)
import windows as W  # noqa: E402

T = datetime(2026, 10, 1, 17, 3, 0, tzinfo=timezone.utc)


class Plan(unittest.TestCase):
    def test_unaligned_until_first_read_then_slots(self):
        p = rc.SelfAlignedPlan(T)
        self.assertEqual(p.due(T), 1)
        p.done(T + timedelta(seconds=4), 5000, True, 1)       # read just after a rollover: computed at T+4s
        roll = T + timedelta(seconds=4)
        self.assertEqual(p.pending[0], roll + timedelta(seconds=210))
        self.assertIsNone(p.due(roll + timedelta(seconds=209)))
        self.assertEqual(p.due(roll + timedelta(seconds=210)), 1)
        p.done(roll + timedelta(seconds=214), 5003, True, 1)  # re-anchors (1-min uptime resolution)
        self.assertEqual(p.pending[0], p.anchor + timedelta(seconds=510))
        self.assertLess(abs(p.pending[0] - (roll + timedelta(seconds=510))), timedelta(minutes=1))

    def test_one_retry_then_skip_after_nine_minutes(self):
        p = rc.SelfAlignedPlan(T)
        p.done(T, 5000, True, 1)
        roll = T
        p.done(roll + timedelta(seconds=215), None, False, 1)
        self.assertEqual(p.pending[:2], (roll + timedelta(seconds=235), 2))   # 20 s after the failure
        p.done(roll + timedelta(seconds=245), None, False, 2)
        self.assertEqual(p.pending[0], roll + timedelta(seconds=510))
        late = roll + timedelta(seconds=541)
        self.assertIsNone(p.due(late))                        # would start after +9:00: skipped
        self.assertEqual(p.pending[0], roll + timedelta(minutes=10, seconds=210))


    def test_late_retry_after_nine_minutes_is_skipped(self):
        p = rc.SelfAlignedPlan(T)
        p.done(T, 5000, True, 1)
        roll = T
        p.done(roll + timedelta(seconds=215), None, False, 2)     # now waiting for the +510 slot
        self.assertEqual(p.pending[0], roll + timedelta(seconds=510))
        p.done(roll + timedelta(seconds=530), None, False, 1)     # failed late in the slot
        self.assertEqual(p.pending[:2], (roll + timedelta(seconds=550), 2))
        self.assertIsNone(p.due(roll + timedelta(seconds=550)))   # retry would start after +9:00
        self.assertEqual(p.pending[0], roll + timedelta(minutes=10, seconds=210))


class ParseSince(unittest.TestCase):
    def test_forms(self):
        want = datetime(2026, 10, 1, 17, 0, 0, tzinfo=timezone.utc)
        for s in ("2026-10-01T17:00:00+00:00", "2026-10-01T17:00:00 00:00", "2026-10-01T17:00:00Z",
                  "2026-10-01T17:00:00"):
            self.assertEqual(rc.parse_since(s), want, s)

    def test_junk(self):
        with self.assertRaises(ValueError):
            rc.parse_since("yesterday")


class RingTests(unittest.TestCase):
    def test_corrupt_file_starts_empty_and_is_set_aside(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ring.json")
            with open(path, "w") as f:
                f.write("{not json")
            r = rc.Ring(path)
            self.assertEqual(r.since("S1", T - timedelta(days=2)), [])
            self.assertTrue(os.path.exists(path + ".bad"))
            self.assertFalse(os.path.exists(path))

    def test_persisted_deduped_and_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ring.json")
            r = rc.Ring(path)
            w1 = W.captured_window(T, 5003, 2, source="backup")
            self.assertTrue(r.add("S1", w1))
            self.assertFalse(r.add("S1", W.captured_window(T + timedelta(minutes=5), 5008, 2, source="backup")))
            r.add("S1", W.captured_window(T - timedelta(hours=30), 3203, 1, source="backup"))
            r.save()
            r2 = rc.Ring(path)
            got = r2.since("S1", T - timedelta(hours=48))
            self.assertEqual([g["count"] for g in got], [2])   # >24 h old pruned
            self.assertEqual(r2.since("S1", T), [])


if __name__ == "__main__":
    unittest.main()
