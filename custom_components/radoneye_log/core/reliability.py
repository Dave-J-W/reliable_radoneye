"""Radio reliability (spec "Reliability (4 h)"). Pure.

Denominator = scheduled reads (A, B; a pull counts as A) while HA was running; numerator = those whose
FIRST attempt succeeded. The value shown describes the previous fixed 4 h block (00/04/08/12/16/20 local).
"""

from __future__ import annotations

from datetime import datetime, timedelta

BLOCK_HOURS = 4


def block_start(local: datetime) -> datetime:
    return local.replace(hour=local.hour - local.hour % BLOCK_HOURS, minute=0, second=0, microsecond=0)


class ReliabilityBlocks:
    def __init__(self, state: dict | None = None) -> None:
        s = state or {}
        self.current: datetime | None = datetime.fromisoformat(s["current"]) if s.get("current") else None
        self.ok: int = s.get("ok", 0)
        self.total: int = s.get("total", 0)
        self.last: dict | None = s.get("last")

    def roll(self, local: datetime) -> None:
        """Move to local's block. Monotone: an earlier block (a late-recorded slot) stays in the current one.

        Blocks are compared as naive wall-clock starts: a restored `current` carries a fixed UTC offset,
        and across a DST change aware arithmetic would size the 00-04 block as 3 or 5 h.
        """
        b = block_start(local)
        if self.current is None:
            self.current = b
            return
        wall_b, wall_cur = b.replace(tzinfo=None), self.current.replace(tzinfo=None)
        if wall_b <= wall_cur:
            return
        if wall_b - wall_cur > timedelta(hours=BLOCK_HOURS):        # whole blocks without HA: unknown
            self.last = {"start": (b - timedelta(hours=BLOCK_HOURS)).isoformat(), "ok": 0, "total": 0}
        else:
            self.last = {"start": self.current.isoformat(), "ok": self.ok, "total": self.total}
        self.current, self.ok, self.total = b, 0, 0

    def record(self, local: datetime, first_attempt_ok: bool) -> None:
        self.roll(local)
        self.total += 1
        self.ok += int(first_attempt_ok)

    def value(self) -> float | None:
        if not self.last or not self.last["total"]:
            return None
        return round(100 * self.last["ok"] / self.last["total"], 1)

    def last_start(self) -> str | None:
        return self.last["start"] if self.last else None

    def to_dict(self) -> dict:
        return {"current": self.current.isoformat() if self.current else None, "ok": self.ok,
                "total": self.total, "last": self.last}


class RadioTime:
    """Seconds of BLE connection time; share of the last hour in percent (integration-wide)."""

    def __init__(self) -> None:
        self._busy: list[tuple[datetime, float]] = []

    def add(self, end_utc: datetime, seconds: float) -> None:
        self._busy.append((end_utc, seconds))

    def share(self, now_utc: datetime, span: timedelta = timedelta(hours=1)) -> float:
        self._busy = [(e, s) for e, s in self._busy if e > now_utc - span]
        return round(100 * sum(s for _, s in self._busy) / span.total_seconds(), 1)
