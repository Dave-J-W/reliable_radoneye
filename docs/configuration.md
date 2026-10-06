# Configuration

[README](../README.md) > Configuration

Everything is configured in the Home Assistant UI. A YAML import exists only to help migration from
`rd200_ble`. Install first: [Installation](installation.md).

## Adding a monitor

### From discovery (usual)

Home Assistant's Bluetooth integration hears every RD200 v2/v3, because they advertise a name beginning with
`FR:`. A discovered monitor appears under **Settings > Devices & services** as *Discovered: Reliable
RadonEye* with its Bluetooth name.

1. Click **Add** on the discovered card.
2. The dialog *Add FR:...* says that Home Assistant will connect once to check the model. Optionally type a
   **Label** (see [Options](#options)). Leave it empty to use the last 4 characters of the serial number.
3. Click **Submit**. Home Assistant connects (for at most 15 seconds plus a short disconnect), reads the
   status packet, and takes the serial number and model from it.

| Outcome | Meaning |
|---|---|
| Entry *Radon &lt;label&gt;* created | Done. The first read is queued at once. |
| *Could not connect. Move closer or try again.* | The connection or read failed (weak signal, the phone app connected, another client holding the monitor). Fix that and click Submit again. |
| *Not supported: only RadonEye RD200 v2/v3* | The monitor does not offer the v2/v3 Bluetooth service (for example an RD200 v1), or its status packet is not recognised. Nothing was written to it. |
| *This monitor is already set up* | An entry with the same Bluetooth address exists. |

This **confirm-by-connecting** step is why an RD200 v1 is refused at setup rather than failing later.

### Manually

**Settings > Devices & services > Add integration > Reliable RadonEye** lists every `FR:` monitor that Home
Assistant currently hears through a connectable adapter or proxy and that is not set up yet. Pick one; the
rest is the same as above. If the list would be empty the flow stops with *No RadonEye monitors heard by Home
Assistant's Bluetooth*: check that the monitor is powered, in range, and that the Bluetooth integration has a
working adapter or proxy.

### What happens next

1. **Device values at once.** The first read is queued immediately; the Radon, 1-day, 1-month and peak
   sensors fill within about a minute (the 1-day, 1-month and peak values stay unknown if the monitor
   rebooted less than 60 minutes ago).
2. **Window check.** While the counting window is being verified, the monitor is read every 5 minutes at
   unaligned times. This *device-values-only* mode typically lasts 3 to 5 hours (longer at very low radon).
   The diagnostic sensor **Counting window check** shows `pending` meanwhile, then `passed` (or `failed`).
3. **Counts mode.** Once verified, reads are aligned to the monitor's 10-minute counting window, each
   window's particle count is archived, and the counts-based radon sensors start reporting (the 1 h value
   after 4 captured windows, the 24 h value after 120).

See [Troubleshooting](troubleshooting.md#counts-based-radon-stays-unavailable) if counts mode does not start.

## Options

Each monitor has its own options: **Settings > Devices & services > Reliable RadonEye > (entry) >
Configure**. Saving the options reloads that monitor. Its measurements are kept, but its device-value
entities are `unavailable` for a minute or so until the next read.

| Option | Default | Allowed values | What it does |
|---|---|---|---|
| **Label** | Last 4 characters of the serial | Any text | Used in the device name (*Radon &lt;label&gt;*), the statistics names and the archive file names. |
| **Counts per hour per Bq/m³ (k)** | `1.27` | 0.01 to 100 | Conversion factor for the counts-based radon. |
| **Backup reader URL** | empty | `http://host:port`, or empty | Address of the optional [backup reader](../extras/backup_reader/README.md). Empty means no network requests at all. |
| **Parallel run: read rd200_ble in its own slot** | off | on / off | Only for a migration from `rd200_ble`. |

### Label

- Choose something short and meaningful, such as `upstairs` or `basement`. It becomes part of the entity IDs
  **when the entities are first created** (`sensor.radon_upstairs_radon`).
- Changing the label later renames the device and the statistics names, and new archive files use the new
  label. It does **not** rename existing entity IDs (Home Assistant keeps an entity ID once created) or the
  config entry's title. Rename those yourself in the UI if you want.
- Avoid characters that are awkward in file names (`/`, `\`, `:`), because the label is used in archive
  file names.

### Counts per hour per Bq/m³ (k)

The counts-based radon is `(counts per hour) / k` in Bq/m³, divided by 37 for pCi/L.

- **Leave it at 1.27** unless you have a reason. The default was measured on two RD200V3 units (1.27 and
  1.28) against the devices' own values.
- **Change it** when you have run the monitor next to a reference instrument for several days, or when the
  *Counts vs device (7 d)* diagnostic stays clearly away from 1 and you want the counts-based value to agree
  with the display. The [FAQ](faq.md#calibration-and-the-factor-k) explains both methods.
- Because the raw counts are stored, a new k applies at once to the counts-based values (they are recomputed
  from the stored windows), and never touches the archive or the hourly counts statistics. States already
  recorded in history keep the k they were computed with; any past period can be recomputed from the archive.
- Each change of k is written to the **logbook** once (*Radon upstairs: factor k changed from 1.27 to 1.3
  counts/h per Bq/m³*) and recorded (time, old value, new value) in the integration's stored state.

### Backup reader URL

Set this only if you run the [backup reader](../extras/backup_reader/README.md) on a second machine, for
example `http://backup-host.local:8765` or `http://192.0.2.10:8765`. Use the base address only, no path.

With a URL set:

- a **Backup reader reachable** binary sensor appears for the monitor (after the reload);
- Home Assistant asks the backup reader's `/status` once an hour, to update that sensor;
- when a window is missed (all of Home Assistant's reads for it failed), after a restart gap, and once an
  hour for any window still missed in the last 24 hours, it asks `/windows` for the windows it lacks and
  adds them to the archive, marked `source = backup`;
- each request times out after 5 seconds. A dead or wrong backup reader never affects Home Assistant's own
  reads; the binary sensor just turns off.

### Parallel run

Use this only while running `rd200_ble` side by side during a migration, with `rd200_ble`'s own polling
disabled. When on, the integration asks `rd200_ble` to refresh (it calls `homeassistant.update_entity` on
`sensor.fr_<serial>_radon_uptime`) at +4:30 of a window, between its own reads, so that the two
integrations never connect to the monitor at the same time. The time `rd200_ble` takes counts toward the
radio time share.

- If that entity does not exist (for example `rd200_ble` is removed, or its entity was renamed), the request
  is skipped silently.
- **Turn it off after the takeover.** If you later roll back and re-add `rd200_ble`, a parallel option that is
  still on would start triggering it again.

See [Migration from rd200_ble](migration-from-rd200_ble.md).

## Read schedule

Each monitor counts particles in 10-minute windows that roll over when its uptime is a multiple of 10
minutes. Every read reports the count of the window that just closed (*counts previous*) and the window in
progress. The integration learns each monitor's rollover time from its uptime and reads like this (times are
after the computed rollover, which can be up to 1 minute later than the true one, because uptime has
1-minute resolution):

| Read | When | Retries | Purpose |
|---|---|---|---|
| **A** | +1:00 | +1:20 and +2:00 | Captures the window that just closed |
| **B** | +6:00 | +6:20 and +7:00 | Second chance for the same window; fresh device value |
| Daily pull | Replaces the first A read at or after **06:00 local** (in device-values-only mode: the first unaligned read at or after 06:00) | One retry at +60 s; if both fail, the next A slots (or unaligned reads) that day try again | Reads the device's stored hourly log |
| Backup reader (optional, other machine) | +3:30 and +8:30 | One retry at +20 s; never starts after +9:00 | Fills windows Home Assistant missed |
| `rd200_ble` (parallel run only) | +4:30 | none | Keeps the two integrations apart |

A retry is only made if it can start before the next rollover. If A and all its retries fail, B still
captures the window; if B also fails, the window is recorded as **missed** (and fetched from the backup
reader if there is one).

In **device-values-only** mode (before the window check passes, or if it fails) reads are simply every
5 minutes, with the same +20 s and +60 s retries. The daily pull still runs: it takes the place of the first of
these reads at or after 06:00 local, with one retry at +60 s. These unaligned reads are not timed around a backup
reader's slots, and they are not counted in first-attempt read success.

## Multiple monitors

Add each monitor separately; each gets its own config entry, device, options and schedule.

- **One Bluetooth connection at a time, integration-wide.** All monitors share one queue. When several reads
  are due, the one whose window closes soonest goes first. A monitor's rollovers follow its own uptime, so
  monitors are normally spread across the 10 minutes; after a power cut they may coincide, and the queue
  serves them one after another.
- **Bounded connection time.** A status read may take at most 15 seconds to connect and read, plus at most
  5 seconds to disconnect (20 seconds worst case). The daily pull may take 23 + 5 seconds. A typical read
  takes a few seconds.
- **Isolation.** One monitor failing never stalls the others; an error in one job is logged and that monitor
  is simply read again a minute later.
- **Radio budget.** Two reads per window per monitor is about 12 short connections per monitor per hour (the
  same in device-values-only mode: one read every 5 minutes). At roughly 5 to 8 seconds per read including the
  disconnect, that is about 1 to 3 % of the radio's time per monitor. This is an estimate, not a measurement;
  the sensor below reports the real figure. The integration-wide
  `sensor.radoneye_radio_time_share` reports the share of the last hour spent connected (including
  `rd200_ble` refreshes in a parallel run).
- **Radio time-share warning.** Above **50 %** a persistent notification *RadonEye radio load* asks you to add
  a Bluetooth proxy near the monitors; it is dismissed automatically when the share drops below 40 %. A high
  share means slow or failing connects, or many monitors on one adapter. See
  [Reliability](reliability.md#radio-time-share).

## YAML import

The YAML block is a **one-time import** for migrations: it creates config entries without the confirm step,
and lets you name the existing statistic that the daily pull should fill. For a fresh install, use the UI
instead.

```yaml
# configuration.yaml (or a package)
reliable_radoneye:
  devices:
    - address: "AA:BB:CC:DD:EE:01"
      serial: "XX01RE000001"
      label: "upstairs"
      statistic_id: sensor.fr_xx01re000001_radon   # optional
      factor: 1.27                                 # optional
      backup_url: "http://backup-host.local:8765"  # optional
      parallel_with_rd200_ble: true                # optional
    - address: "AA:BB:CC:DD:EE:02"
      serial: "XX01RE000002"
      label: "basement"
```

| Key | Required | Type | Meaning |
|---|---|---|---|
| `address` | yes | text | The monitor's Bluetooth address. Becomes the entry's unique ID. |
| `serial` | yes | text | The monitor's serial number, exactly as the device reports it (upper case, for example `XX01RE000001`). Used in file names, statistic IDs and the backup reader requests. |
| `label` | yes | text | As the [Label](#label) option. |
| `statistic_id` | no | entity ID | The statistic the daily log pull fills, for example the old `rd200_ble` radon sensor. Without it, the pull fills this integration's own Radon sensor. See [Migration](migration-from-rd200_ble.md#the-statistic_id-key-and-the-backfill). |
| `factor` | no | number | Initial *k* (default 1.27). |
| `backup_url` | no | text | Initial backup reader URL. |
| `parallel_with_rd200_ble` | no | true/false | Initial parallel-run option (default false). |

How the import behaves:

- At each start, every device in the block is offered to the config flow. A device whose Bluetooth address
  already has an entry is skipped, so **later edits to the YAML are ignored**. Change options in the UI.
- Imported entries are not checked by connecting, so check the serial carefully: it must match the device.
- `statistic_id` can only be set this way; it is not an option in the UI.
- While the block is present, a Repair issue *Remove reliable_radoneye from YAML* appears. Check that the
  monitors are under **Settings > Devices & services > Reliable RadonEye**, then delete the block and restart.
  The entries stay.

Where to find the serial and address: the RadonEye app shows the serial (on the RD200V3 units tested, the
Bluetooth name was `FR:` followed by the serial; this is an observation, as the integration itself only relies on
the name starting with `FR:`, the manifest's `local_name: FR:*` matcher), and the Bluetooth integration's
advertisement list shows the address. If you already use `rd200_ble`, its device page shows both.

## The pull service

`reliable_radoneye.pull` reads each monitor's stored hourly log (the device's rolling log of hourly values;
how far back it reaches depends on the device) and fills hours that are **missing** from the long-term radon
statistics. It never overwrites an existing hour. It runs once a day by itself; the service is for running it on demand.

| Field | Default | Meaning |
|---|---|---|
| `immediate` | `false` | `false`: queue the pull for each monitor's next read slot (the next A read in counts mode, the next unaligned read otherwise). `true`: run it now. |

**Next slot (recommended).** The pull takes the place of the next A read, which is timed to stay clear of
the backup reader, or, in device-values-only mode, the next unaligned read (about every 5 minutes; not timed
around the backup reader). It counts as that day's pull, so the 06:00 pull is skipped that day if it already
ran. It works in every mode.

```yaml
action: reliable_radoneye.pull
```

**Immediately.** One attempt per monitor, as soon as the radio is free, with no retry. Use this if you are
watching the result. It can collide with the backup reader's read of the same monitor.

```yaml
action: reliable_radoneye.pull
data:
  immediate: true
response_variable: pull_result
```

The service can return a response, for example:

```json
{"queued": ["XX01RE000001", "XX01RE000002"], "immediate": true}
```

`queued` lists the serials the pull was requested for. It does not mean the pull has succeeded; see the
result in `sensor.radoneye_log_last_pull` (attributes per label) or in
`/config/reliable_radoneye/pull_log.csv`.

What a pull does:

1. Reads the status and the stored log in one connection (at most 23 seconds plus disconnect).
2. Writes `rd200_<serial>_<label>_<UTC timestamp>.json` (raw status and log) and
   `rd200_<serial>_<label>_latest.csv` (the log with timestamps) under `/config/reliable_radoneye/`.
3. Takes only the log points since the monitor's last power-up (only those have exact timestamps), skips
   the most recent 2 hours (Home Assistant compiles those itself), and imports the ones whose hour is
   missing from the target statistic. Each filled hour is listed in `backfill_audit.csv`.
4. Appends a line to `pull_log.csv` (including the attempt number) and a logbook entry *RadonEye log pull*
   such as *upstairs: ok, 8760 points, filled 2 h*. A pull that fails (after its retry) also gets a row in
   `pull_log.csv`; it is tried again at the next slot, but the logbook shows at most one *FAILED* entry per
   monitor per day.

The number of points depends on how much log the monitor holds; 8760 (a year of hours) is only an example.
