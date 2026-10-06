# The RD200 Bluetooth protocol (read-only subset)

Reliable RadonEye talks to the RadonEye RD200 **v2/v3** over Bluetooth Low Energy GATT. It uses exactly two
commands: status (0x40) and stored history (0x41). Both are read-only. This document describes the GATT layout,
the status packet byte by byte, the history format and its timestamping, the read-only guarantee, and the
session time caps.

Code: `custom_components/reliable_radoneye/protocol.py` (decoding and requests), `ble.py` (the session),
`core/timing.py` (caps), `pull.py` (history timestamps). The fields below were verified on **RD200V3 firmware
V3.0.1**. The real packets used by the tests are in `tests/test_radoneye_protocol.py`, with the serial
replaced by a placeholder.

## Credit

The protocol layer is adapted from the **`radoneye`** library by Artem Butusov, `sormy/radoneye`
(https://github.com/sormy/radoneye), v3.0.0: `interface_v2.py` and `util.py`. MIT License; the text is in
`custom_components/reliable_radoneye/LICENSE-radoneye`. The offsets of the particle counters (0x27, 0x29) come
from that project's `KNOWLEDGE_V2.md`. The counting window behind those counters was measured for this project
(see [counts-method.md](counts-method.md#1-the-rd200-counting-window)).

The library is **vendored, not installed**. `radoneye` pins `bleak~=2.0.0`, which would conflict with Home
Assistant's own `bleak`. `manifest.json` therefore has `"requirements": []`, and the integration uses HA's
`bleak`, `bleak_retry_connector` and Bluetooth manager.

---

## 1. GATT layout

| Role | UUID | Use |
|---|---|---|
| Service | `00001523-0000-1000-8000-00805f9b34fb` | Its presence identifies RD200 v2/v3 (`protocol.supports`) |
| Command characteristic | `00001524-0000-1000-8000-00805f9b34fb` | Write a 1-byte command |
| Status characteristic | `00001525-0000-1000-8000-00805f9b34fb` | Notifies the reply to 0x40 |
| History characteristic | `00001526-0000-1000-8000-00805f9b34fb` | Notifies the reply pages to 0x41 |

Monitors advertise a local name starting `FR:`. The discovery matcher in `manifest.json` is
`{"local_name": "FR:*", "connectable": true}`. Other FTLab products, and the RD200 v1, can match that name too.
The config flow connects before creating an entry: it refuses a device without the v2/v3 service (RD200 v1),
and a device whose model decodes as `unknown`.

### Request/response pattern (`protocol._request`)

```
start_notify(response_char, callback)
write_gatt_char(CHAR_COMMAND, [command])
wait (timeout) for a notification whose byte 0 == command, passed to the handler
    handler returns None  -> keep waiting (multi-page history)
    handler returns value -> done
    handler raises        -> the request fails
stop_notify(response_char)   (errors ignored)
```

| Command | Byte | Response characteristic | Timeout used by `ble.session` |
|---|---|---|---|
| Status | `0x40` | `…1525` | 8 s |
| History | `0x41` | `…1526` (several pages) | 25 s (the 23 s session cap is the binding limit) |

### Commands deliberately excluded

The upstream library also implements commands that **change** the device: the beep, the alarm settings and the
display unit. These were deliberately not vendored. `protocol.py` has no code path that writes anything except
the single bytes `0x40` and `0x41`. The backup reader sends only `0x40`.

---

## 2. Model detection

| Test | Layout | Serial | Model |
|---|---|---|---|
| `data[15] == 0x06` | v2 | `data[8:11] + data[2:8] + data[11:15]` (ASCII) | `data[16:22]` |
| else `data[14] == 0x07` | v3 | `data[2:14]` | `data[15:22]` |
| otherwise | unknown | `"unknown"` | `"unknown"` (the config flow refuses it) |

---

## 3. The status packet

The reply to `0x40` is 68 bytes on the RD200V3 that was tested. All multi-byte integers are **little-endian
unsigned**, and Bq/m³ values are converted to pCi/L as `round(bq / 37, 2)`.

| Offset (dec / hex) | Size | Type | Field (`parse_status` key) | Unit / meaning |
|---|---|---|---|---|
| 0 / 0x00 | 1 | u8 | command echo (`0x40`) | matched by `_request`; not stored |
| 1 / 0x01 | 1 | u8 | not parsed | 0x42 = 66 in the test packets, apparently the length of the remaining bytes |
| 2-13 / 0x02-0x0D | 12 | ASCII | `serial` (v3) | v2 assembles it differently (section 2) |
| 14 / 0x0E | 1 | u8 | v3 marker (`0x07`) | |
| 15-21 / 0x0F-0x15 | 7 | ASCII | `model` (v3), e.g. `RD200V3` | byte 15 is the v2 marker (`0x06`) |
| 22-27 / 0x16-0x1B | 6 | ASCII | `firmware_version`, e.g. `V3.0.1` | a change re-runs the window validator |
| 28-32 / 0x1C-0x20 | 5 | | not parsed | |
| 33 / 0x21 | 2 | u16 | `latest_bq_m3` (also `latest_pci_l`) | Bq/m³: the device's current value |
| 35 / 0x23 | 2 | u16 | `day_avg_bq_m3` (also `day_avg_pci_l`) | Bq/m³: the device's 1-day average |
| 37 / 0x25 | 2 | u16 | `month_avg_bq_m3` (also `month_avg_pci_l`) | Bq/m³: the device's 1-month average |
| **39 / 0x27** | 2 | u16 | **`counts_current`** | alpha counts so far in the open 10-min window |
| **41 / 0x29** | 2 | u16 | **`counts_previous`** | alpha counts of the window that closed last |
| **43 / 0x2B** | 4 | u32 | **`uptime_minutes`** | whole minutes since power-up |
| 47-50 / 0x2F-0x32 | 4 | | not parsed | |
| 51 / 0x33 | 2 | u16 | `peak_bq_m3` (also `peak_pci_l`) | Bq/m³: the device's peak value |
| 53-67 / 0x35-0x43 | 15 | | not parsed | |
| (all) | | hex | `raw_hex` | the whole packet, kept so that undecoded fields can be re-read later from the pull JSON files |

Bytes 47-50 are not parsed. Uptime is read as u32 at 43-46.

### Worked example (test packet `PKT_A`, placeholder serial)

```
40 42 58 58 30 31 52 45 30 30 30 30 30 31 07 52 44 32 30 30 56 33 56 33 2e 30 2e 31 00 01 94 00 06
33 00 1a 00 00 00 00 00 03 00 3d 06 00 00 00 00 00 00 75 00 02 00 00 00 38 22 f9 00 00 00 00 c2 f5 68 3f
```

| Field | Bytes | Value |
|---|---|---|
| v3 marker | `[14] = 07` | v3 layout |
| serial | `[2:14]` | `XX01RE000001` |
| model | `[15:22]` | `RD200V3` |
| firmware | `[22:28]` | `V3.0.1` |
| latest | `[33:35] = 33 00` | 51 Bq/m³ = 1.38 pCi/L |
| day average | `[35:37] = 1a 00` | 26 Bq/m³ |
| month average | `[37:39] = 00 00` | 0 (young boot) |
| counts_current | `[39:41] = 00 00` | 0 |
| counts_previous | `[41:43] = 03 00` | 3 |
| uptime | `[43:47] = 3d 06 00 00` | 1597 min |
| peak | `[51:53] = 75 00` | 117 Bq/m³ = 3.16 pCi/L |

At uptime 1597 min this read captures window index ⌊1597/10⌋ − 1 = 158, which closed 7 minutes earlier
(1597 mod 10 = 7), with 3 counts.

The device's day and month averages read 0 just after a reboot. The engine therefore reports them, and the
peak, as unknown while `uptime_minutes < 60` (`DEVICE_AVG_MIN_UPTIME`).

---

## 4. The history (0x41)

The device keeps a rolling log of **one radon value per hour**, oldest first. The reply arrives as pages on the
history characteristic:

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | command echo `0x41` |
| 1 | 1 | `page_count` |
| 2 | 1 | `page_no` (1-based) |
| 3 | 1 | `value_count` (read, but the values are taken from the whole body) |
| 4… | 2 × n | values, u16 LE, Bq/m³ |

Pages are collected until `page_no == page_count`. `_merge_history` then sorts them, checks that the page
numbers run 1…N with N = `page_count` (otherwise `ValueError`), and concatenates them to give
`{"values_bq_m3": [...], "values_pci_l": [...]}`. In 2026-09 the decoded log was checked against the official
RadonEye app's export of the same monitor, and was identical.

### Timestamping (`pull.timestamped`)

The log has no timestamps. Points are one hour apart, counted from the monitor's power-up, so the newest point
is aligned to the uptime's hour phase:

```
n          = number of points (index i = 0 … n−1, oldest first)
newest     = floor_minute( read_at − (uptime_minutes mod 60) min )
t_i        = newest − (n − 1 − i) h
since_boot = 1  if (n − 1 − i) < ⌊uptime_minutes / 60⌋  else 0
```

Only the newest ⌊uptime/60⌋ points were recorded during the current boot. Their spacing from `newest` is
exact. Older points come from before the last power-up, and the gap of the power-off is unknown, so their
timestamps are not reliable. They are written to the files with `since_boot = 0`, and they are never used for
the backfill.

### Backfill of the long-term radon statistics (`pull.backfill`)

The backfill target is the configured `statistic_id` (from a YAML import) or, if there is none, the entity
registry id of this monitor's `Radon` sensor (unique id `<serial>_radon`). For that statistic:

1. Take the `since_boot` points. Each point stamped `t` fills HA's hour starting at `t` with the minutes
   zeroed (floor(t) to the hour).
2. Skip hours later than `now_hour − 2 h` (`SKIP_RECENT_H`), because HA compiles recent hours itself.
3. Skip every hour that already has a row (`statistics_during_period`, period `hour`, type `mean`). **An
   existing hour is never overwritten.**
4. Import the remaining hours with `async_import_statistics`, using `mean = min = max = value` (pCi/L) and the
   statistic's existing metadata.
5. Append one row per filled hour to `backfill_audit.csv` (see [data-formats.md](data-formats.md)).

If the statistic has no metadata yet, the backfill is skipped with a warning.

---

## 5. The read-only guarantee

- Only `0x40` and `0x41` are ever written to the command characteristic, and both only request data.
- No configuration, alarm, unit or beep command exists in the code.
- The backup reader uses `0x40` only.

A bug in this integration therefore cannot change a monitor's settings or its stored log.

---

## 6. Session caps

A monitor accepts one connection at a time, and other readers (the official app, a backup reader) need the
radio too. The owner's rule is that **no session holds a monitor for more than 30 s**. `ble.session` enforces
this with `core/timing.capped_then_close`:

```
work  = establish_connection(max_attempts=1) → supports() → read_status(8 s) [→ read_history(25 s)]
close = client.disconnect()

result = wait_for(work, cap_s)              # cap covers connect + read only
finally: wait_for(close, 5 s)               # ALWAYS runs, separately bounded; errors only logged
```

| Session | Connect + read cap | Disconnect cap | Worst case held |
|---|---|---|---|
| Status read (`A`, `B`, `free`, config-flow confirm) | `CONNECT_CAP_S = 15` s | `DISCONNECT_CAP_S = 5` s | 20 s |
| Pull (`pull`, `pull_now`) | `PULL_CAP_S = 23` s | 5 s | 28 s |

The rules for how a session ends:

- A result obtained inside the cap is **returned even if the disconnect times out or fails**.
- If the work times out or fails, its exception propagates, but only after the disconnect has run.
- If the task is cancelled (an entry unloaded mid-read, for example), the disconnect still runs.

All of this is tested in `tests/test_timing.py`.

`PULL_CAP_S` was set to 23 rather than 25, so that the worst case keeps a 2 s margin below 30 s.

HA's Bluetooth manager picks the adapter or ESPHome proxy at connect time. `ble_device_callback` re-resolves
the device if it moves to a different adapter. If no HA adapter currently hears the monitor, the session fails
at once with "not currently heard by any HA Bluetooth adapter".

The **backup reader** has its own pattern. It scans for up to 6 s, then `wait_for(session, 15 s)` covers
connect, read and disconnect together, with a `BleakClient` connect timeout of 12 s and a status timeout of 6 s.
