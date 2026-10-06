"""RD200 counting windows: identity, capture from status reads, dedupe, reboots, model validation.

Pure: stdlib only and NO relative imports (the optional backup reader imports this file on its own).
Window k of a boot covers device uptime [10k, 10k + 10) minutes; a status read at uptime u reports the
window that closed at the last multiple of 10 as `counts_previous`. Uptime has 1-minute resolution, so a
computed rollover time is up to 1 min LATER than the true one (spec "Read timing").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

WINDOW_MIN = 10
TEN = timedelta(minutes=WINDOW_MIN)
SAME_WINDOW = timedelta(minutes=5)   # end times closer than this belong to the same window


@dataclass(frozen=True)
class Window:
    end_utc: datetime
    index: int
    count: int
    source: str                    # "ha" | "backup"
    read_at_utc: datetime
    device_bq: int | None = None   # device's latest_bq_m3 at the capturing read (None from the backup)

    def to_dict(self) -> dict:
        return {"end_utc": self.end_utc.isoformat(), "index": self.index, "count": self.count,
                "source": self.source, "read_at_utc": self.read_at_utc.isoformat(), "device_bq": self.device_bq}

    @staticmethod
    def from_dict(d: dict) -> "Window":
        return Window(datetime.fromisoformat(d["end_utc"]), int(d["index"]), int(d["count"]), d["source"],
                      datetime.fromisoformat(d["read_at_utc"]), d.get("device_bq"))

    def to_row(self) -> str:
        """Compact Store form "end_epoch_s,index,count,h|b,read_at_epoch_s,device_bq" (whole seconds; HA's
        Store writes indent-2 JSON, where one string per window is far smaller than a dict or a list)."""
        bq = "" if self.device_bq is None else str(self.device_bq)
        return (f"{round(self.end_utc.timestamp())},{self.index},{self.count},{self.source[0]},"
                f"{round(self.read_at_utc.timestamp())},{bq}")

    @staticmethod
    def from_row(row: str) -> "Window":
        end, index, count, src, read_at, bq = row.split(",")
        return Window(datetime.fromtimestamp(int(end), timezone.utc), int(index), int(count),
                      "ha" if src == "h" else "backup", datetime.fromtimestamp(int(read_at), timezone.utc),
                      int(bq) if bq else None)

    @staticmethod
    def load(item) -> "Window":
        """Either Store form: a compact row (current) or a dict (before the final-review fix I2)."""
        return Window.from_row(item) if isinstance(item, str) else Window.from_dict(item)


def last_rollover(read_at: datetime, uptime_min: int) -> datetime:
    return read_at - timedelta(minutes=uptime_min % WINDOW_MIN)


def captured_window(read_at: datetime, uptime_min: int, counts_previous: int, source: str = "ha",
                    device_bq: int | None = None) -> Window | None:
    if uptime_min < WINDOW_MIN:
        return None
    return Window(last_rollover(read_at, uptime_min), uptime_min // WINDOW_MIN - 1, int(counts_previous),
                  source, read_at, device_bq)


class WindowLog:
    """Captured windows of one monitor (all boots), ordered by end time."""

    def __init__(self, windows=()) -> None:
        self._w: list[Window] = sorted(windows, key=lambda w: w.end_utc)

    def __len__(self) -> int:
        return len(self._w)

    def all(self) -> list[Window]:
        return list(self._w)

    def find(self, end_utc: datetime) -> Window | None:
        for w in self._w:
            if abs(w.end_utc - end_utc) < SAME_WINDOW:
                return w
        return None

    def add(self, w: Window) -> tuple[bool, str | None]:
        """(changed, warning). The first copy wins, except that HA's copy replaces the backup's."""
        old = self.find(w.end_utc)
        if old is None:
            self._w.append(w)
            self._w.sort(key=lambda x: x.end_utc)
            return True, None
        if old.count == w.count:
            return False, None
        msg = f"window ending {w.end_utc:%Y-%m-%d %H:%M}Z: {old.source}={old.count}, {w.source}={w.count}"
        if old.source == "backup" and w.source == "ha":
            self._w[self._w.index(old)] = w
            return True, msg + "; kept ha"
        return False, msg + f"; kept {old.source}"

    def between(self, start: datetime, end: datetime) -> list[Window]:
        return [w for w in self._w if start < w.end_utc <= end]

    def latest_end(self) -> datetime | None:
        return self._w[-1].end_utc if self._w else None

    def prune(self, before: datetime) -> None:
        self._w = [w for w in self._w if w.end_utc >= before]

    def to_list(self) -> list[dict]:
        return [w.to_dict() for w in self._w]

    def to_rows(self) -> list[str]:
        return [w.to_row() for w in self._w]


def hour_totals(windows: list[Window], hour_start: datetime) -> tuple[int, int]:
    """(counts, windows captured) for windows that ENDED in [hour_start, hour_start + 1 h)."""
    ws = [w for w in windows if hour_start <= w.end_utc < hour_start + timedelta(hours=1)]
    return sum(w.count for w in ws), len(ws)


class BootTracker:
    REBOOT_SLACK_MIN = 2   # uptime floor (1 min) + clock jitter between the two reads

    def __init__(self, state: dict | None = None) -> None:
        s = state or {}
        self.boot_utc: datetime | None = datetime.fromisoformat(s["boot_utc"]) if s.get("boot_utc") else None
        self.last_uptime: int | None = s.get("last_uptime")
        self.last_read_utc: datetime | None = (datetime.fromisoformat(s["last_read_utc"])
                                               if s.get("last_read_utc") else None)

    def observe(self, read_at: datetime, uptime_min: int) -> bool:
        """True when the monitor restarted since the last read: uptime grew less than the time that passed.

        Works across an HA restart (state persisted): a reboot while HA was down can leave uptime above
        last_uptime, so compare against last_uptime + elapsed rather than last_uptime alone. Drift-safe:
        never compares boot_utc values.
        """
        rebooted = False
        if self.last_uptime is not None:
            elapsed = (read_at - self.last_read_utc).total_seconds() / 60 if self.last_read_utc else 0.0
            rebooted = uptime_min < self.last_uptime + max(elapsed, 0.0) - self.REBOOT_SLACK_MIN \
                or uptime_min < self.last_uptime
        if self.boot_utc is None or rebooted:
            self.boot_utc = (read_at - timedelta(minutes=uptime_min)).replace(second=0, microsecond=0)
        self.last_uptime, self.last_read_utc = uptime_min, read_at
        return rebooted

    def to_dict(self) -> dict:
        return {"boot_utc": self.boot_utc.isoformat() if self.boot_utc else None, "last_uptime": self.last_uptime,
                "last_read_utc": self.last_read_utc.isoformat() if self.last_read_utc else None}


class WindowValidator:
    """The trial analysis, live (spec "Other models and firmware").

    Same-block pairs (two reads in one 10-min uptime block) must never show a rollover: a changed
    `previous` or a falling `current` is a violation. Adjacent-block pairs usually DO show one; a
    "silent" crossing (previous unchanged and current not falling) happens by chance for real 10-min
    windows (~10 % in the trial) but always for longer windows.
    """

    MIN_SAME = 20
    MIN_SAME_IF_VIOLATED = 100      # tolerate 1-2 glitches, but only with much more evidence
    FAIL_VIOLATIONS = 3
    MIN_CROSS = 20
    MAX_SILENT_SHARE = 0.3
    FAIL_CROSS = 60
    RETEST_AFTER = timedelta(hours=24)   # a failed validation runs again from scratch this long after failing

    def __init__(self, state: dict | None = None) -> None:
        s = state or {}
        self.status: str = s.get("status", "pending")
        self.same_ok: int = s.get("same_ok", 0)
        self.violations: int = s.get("violations", 0)
        self.crossings: int = s.get("crossings", 0)
        self.silent: int = s.get("silent", 0)
        self.failed_at: datetime | None = datetime.fromisoformat(s["failed_at"]) if s.get("failed_at") else None
        self._prev: tuple[int, int, int] | None = None   # not persisted: the first read after a restart only primes

    def _retest_if_due(self, at: datetime | None) -> None:
        if self.status != "failed" or at is None:
            return
        if self.failed_at is None:                       # failed before failed_at existed: start the clock now
            self.failed_at = at
        elif at - self.failed_at >= self.RETEST_AFTER:
            self.status = "pending"                      # failed_at kept until a pass: marks a re-test
            self.same_ok = self.violations = self.crossings = self.silent = 0

    def observe(self, uptime_min: int, current: int, previous: int, at: datetime | None = None) -> str:
        self._retest_if_due(at)
        prev, self._prev = self._prev, (uptime_min, current, previous)
        if prev is None or self.status != "pending" or uptime_min <= prev[0]:
            return self.status
        pu, pc, pp = prev
        if uptime_min // WINDOW_MIN == pu // WINDOW_MIN:
            if previous != pp or current < pc:
                self.violations += 1
            else:
                self.same_ok += 1
        elif uptime_min // WINDOW_MIN == pu // WINDOW_MIN + 1:
            self.crossings += 1
            if previous == pp and current >= pc:
                self.silent += 1
        silent_ok = self.silent <= self.MAX_SILENT_SHARE * self.crossings
        if self.violations >= self.FAIL_VIOLATIONS or (self.crossings >= self.FAIL_CROSS and not silent_ok):
            self.status = "failed"
            self.failed_at = at
        else:
            need = self.MIN_SAME if self.violations == 0 else self.MIN_SAME_IF_VIOLATED
            if self.same_ok >= need and self.crossings >= self.MIN_CROSS and silent_ok:
                self.status = "passed"
                self.failed_at = None
        return self.status

    def to_dict(self) -> dict:
        return {"status": self.status, "same_ok": self.same_ok, "violations": self.violations,
                "crossings": self.crossings, "silent": self.silent,
                "failed_at": self.failed_at.isoformat() if self.failed_at else None}
