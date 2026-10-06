"""core/engine.py simulated against fake RD200s (spec "Testing" - engine simulations)."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tests.sim import FakeRD200, poisson_counts, simulate
from core.engine import MonitorEngine, WriteWindow, FetchBackup, Notify, ReadResult  # sim put core on sys.path
from core.schedule import MIN_GAP, Job
from core.windows import Window

TZ = ZoneInfo("America/Chicago")
START = datetime(2026, 10, 1, 17, 0, 7, tzinfo=timezone.utc)       # 12:00:07 CDT
PASSED = {"status": "passed", "same_ok": 30, "violations": 0, "crossings": 30, "silent": 3}


def engine(serial="A1", state=None, backup=False, parallel=False, now=START, k=1.27):
    return MonitorEngine(serial, serial, k, TZ, now, state=state if state is not None else {"validator": PASSED},
                         backup=backup, parallel=parallel)


def dev(serial="A1", uptime_min=5003, counts=lambda w: 2):
    return FakeRD200(serial, START - timedelta(minutes=uptime_min, seconds=20), counts)


class Timing(unittest.TestCase):
    def test_aligned_reads_at_plus_one_and_plus_six(self):
        e, d = engine(), dev()
        _, done = simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=1))
        ab = [j for j in done if j.kind in ("A", "B")]
        self.assertGreater(len(ab), 10)
        for j in ab:
            off = (j.slot - j.rollover).total_seconds()
            self.assertIn(off, (60, 360))
            true_roll = d.boot_utc + timedelta(minutes=10 * ((j.rollover - d.boot_utc).total_seconds() // 600))
            self.assertLessEqual(abs((j.rollover - true_roll).total_seconds()), 60)


class Capture(unittest.TestCase):
    def test_a_day_without_failures_captures_every_window_once(self):
        e = engine()
        acts, _ = simulate({"A1": e}, {"A1": dev(counts=lambda w: w % 5)}, START, START + timedelta(hours=24))
        writes = [a for a in acts if isinstance(a, WriteWindow)]
        idx = [a.window.index for a in writes]
        self.assertEqual(len(idx), len(set(idx)))
        self.assertGreaterEqual(len(idx), 143)
        self.assertEqual(idx, list(range(idx[0], idx[0] + len(idx))))
        s = e.entity_states(START + timedelta(hours=24))
        self.assertEqual(s["window_capture_24h"], 100.0)
        self.assertEqual(s["missed_windows_24h"], 0)
        self.assertIsNotNone(s["radon_counts_24h"])

    def test_failed_a_is_covered_by_b_and_lowers_reliability(self):
        e = engine()
        fails = lambda serial, t, job: job.kind == "A"
        acts, _ = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=9))
        acts_f, _ = simulate({"A1": (e2 := engine())}, {"A1": dev()}, START, START + timedelta(hours=9), fails)
        cut = START + timedelta(hours=9) - timedelta(minutes=7)     # B (+6:00) of later windows falls past the horizon
        n_ok = len([a for a in acts if isinstance(a, WriteWindow) and a.window.end_utc <= cut])
        n_f = len([a for a in acts_f if isinstance(a, WriteWindow) and a.window.end_utc <= cut])
        self.assertEqual(n_ok, n_f)                                  # B caught every window
        self.assertLessEqual(e2.entity_states(START + timedelta(hours=9))["first_attempt_success_4h"], 50.0)

    def test_window_lost_without_backup_is_missed_and_not_fetched(self):
        e = engine()
        bad = (START + timedelta(hours=2), START + timedelta(hours=2, minutes=10))
        fails = lambda serial, t, job: bad[0] <= t < bad[1] + timedelta(minutes=8) and job.kind in ("A", "B")
        acts, _ = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=4), fails)
        self.assertFalse([a for a in acts if isinstance(a, FetchBackup)])
        self.assertGreaterEqual(e.entity_states(START + timedelta(hours=4))["missed_windows_24h"], 1)

    def test_window_lost_with_backup_asks_the_backup(self):
        e = engine(backup=True)
        bad = (START + timedelta(hours=2), START + timedelta(hours=2, minutes=10))
        fails = lambda serial, t, job: bad[0] <= t < bad[1] + timedelta(minutes=8) and job.kind in ("A", "B")
        acts, _ = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=4), fails)
        self.assertTrue([a for a in acts if isinstance(a, FetchBackup)])


class Guards(unittest.TestCase):
    def test_next_slot_keeps_min_gap_when_anchor_moves_later(self):
        e = engine()
        e.anchor = roll = START
        a = Job(roll + timedelta(minutes=10), roll + timedelta(seconds=60), "A1", "A", roll + timedelta(seconds=60), roll)
        fin = roll + timedelta(seconds=62)
        d = FakeRD200("A1", fin - timedelta(minutes=5000, seconds=30))    # uptime 5000 min: new anchor = fin
        jobs, _ = e.on_result(a, ReadResult(True, fin, d.status(fin), -70), fin)
        self.assertEqual(e.anchor, fin)                                 # anchor moved 62 s later
        chain = [j for j in jobs if j.kind != "parallel"]
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].kind, "B")
        self.assertGreaterEqual(chain[0].slot - a.slot, MIN_GAP)

    def test_failed_b_outside_counts_mode_is_not_missed_or_fetched(self):
        e = engine(state={"validator": {"status": "pending"}}, backup=True)
        roll = START
        b = Job(roll + timedelta(minutes=10), roll + timedelta(seconds=420), "A1", "B", roll + timedelta(seconds=360),
                roll, attempt=3)
        _, acts = e.on_result(b, ReadResult(False, b.due + timedelta(seconds=5), error="TimeoutError"), b.due)
        self.assertFalse([x for x in acts if isinstance(x, FetchBackup)])
        self.assertEqual(e.entity_states(b.due)["missed_windows_24h"], 0)


class Restart(unittest.TestCase):
    def test_store_round_trip_keeps_measurements_and_gap_is_not_observed(self):
        d = dev()
        e = engine()
        simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=3))
        state = e.to_state(START + timedelta(hours=3))
        later = START + timedelta(hours=5)                          # HA down 2 h, no backup
        e2 = engine(state=state, now=later)
        self.assertEqual(len(e2.log), len(e.log))
        simulate({"A1": e2}, {"A1": d}, later, later + timedelta(hours=1))
        s = e2.entity_states(later + timedelta(hours=1))
        self.assertEqual(s["missed_windows_24h"], 0)                # downtime is not "missed"
        self.assertEqual(s["window_capture_24h"], 100.0)

    def test_device_values_unavailable_until_first_read_after_restart(self):
        e = engine()
        self.assertTrue(e.entity_states(START)["stale"])
        self.assertIsNone(e.entity_states(START)["radon"])


class Reboot(unittest.TestCase):
    def test_reboot_notified_and_averages_unknown_for_an_hour(self):
        boot1 = START - timedelta(hours=10)
        d = FakeRD200("A1", boot1)
        e = engine()
        simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=1))
        d.boot_utc = START + timedelta(hours=1, minutes=2)          # power cut
        acts, _ = simulate({"A1": e}, {"A1": d}, START + timedelta(hours=1, minutes=3), START + timedelta(hours=1, minutes=40))
        self.assertTrue([a for a in acts if isinstance(a, Notify) and a.kind == "reboot"])
        s = e.entity_states(START + timedelta(hours=1, minutes=40))
        self.assertIsNotNone(s["radon"])
        self.assertIsNone(s["radon_1_day_level"])


class Pull(unittest.TestCase):
    def test_first_a_after_six_is_a_pull_with_one_retry_then_next_window(self):
        start = datetime(2026, 10, 2, 10, 50, 7, tzinfo=timezone.utc)   # 05:50 CDT
        e = engine(now=start)
        tries = []

        def fails(serial, t, job):
            if job.kind == "pull":
                tries.append(job)
                return len(tries) <= 2                             # first window's pull fails twice
            return False
        _, done = simulate({"A1": e}, {"A1": dev()}, start, start + timedelta(minutes=40), fails)
        pulls = [j for j in done if j.kind == "pull"]
        self.assertEqual([p.attempt for p in pulls], [1, 2, 1])
        self.assertEqual((pulls[1].due - pulls[0].due).total_seconds(), 60)
        self.assertGreaterEqual(pulls[0].slot.astimezone(TZ).hour, 6)
        self.assertEqual(len([j for j in done if j.kind == "pull" and j.attempt == 1]), 2)  # none after success


class Staleness(unittest.TestCase):
    def test_device_values_go_unavailable_but_reliability_keeps_reporting(self):
        e = engine()
        d = dev()
        simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=4))
        dead_from = START + timedelta(hours=4)
        simulate({"A1": e}, {"A1": d}, dead_from, START + timedelta(hours=9), fails=lambda s, t, j: True)
        s = e.entity_states(START + timedelta(hours=9))
        self.assertTrue(s["stale"])
        self.assertIsNone(s["radon"])
        self.assertEqual(s["first_attempt_success_4h"], 0.0)
        acts = e.check(START + timedelta(hours=9))
        self.assertTrue([a for a in acts if isinstance(a, Notify) and a.kind == "unreachable"]
                        or e.unreachable_notified)


class ManyMonitors(unittest.TestCase):
    def test_five_monitors_one_dead_and_a_power_cut_phase(self):
        boot = START - timedelta(minutes=50003)                      # all share a phase (power cut)
        devices = {f"M{i}": FakeRD200(f"M{i}", boot) for i in range(5)}
        engines = {s: engine(serial=s) for s in devices}
        fails = lambda serial, t, job: serial == "M4"
        acts, _ = simulate(engines, devices, START, START + timedelta(hours=6), fails)
        for s in ("M0", "M1", "M2", "M3"):
            self.assertEqual(engines[s].entity_states(START + timedelta(hours=6))["window_capture_24h"], 100.0, s)
        self.assertTrue(engines["M4"].entity_states(START + timedelta(hours=6))["stale"])


class Validation(unittest.TestCase):
    def test_no_windows_until_validated_then_aligned(self):
        e = engine(state={})                                         # fresh monitor: validator pending
        acts, done = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=8))
        first_write = next(a for a in acts if isinstance(a, WriteWindow))
        self.assertGreaterEqual(first_write.window.read_at_utc, START + timedelta(hours=3))  # >= 20 same-block pairs first
        self.assertEqual(done[0].kind, "free")
        self.assertIn("A", [j.kind for j in done])
        self.assertTrue(e.counts_mode)


class Retest(unittest.TestCase):                                            # final review I3
    FAILED = {"status": "failed", "same_ok": 5, "violations": 0, "crossings": 60, "silent": 25}

    def test_failed_validation_retests_24h_later_and_passes(self):
        state = {"validator": {**self.FAILED, "failed_at": (START - timedelta(hours=20)).isoformat()}}
        e = engine(state=state)
        acts, done = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=10))
        first_write = next(a for a in acts if isinstance(a, WriteWindow))
        self.assertGreaterEqual(first_write.window.read_at_utc, START + timedelta(hours=4))   # device values until then
        self.assertTrue(e.counts_mode)
        passed = [a for a in acts if isinstance(a, Notify) and a.kind == "validation_passed"]
        self.assertEqual(len(passed), 1)                                    # the hub deletes the repair issue
        self.assertTrue(all(j.kind == "free" for j in done if j.slot < START + timedelta(hours=4)))

    def test_failed_validation_not_retested_before_24h(self):
        state = {"validator": {**self.FAILED, "failed_at": (START - timedelta(hours=1)).isoformat()}}
        e = engine(state=state)
        acts, done = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=22))
        self.assertEqual(e.validator.status, "failed")
        self.assertFalse([a for a in acts if isinstance(a, WriteWindow)])
        self.assertTrue(all(j.kind == "free" for j in done))


class MissedSince(unittest.TestCase):                                       # final review I4
    def test_earliest_missed_window_in_the_last_24h(self):
        e = engine()
        now = START + timedelta(hours=30)
        e.outcomes = {(now - timedelta(hours=25)).isoformat(): "missed",          # too old
                      (now - timedelta(hours=20)).isoformat(): "captured",
                      (now - timedelta(hours=10)).isoformat(): "missed",
                      (now - timedelta(hours=3)).isoformat(): "missed",
                      (now - timedelta(hours=2)).isoformat(): "not_observed"}
        self.assertEqual(e.missed_since(now), now - timedelta(hours=10))
        e.outcomes[(now - timedelta(hours=10)).isoformat()] = "captured"           # the backup filled it
        self.assertEqual(e.missed_since(now), now - timedelta(hours=3))

    def test_none_when_nothing_missed(self):
        e = engine()
        self.assertIsNone(e.missed_since(START))
        e.outcomes = {(START - timedelta(hours=1)).isoformat(): "captured"}
        self.assertIsNone(e.missed_since(START))

    def test_missed_window_is_filled_by_a_later_backup_fetch(self):
        e = engine(backup=True)
        bad = (START + timedelta(hours=2), START + timedelta(hours=2, minutes=10))
        fails = lambda serial, t, job: bad[0] <= t < bad[1] + timedelta(minutes=8) and job.kind in ("A", "B")
        d = dev()
        simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=3), fails)
        now = START + timedelta(hours=3)
        since = e.missed_since(now)
        self.assertIsNotNone(since)
        missed = [datetime.fromisoformat(k) for k, v in e.outcomes.items() if v == "missed"]
        backup = [Window(m, 0, 2, "backup", m + timedelta(seconds=210)) for m in missed]
        e.on_backup(backup, now)
        self.assertIsNone(e.missed_since(now))


class Factor(unittest.TestCase):
    def test_factor_change_rescales_and_is_logged(self):
        e = engine()
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=2))
        v1 = e.entity_states(START + timedelta(hours=2))["radon_counts_1h"].value_pci
        state = e.to_state(START + timedelta(hours=2))
        e2 = engine(state=state, k=2.54, now=START + timedelta(hours=2))
        e2.log = e.log
        self.assertAlmostEqual(e2.entity_states(START + timedelta(hours=2))["radon_counts_1h"].value_pci, v1 / 2)
        self.assertEqual(e2.to_state(START + timedelta(hours=2))["factor_log"][-1]["new"], 2.54)


class ReviewFixes(unittest.TestCase):
    def test_late_slot_after_block_boundary_keeps_closed_block(self):          # I1
        e = engine()
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=3, minutes=55))
        boundary = datetime(2026, 10, 1, 16, 0, tzinfo=TZ).astimezone(timezone.utc)
        s = e.entity_states(boundary + timedelta(seconds=5))
        self.assertEqual(s["first_attempt_success_4h"], 100.0)
        slot = boundary - timedelta(seconds=2)                                   # queued 15:59:58, run late
        late = Job(slot - timedelta(seconds=60) + timedelta(minutes=10), slot, "A1", "A", slot,
                   slot - timedelta(seconds=60))
        e.on_result(late, ReadResult(False, boundary + timedelta(seconds=10), error="TimeoutError"),
                    boundary + timedelta(seconds=10))
        s = e.entity_states(boundary + timedelta(seconds=15))
        self.assertEqual(s["first_attempt_success_4h"], 100.0)
        self.assertEqual(s["reliability_block_start"], datetime(2026, 10, 1, 12, 0, tzinfo=TZ).isoformat())

    def test_failures_after_restart_are_missed_but_downtime_is_not(self):     # I2
        d = dev()
        e = engine()
        simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=3))
        state = e.to_state(START + timedelta(hours=3))
        later = START + timedelta(hours=5)                                      # HA down 2 h
        e2 = engine(state=state, now=later)
        fails = lambda serial, t, job: t < later + timedelta(hours=2)          # then 2 h of failed reads
        simulate({"A1": e2}, {"A1": d}, later, later + timedelta(hours=3), fails)
        s = e2.entity_states(later + timedelta(hours=3))
        self.assertGreaterEqual(s["missed_windows_24h"], 11)
        for key, v in e2.outcomes.items():
            end = datetime.fromisoformat(key)
            if START + timedelta(hours=3) < end <= later:
                self.assertEqual(v, "not_observed", key)
            if later + timedelta(minutes=10) < end <= later + timedelta(hours=1, minutes=50):
                self.assertEqual(v, "missed", key)

    def test_gap_marking_is_clamped_to_keep(self):                               # I2 clamp
        e = engine()
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=1))
        state = e.to_state(START + timedelta(hours=1))
        later = START + timedelta(days=30)
        d = FakeRD200("A1", later - timedelta(minutes=50003))
        e2 = engine(state=state, now=later)
        simulate({"A1": e2}, {"A1": d}, later, later + timedelta(minutes=20))
        oldest = min(datetime.fromisoformat(k) for k, v in e2.outcomes.items() if v == "not_observed")
        self.assertGreaterEqual(oldest, later - timedelta(days=8) - timedelta(minutes=10))

    def test_reboot_while_ha_down_is_detected_after_restore(self):             # I3
        d = FakeRD200("A1", START - timedelta(hours=10))
        e = engine()
        simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=1))
        state = e.to_state(START + timedelta(hours=1))
        d.boot_utc = START + timedelta(hours=2)                                 # power cut while HA is down
        later = START + timedelta(hours=2, minutes=30)
        e2 = engine(state=state, now=later)
        acts, _ = simulate({"A1": e2}, {"A1": d}, later, later + timedelta(minutes=20))
        self.assertTrue([a for a in acts if isinstance(a, Notify) and a.kind == "reboot"])
        self.assertIsNone(e2.entity_states(later + timedelta(minutes=20))["radon_1_day_level"])

    def test_request_pull_before_six_pulls_in_next_window(self):              # I4
        start = datetime(2026, 10, 2, 7, 0, 7, tzinfo=timezone.utc)            # 02:00 CDT
        e = engine(now=start)
        e.request_pull()
        _, done = simulate({"A1": e}, {"A1": dev()}, start, start + timedelta(minutes=40))
        pulls = [j for j in done if j.kind == "pull"]
        self.assertEqual(len(pulls), 1)                                        # flag cleared by the pull
        self.assertLessEqual(pulls[0].slot - start, timedelta(minutes=12))
        self.assertEqual(pulls[0].slot.astimezone(TZ).hour, 2)

    def test_counts_values_available_right_after_restore(self):               # Minor 1
        e = engine()
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=2))
        at = START + timedelta(hours=2)
        e2 = engine(state=e.to_state(at), now=at)
        self.assertIsNotNone(e2.entity_states(at)["radon_counts_1h"])


class Persistence(unittest.TestCase):                                       # final review I2
    def test_new_format_round_trip(self):
        e = engine()
        simulate({"A1": e}, {"A1": dev(counts=lambda w: w % 5)}, START, START + timedelta(hours=3))
        at = START + timedelta(hours=3)
        state = json.loads(json.dumps(e.to_state(at)))                      # what the Store gives back
        self.assertTrue(all(isinstance(r, str) for r in state["windows"]))
        e2 = engine(state=state, now=at)
        self.assertEqual(len(e2.log), len(e.log))
        for a, b in zip(e.log.all(), e2.log.all()):
            self.assertLessEqual(abs((a.end_utc - b.end_utc).total_seconds()), 0.5)
            self.assertEqual((a.index, a.count, a.source, a.device_bq), (b.index, b.count, b.source, b.device_bq))
        self.assertEqual(e2.entity_states(at)["radon_counts_1h"].n, e.entity_states(at)["radon_counts_1h"].n)
        self.assertEqual(e2.entity_states(at)["window_capture_24h"], 100.0)

    def test_old_dict_format_still_loads(self):
        e = engine()
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=3))
        at = START + timedelta(hours=3)
        state = e.to_state(at)
        state["windows"] = [w.to_dict() for w in e.log.all()]               # the live Store today
        e2 = engine(state=json.loads(json.dumps(state)), now=at)
        self.assertEqual(e2.log.all(), e.log.all())
        self.assertIsNotNone(e2.entity_states(at)["radon_counts_1h"])

    def test_outcomes_trimmed_to_25h_windows_kept_8_days(self):
        e = engine()
        at = START + timedelta(days=8)
        for i in range(8 * 144):
            end = at - timedelta(minutes=10 * i, seconds=7)
            e.log.add(Window(end, 30000 - i, 2, "ha", end + timedelta(seconds=61), 10))
            e.outcomes[end.isoformat()] = "captured"
        state = e.to_state(at)
        self.assertEqual(len(state["windows"]), 8 * 144)                    # KEEP = 8 days, unchanged
        oldest = min(datetime.fromisoformat(k) for k in state["outcomes"])
        self.assertGreaterEqual(oldest, at - timedelta(hours=25))
        self.assertGreaterEqual(len(state["outcomes"]), 149)

    def test_full_8_day_two_monitor_state_under_200kb(self):
        data = {}
        at = START + timedelta(days=8)
        for serial in ("RU22102020002", "RU22102020001"):
            e = engine(serial=serial)
            simulate({serial: e}, {serial: dev(serial=serial)}, START, START + timedelta(hours=1))
            for i in range(8 * 144):                                         # realistic: microseconds, 5-digit index
                end = at - timedelta(minutes=10 * i, seconds=7, microseconds=123456)
                e.log.add(Window(end, 23603 - i, 12, "ha" if i % 9 else "backup",
                                 end + timedelta(seconds=61, microseconds=654321), 123 if i % 9 else None))
                e.outcomes[end.isoformat()] = "captured" if i % 50 else "missed"
            data[serial] = e.to_state(at)
        size = len(json.dumps(data, indent=2).encode())                     # HA's Store writes indent-2 JSON
        self.assertLess(size, 200_000, size)


class Parallel(unittest.TestCase):
    def test_rd200_ble_slot_at_plus_270(self):
        e = engine(parallel=True)
        _, done = simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=1))
        par = [j for j in done if j.kind == "parallel"]
        self.assertGreaterEqual(len(par), 5)
        self.assertTrue(all((j.slot - j.rollover).total_seconds() == 270 for j in par))

    def test_free_mode_also_pokes_rd200_ble_once_per_window(self):            # final review: live defect
        e = engine(state={}, parallel=True)                                 # fresh monitor: validator pending
        d = dev()
        _, done = simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=1))
        self.assertFalse(e.counts_mode)
        par = [j for j in done if j.kind == "parallel"]
        free = [j for j in done if j.kind == "free"]
        self.assertGreaterEqual(len(par), 5)
        self.assertTrue(all((j.slot - j.rollover).total_seconds() == 270 for j in par))
        self.assertEqual(len({j.rollover for j in par}), len(par))          # one poke per window
        for j in par:                                                       # the device's own window
            true_roll = d.boot_utc + timedelta(minutes=10 * ((j.rollover - d.boot_utc).total_seconds() // 600))
            self.assertLessEqual(abs((j.rollover - true_roll).total_seconds()), 60)
        minute = lambda t: t.replace(second=0, microsecond=0)
        self.assertFalse({minute(j.slot) for j in par} & {minute(j.slot) for j in free})

    def test_free_mode_poke_once_per_window_at_every_read_phase(self):
        durations = (2.0, 14.0, 5.5, 11.0, 3.2)                              # real reads vary: the anchor's seconds move
        for phase_s in range(0, 600, 15):                                   # free reads at any offset in the window
            for read_s in (5.0, lambda i: durations[i % len(durations)]):
                e = engine(state={}, parallel=True)
                d = FakeRD200("A1", START - timedelta(minutes=5000, seconds=phase_s))
                _, done = simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=1), read_s=read_s)
                par = [j for j in done if j.kind == "parallel"]
                windows = [(j.rollover - d.boot_utc).total_seconds() // 600 for j in par]
                self.assertEqual(len(set(windows)), len(windows), (phase_s, windows))   # one poke per device window
                self.assertGreaterEqual(len(par), 5, phase_s)


class LowRadonValidation(unittest.TestCase):                               # parked: false fail at low radon
    """Free-mode validation against seeded Poisson devices (tests/sim.poisson_counts)."""

    def validate(self, window_min, mean_per_window, hours, seed):
        e = engine(state={})                                                # fresh monitor: validator pending
        d = FakeRD200("A1", START - timedelta(minutes=5003, seconds=20), poisson_counts(seed, mean_per_window),
                      window_min=window_min)
        acts, _ = simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=hours))
        return e, acts

    def test_low_radon_ten_minute_device_passes(self):
        hours = []
        for seed in range(10):                                              # 0.5 counts/window: 91 % of windows 0 or 1
            with self.subTest(seed=seed):
                e, acts = self.validate(10, 0.5, 14, seed)
                self.assertEqual(e.validator.status, "passed", e.validator.to_dict())
                self.assertFalse([a for a in acts if isinstance(a, Notify) and a.kind == "validation_failed"])
                first_write = next(a for a in acts if isinstance(a, WriteWindow))   # counts mode from the pass on
                hours.append((first_write.window.read_at_utc - START).total_seconds() / 3600)
        self.assertLessEqual(sorted(hours)[8], 7, hours)                    # 9 of 10 within 7 h (a run of 1s: ~13 h)

    def test_longer_windows_fail_at_moderate_counts(self):
        for window_min in (20, 60):                                         # 2 counts per 10 min, like the trial
            for seed in range(3):
                with self.subTest(window_min=window_min, seed=seed):
                    e, acts = self.validate(window_min, 2 * window_min / 10, 24, seed)
                    self.assertEqual(e.validator.status, "failed", e.validator.to_dict())
                    self.assertEqual(len([a for a in acts if isinstance(a, Notify)
                                          and a.kind == "validation_failed"]), 1)
                    self.assertFalse([a for a in acts if isinstance(a, WriteWindow)])

    def test_twenty_minute_windows_fail_at_low_counts(self):
        # Here the filter drops many crossings, and the counted silent share tends to 1/3 at very low counts,
        # just over the 0.30 limit. Simulated (200 seeds): fails by 34 h at 0.2/10 min, by 18 h at 0.5.
        for per_10_min in (0.2, 0.5):
            for seed in range(3):
                with self.subTest(per_10_min=per_10_min, seed=seed):
                    e, acts = self.validate(20, 2 * per_10_min, 72, seed)   # 72 h: the 24 h re-test may follow
                    kinds = [a.kind for a in acts if isinstance(a, Notify) and a.kind.startswith("validation")]
                    self.assertGreaterEqual(kinds.count("validation_failed"), 1, e.validator.to_dict())  # a verdict
                    self.assertNotIn("validation_passed", kinds)
                    self.assertNotEqual(e.validator.status, "passed")
                    self.assertFalse([a for a in acts if isinstance(a, WriteWindow)])        # never in counts mode


class ConflictOnce(unittest.TestCase):                                     # parked: hourly re-fetch re-warned
    def conflicts(self, acts):
        return [a for a in acts if isinstance(a, Notify) and a.kind == "count_conflict"]

    def test_same_conflicting_backup_window_warns_once(self):
        e = engine(backup=True)
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=1))
        w = e.log.all()[-1]                                                 # HA's copy: 2 counts
        stale = Window(w.end_utc + timedelta(seconds=40), w.index, w.count + 3, "backup", w.read_at_utc)
        now = START + timedelta(hours=1)
        acts = []
        for hour in range(3):                                               # the hub's hourly re-fetch
            acts += e.on_backup([stale], now + timedelta(hours=hour))
        self.assertEqual(len(self.conflicts(acts)), 1)
        self.assertEqual(e.log.find(w.end_utc).source, "ha")               # HA's copy kept every time
        state = json.loads(json.dumps(e.to_state(now + timedelta(hours=3))))   # survives a restart
        e2 = engine(state=state, backup=True, now=now + timedelta(hours=3))
        self.assertFalse(self.conflicts(e2.on_backup([stale], now + timedelta(hours=4))))

    def test_warned_windows_pruned_with_the_window_log(self):
        e = engine(backup=True)
        simulate({"A1": e}, {"A1": dev()}, START, START + timedelta(hours=1))
        w = e.log.all()[-1]
        e.on_backup([Window(w.end_utc, w.index, w.count + 3, "backup", w.read_at_utc)], START + timedelta(hours=1))
        self.assertEqual(len(e.to_state(START + timedelta(days=1))["conflicts_warned"]), 1)
        self.assertEqual(e.to_state(START + timedelta(days=9))["conflicts_warned"], [])


class FirmwareChangeWhileFailed(unittest.TestCase):                       # parked: stale repair issue
    FAILED = {"status": "failed", "same_ok": 5, "violations": 0, "crossings": 60, "silent": 25}

    def run_firmware_update(self, validator):
        e = engine(state={"validator": validator, "firmware": "V3.0.1"})
        d = dev()
        d.firmware = "V3.0.2"                                               # updated while HA was away
        acts, _ = simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=10))
        return e, [a.kind for a in acts if isinstance(a, Notify) and a.kind.startswith("validation")]

    def test_pass_after_firmware_change_clears_the_failure(self):
        failed = {**self.FAILED, "failed_at": (START - timedelta(hours=1)).isoformat()}   # re-test not yet due
        e, kinds = self.run_firmware_update(failed)
        self.assertEqual(e.firmware, "V3.0.2")
        self.assertTrue(e.counts_mode)
        self.assertEqual(kinds, ["validation_passed"])                      # the hub deletes the repair issue
        self.assertIsNone(e.validator.failed_at)

    def test_old_failed_state_without_failed_at_also_clears(self):
        e, kinds = self.run_firmware_update(dict(self.FAILED))
        self.assertEqual(kinds, ["validation_passed"])
        self.assertIsNone(e.validator.failed_at)

    def test_pass_after_firmware_change_from_passed_is_silent(self):
        e, kinds = self.run_firmware_update(PASSED)
        self.assertTrue(e.counts_mode)
        self.assertEqual(kinds, [])                                         # no repair issue to clear


if __name__ == "__main__":
    unittest.main()
