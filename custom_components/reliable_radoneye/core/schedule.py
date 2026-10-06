"""Read timing (spec "Read timing", "Many monitors, one radio"). Pure.

Offsets are from the COMPUTED rollover (up to 1 min after the true one). HA reads A at +1:00 and B at
+6:00; a second poller owns +3:30 and +8:30; rd200_ble (parallel run only) +4:30.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from .windows import TEN

A_OFFSET = timedelta(seconds=60)
B_OFFSET = timedelta(seconds=360)
PARALLEL_OFFSET = timedelta(seconds=270)
RETRY_DELAYS = (timedelta(0), timedelta(seconds=20), timedelta(seconds=60))
PULL_RETRY_DELAYS = (timedelta(0), timedelta(seconds=60))
UNALIGNED_EVERY = timedelta(minutes=5)
MIN_GAP = timedelta(minutes=2)      # next slot at least this far after the last one (anchor jitter is <= 1 min)
# Caps bound connect + read only; the disconnect is bounded separately (DISCONNECT_CAP_S), so the worst-case
# time a monitor connection is held is cap + 5 s: status 20 s, pull 28 s - both inside the owner's 30 s rule.
CONNECT_CAP_S = 15
PULL_CAP_S = 23
DISCONNECT_CAP_S = 5
PULL_FROM = time(6, 0)


@dataclass(frozen=True)
class Job:
    deadline: datetime
    due: datetime
    serial: str
    kind: str                         # "A" | "B" | "pull" | "pull_now" | "free" | "parallel"
    slot: datetime
    rollover: datetime | None = None  # computed rollover that closed the target window
    attempt: int = 1


def a_or_b_after(anchor: datetime, after: datetime) -> tuple[str, datetime, datetime]:
    """First A/B slot strictly after `after`, for rollovers at anchor + n x 10 min: (kind, slot, rollover)."""
    n = (after - anchor) // TEN - 1
    while True:
        roll = anchor + n * TEN
        for kind, off in (("A", A_OFFSET), ("B", B_OFFSET)):
            if roll + off > after:
                return kind, roll + off, roll
        n += 1


def free_job(serial: str, at: datetime) -> Job:
    return Job(at + UNALIGNED_EVERY, at, serial, "free", at)


def parallel_job(serial: str, rollover: datetime) -> Job:
    at = rollover + PARALLEL_OFFSET
    return Job(rollover + TEN, at, serial, "parallel", at, rollover)


def retry_job(job: Job) -> Job | None:
    if job.kind in ("parallel", "pull_now"):
        return None
    delays = PULL_RETRY_DELAYS if job.kind == "pull" else RETRY_DELAYS
    if job.attempt >= len(delays):
        return None
    return replace(job, due=job.slot + delays[job.attempt], attempt=job.attempt + 1)


class JobQueue:
    """One BLE connection at a time, integration-wide: due jobs are served earliest deadline first."""

    def __init__(self) -> None:
        self._jobs: list[Job] = []

    def __len__(self) -> int:
        return len(self._jobs)

    def put(self, job: Job) -> None:
        if job not in self._jobs:
            self._jobs.append(job)

    def pop_due(self, now: datetime) -> Job | None:
        due = [j for j in self._jobs if j.due <= now]
        if not due:
            return None
        job = min(due, key=lambda j: (j.deadline, j.due))
        self._jobs.remove(job)
        return job

    def next_due(self) -> datetime | None:
        return min((j.due for j in self._jobs), default=None)

    def has_chain(self, serial: str) -> bool:
        return any(j.serial == serial and j.kind != "parallel" for j in self._jobs)

    def drop(self, serial: str) -> None:
        self._jobs = [j for j in self._jobs if j.serial != serial]
