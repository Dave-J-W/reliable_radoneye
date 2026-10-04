"""Tests for custom_components/radoneye_log/protocol.py against real RD200V3 status packets.

Run with:
    python -m unittest discover -s tests -v
Packets captured 2026-09-29 from two RD200V3 units (command 0x40, read-only); the expected values
were cross-checked against another integration reading the same monitors at the same time.
"""

import importlib.util
import pathlib
import unittest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "custom_components/radoneye_log/protocol.py"
_spec = importlib.util.spec_from_file_location("radoneye_protocol", _PATH)
protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol)

PKT_B = bytearray.fromhex(
    "4042585830315245303030303032075244323030563356332e302e3100009400061d00240020000100060043950300"
    "000000007701020000003822b200000000c2f5683f")
PKT_A = bytearray.fromhex(
    "4042585830315245303030303031075244323030563356332e302e31000194000633001a000000000003003d060000"
    "000000007500020000003822f900000000c2f5683f")


class ParseStatus(unittest.TestCase):
    def test_counts_B(self):
        s = protocol.parse_status(PKT_B)
        self.assertEqual(s["counts_current"], 1)
        self.assertEqual(s["counts_previous"], 6)

    def test_counts_A(self):
        s = protocol.parse_status(PKT_A)
        self.assertEqual(s["counts_current"], 0)
        self.assertEqual(s["counts_previous"], 3)

    def test_existing_fields_unchanged(self):
        s = protocol.parse_status(PKT_B)
        self.assertEqual(s["serial"], "XX01RE000002")
        self.assertEqual(s["model"], "RD200V3")
        self.assertEqual(s["latest_bq_m3"], 29)
        self.assertEqual(s["uptime_minutes"], 234819)
        self.assertEqual(s["peak_pci_l"], 10.14)

    def test_bq_averages_exposed(self):
        s = protocol.parse_status(PKT_A)
        self.assertEqual(s["day_avg_bq_m3"], 26)
        self.assertEqual(s["month_avg_bq_m3"], 0)
        self.assertEqual(s["peak_bq_m3"], 117)

    def test_raw_hex_round_trips(self):
        s = protocol.parse_status(PKT_A)
        self.assertEqual(bytearray.fromhex(s["raw_hex"]), PKT_A)


if __name__ == "__main__":
    unittest.main()
