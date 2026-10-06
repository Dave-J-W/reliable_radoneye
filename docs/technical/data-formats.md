# Data formats

This document lists everything Reliable RadonEye writes: the count archive, the external statistics, the pull
files, the Store, and the optional backup reader's ring and HTTP API. It also gives their sizes and growth.

The examples use placeholder identifiers: serial `XX01RE000001`, label `0001` and Bluetooth address
`AA:BB:CC:DD:EE:01`.

All files except the Store live in **`/config/reliable_radoneye/`** (`hass.config.path("reliable_radoneye")`).
The spec used `/config/radoneye_logs/` and the domain `radoneye_log`; the code uses the names above.

---

## 1. The count archive (`counts_<serial>_<label>_<YYYY-MM-DD>.csv`)

Written by `core/archive.append_window`, once for each `WriteWindow` action.

- **One file per monitor per UTC day.** The date is the window's `end_utc` date, so a file starts at the first
  window that ended after 00:00 UTC.
- **Append-only.** A row is never rewritten or deleted. The header is written when the file is created.
- **Late windows** are appended when they become known, with their true end time. These come from the backup
  reader, or from a refetch after a restart. A day's file is therefore not necessarily in time order.
- Rows exist only in **counts mode**. A monitor that is validating, or whose validation failed, writes none.

| Column | Example | Meaning |
|---|---|---|
| `window_end_utc` | `2026-10-01T12:00:08.123456+00:00` | Computed rollover that closed the window (ISO 8601, UTC). It is up to 1 min *after* the true rollover, and it carries the seconds of the capturing read. |
| `boot_utc` | `2026-09-01T00:00:00+00:00` | Monitor's power-up time (read − uptime, floored to the minute) for HA rows. **Empty for backup rows.** |
| `window_index` | `2299` | ⌊uptime/10⌋ − 1 at the capturing read: 0-based window number within the boot. |
| `count` | `2` | Exact alpha counts in that 10-minute window (`counts_previous`). |
| `source` | `ha` / `backup` | Who captured it. |
| `read_at_utc` | `2026-10-01T12:01:08.123456+00:00` | When the capturing read finished. For a backup row this is the backup's own read time. |

### Reading the archive correctly

- **One row per window, usually.** The engine dedupes before writing, so normally each window appears once. A
  second row for the same window appears only in two cases:
  - HA's copy replaced a backup copy that had a *different* count. The engine raises a `count_conflict`
    notification when that happens;
  - after a crash, windows lost from the Store were fetched again from the backup.
- **Take the last row for each window.** Two copies of one window can have different `window_end_utc` strings,
  because the seconds come from different reads and different clocks. So "the same window" must mean the same
  as in the code: **end times less than 5 minutes apart** (`SAME_WINDOW`). The `archive.py` docstring says
  "readers take the LAST row per window_end_utc". Read that as: group the rows by window (end times within
  5 min), then take the last row in file order. Within a group, a row with `source=ha` is the one HA kept.
- **Hourly rate and radon for any period and any factor:**

  ```
  rate [counts/h]   = Σ count / (rows × 10 min / 60)
  radon [pCi/L]     = rate / k / 37
  ```

  Missing windows are simply absent: they are not zero rows. See
  [counts-method.md](counts-method.md#4-radon-from-counts).

---

## 2. External statistics

`stats.write_hour` writes two **external statistics** per monitor (`async_add_external_statistics`, source
`reliable_radoneye`):

| `statistic_id` | Name | Unit | Value per clock hour |
|---|---|---|---|
| `reliable_radoneye:counts_<serial lowercase>` | `Radon <label> counts per hour` | `counts` | Σ counts of the windows whose **end** fell in [hour, hour + 1 h) |
| `reliable_radoneye:windows_<serial lowercase>` | `Radon <label> windows per hour` | `windows` | the number of such windows captured (0-6, normally 6) |

- The metadata is `has_mean = True` and `has_sum = False`. On HA 2025.10 and later it also sets
  `mean_type = ARITHMETIC`.
- `mean`, `min` and `max` all hold the **same exact integer** for the hour. There is one value per hour, so
  `min` and `max` add nothing; they are filled so that the usual statistics cards work.
- **When it is written:** every time a window is added. The engine recomputes the hour from its window log
  (`hour_totals`), so a late window rewrites its hour. Hours are UTC clock hours.
- **Exact rate for any past hour:** counts / (windows × 10 min). Divide by *k* (and by 37) for radon. This
  long-term record outlives the recorder's state history, which is why `Radon (counts, 24 h)` has no
  `state_class`.
- **One known edge case** (review M6): if HA crashes between Store saves, windows of the current hour that
  were captured after the last save are lost from the Store, although they stay in the CSV. A later window in
  the same hour then rewrites that hour from the incomplete log, and undercounts it. A clean restart flushes
  the Store first. The CSV stays the exact record.

### Entity statistics

These are the usual recorder statistics, not external ones.

| Entity | `state_class` | Unit |
|---|---|---|
| Radon, Radon 1-day level, Radon 1-month level, Radon peak | `measurement` | pCi/L (device class radon) |
| Radon (counts, 1 h) | `measurement` | pCi/L |
| Radon (counts, 24 h) | none | pCi/L |
| First-attempt read success (4 h) | `measurement` | % |
| Diagnostics: window capture, missed windows, counts vs device, signal strength, last good read; last boot; radio time share; last pull | none | |

The unique ids are `<serial>_<key>`, for example `XX01RE000001_radon_counts_1h`. There are also
`<serial>_backup_reachable`, `reliable_radoneye_last_pull` and `reliable_radoneye_radio_share`.

---

## 3. Pull files

These are written by `pull.py` after each daily pull, or after `reliable_radoneye.pull`.

### `rd200_<serial>_<label>_<YYYYmmddTHHMMSSZ>.json`

One file per successful pull (`indent=1`):

```json
{
 "read_at_utc": "2026-10-02T11:02:15.123456+00:00",
 "label": "0001",
 "status": { "serial": "XX01RE000001", "model": "RD200V3", "firmware_version": "V3.0.1",
             "latest_bq_m3": 51, "latest_pci_l": 1.38, "...": "...", "uptime_minutes": 1597,
             "raw_hex": "4042…" },
 "history": { "values_bq_m3": [ … ], "values_pci_l": [ … ] },
 "source": "HA custom_components/reliable_radoneye"
}
```

`status` is the full `parse_status` dict, including `raw_hex`. `history` is the decoded log, oldest first.

### `rd200_<serial>_<label>_latest.csv`

This file is **overwritten** on each pull. It is the timestamped log (see
[protocol.md](protocol.md#timestamping-pulltimestamped)):

| Column | Meaning |
|---|---|
| `data_no` | 1-based position, oldest first |
| `timestamp_utc` | `YYYY-mm-ddTHH:MM:SSZ` |
| `timestamp_local` | the same, in HA's time zone, `YYYY-mm-dd HH:MM` |
| `radon_pci_l` | value, pCi/L (2 decimals) |
| `radon_bq_m3` | value, Bq/m³ |
| `since_boot` | `1` if recorded in the current boot (exact timestamp), else `0` |

### `pull_log.csv`

One row per pull outcome. The outcome is either a success, or the final failure: attempt 2 of a scheduled
pull, or a `pull_now`.

| Column | Meaning |
|---|---|
| `run_started_utc` | when the outcome was recorded |
| `label` | monitor label |
| `ok` | `True` / `False` |
| `attempts` | always empty in 0.3.1 (the result dict has no `attempts` key) |
| `points` | number of history points |
| `uptime_minutes`, `latest_pci_l` | from the status in the same session |
| `filled_hours` | statistics hours the backfill filled |
| `error` | error text on failure (truncated to 200 characters), or `no history` |

The `RadonEye log last pull` sensor shows the latest finish time. Its attributes are `all_ok`,
`radon_<label>` (`ok` / `failed`) and `radon_<label>_detail`. The pull also writes a logbook entry
"RadonEye log pull".

### `backfill_audit.csv`

One row per statistics hour the backfill filled:

| Column | Value |
|---|---|
| `written_at_utc` | when it was written |
| `statistic_id` | the filled statistic, e.g. `sensor.<radon entity>` |
| `hour_start_utc` | `YYYY-mm-ddTHH:MMZ` |
| `radon_pci_l` | value written (mean = min = max) |
| `mode` | always `daily-ha` |
| `shift_h` | always `0` |
| `r` | always empty |

The last three columns keep the format of an earlier desktop backfill tool.

---

## 4. Logs

The integration logs under `custom_components.reliable_radoneye`:

- at `debug`: each failed attempt (`RadonEye <label> <kind> attempt <n> failed: <error>`), a disconnect that did
  not complete cleanly, and backup-reader errors;
- at `info`: the backfill;
- at `exception`: the loop.

---

## 5. The Store

The Store is the file **`/config/.storage/reliable_radoneye.state`**, written by HA's `Store` (version 1) as
indent-2 JSON. It is written at most once per 10 minutes; see
[architecture.md](architecture.md#6-persistence).

```json
{
  "version": 1, "minor_version": 1, "key": "reliable_radoneye.state",
  "data": {
    "XX01RE000001": { …per-monitor state… },
    "XX01RE000002": { … }
  }
}
```

Per-monitor keys (`MonitorEngine.to_state`):

| Key | Type | Content | Retention |
|---|---|---|---|
| `windows` | list of strings | compact rows `"end_epoch_s,index,count,h\|b,read_at_epoch_s,device_bq"`, e.g. `"1790856008,2299,2,h,1790856068,75"`. Times are whole UTC seconds; `device_bq` is empty when unknown (backup rows). Older Stores hold dicts, and both forms load. | 8 days (`KEEP`) |
| `outcomes` | object | window end (ISO) → `captured` / `missed` / `not_observed` | 25 h (`OUTCOMES_KEEP`) |
| `reliability` | object | `current` (ISO block start), `ok`, `total`, `last: {start, ok, total}` | current and previous 4 h block |
| `validator` | object | `status` (`pending` / `passed` / `failed`), `same_ok`, `violations`, `crossings`, `silent`, `failed_at` (ISO or null) | until the next reset |
| `firmware` | string | last seen `firmware_version` | |
| `factor_log` | list | `{at, old, new}` for each change of *k* | **never truncated** |
| `pull_date` | string | local date (ISO) of the last successful pull | |
| `k` | number | the factor in force at the last save | |
| `boot` | object | `boot_utc`, `last_uptime`, `last_read_utc` | |
| `conflicts_warned` | list of int | window end times (epoch s) already reported as a count conflict | 8 days |

The Store must only be edited with HA stopped. A pending delayed save would otherwise overwrite the edit.

---

## 6. Backup reader (`extras/backup_reader/rd200_counts.py`)

### `monitors.json`

The reader's configuration. It is read once at start.

```json
[ {"label": "0001", "serial": "XX01RE000001", "address": "AA:BB:CC:DD:EE:01"} ]
```

### `~/radon_counts/ring.json`

A 24 h ring of captured windows per monitor. It is rewritten atomically (`.tmp` then `os.replace`) whenever a
window is added, and it survives reboots. A file that cannot be read is renamed to `ring.json.bad`, and the
reader starts with an empty ring.

```json
{"XX01RE000001": [
  {"end_utc": "2026-10-01T12:00:08.123456+00:00", "index": 2299, "count": 2, "source": "backup",
   "read_at_utc": "2026-10-01T12:03:38.123456+00:00", "device_bq": null}, …]}
```

Windows older than 24 h are pruned, measured from the newest `read_at_utc`. Windows are deduplicated with the
same `WindowLog` code as HA.

### HTTP API (port 8765, no authentication: use it only on a trusted LAN)

| Request | 200 response |
|---|---|
| `GET /windows?serial=<serial>&since=<UTC ISO>` | JSON list of ring entries (as above) with `end_utc > since`. `serial` is upper-cased. `since` accepts a `Z` suffix, a `+` decoded as a space, or a naive time (taken as UTC); the default is 1970. |
| `GET /status` | `{"monitors": [...monitors.json...], "last": {label: {at, ok, attempt, rssi, uptime_minutes, error}}, "pending": {serial: next due ISO}}` |

The error responses are:

- `400 {"ok": false, "error": "bad since: …"}`;
- `404 {"ok": false}` for anything else.

Every response closes the connection.

HA calls `/status` hourly to set the `Backup reader reachable` sensor. It calls `/windows` to fill missed or
not-observed windows, with `since` = the missed window's end − 12 min, or the last stored window after a
restart. Every HTTP call has a 5 s total timeout. HA stores the backup's windows with `source = backup` and no
`device_bq`.

The reader's own schedule:

- reads at the computed rollover + 3:30 and + 8:30;
- never starts a read after + 9:00;
- one retry 20 s after a failure;
- unaligned reads every 5 min until its first good read.

---

## 7. Sizes and growth (per monitor)

These were measured with the code's own writers on synthetic data at 2 counts per window, except where marked
as an estimate.

| Item | Per day | Per year | Notes |
|---|---|---|---|
| Count CSV | ~15 KB (144 rows × ~104 B) | ~5.4 MB | Microsecond ISO timestamps; backup rows are ~25 B shorter. The spec's estimate of "~6 KB/day, ~2 MB/year" assumed shorter timestamps. |
| External statistics | 48 rows (2 ids × 24 h) | 17,520 rows | Each row is rewritten up to 6 times in its hour. |
| Store | constant ~61 KB once 8 days are held | constant | Indent-2 JSON. Two monitors are ~122 KB (test guard < 200 KB). Rewritten at most 144×/day, so at most ~8.8 MB/day of writes per monitor. |
| Pull JSON | 1 file: ~1 KB of status + ~16 B per history point | 365 files | The size depends on how many points the device's log holds (not measured here). |
| `_latest.csv` | overwritten, ~55 B per point | constant | |
| `pull_log.csv` | ~1 row (~80 B) | ~30 KB | |
| `backfill_audit.csv` | one row (~80 B) per filled hour | usually small | Only hours missing from statistics are filled. |
| Backup `ring.json` | constant ~24 KB (144 windows × ~165 B) | constant | On the backup machine. |
| Recorder (entity states) | ~700 state writes (spec estimate) | ~2.5 MB steady at 10-day retention (estimate) | Long-term statistics growth was estimated at ~+4.5 MB/year per monitor (spec: +9 MB/year for 2 monitors). Not re-measured. |
