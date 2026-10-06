# Reliable RadonEye for Home Assistant

A Home Assistant custom integration for **RadonEye RD200 v2/v3** radon monitors. It reads them over Home
Assistant's own Bluetooth (local adapters or ESPHome Bluetooth proxies), keeps the **raw particle counts** the
monitor reports, and derives radon from those counts with an **honest uncertainty interval**.

> **Not affiliated with, endorsed by, or supported by Ecosense or FTLab.** "RadonEye" and "RD200" are their
> product names. This integration only **reads** from the monitor; it never changes a setting on the device.

## Why it exists

The RD200 is a good detector with a fragile Bluetooth link. A radon integration that silently shows
`unknown`, or keeps showing an old number as if it were new, is worse than no integration. Reliable RadonEye
was built around three ideas:

1. **Reliability first.** Reads are scheduled around the monitor's own 10-minute counting window, with
   retries and a second chance in every window, so one failed read costs nothing.
2. **Keep the raw data.** Every 10-minute particle count is archived exactly (CSV files and hourly
   statistics). The radon value is derived from the counts, so changing the conversion factor later never
   loses or distorts anything.
3. **Be honest about uncertainty.** Counts-based radon comes with a 68 % confidence interval that is exact
   even at very low counts. A value that is too old becomes `unavailable` rather than pretending to be current.

### Compared with the community `rd200_ble` integration

`rd200_ble` is the long-standing community integration for these monitors. The comparison below comes from a
48-hour side-by-side run on two RD200V3 monitors (firmware V3.0.1) on one Home Assistant host, with
`rd200_ble` 0.5.3. Your numbers will differ with your radio conditions.

| | `rd200_ble` 0.5.3 | Reliable RadonEye |
|---|---|---|
| Read schedule | Every 10 min, one attempt, no retry | About every 5 min, aligned to the monitor's counting window; up to 3 attempts per read |
| Successful reads (parallel run, 24 h) | About **66 %** on the weaker link, about 99 % on the stronger one | **100 %** of 10-minute windows captured on both monitors |
| Radon shown as `unknown` | About **35 %** of its radon state changes (a parser bug after an early disconnect) | Not from read glitches; `unavailable` only after 20 min without any good read |
| Raw particle counts | Live current/previous count sensors | Exact per-window archive (CSV + hourly statistics) |
| Radon from counts, with uncertainty | No | 1 h and 24 h, exact Poisson (Garwood) 68 % interval |
| Read-reliability reporting | No | First-attempt success (4 h), window capture, missed windows, radio time share |
| Fills gaps in long-term statistics from the device's stored log | No | Daily, never overwrites |
| Optional second reader on another machine | No | Yes (`extras/backup_reader`) |

## Features

- Bluetooth discovery and a UI config flow; RD200 v1 is detected and refused.
- Reads about every 5 minutes, aligned to each monitor's 10-minute counting window: a first read about
  1 minute after a window closes and a second chance about 5 minutes later, each with up to two retries.
- Any number of monitors through one integration, served **one Bluetooth connection at a time**.
- Exact raw-count archive: one CSV row per captured window under `/config/reliable_radoneye/`, plus two
  hourly external statistics per monitor (`reliable_radoneye:counts_<serial>` and
  `reliable_radoneye:windows_<serial>`).
- Counts-based radon over 1 h and 24 h with lower/upper bounds, and a configurable conversion factor *k*.
- The device's own values: latest radon, 1-day and 1-month averages, peak, last boot.
- Reliability diagnostics: first-attempt read success per 4 h block (kept in long-term statistics), window
  capture and missed windows over 24 h, a counts-vs-device check over 7 days, radio time share.
- A live check, per monitor, that its counts really follow a 10-minute window before counts mode is used,
  shown by the *Counting window check* diagnostic sensor (pending, passed or failed).
- Daily pull of the device's stored hourly log that fills **missing** hours in the long-term radon
  statistics (never overwrites), in every mode; `reliable_radoneye.pull` service.
- Honest staleness, persistent notifications for an unreachable monitor or an overloaded radio, and a Repair
  issue for a monitor whose counting window is not recognised.
- Everything is local. No cloud, no account.

## Supported hardware

| Device | Status |
|---|---|
| RadonEye RD200 v3 (Bluetooth name `FR:...`) | Supported. Verified on firmware V3.0.1. |
| RadonEye RD200 v2 (Bluetooth name `FR:...`) | Supported by the protocol code; not tested on real hardware. |
| RadonEye RD200 v1 | Refused at setup (different Bluetooth protocol). |
| Other RadonEye or Ecosense models | Not supported. |

Firmware other than V3.0.1 is not assumed to behave the same. Each monitor's counting window is checked live;
until it passes, the monitor runs in **device-values-only** mode (see
[Troubleshooting](docs/troubleshooting.md#counts-based-radon-stays-unavailable)).

You also need a Bluetooth adapter on the Home Assistant host, or an ESPHome Bluetooth proxy that makes active
connections, within reasonable range of each monitor. See [Installation](docs/installation.md).

## Quick start (5 minutes)

1. **Install** through HACS as a custom repository (`https://github.com/Dave-J-W/reliable_radoneye`,
   category *Integration*), or copy `custom_components/reliable_radoneye` into `/config/custom_components/`.
   Restart Home Assistant.
2. **Close the RadonEye phone app.** A monitor accepts one Bluetooth connection at a time.
3. Open **Settings > Devices & services**. A discovered monitor appears as *Reliable RadonEye*; click
   **Add**. (Or **Add integration > Reliable RadonEye** and pick the monitor from the list.)
4. Optionally type a **label** such as `upstairs` (default: the last 4 characters of the serial) and
   **Submit**. Home Assistant connects once to read the serial number and model.
5. Within about 5 minutes `sensor.radon_upstairs_radon` shows the device's value. Counts-based radon
   follows once the monitor's counting window has been verified, usually after **3 to 5 hours**
   (`sensor.radon_upstairs_counting_window_check` then changes from `pending` to `passed`).

Migrating from `rd200_ble` and want to keep your history? Read
[Migration from rd200_ble](docs/migration-from-rd200_ble.md) **before** step 3.

## What you get

Per monitor, with the label `upstairs`:

| Entity | Example entity ID | Notes |
|---|---|---|
| Radon | `sensor.radon_upstairs_radon` | Device's latest value, pCi/L |
| Radon 1-day level / 1-month level / peak | `sensor.radon_upstairs_radon_1_day_level` ... | Device's own averages and peak |
| Last boot | `sensor.radon_upstairs_last_boot` | When the monitor last restarted |
| Radon (counts, 1 h) | `sensor.radon_upstairs_radon_counts_1_h` | Derived from 6 windows, with `lower`/`upper` |
| Radon (counts, 24 h) | `sensor.radon_upstairs_radon_counts_24_h` | Derived from 144 windows, with `lower`/`upper` |
| First-attempt read success (4 h) | `sensor.radon_upstairs_first_attempt_read_success_4_h` | Radio quality, % |
| Window capture (24 h), Missed windows (24 h), Counts vs device (7 d) | `sensor.radon_upstairs_window_capture_24_h` ... | Diagnostics |
| Counting window check | `sensor.radon_upstairs_counting_window_check` | Diagnostic: `pending`, `passed` or `failed` |
| Signal strength, Last good read | `sensor.radon_upstairs_signal_strength` ... | Diagnostics, disabled by default |
| Backup reader reachable | `binary_sensor.radon_upstairs_backup_reader_reachable` | Only with a backup reader URL |

Integration-wide: `sensor.radoneye_log_last_pull` (outcome of the daily log pull) and
`sensor.radoneye_radio_time_share`. Full details in [Entities](docs/entities.md).

## Documentation

| Guide | What it covers |
|---|---|
| [Installation](docs/installation.md) | Requirements, HACS and manual install, Bluetooth placement, upgrading, uninstalling |
| [Configuration](docs/configuration.md) | Setup flow, every option, multiple monitors, YAML import, the `pull` service |
| [Entities](docs/entities.md) | Every entity, attributes, availability, statistics, charting the hourly counts |
| [Reliability](docs/reliability.md) | What the reliability numbers mean, what good looks like, improving a weak link |
| [Migration from rd200_ble](docs/migration-from-rd200_ble.md) | Parallel run, taking over the old entity IDs with statistics intact, rollback |
| [Troubleshooting](docs/troubleshooting.md) | Symptoms, causes and fixes; debug logging |
| [FAQ](docs/faq.md) | Calibration, differences from the display, privacy, read-only guarantee |
| [Backup reader](extras/backup_reader/README.md) | The optional second reader on another machine |

Technical documentation for developers and the curious:
[architecture](docs/technical/architecture.md), [the counts method](docs/technical/counts-method.md),
[the Bluetooth protocol](docs/technical/protocol.md), [data formats](docs/technical/data-formats.md) and
[development](docs/technical/development.md).

## Limitations

- Verified only on **RD200V3, firmware V3.0.1**. Other firmware is validated live and falls back to
  device values only if its counting window does not match.
- At **zero radon** every window holds 0 counts, which cannot prove a 10-minute window, so counts mode stays
  off (device values still work). At very low radon it can take many hours to switch on.
- The **1 h** counts value is noisy by nature (often 15 to 30 % either way at typical indoor levels; see its
  `lower`/`upper`). Use the 24 h value for trends.
- A monitor accepts **one Bluetooth connection at a time**. The phone app, another integration or another
  script reading the same monitor will collide with Home Assistant's reads.
- Before the counting window check has passed (or after it failed), reads are not aligned to the window, so
  the daily log pull rides on an unaligned read and is not timed to stay clear of a backup reader (see
  [Configuration](docs/configuration.md#the-pull-service)).
- The conversion factor *k* is a starting point measured on two units, not an independent calibration (see
  the [FAQ](docs/faq.md#calibration-and-the-factor-k)).
- Radon values are in pCi/L (1 pCi/L = 37 Bq/m³).

## Early adopters

A pre-release version used the domain `radoneye_log`. Remove that integration and its config entries before
installing this one; the two are not migrated automatically.

## Credits and licence

The RD200 Bluetooth protocol code is adapted from [sormy/radoneye](https://github.com/sormy/radoneye) (MIT,
copyright Artem Butusov); its licence is included as `custom_components/reliable_radoneye/LICENSE-radoneye`.
The particle-count fields come from that project's protocol notes.

Reliable RadonEye is released under the MIT licence; see [LICENSE](LICENSE). Changes are listed in
[CHANGELOG.md](CHANGELOG.md).

This project is independent. It is not affiliated with, endorsed by, or supported by Ecosense, FTLab, or the
authors of `rd200_ble`. It is not a certified radon measurement; for decisions about mitigation, follow your
national radon guidance.
