"""Raw-count archive, one CSV per monitor per UTC day (spec "Raw-count archive (exact)"). Pure.

Append-only. A window learned late (backup reader, restart) is appended when learned with its true end
time; in the rare case HA later replaces a backup copy with a different count, a second row for the same
window_end_utc is appended - readers take the LAST row per window_end_utc.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime

from .windows import Window

HEADER = ["window_end_utc", "boot_utc", "window_index", "count", "source", "read_at_utc"]


def csv_path(out_dir: str, serial: str, label: str, end_utc: datetime) -> str:
    return os.path.join(out_dir, f"counts_{serial}_{label}_{end_utc:%Y-%m-%d}.csv")


def append_window(out_dir: str, serial: str, label: str, window: Window, boot_utc: datetime | None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = csv_path(out_dir, serial, label, window.end_utc)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow([window.end_utc.isoformat(), boot_utc.isoformat() if boot_utc else "", window.index,
                    window.count, window.source, window.read_at_utc.isoformat()])
    return path


def read_counts(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with open(p, newline="", encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
    return rows
