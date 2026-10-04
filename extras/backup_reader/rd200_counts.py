"""RadonEye RD200 backup reader (own Bluetooth radio, e.g. a spare Raspberry Pi).

Self-aligning: each read's uptime gives the monitor's 10-min window rollover; it reads at rollover
+ 3:30 and + 8:30 (between Home Assistant's + 1:00 and + 6:00) and never starts a read after + 9:00.
Until a monitor's first good read it reads every 5 min. One retry 20 s after a failure.
Keeps a 24 h ring of captured windows per monitor (ring.json, survives reboots) and serves it:
    GET :8765/windows?serial=<serial>&since=<UTC ISO>   -> [{end_utc, index, count, read_at_utc, ...}]
    GET :8765/status                                    -> monitors, pending slots, last results
Monitors from monitors.json: [{"address": ..., "serial": ..., "label": ...}]. No CSV files (spec decision 4).
READ-ONLY on the monitors (0x40 only). Each connection capped at 15 s.
Install with protocol.py and windows.py beside it; systemd unit rd200-counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import protocol   # custom_components/radoneye_log/protocol.py
import windows    # custom_components/radoneye_log/core/windows.py

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.expanduser("~/radon_counts")
MONITORS_FILE = os.path.join(HERE, "monitors.json")
RING_FILE = os.path.join(DATA_DIR, "ring.json")
PORT = 8765
SLOTS = (timedelta(seconds=210), timedelta(seconds=510))
LATEST_START = timedelta(seconds=540)
RETRY_AFTER = timedelta(seconds=20)
UNALIGNED_EVERY = timedelta(minutes=5)
MIN_GAP = timedelta(minutes=2)
RING_SPAN = timedelta(hours=24)
SCAN_TIMEOUT_S = 6
CONNECT_CAP_S = 15

_LOG = logging.getLogger("rd200_backup")


# ---------------------------------------------------------------- pure (unit-tested)

class SelfAlignedPlan:
    """pending = (due, attempt, slot, rollover or None)."""

    def __init__(self, start: datetime) -> None:
        self.anchor: datetime | None = None
        self.pending: tuple[datetime, int, datetime, datetime | None] = (start, 1, start, None)

    def _next_slot(self, after: datetime) -> tuple[datetime, datetime]:
        n = (after - self.anchor) // windows.TEN - 1
        while True:
            roll = self.anchor + n * windows.TEN
            for off in SLOTS:
                if roll + off > after:
                    return roll + off, roll
            n += 1

    def _advance(self, now: datetime) -> None:
        slot = self.pending[2]
        if self.anchor is None:
            at = max(now, slot + UNALIGNED_EVERY)
            self.pending = (at, 1, at, None)
            return
        at, roll = self._next_slot(max(now, slot + MIN_GAP))
        self.pending = (at, 1, at, roll)

    def due(self, now: datetime) -> int | None:
        at, attempt, slot, roll = self.pending
        if now < at:
            return None
        if roll is not None and now > roll + LATEST_START:
            self._advance(now)
            return None
        return attempt

    def done(self, now: datetime, uptime_min: int | None, ok: bool, attempt: int) -> None:
        if ok and uptime_min is not None:
            self.anchor = windows.last_rollover(now, uptime_min)
        if not ok and attempt == 1:
            at, _, slot, roll = self.pending
            self.pending = (now + RETRY_AFTER, 2, slot, roll)
            return
        self._advance(now)


def parse_since(s: str) -> datetime:
    """UTC ISO timestamp from a query string: tolerates 'Z', a '+' decoded to a space, and naive (= UTC)."""
    s = s.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    if " " in s:
        s = s.replace(" ", "+")
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class Ring:
    def __init__(self, path: str) -> None:
        self.path = path
        self.logs: dict[str, windows.WindowLog] = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = {serial: windows.WindowLog(windows.Window.from_dict(r) for r in rows)
                              for serial, rows in json.load(f).items()}
                self.logs = loaded
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
                _LOG.warning("ring %s unreadable (%s); starting empty", path, err)
                try:
                    os.replace(path, path + ".bad")
                except OSError:
                    pass

    def add(self, serial: str, w: windows.Window) -> bool:
        log = self.logs.setdefault(serial, windows.WindowLog())
        changed, _ = log.add(w)
        log.prune(w.read_at_utc - RING_SPAN)
        return changed

    def since(self, serial: str, t: datetime) -> list[dict]:
        log = self.logs.get(serial)
        if log is None:
            return []
        newest = log.all()[-1].read_at_utc if len(log) else t
        log.prune(newest - RING_SPAN)
        return [w.to_dict() for w in log.all() if w.end_utc > t]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({s: log.to_list() for s, log in self.logs.items()}, f)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------- Bluetooth

async def read_status(address: str) -> tuple[dict, int | None]:
    from bleak import BleakClient, BleakScanner

    seen: dict = {}

    def match(device, adv) -> bool:
        if device.address.upper() == address.upper():
            seen["rssi"] = adv.rssi
            return True
        return False

    device = await BleakScanner.find_device_by_filter(match, timeout=SCAN_TIMEOUT_S)
    if device is None:
        raise RuntimeError(f"not heard within {SCAN_TIMEOUT_S} s")

    async def session() -> dict:
        async with BleakClient(device, timeout=CONNECT_CAP_S - 3) as client:
            if not protocol.supports(client):
                raise RuntimeError("RadonEye v2/v3 service not found")
            return await protocol.read_status(client, timeout=6)

    return await asyncio.wait_for(session(), CONNECT_CAP_S), seen.get("rssi")


# ---------------------------------------------------------------- service

class Service:
    def __init__(self) -> None:
        with open(MONITORS_FILE, encoding="utf-8") as f:
            self.monitors = json.load(f)
        start = datetime.now(timezone.utc)
        self.plans = {m["serial"]: SelfAlignedPlan(start) for m in self.monitors}
        self.ring = Ring(RING_FILE)
        self.last: dict[str, dict] = {}

    async def handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 5)).decode(errors="replace").split()
            while (await asyncio.wait_for(reader.readline(), 5)) not in (b"\r\n", b"\n", b""):
                pass
            method, target = (line + ["", ""])[:2]
            url = urlparse(target)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if method == "GET" and url.path == "/windows" and q.get("serial"):
                try:
                    since = parse_since(q.get("since", "1970-01-01T00:00:00+00:00"))
                    code, body = 200, self.ring.since(q["serial"].upper(), since)
                except ValueError as err:
                    code, body = 400, {"ok": False, "error": f"bad since: {err}"}
            elif method == "GET" and url.path == "/status":
                code, body = 200, {"monitors": self.monitors, "last": self.last,
                                   "pending": {s: p.pending[0].isoformat() for s, p in self.plans.items()}}
            else:
                code, body = 404, {"ok": False}
            data = json.dumps(body).encode()
            writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + data)
            await writer.drain()
        except Exception as err:  # noqa: BLE001 - a bad request must never stop the reader
            _LOG.debug("http: %s", err)
        finally:
            writer.close()

    async def poll_forever(self) -> None:
        while True:
            for mon in self.monitors:
                plan = self.plans[mon["serial"]]
                attempt = plan.due(datetime.now(timezone.utc))
                if attempt is None:
                    continue
                try:
                    status, rssi = await read_status(mon["address"])
                    err = None
                except Exception as e:  # noqa: BLE001
                    status, rssi, err = None, None, f"{type(e).__name__}: {e}"[:200]
                now = datetime.now(timezone.utc)
                u = None
                ok = bool(status)
                try:
                    if status:
                        u = int(status["uptime_minutes"])
                        w = windows.captured_window(now, u, int(status["counts_previous"]), "backup")
                        if w is not None and self.ring.add(mon["serial"], w):
                            self.ring.save()
                    self.last[mon["label"]] = {"at": now.isoformat(), "ok": ok, "attempt": attempt,
                                               "rssi": rssi, "uptime_minutes": u, "error": err}
                    _LOG.info("%s attempt %s ok=%s rssi=%s %s", mon["label"], attempt, ok, rssi, err or "")
                except Exception:  # noqa: BLE001 - one bad reply must not stop the reader
                    _LOG.exception("%s: handling a read failed", mon["label"])
                    ok = False
                finally:
                    plan.done(now, u if ok else None, ok, attempt)
            await asyncio.sleep(1)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    svc = Service()
    server = await asyncio.start_server(svc.handle_http, "0.0.0.0", PORT)
    _LOG.info("listening on :%s, %d monitors, ring %s", PORT, len(svc.monitors), RING_FILE)
    async with server:
        await svc.poll_forever()


if __name__ == "__main__":
    asyncio.run(main())
