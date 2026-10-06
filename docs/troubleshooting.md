# Troubleshooting

[README](../README.md) > Troubleshooting

Each section starts from what you see, then gives the likely causes and what to do. Most problems come down to
one of two things: the monitor is still being validated, or the Bluetooth link is weak. If you are unsure,
turn on [debug logging](#debug-logging) for 15 minutes first.

## Quick index

| Symptom | Section |
|---|---|
| Radon works, counts-based radon stays `unavailable` | [Counts-based radon stays unavailable](#counts-based-radon-stays-unavailable) |
| Repair: *counting window not recognised* | [Window not recognised](#repair-counting-window-not-recognised) |
| Radon is `unavailable`, or a *has not been read since* notification | [Device values unavailable](#device-values-unavailable-or-stale) |
| First-attempt read success is low | [Low first-attempt success](#low-first-attempt-read-success) |
| `BleakCharacteristicNotFoundError` in the log | [BleakCharacteristicNotFoundError](#bleakcharacteristicnotfounderror) |
| Slow connects, `le-connection-abort-by-local`, `TimeoutError` | [Slow connects and connect aborts](#slow-connects-and-connect-aborts) |
| Backup reader reachable is off | [Backup reader unreachable](#backup-reader-unreachable) |
| Last pull shows `failed`, or no hours filled | [Daily log pull problems](#daily-log-pull-problems) |
| Notification about a count conflict | [Count conflict](#count-conflict-notification) |
| Notification *RadonEye radio load* | [Radio load](#radio-load-notification) |
| Monitor not discovered, or *Could not connect* at setup | [Setup problems](#setup-problems) |
| Integration fails to load after installing an older version | [After a downgrade](#the-integration-fails-to-load-after-a-downgrade) |

## Counts-based radon stays unavailable

**What you see**

- **Radon** and the device averages work normally.
- **Radon (counts, 1 h)** and **(counts, 24 h)** are `unavailable`.
- **Window capture** and **First-attempt read success** are `unknown`.
- There is no Repair issue.

**Cause: the window check is still pending.** Before trusting the particle counts, the integration checks live
that this monitor's counts follow a 10-minute window. Until the check passes, the monitor is read every
5 minutes in *device-values-only* mode. To pass, the check needs all of the following:

- at least **20 pairs of reads inside the same 10-minute window** with no sign of a rollover;
- at least **20 counted crossings**: pairs of reads in adjacent windows where at least one of the two "previous
  window" counts is 1 or more;
- among those crossings, no more than 30 % "silent". A silent crossing is one where nothing appeared to change.
  That happens by chance with real 10-minute windows, but nearly always with longer ones.

**How long it takes**

| Radon level | Typical time to pass |
|---|---|
| Normal indoor levels (several counts per window) | about **3 to 5 hours**. Each 5-minute read pair adds to one of the two tallies. |
| Very low (most windows 0 or 1 count) | longer: many hours. Crossings where both counts are 0 do not count, because they look the same whatever the window length. |
| **Zero** (every window 0) | **never**. Such data cannot tell a 10-minute window from a longer one, so the monitor stays in device-values-only mode. This is by design. |

Failed reads do not count, so a weak link also slows the check down. A restart keeps the tallies; only the first
read after it is used as a starting point and not counted.

The counts sensors also need enough captured windows once counts mode starts:

- **1 h value:** 4 of the last 6 windows, so about 40 minutes.
- **24 h value:** 120 of the last 144 windows, so about **20 hours**.

**What to do:** usually nothing; wait. To see progress, look at the integration's stored state
`/config/.storage/reliable_radoneye.state`, read-only, for example through the File editor or Samba add-on. Under
each serial, `"validator"` shows `status` (`pending`, `passed` or `failed`) together with the `same_ok`,
`crossings` and `silent` tallies. The file is saved at most every 10 minutes.

## Repair: counting window not recognised

**What you see:** a Repair issue *Radon upstairs: counting window not recognised*, a logbook entry saying counts
mode is off, and device values only.

**Cause:** the window check **failed**. Either of these makes it fail:

- 3 pairs of reads inside one window showed a rollover;
- after at least 60 crossings, more than 30 % were silent, which suggests a window longer than 10 minutes.

Possible reasons:

- **Different firmware or a different model.** The 10-minute window was measured on RD200V3 firmware V3.0.1.
  Other firmware may count differently, and then the fallback is the correct behaviour.
- **Very low radon on version 0.3.0.** That version could fail falsely at near-zero radon. Version 0.3.1 fixed
  this by ignoring 0-to-0 crossings, so upgrade.
- **Read glitches.** The check tolerates one or two of these. It fails only after three.

**What happens next, automatically**

- The check is **re-run from scratch 24 hours after it failed**, and again every 24 hours while it keeps
  failing.
- If a re-test passes, the Repair issue disappears, the logbook says *counting window recognised on re-test;
  counts mode on*, and counts mode starts.
- A **firmware change** detected on the monitor also starts a fresh check, and a pass then clears the issue too.

**What to do:**

- If the issue keeps coming back, the monitor's counting window is probably different. The device values
  remain correct.
- Please open an issue with the model, the firmware version and the `validator` block from the stored state
  (see above).

The issue clears by itself when a check passes.

## Device values unavailable or stale

**What you see**

- **Radon**, 1-day, 1-month and peak are `unavailable`.
- After an hour, a persistent notification *Radon upstairs has not been read since ... UTC* appears.

**Rule:** device values go `unavailable` after **20 minutes** with no good read. After a Home Assistant restart
they are also `unavailable` until the first read, which normally comes within a few minutes. This is deliberate:
an old value is never shown as current.

| Cause | How to tell | Fix |
|---|---|---|
| Monitor not heard by any adapter or proxy | Debug log: `not currently heard by any HA Bluetooth adapter` | Check the monitor's power and that the adapter or proxy works. Look in the Bluetooth integration for the monitor's advertisements. |
| Phone app connected | It happens while you use the app | Close the app completely. A monitor accepts one connection at a time. |
| Another integration or script reading the same monitor | Several clients in the Bluetooth logs | Stop the other client, or use a [parallel run](migration-from-rd200_ble.md#2-parallel-run-recommended) during migration. |
| Weak link | Low first-attempt success, many aborts in the debug log | See [Reliability](reliability.md#improving-a-weak-link). |
| Bluetooth adapter stuck | Other Bluetooth devices are affected too | Reload the Bluetooth integration, or restart the host. |

The notification is dismissed automatically at the next good read, and the logbook then shows *Radon upstairs
is reachable again*.

If **1-day level, 1-month level and peak** show `unknown` but **Radon** works, the monitor restarted less than
60 minutes ago. It reports 0 for these after a restart, so they are hidden for that hour. Check **Last boot**.

## Low first-attempt read success

**What you see:** **First-attempt read success (4 h)** well below 90 %, possibly only at certain times of day.

**First check Window capture (24 h).** If it is 100 %, no data is being lost: retries are rescuing the windows.

- **Cause in practice:** slow or aborted connections on a marginal link. The read gets 15 seconds to connect and
  read, so a connect that takes longer counts as a failure, and the retry 20 seconds later usually connects at
  once.
- **Real case:** a monitor at about -83 dBm on a Raspberry Pi's built-in radio fell to 0–40 % for most of two
  nights and was at 90–100 % in the daytime. Capture stayed at 100 %.
- **Before version 0.3.1:** the 15-second limit also covered the disconnect, which itself took 2–4 seconds. Some
  reads that had already succeeded were therefore discarded, which understated first-attempt success. Version
  0.3.1 bounds the disconnect separately; upgrade if you are on 0.3.0.

**Fix:** see [Improving a weak link](reliability.md#improving-a-weak-link).

## BleakCharacteristicNotFoundError

**What you see:** reads fail with `BleakCharacteristicNotFoundError` in the debug log, typically right after a
restart.

**Cause:** another client was holding the monitor, or had just held it, so the connection did not present the
expected Bluetooth services. In a real case this happened after a Home Assistant restart, while `rd200_ble`'s
own start-up retries held one monitor for about 13 minutes. Once `rd200_ble` let go, reads recovered without
any action.

**What to do**

1. Wait 15 to 30 minutes. It normally clears by itself.
2. Make sure nothing else connects to the monitor: phone app, `rd200_ble` with polling on, scripts.
3. If it persists, reload the entry (**Settings > Devices & services > Reliable RadonEye > (entry) >
   three-dot menu > Reload**).
4. As a last resort, unplug the monitor for 10 seconds. This is general Bluetooth advice, not verified for this
   specific error. The monitor's averages are then unknown for an hour, and its window numbering restarts.

## Slow connects and connect aborts

**What you see** in the debug log (Bluetooth loggers):

- `retry due to le-connection-abort-by-local`, sometimes several times for one connection;
- `Failed to connect after 1 attempt(s): TimeoutError` or `BleakNotFoundError`;
- in the integration's own log: `RadonEye upstairs A attempt 1 failed: TimeoutError: ...`.

**Cause:** the radio level (BlueZ) aborted connection attempts and retried internally. On a marginal link this
can stretch a connect to 8–13 seconds. The integration allows each attempt 15 seconds to connect and read,
then retries at +20 s and +60 s.

**What to do:**

- Improve the link: [Reliability](reliability.md#improving-a-weak-link).
- **Do not** try to give the connection more time. The limits are deliberate: every connection is over within
  20 seconds (28 seconds for the daily pull), so that one slow monitor cannot hold up the radio for others.

## Backup reader unreachable

**What you see:** **Backup reader reachable** is off.

Home Assistant asks the backup reader's `/status` once an hour, and `/windows` when it needs windows. Each
request times out after 5 seconds.

Check, in order:

1. **The URL in the options.** It should be `http://<host>:8765`, with `http://`, with the port, and with no path
   or trailing endpoint.
2. **From another computer on the same network**, open `http://<host>:8765/status`. You should see JSON.
3. **On the backup machine**:
   - `systemctl status rd200-counts` shows the service state;
   - `journalctl -u rd200-counts -n 50` shows its recent log;
   - `curl http://localhost:8765/status` checks the server locally.
4. **Firewall:** port 8765/TCP must be reachable from the Home Assistant host.
5. **Name resolution:** if you use a `.local` name, check that Home Assistant can resolve it, or use the IP
   address.

A dead backup reader never affects Home Assistant's own reads. Without it, windows missed during a Home
Assistant restart are recorded as *not observed* instead of being filled. See the
[backup reader guide](../extras/backup_reader/README.md#operations).

## Daily log pull problems

**What you see:** `sensor.radoneye_log_last_pull` shows `radon_upstairs: failed`, or `all_ok: false`, or hours are
missing from the radon history.

| Cause | How to tell | Fix |
|---|---|---|
| Monitor in device-values-only mode | Counts sensors are `unavailable` | The scheduled pull runs only in counts mode. Call `reliable_radoneye.pull` with `immediate: true`. |
| Connection failed | `radon_upstairs_detail` shows `error`; `pull_log.csv` has the error | The pull is retried after 60 s, then at the following A read slots that day. Improve the link if it keeps failing. |
| Pull succeeded but `filled_hours` is 0 | Detail shows `ok: true, filled_hours: 0` | Normal when no hours are missing. Only hours since the monitor's last power-up, and older than 2 hours, are filled. |
| Backfill target missing | Log warning *no statistics metadata for ...; backfill skipped* | The `statistic_id` from YAML does not exist, or the Radon sensor has no statistics yet. |

## Count conflict notification

**What you see:** a notification such as *Radon upstairs: window ending 2026-10-05 14:20Z: backup=3, ha=2; kept
ha*.

**Cause:** Home Assistant and the backup reader reported different counts for the same 10-minute window. Home
Assistant's own copy is always kept. The warning appears at most once per window.

**What to do:**

- **Once in a while:** ignore it.
- **Often:** check the backup reader's `monitors.json`. A serial paired with the wrong Bluetooth address, for
  example with two monitors swapped, makes the two readers disagree all the time.

## Radio load notification

**What you see:** *RadonEye Bluetooth reads used NN % of the last hour. Add a Bluetooth proxy near the monitors.*

**Cause:** the integration spent more than half of the last hour connected. This comes from many monitors on one
adapter, or many slow and failing connects, each of which can take up to 20 seconds. The notification clears
itself below 40 %.

**What to do:** add a Bluetooth proxy near the monitors, and see
[Reliability](reliability.md#radio-time-share).

## Setup problems

| Symptom | Cause | Fix |
|---|---|---|
| Monitor not discovered | Out of range, no connectable adapter or proxy, or the monitor is already configured | Check the Bluetooth integration hears an `FR:...` device. Passive-only proxies do not count. |
| *No RadonEye monitors heard by Home Assistant's Bluetooth* | As above | As above |
| *Could not connect. Move closer or try again.* | Weak link, or the phone app or another client connected | Close the app, move the monitor closer for setup if needed, and try again. |
| *Not supported: only RadonEye RD200 v2/v3* | An RD200 v1, or a status packet that was not recognised | v1 is not supported. If you have a newer model, please open an issue. |
| Entity names show as raw keys | The `translations/` folder is missing (incomplete manual copy) | Reinstall the whole folder. |
| Integration does not load: *No module named ...core...* | The `core/` folder is missing (incomplete manual copy) | Reinstall the whole folder ([Installation](installation.md#manual-install)). |

## The integration fails to load after a downgrade

**What you see:** after installing an **older** version, the entries fail to set up, with an error while
loading the stored state.

**Cause:** newer versions can change the format of `/config/.storage/reliable_radoneye.state`, and an older
version may not read it. This happened during development, when the stored window list moved to a compact
format. Additive changes are safe: for example, 0.3.1 added a key that 0.3.0 simply ignores.

**Fix:**

1. **Stop Home Assistant.** The file is rewritten on shutdown, so editing it while Home Assistant runs does not
   work.
2. Remove the `"windows"` entry under each serial in that file, or set aside the whole file (rename it to
   `reliable_radoneye.state.bak`).
3. Start Home Assistant.

What you lose by doing this:

- the in-memory window history (up to 8 days). The counts sensors are `unavailable` until enough new windows are
  captured: about 40 minutes for 1 h, about 20 hours for 24 h;
- if you set aside the whole file, also the reliability counters and the window-check result, so the monitor
  re-validates.

The **CSV archive and the long-term statistics are not affected.**

## Debug logging

Turn debug logging on for a short window (10 to 15 minutes is enough to catch a few reads), then turn it off
again, because the Bluetooth loggers are verbose.

**From the UI** (Developer tools > Actions):

```yaml
action: logger.set_level
data:
  custom_components.reliable_radoneye: debug
  bleak_retry_connector: debug
  homeassistant.components.bluetooth: debug
```

Add `bleak.backends.bluezdbus.client: debug` (local adapters on Linux) or `habluetooth: debug` to see
connection aborts and timing at the radio level.

Set the same loggers back to `warning` afterwards (or to your usual level):

```yaml
action: logger.set_level
data:
  custom_components.reliable_radoneye: warning
  bleak_retry_connector: warning
  homeassistant.components.bluetooth: warning
```

**Permanently, in `configuration.yaml`** (needs a restart):

```yaml
logger:
  default: warning
  logs:
    custom_components.reliable_radoneye: debug
```

What the integration logs at debug level:

- every failed attempt: `RadonEye <label> <A|B|pull|free> attempt <n> failed: <error>`;
- problems with the backup reader;
- disconnects that did not complete cleanly.

At info level it logs how many statistics hours each daily pull filled. Errors in a job are logged with a
traceback at error level whatever the setting.

View the log under **Settings > System > Logs**. Use the *Show raw logs* option to see debug lines.

## Still stuck?

Open an issue with:

- the version;
- the model and firmware (shown in `sensor.radoneye_log_last_pull`'s detail attribute after a pull);
- what you see;
- a debug log excerpt covering a few reads.

Before posting, remove or replace serial numbers and Bluetooth addresses if you consider them private.
