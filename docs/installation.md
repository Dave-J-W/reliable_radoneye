# Installation

[README](../README.md) > Installation

This guide covers what you need, how to install, where to put your Bluetooth radio, and how to upgrade or
remove the integration. When it is installed, continue with [Configuration](configuration.md).

## Requirements

| Requirement | Details |
|---|---|
| Home Assistant | **2026.9.0 or newer** (the minimum in `hacs.json`, and the version it was verified on). |
| Home Assistant integrations | **Bluetooth** (set up with at least one adapter or proxy) and **Recorder** (on by default). The integration declares both as dependencies. |
| A Bluetooth radio that can **connect** | A Bluetooth adapter on the Home Assistant host (built-in or USB), or an ESPHome Bluetooth proxy with active connections enabled. Passive-only listeners cannot be used: the monitor must be connected to, not just heard. |
| Monitor | RadonEye **RD200 v2 or v3**. Its Bluetooth name starts with `FR:`. RD200 v1 is refused. |
| Extra Python packages | None. The integration uses Home Assistant's own Bluetooth stack (`bleak`, `bleak-retry-connector`). |
| Optional | A second Linux machine with its own Bluetooth radio for the [backup reader](../extras/backup_reader/README.md). |

Only one Bluetooth client can talk to a monitor at a time. Close the RadonEye phone app, and do not run
another integration or script against the same monitor unless you are doing a deliberate
[parallel run](migration-from-rd200_ble.md#2-parallel-run-recommended).

## Install with HACS (custom repository)

1. In Home Assistant, open **HACS**.
2. Open the three-dot menu (top right) and choose **Custom repositories**.
3. Enter `https://github.com/Dave-J-W/reliable_radoneye`, choose the category **Integration**, and click
   **Add**.
4. Search HACS for **Reliable RadonEye**, open it, and click **Download**. Pick the latest version.
5. **Restart Home Assistant** (Settings > System > three-dot menu > Restart).
6. Go on to [Configuration](configuration.md#adding-a-monitor).

## Manual install

1. Download the repository (for example as a ZIP of the latest release) and unpack it.
2. Copy the **whole** folder `custom_components/reliable_radoneye` into your Home Assistant configuration
   directory, so that you end up with:

   ```text
   /config/custom_components/reliable_radoneye/__init__.py
   /config/custom_components/reliable_radoneye/manifest.json
   /config/custom_components/reliable_radoneye/core/...          <- must be there
   /config/custom_components/reliable_radoneye/translations/...  <- must be there
   ...
   ```

3. Restart Home Assistant.

> **Copy the subfolders too.** The decision logic lives in `core/` and the entity names in `translations/`.
> A copy or sync tool that only copies top-level files leaves the integration unable to load (in a real
> deployment this caused a failed restart). If you sync with a script, check that it copies subdirectories
> and, on upgrades, that it removes files that no longer exist in the new version.

## Bluetooth placement

Placement matters more than anything else for read reliability. The RD200 is a low-power Bluetooth device,
and every read needs a full connection, not just a received advertisement.

### Signal strength guidance

You can see the signal strength of a monitor in two places:

- the Bluetooth integration's advertisement monitor (**Settings > Devices & services > Bluetooth**, where
  your Home Assistant version offers it) or its diagnostics download, which show the RSSI each adapter or
  proxy hears;
- the integration's own **Signal strength** sensor (disabled by default; enable it under the monitor's
  device page, see [Entities](entities.md#diagnostics)).

Rules of thumb (general Bluetooth Low Energy experience, not a guarantee):

| RSSI at the adapter or proxy | What to expect |
|---|---|
| -70 dBm or stronger | Comfortable. Connects are fast and first attempts nearly always succeed. |
| -70 to -80 dBm | Usually fine. Occasional slow connects. |
| -80 to -90 dBm | Marginal. Expect connection aborts and slow connects at times, especially in busy radio periods. Retries keep the data complete, but first-attempt success will dip. |
| Weaker than -90 dBm | Unreliable. Move the monitor or add a proxy. |

**A real example.** One monitor sat at about **-83 dBm** from a Raspberry Pi's built-in Bluetooth radio, with
no proxy. Most of the time it was fine, but on two nights in a row its connects repeatedly aborted at the
radio level (`le-connection-abort-by-local` in the debug log) and took 8 to 13 seconds, so first-attempt
success fell as low as 0 to 40 % for several hours. Window capture stayed at 100 % throughout, because the
retry 20 seconds later reconnected in under a second. A second machine with its own radio, at a similar
signal, read the same monitor without a single failure over the same period. The cause was the link to the
Home Assistant host's radio, not the integration. See [Reliability](reliability.md) for how to read these
numbers.

### Proxies versus onboard radios

- **ESPHome Bluetooth proxy (ESP32).** The usual answer to a weak link: put a proxy near the monitors.
  Home Assistant's Bluetooth manager automatically connects through whichever adapter or proxy hears the
  monitor best, so no configuration is needed in this integration. The proxy must have **active
  connections** enabled (in ESPHome, `bluetooth_proxy:` with `active: true`). An Ethernet-connected ESP32 is
  more robust than a Wi-Fi one, because Wi-Fi and Bluetooth share the same 2.4 GHz radio on an ESP32.
- **Built-in radio of the Home Assistant host** (for example a Raspberry Pi). Works when the monitor is
  close. It is often in a metal case, near other electronics, and shares its antenna with Wi-Fi on some
  boards.
- **USB Bluetooth adapter.** A supported adapter on a short USB extension cable, away from USB 3 ports and
  disks (USB 3 is a known 2.4 GHz noise source), can be placed for a better line of sight.

Other practical tips:

- Raise the monitor off the floor and away from metal, appliances and chargers, while following the
  manufacturer's placement advice for radon measurement.
- Each read holds the connection for a few seconds. Many other Bluetooth devices connecting through the same
  adapter compete for it; the **radio time share** sensor shows how much of the last hour this integration
  used (see [Reliability](reliability.md#radio-time-share)).

## Upgrading

1. In HACS, open **Reliable RadonEye** and click **Update** (or download the new version manually, replacing
   the whole folder).
2. Read [CHANGELOG.md](../CHANGELOG.md) for anything that needs action.
3. Restart Home Assistant.

What survives an upgrade or restart:

- The integration keeps its working state (the last 8 days of windows, reliability counters, validation
  status) in `/config/.storage/reliable_radoneye.state`. It is saved at most every 10 minutes and on a
  clean shutdown, so a restart blanks nothing that was measured. After a crash, up to about 10 minutes of
  windows may be missing from that state; the CSV archive still has them.
- The device-value entities are `unavailable` after a restart until the first good read (normally within a
  few minutes). Counts-based values come back immediately from the stored windows.

**Downgrading.** Newer versions can add to the format of the stored state. An older version is not
guaranteed to read a state file written by a newer one; during development one such change made an older
build fail at setup. If a downgrade fails to load, see
[Troubleshooting](troubleshooting.md#the-integration-fails-to-load-after-a-downgrade).

## Uninstalling

1. **Settings > Devices & services > Reliable RadonEye**: for each monitor entry, open the three-dot menu and
   choose **Delete**.
2. If you added the YAML import block (see [Configuration](configuration.md#yaml-import)), remove it, or the
   monitors will be imported again at the next start.
3. In HACS, open **Reliable RadonEye** and choose **Remove** (or delete
   `/config/custom_components/reliable_radoneye`).
4. Restart Home Assistant.

What is left behind on purpose (your data stays yours):

| Left behind | Where | What to do |
|---|---|---|
| Raw-count archive, daily log pulls, pull and backfill logs | `/config/reliable_radoneye/` | Keep as a record, copy elsewhere, or delete the folder. |
| Stored working state | `/config/.storage/reliable_radoneye.state` | Safe to delete with Home Assistant stopped. If you reinstall later, deleting it means each monitor re-validates its counting window. |
| Long-term statistics of the Radon, 1-day, 1-month, peak, counts 1 h and first-attempt sensors | Recorder database | Home Assistant may list them under **Developer tools > Statistics** as having no matching entity. They do no harm. Delete them there only if you no longer want the history. |
| Hourly external statistics `reliable_radoneye:counts_<serial>` and `reliable_radoneye:windows_<serial>` | Recorder database | They stay in the database. They are small (two rows per monitor per hour). |

If you are switching back to `rd200_ble` after a takeover of its entity IDs, follow
[Rollback](migration-from-rd200_ble.md#rollback) instead, so that the long-term radon history continues.
