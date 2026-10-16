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


STATUS = {"serial": "S1", "model": "RD200", "firmware_version": "V3.0.1", "uptime_minutes": 5003,
          "latest_bq_m3": 18, "latest_pci_l": 0.49, "day_avg_bq_m3": 15, "day_avg_pci_l": 0.41,
          "month_avg_bq_m3": 13, "month_avg_pci_l": 0.35, "counts_current": 1, "counts_previous": 2,
          "peak_bq_m3": 92, "peak_pci_l": 2.49, "raw_hex": "00ff"}


class LatestTests(unittest.TestCase):                    # revision 3: device values for HA while its link fails
    def test_record_keeps_device_values_uptime_rssi_and_time_only(self):
        rec = rc.latest_record(STATUS, -84, T)
        self.assertEqual(rec, {"read_at_utc": T.isoformat(), "uptime_minutes": 5003, "rssi": -84,
                               "latest_bq_m3": 18, "latest_pci_l": 0.49, "day_avg_bq_m3": 15, "day_avg_pci_l": 0.41,
                               "month_avg_bq_m3": 13, "month_avg_pci_l": 0.35, "peak_bq_m3": 92, "peak_pci_l": 2.49})

    def test_persisted_beside_ring_and_missing_or_corrupt_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "latest.json")
            self.assertIsNone(rc.Latest(path).get("S1"))                       # no file yet (old installs)
            lt = rc.Latest(path)
            lt.set("s1", rc.latest_record(STATUS, -84, T))
            lt.save()
            self.assertEqual(rc.Latest(path).get("S1")["latest_pci_l"], 0.49)
            with open(path, "w") as f:
                f.write("[broken")
            self.assertIsNone(rc.Latest(path).get("S1"))
            self.assertTrue(os.path.exists(path + ".bad"))

    def test_failed_save_is_logged_not_raised(self):                       # review: a good read stays good
        class Broken:
            def save(self):
                raise OSError("disk full")
        with self.assertLogs("rd200_backup", "ERROR") as logs:
            self.assertFalse(rc.save_quietly(Broken(), "latest"))
        self.assertIn("latest", logs.output[0])
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(rc.save_quietly(rc.Latest(os.path.join(d, "latest.json")), "latest"))

    def test_routes_latest_404_unknown_and_windows_status_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            mons = [{"address": "AA:BB", "serial": "S1", "label": "one"}]
            svc = rc.Service(monitors=mons, ring_path=os.path.join(d, "ring.json"),
                             latest_path=os.path.join(d, "latest.json"))
            self.assertEqual(svc.route("GET", "/latest?serial=S1")[0], 404)     # nothing read yet
            svc.latest.set("S1", rc.latest_record(STATUS, -84, T))
            code, body = svc.route("GET", "/latest?serial=s1")
            self.assertEqual((code, body["latest_pci_l"], body["read_at_utc"]), (200, 0.49, T.isoformat()))
            self.assertEqual(svc.route("GET", "/latest?serial=NOPE")[0], 404)
            self.assertEqual(svc.route("GET", "/latest")[0], 404)
            svc.ring.add("S1", W.captured_window(T, 5003, 2, source="backup"))
            code, body = svc.route("GET", "/windows?serial=S1&since=2026-10-01T00:00:00Z")
            self.assertEqual((code, [r["count"] for r in body]), (200, [2]))
            self.assertEqual(svc.route("GET", "/windows?serial=S1&since=junk")[0], 400)
            code, body = svc.route("GET", "/status")
            self.assertEqual((code, sorted(body)), (200, ["last", "monitors", "pending"]))


if __name__ == "__main__":
    unittest.main()
