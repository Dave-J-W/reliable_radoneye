"""Runs the RadonEye engines against real time, Bluetooth, storage, statistics and notifications.

One background task serves the integration-wide queue (one BLE connection at a time). Everything that
decides anything is in core/ (tested); this module only executes jobs and actions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import ble, pull, stats
from .const import DEFAULT_FACTOR, DOMAIN, SIGNAL_HOURLY, SIGNAL_HUB, signal_monitor
from .core.archive import append_window
from .core.engine import FetchBackup, MonitorEngine, Notify, ReadResult, WriteWindow
from .core.reliability import RadioTime
from .core.schedule import CONNECT_CAP_S, PULL_CAP_S, Job, JobQueue, free_job
from .core.windows import TEN, Window, hour_totals

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY_S = 600
RADIO_WARN = 50.0
RADIO_CLEAR = 40.0


@dataclass
class Monitor:
    entry_id: str
    address: str
    serial: str
    label: str
    statistic_id: str | None
    backup_url: str
    parallel: bool
    engine: MonitorEngine
    backup_ok: bool | None = None
    _states: dict | None = field(default=None, repr=False)
    _states_minute: datetime | None = None

    def states(self, now: datetime) -> dict:
        """engine.entity_states, computed once per signal and minute and shared by all entities."""
        minute = now.replace(second=0, microsecond=0)
        if self._states is None or self._states_minute != minute:
            self._states, self._states_minute = self.engine.entity_states(now), minute
        return self._states


class Hub:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store = Store(hass, 1, f"{DOMAIN}.state")
        self.data: dict = {}
        self.monitors: dict[str, Monitor] = {}
        self.queue = JobQueue()
        self.radio = RadioTime()
        self.tz = ZoneInfo(hass.config.time_zone)
        self.out_dir = hass.config.path("radoneye_logs")
        self.last_pull: dict = {}
        self._task: asyncio.Task | None = None
        self._loaded = False
        self._radio_warned = False
        self._save_pending = False
        self._last_hour: datetime | None = None

    # ------------------------------------------------------------------ lifecycle
    async def async_add(self, entry: ConfigEntry) -> Monitor:
        if not self._loaded:
            self.data = await self.store.async_load() or {}
            self._loaded = True
        d, o = entry.data, entry.options
        now = dt_util.utcnow()
        label = o.get("label") or d["label"]
        backup_url = (o.get("backup_url") or "").strip()
        parallel = bool(o.get("parallel_with_rd200_ble"))
        eng = MonitorEngine(d["serial"], label, float(o.get("factor", DEFAULT_FACTOR)), self.tz, now,
                            state=self.data.get(d["serial"]), backup=bool(backup_url), parallel=parallel)
        mon = Monitor(entry.entry_id, d["address"], d["serial"], label, d.get("statistic_id"), backup_url,
                      parallel, eng)
        self.monitors[mon.serial] = mon
        for job in eng.initial_jobs(now):
            self.queue.put(job)
        if self._task is None:
            self._task = self.hass.async_create_background_task(self._run(), f"{DOMAIN} hub")
        return mon

    async def async_remove(self, serial: str) -> None:
        self.queue.drop(serial)
        mon = self.monitors.pop(serial, None)
        if mon:
            now = dt_util.utcnow()
            for s, m in self.monitors.items():
                self.data[s] = m.engine.to_state(now)
            self.data[serial] = mon.engine.to_state(now)
            await self.store.async_save(self.data)
            self._save_pending = False  # an immediate save cancels any pending delayed one
        if not self.monitors and self._task:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _save(self) -> None:
        if self._save_pending:
            return

        def data() -> dict:
            self._save_pending = False
            now = dt_util.utcnow()
            for s, m in self.monitors.items():
                self.data[s] = m.engine.to_state(now)
            return self.data
        self._save_pending = True
        self.store.async_delay_save(data, SAVE_DELAY_S)

    # ------------------------------------------------------------------ loop
    async def _run(self) -> None:
        last_minute = None
        while True:
            try:
                now = dt_util.utcnow()
                job = self.queue.pop_due(now)
                if job is not None and job.serial in self.monitors:
                    try:
                        await self._execute(job)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - keep the chain alive: queue a recovery read
                        _LOGGER.exception("RadonEye job %s/%s failed", job.serial, job.kind)
                        if (job.serial in self.monitors and job.kind != "parallel"
                                and not self.queue.has_chain(job.serial)):
                            self.queue.put(free_job(job.serial, dt_util.utcnow() + timedelta(seconds=60)))
                    continue
                minute = now.replace(second=0, microsecond=0)
                if minute != last_minute:
                    last_minute = minute
                    await self._each_minute(now)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad job must never stop the loop
                _LOGGER.exception("RadonEye hub loop error")
                await asyncio.sleep(5)

    async def _execute(self, job: Job) -> None:
        mon = self.monitors[job.serial]
        if job.kind == "parallel":
            await self._poke_rd200_ble(mon)
            return
        t0 = time.monotonic()
        status = history = rssi = err = None
        with_history = job.kind in ("pull", "pull_now")
        try:
            status, history, rssi = await ble.session(
                self.hass, mon.address, with_history, PULL_CAP_S if with_history else CONNECT_CAP_S)
        except Exception as e:  # noqa: BLE001 - every BLE failure is a failed attempt
            err = f"{type(e).__name__}: {e}"[:200]
            _LOGGER.debug("RadonEye %s %s attempt %s failed: %s", mon.label, job.kind, job.attempt, err)
        dur = time.monotonic() - t0
        now = dt_util.utcnow()
        self.radio.add(now, dur)
        if self.monitors.get(mon.serial) is not mon:
            return  # removed or reloaded while connected: discard the result, queue nothing
        res = ReadResult(ok=status is not None, finished_utc=now, status=status, rssi=rssi, error=err, duration_s=dur)
        jobs, actions = mon.engine.on_result(job, res, now)
        for j in jobs:
            self.queue.put(j)
        await self._apply(mon, actions)
        self._save()
        if with_history and (res.ok or job.kind == "pull_now" or job.attempt >= 2):
            try:
                await self._after_pull(mon, status, history, now, err)
            except Exception:  # noqa: BLE001 - the pull archive is best-effort
                _LOGGER.exception("RadonEye %s: pull bookkeeping failed", mon.label)
        self._signal(mon)

    async def _each_minute(self, now: datetime) -> None:
        for mon in list(self.monitors.values()):
            await self._apply(mon, mon.engine.check(now))
            self._signal(mon)
        share = self.radio.share(now)
        if share > RADIO_WARN and not self._radio_warned:
            self._radio_warned = True
            persistent_notification.async_create(
                self.hass, f"RadonEye Bluetooth reads used {share} % of the last hour. Add a Bluetooth proxy "
                "near the monitors.", title="RadonEye radio load", notification_id=f"{DOMAIN}_radio_load")
        elif share < RADIO_CLEAR and self._radio_warned:
            self._radio_warned = False
            persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_radio_load")
        hour = now.replace(minute=0, second=0, microsecond=0)
        if hour != self._last_hour:
            self._last_hour = hour
            for mon in self.monitors.values():
                if mon.backup_url:
                    self.hass.async_create_task(self._ping_backup(mon))
                    missed = mon.engine.missed_since(now)          # ask again for windows still missed (I4)
                    if missed is not None:
                        self.hass.async_create_task(self._fetch_backup(mon, missed - TEN - timedelta(minutes=2)))
            async_dispatcher_send(self.hass, SIGNAL_HOURLY)

    def _signal(self, mon: Monitor) -> None:
        mon._states = None                     # the monitor changed: its entities recompute once, then share
        async_dispatcher_send(self.hass, signal_monitor(mon.serial))

    # ------------------------------------------------------------------ actions
    async def _apply(self, mon: Monitor, actions: list) -> None:
        for a in actions:
            if isinstance(a, WriteWindow):
                await self.hass.async_add_executor_job(append_window, self.out_dir, mon.serial, mon.label,
                                                       a.window, a.boot_utc)
                hour = a.window.end_utc.replace(minute=0, second=0, microsecond=0)
                counts, n = hour_totals(mon.engine.log.all(), hour)
                stats.write_hour(self.hass, mon.serial, mon.label, hour, counts, n)
            elif isinstance(a, FetchBackup):
                self.hass.async_create_task(self._fetch_backup(mon, a.since_utc))
            elif isinstance(a, Notify):
                self._notify(mon, a)

    def _notify(self, mon: Monitor, a: Notify) -> None:
        if a.kind == "validation_failed":
            ir.async_create_issue(self.hass, DOMAIN, f"window_not_recognised_{mon.serial}", is_fixable=False,
                                  severity=ir.IssueSeverity.WARNING, translation_key="window_not_recognised",
                                  translation_placeholders={"label": mon.label})
        elif a.kind == "validation_passed":
            ir.async_delete_issue(self.hass, DOMAIN, f"window_not_recognised_{mon.serial}")
        elif a.kind in ("unreachable", "count_conflict"):
            persistent_notification.async_create(self.hass, a.message, title=f"Radon {mon.label}",
                                                 notification_id=f"{DOMAIN}_{a.kind}_{mon.serial}")
        elif a.kind == "reachable":
            persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_unreachable_{mon.serial}")
        self.hass.async_create_task(self.hass.services.async_call(
            "logbook", "log", {"name": f"Radon {mon.label}", "message": a.message}))

    async def _get_backup(self, mon: Monitor, path: str, params: dict | None = None):
        session = async_get_clientsession(self.hass)
        async with session.get(f"{mon.backup_url.rstrip('/')}{path}", params=params,
                               timeout=aiohttp.ClientTimeout(total=5)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _ping_backup(self, mon: Monitor) -> None:
        try:
            await self._get_backup(mon, "/status")
            mon.backup_ok = True
        except Exception:  # noqa: BLE001
            mon.backup_ok = False
        self._signal(mon)

    async def _fetch_backup(self, mon: Monitor, since: datetime) -> None:
        try:
            rows = await self._get_backup(mon, "/windows", {"serial": mon.serial, "since": since.isoformat()})
            windows = [Window(datetime.fromisoformat(r["end_utc"]), int(r["index"]), int(r["count"]), "backup",
                              datetime.fromisoformat(r["read_at_utc"])) for r in rows]
            mon.backup_ok = True
        except Exception as err:  # noqa: BLE001 - a dead or malformed backup never affects reads
            mon.backup_ok = False
            _LOGGER.debug("RadonEye %s: backup reader: %s", mon.label, err)
            self._signal(mon)
            return
        if self.monitors.get(mon.serial) is not mon:
            return
        await self._apply(mon, mon.engine.on_backup(windows, dt_util.utcnow()))
        self._save()
        self._signal(mon)

    async def _poke_rd200_ble(self, mon: Monitor) -> None:
        entity_id = f"sensor.fr_{mon.serial.lower()}_radon_uptime"
        if self.hass.states.get(entity_id) is None:
            return
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True), 20)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("rd200_ble poke %s: %s", entity_id, err)
        self.radio.add(dt_util.utcnow(), time.monotonic() - t0)

    async def _after_pull(self, mon: Monitor, status, history, now: datetime, err: str | None) -> None:
        dev = {"serial": mon.serial, "label": mon.label,
               "statistic_id": pull.radon_statistic_id(self.hass, mon.serial, mon.statistic_id)}
        if status is None or history is None:
            res = {"ok": False, "error": err or "no history"}
        else:
            points = pull.timestamped(history, int(status["uptime_minutes"]), now)
            await self.hass.async_add_executor_job(pull.write_files, self.out_dir, dev, status, history, now, points)
            filled = await pull.backfill(self.hass, self.out_dir, dev, points) if dev["statistic_id"] else 0
            res = {"ok": True, "points": len(points), "uptime_minutes": status["uptime_minutes"],
                   "latest_pci_l": status["latest_pci_l"], "firmware": status["firmware_version"],
                   "read_at": now.isoformat(), "filled_hours": filled}
        await self.hass.async_add_executor_job(pull.append_pull_log, self.out_dir, now, dev, res)
        self.last_pull[mon.label] = {**res, "finished": now.isoformat()}
        async_dispatcher_send(self.hass, SIGNAL_HUB)
        msg = (f"ok, {res['points']} points, filled {res['filled_hours']} h" if res["ok"]
               else f"FAILED ({res['error']})")
        self.hass.async_create_task(self.hass.services.async_call(
            "logbook", "log", {"name": "RadonEye log pull", "message": f"{mon.label}: {msg}"}))

    # ------------------------------------------------------------------ services
    def queue_pull(self, immediate: bool) -> list[str]:
        now = dt_util.utcnow()
        for mon in self.monitors.values():
            if immediate:
                self.queue.put(Job(now, now, mon.serial, "pull_now", now))
            else:
                mon.engine.request_pull()
        return list(self.monitors)
