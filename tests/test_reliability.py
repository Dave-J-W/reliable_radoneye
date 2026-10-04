"""core/reliability.py: first-attempt success per fixed 4 h block; radio-time share."""

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/radoneye_log"))
from core import reliability as R  # noqa: E402

TZ = ZoneInfo("America/Chicago")


def local(h, m=0, day=30):
    return datetime(2026, 9, day, h, m, tzinfo=TZ)


class Blocks(unittest.TestCase):
    def test_value_describes_the_previous_block(self):
        b = R.ReliabilityBlocks()
        for i in range(24):                      # 08:00-11:59: 24 reads, 18 first-attempt ok
            b.record(local(8, i * 10 % 60) + timedelta(hours=i // 6), i % 4 != 0)
        self.assertIsNone(b.value())             # block still open
        b.roll(local(12, 1))
        self.assertEqual(b.value(), 75.0)
        self.assertEqual(b.last_start(), local(8).isoformat())

    def test_a_block_with_no_reads_is_unknown(self):
        b = R.ReliabilityBlocks()
        b.record(local(8, 5), True)
        b.roll(local(20, 1))                      # HA down 12:00-20:00
        self.assertIsNone(b.value())
        self.assertEqual(b.last_start(), local(16).isoformat())

    def test_round_trip(self):
        b = R.ReliabilityBlocks()
        b.record(local(8, 5), True)
        b.roll(local(12, 1))
        self.assertEqual(R.ReliabilityBlocks(b.to_dict()).value(), 100.0)

    def test_an_earlier_block_never_rolls_back(self):
        b = R.ReliabilityBlocks()
        b.record(local(15, 50), True)
        b.record(local(15, 55), False)
        b.roll(local(16, 0) + timedelta(seconds=5))
        b.record(local(15, 59) + timedelta(seconds=58), True)    # late-recorded slot of the closed block
        self.assertEqual(b.value(), 50.0)
        self.assertEqual(b.last_start(), local(12).isoformat())
        self.assertEqual(b.current, local(16))
        self.assertEqual((b.ok, b.total), (1, 1))                # counted into the current block

    def test_restored_block_across_fall_back(self):
        state = {"current": datetime(2026, 11, 1, 0, 0, tzinfo=TZ).isoformat(), "ok": 10, "total": 20, "last": None}
        self.assertEqual(state["current"], "2026-11-01T00:00:00-05:00")   # restored as a fixed offset
        b = R.ReliabilityBlocks(state)
        b.roll(datetime(2026, 11, 1, 4, 1, tzinfo=TZ))                     # 04:01 CST, 5 real hours later
        self.assertEqual(b.value(), 50.0)
        self.assertEqual(b.last_start(), "2026-11-01T00:00:00-05:00")


class Radio(unittest.TestCase):
    def test_share_of_last_hour(self):
        r = R.RadioTime()
        now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        r.add(now - timedelta(minutes=90), 300)   # outside the hour
        r.add(now - timedelta(minutes=10), 36)
        r.add(now - timedelta(minutes=5), 36)
        self.assertEqual(r.share(now), 2.0)


if __name__ == "__main__":
    unittest.main()
