"""Fake RD200s and a fake clock for engine simulations (not a test module)."""

import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye"))
from core.engine import ReadResult  # noqa: E402
from core.schedule import JobQueue  # noqa: E402


class FakeRD200:
    """10-min windows rolling over at uptime = 0 (mod 10); window w holds counts_for(w) counts."""

    def __init__(self, serial, boot_utc, counts_for=lambda w: 2, model="RD200V3", firmware="V3.0.1"):
        self.serial, self.boot_utc, self.counts_for = serial, boot_utc, counts_for
        self.model, self.firmware = model, firmware

    def status(self, now: datetime) -> dict:
        up_s = (now - self.boot_utc).total_seconds()
        u = int(up_s // 60)
        w = u // 10
        frac = (up_s / 60 - w * 10) / 10
        return {"serial": self.serial, "model": self.model, "firmware_version": self.firmware,
                "uptime_minutes": u, "counts_current": int(self.counts_for(w) * frac),
                "counts_previous": self.counts_for(w - 1) if w >= 1 else 0,
                "latest_bq_m3": 10, "latest_pci_l": 0.27, "day_avg_pci_l": 0.3, "month_avg_pci_l": 0.25,
                "peak_pci_l": 2.0}


def simulate(engines: dict, devices: dict, start: datetime, end: datetime, fails=lambda serial, t, job: False,
             read_s: float = 5.0):
    """Run engines against devices on one radio. Returns (actions, executed_jobs)."""
    q = JobQueue()
    for e in engines.values():
        for j in e.initial_jobs(start):
            q.put(j)
    actions, done = [], []
    now = start
    while True:
        due = q.next_due()
        if due is None or due > end:
            break
        now = max(now, due)
        job = q.pop_due(now)
        if job.kind == "parallel":
            done.append(job)
            continue
        fin = now + timedelta(seconds=read_s(len(done)) if callable(read_s) else read_s)
        dev = devices[job.serial]
        ok = not fails(job.serial, now, job)
        res = ReadResult(ok, fin, dev.status(fin) if ok else None, -70 if ok else None, None if ok else "TimeoutError")
        jobs, acts = engines[job.serial].on_result(job, res, fin)
        for j in jobs:
            q.put(j)
        actions += acts
        done.append(job)
        now = fin
    return actions, done
