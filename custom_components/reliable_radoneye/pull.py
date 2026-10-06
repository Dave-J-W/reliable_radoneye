"""Pull helpers for reliable_radoneye: write the raw RD200 log files, the pull/audit CSVs, and fill hours
missing from the long-term radon statistics.

READ-ONLY on the monitors (status + history only). Never overwrites an existing statistics hour.

Timestamps (verified 2026-09-27): the log is a rolling
buffer, oldest first, one point per hour counted from the monitor's power-up; newest point =
read time - (uptime_minutes mod 60). Only points since the last power-up (`since_boot`) are exact,
so only those are used for the backfill; a log point stamped T fills HA's hour starting at floor(T).
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import partial

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

SKIP_RECENT_H = 2                 # HA compiles the latest hours itself


def timestamped(history: dict, uptime_min: int, read_at: datetime) -> list[tuple[datetime, float, int, int]]:
    """(timestamp_utc, pci_l, bq_m3, since_boot) per point, oldest first."""
    pci, bq = history["values_pci_l"], history["values_bq_m3"]
    newest = (read_at - timedelta(minutes=uptime_min % 60)).replace(second=0, microsecond=0)
    since_boot = uptime_min // 60
    n = len(pci)
    return [(newest - timedelta(hours=n - 1 - i), pci[i], bq[i], 1 if n - 1 - i < since_boot else 0)
            for i in range(n)]


def write_files(out_dir, dev, status, history, read_at, points) -> None:
    base = os.path.join(out_dir, f"rd200_{dev['serial']}_{dev['label']}")
    stamp = read_at.strftime("%Y%m%dT%H%M%SZ")
    with open(f"{base}_{stamp}.json", "w", encoding="utf-8") as f:
        json.dump({"read_at_utc": read_at.isoformat(), "label": dev["label"], "status": status,
                   "history": history, "source": "HA custom_components/reliable_radoneye"}, f, indent=1)
    with open(f"{base}_latest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["data_no", "timestamp_utc", "timestamp_local", "radon_pci_l", "radon_bq_m3", "since_boot"])
        for i, (t, p, b, sb) in enumerate(points, start=1):
            w.writerow([i, t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        dt_util.as_local(t).strftime("%Y-%m-%d %H:%M"), p, b, sb])


def append_pull_log(out_dir, started, dev, res) -> None:
    path = os.path.join(out_dir, "pull_log.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_started_utc", "label", "ok", "attempts", "points", "uptime_minutes",
                        "latest_pci_l", "filled_hours", "error"])
        w.writerow([started.strftime("%Y-%m-%dT%H:%M:%SZ"), dev["label"], res.get("ok"), res.get("attempts", ""),
                    res.get("points", ""), res.get("uptime_minutes", ""), res.get("latest_pci_l", ""),
                    res.get("filled_hours", ""), res.get("error", "")])


async def backfill(hass: HomeAssistant, out_dir: str, dev: dict, points) -> int:
    sid = dev["statistic_id"]
    rec = get_instance(hass)
    meta = await rec.async_add_executor_job(partial(get_metadata, hass, statistic_ids={sid}))
    if sid not in meta:
        _LOGGER.warning("RadonEye %s: no statistics metadata for %s; backfill skipped", dev["label"], sid)
        return 0
    metadata = dict(meta[sid][1])
    exact = [(t.replace(minute=0), p) for t, p, _, sb in points if sb]
    if not exact:
        return 0
    cutoff = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=SKIP_RECENT_H)
    have = await rec.async_add_executor_job(
        statistics_during_period, hass, exact[0][0] - timedelta(hours=1), None, {sid}, "hour", None, {"mean"})
    have_starts = {datetime.fromtimestamp(r["start"], timezone.utc) if isinstance(r["start"], (int, float))
                   else r["start"] for r in have.get(sid, [])}
    todo = sorted({t: p for t, p in exact if t < cutoff and t not in have_starts}.items())
    if not todo:
        return 0
    async_import_statistics(hass, metadata, [{"start": t, "mean": p, "min": p, "max": p} for t, p in todo])
    stamp = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    await hass.async_add_executor_job(append_audit, out_dir, [[stamp, sid, t.strftime("%Y-%m-%dT%H:%MZ"), p,
                                                               "daily-ha", 0, ""] for t, p in todo])
    _LOGGER.info("RadonEye %s: filled %d empty statistics hours", dev["label"], len(todo))
    return len(todo)


def append_audit(out_dir, rows) -> None:
    path = os.path.join(out_dir, "backfill_audit.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["written_at_utc", "statistic_id", "hour_start_utc", "radon_pci_l", "mode", "shift_h", "r"])
        w.writerows(rows)


def radon_statistic_id(hass, serial: str, configured: str | None) -> str | None:
    """The statistic the backfill fills: the YAML-imported id if any (sensor.fr_*), else our Radon entity's id."""
    if configured:
        return configured
    from homeassistant.helpers import entity_registry as er
    from .const import DOMAIN
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{serial}_radon")
