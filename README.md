# RadonEye RD200 (counts) for Home Assistant

A Home Assistant custom integration for **RadonEye RD200 v2/v3** radon monitors. It reads them over Home
Assistant's own Bluetooth and keeps the **raw particle counts** the monitor reports, not just its displayed
radon value.

> Not affiliated with, endorsed by, or supported by Ecosense or FTLab. "RadonEye" is their product name.
> This integration only **reads** from the monitor; it never writes settings or changes anything on the device.

## What it does

- Reads each monitor over Home Assistant's Bluetooth (local adapters or ESPHome Bluetooth proxies) about
  **every 5 minutes**, with retries. The monitor counts particles in **10-minute windows**; reads are aligned
  to those windows so each one is captured with plenty of margin (a first read about 1 minute after a window
  closes, a second chance at about 6 minutes).
- **Archives the raw counts exactly**: one CSV row per captured window under `/config/radoneye_logs/`, and two
  hourly *external statistics* per monitor (counts, and windows captured), so exact counts survive any later
  change of the conversion factor.
- **Derives radon from the counts**, with a 68 % exact Poisson (Garwood) confidence interval, over the last
  1 h and 24 h.
- Reports **read reliability** (first-attempt success per 4 h block, window capture, missed windows).
- Pulls the monitor's stored hourly log once a day (after 06:00 local) and fills hours missing from the
  long-term radon statistics. It never overwrites an existing hour.
- Optionally cooperates with a **second reader** on another machine, so windows Home Assistant missed (for
  example during a restart) can be filled in.
- Handles any number of monitors from one integration, one Bluetooth connection at a time.

## Supported devices

| Device | Status |
|---|---|
| RD200 v2 / v3 (Bluetooth name `FR:*`) | supported |
| RD200 v1 | refused (different protocol) |
| Other RadonEye models | not supported |

The 10-minute counting-window behaviour was measured on RD200V3 firmware V3.0.1. Other firmware is not
assumed to behave the same: each monitor is checked **live** by a window-model validator (it needs at least
20 same-window read pairs with no change, and every observed rollover at the expected time). Until it passes,
a monitor works in *device-values-only* mode (unaligned 5-minute reads, no counts-based radon). If it fails, a
Repair issue says so.

## Installation

### HACS (custom repository)

1. HACS > three-dot menu > **Custom repositories**.
2. Add `https://github.com/<OWNER>/<REPO>` with category **Integration**.
3. Install **RadonEye RD200 (counts)** and restart Home Assistant.

### Manual

Copy the `custom_components/radoneye_log` folder into your Home Assistant `config/custom_components/`
directory and restart.

Requires Home Assistant 2026.9.0 or newer (the version this was verified on), the Bluetooth integration and
the Recorder.

## Setup

Monitors are discovered automatically over Bluetooth. Open **Settings > Devices & services** and confirm the
discovered monitor, or choose **Add integration > RadonEye RD200** and pick one from the list. Home Assistant
connects once to read the serial number and model (v1 is refused). You can give the monitor a label; the
default is the last 4 digits of its serial.

Options (per monitor, **Configure**):

| Option | Meaning |
|---|---|
| Label | Used in the device name ("Radon <label>"), entity IDs and file names. |
| Counts per hour per Bq/m³ (k) | Conversion factor for counts-based radon. Default 1.27. See below. |
| Backup reader URL | Optional address of the backup reader, for example `http://backup-host.local:8765`. Empty means no requests are made. |
| Parallel run | Also asks the older `rd200_ble` integration to refresh in its own time slot (only useful while migrating). |

## Entities

Per monitor (entity IDs look like `sensor.rd200_<label>_<key>`):

| Entity | Meaning |
|---|---|
| Radon | The device's latest value, pCi/L. |
| Radon 1-day level, 1-month level, peak | The device's own averages and peak, pCi/L. Unknown for the first 60 minutes after the monitor reboots (it reports 0 then). |
| Last boot | When the monitor last (re)started. Changes only on reboot. |
| Radon (counts, 1 h) | Radon derived from the last 6 windows. Attributes `lower`, `upper` (68 % interval), `coverage`, `factor`. Unavailable below 4 of 6 windows. |
| Radon (counts, 24 h) | The same over the last 144 windows. Unavailable below 120 of 144. No long-term statistics of its own (the hourly counts statistics cover that). |
| First-attempt read success (4 h) | Percentage of scheduled reads that worked on the first try, for the **previous** fixed 4 h block (00/04/08/12/16/20 local). Kept in long-term statistics. |
| Window capture (24 h), Missed windows (24 h), Counts vs device (7 d) | Diagnostics, recomputed hourly. The last compares counts-based radon with the device's own value; drift away from 1 suggests a mis-set k. |
| Signal strength, Last good read | Diagnostics, disabled by default. |
| Backup reader reachable | Binary sensor, only present when a backup reader URL is set. |

Integration-wide: **RadonEye log last pull** (outcome of the daily log pull per monitor) and **RadonEye radio
time share** (fraction of time spent on Bluetooth connections; above 50 % you get a notification, and should
add a Bluetooth proxy).

**Stale rule.** The device-value entities (Radon, 1-day, 1-month, peak) become `unavailable` after
**20 minutes** without a good read, and after a Home Assistant restart until the first read. A stale value is
never shown as current. Counts-based values follow their own coverage rule instead; reliability and
diagnostics always report. A monitor unreachable for an hour raises a persistent notification.

The service `radoneye_log.pull` queues the stored-log pull for the next read slot (or runs it now with
`immediate: true`, which can collide with another reader).

## How radon is derived from counts

At normal indoor radon the detector produces only a handful of counts per 10-minute window (about 2 at
10 Bq/m³). With N counts over T hours of captured windows:

    radon (Bq/m³) = (N / T) / k        pCi/L = Bq/m³ / 37

Missing windows shorten T; they never count as zero. The 68 % interval is the exact Poisson (Garwood)
interval on N, scaled the same way, so it stays honest at low counts, including N = 0.

**The factor k** is counts per hour per Bq/m³. The default **1.27** was measured on two units (1.27 and 1.28)
by comparing counts per hour with the device's own Bq/m³ value. Treat it as a starting point: the device
probably derives its value from the same counts, so this is a consistency check, not an independent
calibration. To calibrate your own unit, run it next to a reference instrument for a few days and set
k = (counts per hour) / (reference Bq/m³). Because the counts are stored, changing k re-scales every derived
value and never touches the archive. Each change is logged.

## Optional backup reader

`extras/backup_reader/` contains a small stand-alone script for a second machine with its own Bluetooth radio
(for example a Raspberry Pi). It reads the same monitors in the gaps between Home Assistant's reads (at +3:30
and +8:30 of each window, never after +9:00), keeps the last 24 hours of windows, and serves them over HTTP so
Home Assistant can fetch any it missed. Without it every feature works; only gap-filling after a restart is
lost, and those windows are recorded as *not observed* rather than missed. See
[extras/backup_reader/README.md](extras/backup_reader/README.md).

## Migrating from rd200_ble

1. **Import.** Add the YAML below (once) and restart; the monitors appear as config entries. `statistic_id` is
   the entity whose long-term statistics the daily log pull should back-fill (for example the old
   integration's radon sensor). A Repair then asks you to remove the YAML; do so after checking the entries.

   ```yaml
   radoneye_log:
     devices:
       - address: "AA:BB:CC:DD:EE:FF"
         serial: "XX01RE000001"
         label: "Upstairs"
         statistic_id: sensor.your_old_radon_sensor
   ```

2. **Parallel run.** The new entities use temporary IDs `sensor.rd200_<label>_*`. Disable polling on the old
   integration's config entries (Devices & services > entry > three-dot menu > *Disable polling for updates*)
   so only one client talks to each monitor; a monitor accepts a single connection at a time. Run both for a
   day or two and compare.
3. **Entity-ID takeover (statistics continue).** Remove the old integration's config entries first, then
   rename the new Radon, 1-day, 1-month and peak entities to the old entity IDs. Beforehand, check that the old
   statistics have the same unit (`pCi/L`), unit class and mean/sum type as the new entities.

   Rehearsal finding (Home Assistant 2026.9.4, disposable test sensors): renaming an entity onto an ID that
   already has statistics and state-history rows does not merge or overwrite anything; the recorder logs a
   warning and leaves the old rows alone. The old ID's statistics keep one row for every hour with no gap, the
   hour of the rename is a blend, and afterwards the old ID carries the new sensor's values. The new sensor's
   earlier rows under its temporary ID stay orphaned (neither lost nor merged). Home Assistant may then show
   "orphaned statistics" or "state class removed" repairs; **do not accept any offer to delete**.
4. Keep the old integration installed but unused for a couple of weeks so you can roll back.

## Known limitations

- At very low radon, equal counts in adjacent windows are common, and the window validator can **falsely
  fail** ("counting window not recognised"). It re-tests every 24 hours, so a transient failure is not a fault.
- The 1 h counts value is noisy by nature (see its interval); use the 24 h value for trends.
- A monitor accepts one Bluetooth connection at a time. Do not run the phone app, another integration or
  another script against the same monitor during its read slots.
- Verified only on RD200V3 firmware V3.0.1.
- Not affiliated with Ecosense or FTLab.

## Credits

The RD200 Bluetooth protocol knowledge comes from [sormy/radoneye](https://github.com/sormy/radoneye) (MIT,
copyright Artem Butusov); its licence is included as `custom_components/radoneye_log/LICENSE-radoneye`.

## Development

The decision logic is pure Python with no Home Assistant imports (`custom_components/radoneye_log/core/`) and
is unit- and simulation-tested. `protocol.py` imports `bleak`, so install it first:

    pip install bleak
    python -m unittest discover -s tests -t .

## Licence

MIT, see [LICENSE](LICENSE).
