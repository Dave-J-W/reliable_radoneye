"""Per-monitor state machine for RadonEye B (spec "Architecture", "Read timing", "Failure handling").

Pure: no Home Assistant, no clock, no I/O. The hub feeds it read results and the time; it returns
follow-up jobs and actions. Everything it needs after a restart is in to_state() (spec "Persistence").
Each monitor always has exactly one pending chain job (plus, in a parallel run, an rd200_ble job).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .radon_math import Derived, derive
from .reliability import ReliabilityBlocks
from .schedule import MIN_GAP, PULL_FROM, UNALIGNED_EVERY, Job, a_or_b_after, free_job, parallel_job, retry_job
from .windows import (SAME_WINDOW, TEN, BootTracker, Window, WindowLog, WindowValidator, captured_window,
                      last_rollover)

STALE_AFTER = timedelta(minutes=20)
NOTIFY_UNREACHABLE_AFTER = timedelta(hours=1)
DEVICE_AVG_MIN_UPTIME = 60
KEEP = timedelta(days=8)
DAY = timedelta(hours=24)
OUTCOMES_KEEP = timedelta(hours=25)   # only the 24 h diagnostics read outcomes (final review I2)


@dataclass
class ReadResult:
    ok: bool
    finished_utc: datetime
    status: dict | None = None
    rssi: int | None = None
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class WriteWindow:
    serial: str
    window: Window
    boot_utc: datetime | None


@dataclass
class FetchBackup:
    serial: str
    since_utc: datetime


@dataclass
class Notify:
    serial: str
    kind: str
    message: str


class MonitorEngine:
    def __init__(self, serial: str, label: str, k: float, tz: tzinfo, now_utc: datetime,
                 state: dict | None = None, backup: bool = False, parallel: bool = False) -> None:
        s = state or {}
        self.serial, self.label, self.tz = serial, label, tz
        self.backup, self.parallel = backup, parallel
        self.log = WindowLog(Window.load(x) for x in s.get("windows", []))   # compact rows or old dicts
        self.outcomes: dict[str, str] = dict(s.get("outcomes", {}))   # window end ISO -> captured|missed|not_observed
        self.reliability = ReliabilityBlocks(s.get("reliability"))
        self.validator = WindowValidator(s.get("validator"))
        self.firmware: str | None = s.get("firmware")
        self.factor_log: list[dict] = list(s.get("factor_log", []))
        self.pull_date: str | None = s.get("pull_date")
        old_k = s.get("k")
        if old_k is not None and float(old_k) != float(k):
            self.factor_log.append({"at": now_utc.isoformat(), "old": float(old_k), "new": float(k)})
        self.k = float(k)
        self.boot = BootTracker(s.get("boot"))
        self.pull_requested = False                                  # request_pull(): next A is a pull, any hour
        self.anchor: datetime | None = None
        self.last_good: datetime | None = None
        self.last_status: dict | None = None
        self.rssi: int | None = None
        self.started = now_utc
        self.unreachable_notified = False
        self._poked: datetime | None = None                         # rollover of the last free-mode rd200_ble job
        self._end_before_start = self.log.latest_end()
        self._gap_checked = False

    # ------------------------------------------------------------------ properties / helpers
    @property
    def counts_mode(self) -> bool:
        return self.validator.status == "passed"

    def _outcome_key(self, end: datetime) -> str:
        for key in self.outcomes:
            if abs(datetime.fromisoformat(key) - end) < SAME_WINDOW:
                return key
        return end.isoformat()

    def _set_outcome(self, end: datetime, status: str) -> None:
        key = self._outcome_key(end)
        if self.outcomes.get(key) == "captured":
            return
        self.outcomes[key] = status

    def _pull_due(self, slot: datetime) -> bool:
        local = slot.astimezone(self.tz)
        if self.pull_requested:
            return True
        return local.time() >= PULL_FROM and self.pull_date != local.date().isoformat()

    def request_pull(self) -> None:
        self.pull_requested = True

    # ------------------------------------------------------------------ scheduling
    def initial_jobs(self, now: datetime) -> list[Job]:
        return [free_job(self.serial, now)]

    def _schedule_next(self, job: Job, now: datetime) -> list[Job]:
        if self.anchor is None or not self.counts_mode:
            nxt = free_job(self.serial, max(now, job.slot + UNALIGNED_EVERY))
            jobs = [nxt]
            if self.parallel and self.anchor is not None:      # anchor: set by every good read, any mode
                roll = self._latest_rollover(nxt.slot)          # rd200_ble at +4:30 of that window, once:
                if self._poked is None or abs(roll - self._poked) >= SAME_WINDOW:   # anchors differ by seconds
                    self._poked = roll
                    jobs.append(parallel_job(self.serial, roll))
            return jobs
        kind, slot, roll = a_or_b_after(self.anchor, max(now, job.slot + MIN_GAP))
        if kind == "A" and self._pull_due(slot):
            kind = "pull"
        jobs = [Job(roll + TEN, slot, self.serial, kind, slot, roll)]
        if kind != "B" and self.parallel:
            jobs.append(parallel_job(self.serial, roll))
        return jobs

    # ------------------------------------------------------------------ results
    def on_result(self, job: Job, res: ReadResult, now: datetime) -> tuple[list[Job], list]:
        if job.kind == "parallel":
            return [], []
        actions: list = []
        if job.attempt == 1 and job.kind in ("A", "B", "pull"):
            self.reliability.record(job.slot.astimezone(self.tz), res.ok)
        if res.ok and res.status:
            actions += self._absorb(res)
            if job.kind in ("pull", "pull_now"):
                self.pull_date = job.slot.astimezone(self.tz).date().isoformat()
                self.pull_requested = False
            if job.kind == "pull_now":
                return [], actions
            return self._schedule_next(job, now), actions
        retry = retry_job(job)
        if retry is not None and retry.due < job.deadline:
            return [retry], actions
        if job.kind == "pull_now":
            return [], actions
        if job.kind == "B" and self.counts_mode and job.rollover and self.log.find(job.rollover) is None:
            self._set_outcome(job.rollover, "missed")
            if self.backup:
                actions.append(FetchBackup(self.serial, job.rollover - TEN - timedelta(minutes=2)))
        return self._schedule_next(job, now), actions

    def _absorb(self, res: ReadResult) -> list:
        st, t = res.status, res.finished_utc
        u = int(st["uptime_minutes"])
        acts: list = []
        fw = st.get("firmware_version")
        if fw and fw != self.firmware:
            if self.firmware is not None:
                self.validator = WindowValidator()       # new firmware: validate the window model again
            self.firmware = fw
        if self.boot.observe(t, u):
            acts.append(Notify(self.serial, "reboot", f"Radon {self.label} restarted (uptime {u} min)."))
        before = self.validator.status
        retest = before == "failed" or self.validator.failed_at is not None
        self.validator.observe(u, int(st["counts_current"]), int(st["counts_previous"]), t)
        if before != "failed" and self.validator.status == "failed":
            acts.append(Notify(self.serial, "validation_failed",
                               f"Radon {self.label}: counting window not recognised on {st.get('model')} {fw}; "
                               "running with the device's own values only. Re-tested in 24 h."))
        if retest and before != "passed" and self.validator.status == "passed":
            acts.append(Notify(self.serial, "validation_passed",
                               f"Radon {self.label}: counting window recognised on re-test; counts mode on."))
        self.last_good, self.last_status, self.rssi = t, st, res.rssi
        self.anchor = last_rollover(t, u)
        if self.unreachable_notified:
            self.unreachable_notified = False
            acts.append(Notify(self.serial, "reachable", f"Radon {self.label} is reachable again."))
        if self.counts_mode:
            w = captured_window(t, u, int(st["counts_previous"]), "ha", st.get("latest_bq_m3"))
            if w is not None:
                acts += self._gap_after_restart(w)
                acts += self._add_window(w)
        return acts

    def _add_window(self, w: Window) -> list:
        changed, warning = self.log.add(w)
        acts: list = []
        if changed:
            self._set_outcome(w.end_utc, "captured")
            acts.append(WriteWindow(self.serial, w, self.boot.boot_utc if w.source == "ha" else None))
        if warning:
            acts.append(Notify(self.serial, "count_conflict", f"Radon {self.label}: {warning}"))
        return acts

    def _gap_after_restart(self, w: Window) -> list:
        if self._gap_checked:
            return []
        self._gap_checked = True
        prev = self._end_before_start
        if prev is None or w.end_utc - prev <= TEN + SAME_WINDOW:
            return []
        end = prev + TEN
        oldest = w.end_utc - KEEP                     # no clock here: the gap's end stands in for "now"
        if end < oldest:
            end += ((oldest - end) // TEN + 1) * TEN
        while end < w.end_utc - SAME_WINDOW:
            if self.log.find(end) is None:            # HA down -> not observed; HA up but no read -> missed
                self._set_outcome(end, "not_observed" if end <= self.started else "missed")
            end += TEN
        return [FetchBackup(self.serial, prev)] if self.backup else []

    def missed_since(self, now: datetime) -> datetime | None:
        """End of the earliest window still "missed" in the last 24 h (the backup ring's span), or None.
        The hub asks the backup again hourly from there, so a window the backup only got after HA's
        first request is still filled."""
        ends = [datetime.fromisoformat(k) for k, v in self.outcomes.items() if v == "missed"]
        return min((t for t in ends if now - DAY < t <= now), default=None)

    def on_backup(self, windows: list[Window], now: datetime) -> list:
        acts: list = []
        for w in windows:
            acts += self._add_window(w)
        return acts

    # ------------------------------------------------------------------ periodic
    def check(self, now: datetime) -> list:
        ref = self.last_good or self.started
        if not self.unreachable_notified and now - ref > NOTIFY_UNREACHABLE_AFTER:
            self.unreachable_notified = True
            return [Notify(self.serial, "unreachable",
                           f"Radon {self.label} has not been read since {ref:%Y-%m-%d %H:%M} UTC.")]
        return []

    # ------------------------------------------------------------------ entity values
    def _latest_rollover(self, now: datetime) -> datetime | None:
        ref = self.anchor or self.log.latest_end()    # after a restart, before the first read: stored windows
        if ref is None:
            return None
        return ref + ((now - ref) // TEN) * TEN

    def _derived(self, now: datetime, expected: int, minimum: int) -> Derived | None:
        last = self._latest_rollover(now)
        if last is None or not self.counts_mode:
            return None
        tol = timedelta(minutes=2)
        ws = self.log.between(last - expected * TEN + tol, last + tol)
        return derive([w.count for w in ws], expected, minimum, self.k)

    def _diagnostics(self, now: datetime) -> dict:
        recent = [v for k, v in self.outcomes.items() if now - DAY < datetime.fromisoformat(k) <= now]
        cap, miss = recent.count("captured"), recent.count("missed")
        ratio = None
        week = [w for w in self.log.between(now - timedelta(days=7), now) if w.device_bq is not None]
        if len(week) >= 144:
            counts_bq = sum(w.count for w in week) / (len(week) / 6) / self.k
            device_bq = sum(w.device_bq for w in week) / len(week)
            ratio = round(counts_bq / device_bq, 3) if device_bq > 0 else None
        return {"window_capture_24h": round(100 * cap / (cap + miss), 1) if cap + miss else None,
                "missed_windows_24h": miss, "counts_device_ratio_7d": ratio}

    def entity_states(self, now: datetime) -> dict:
        stale = self.last_good is None or now - self.last_good > STALE_AFTER
        st = self.last_status or {}
        young = int(st.get("uptime_minutes", 0)) < DEVICE_AVG_MIN_UPTIME

        def device(key: str, average: bool = False):
            if stale or not st or (average and young):
                return None
            return st.get(key)

        self.reliability.roll(now.astimezone(self.tz))
        out = {"stale": stale, "radon": device("latest_pci_l"),
               "radon_1_day_level": device("day_avg_pci_l", True),
               "radon_1_month_level": device("month_avg_pci_l", True),
               "radon_peak": device("peak_pci_l", True),
               "last_boot": self.boot.boot_utc, "rssi": self.rssi, "last_good_read": self.last_good,
               "radon_counts_1h": self._derived(now, 6, 4), "radon_counts_24h": self._derived(now, 144, 120),
               "first_attempt_success_4h": self.reliability.value(),
               "reliability_block_start": self.reliability.last_start(),
               "counts_mode": self.validator.status, "factor": self.k}
        out.update(self._diagnostics(now))
        return out

    # ------------------------------------------------------------------ persistence
    def to_state(self, now: datetime) -> dict:
        self.log.prune(now - KEEP)
        self.outcomes = {k: v for k, v in self.outcomes.items() if datetime.fromisoformat(k) >= now - OUTCOMES_KEEP}
        return {"windows": self.log.to_rows(), "outcomes": self.outcomes,
                "reliability": self.reliability.to_dict(), "validator": self.validator.to_dict(),
                "firmware": self.firmware, "factor_log": self.factor_log, "pull_date": self.pull_date,
                "k": self.k, "boot": self.boot.to_dict()}
