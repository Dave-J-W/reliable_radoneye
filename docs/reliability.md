# Reliability

[README](../README.md) > Reliability

Reliable RadonEye reports how well it is reading your monitors, so that you never have to guess whether a
gap in a graph is real. This page explains the four numbers, what good looks like, and how to improve a weak
link.

## The short version

| Question | Look at | Good |
|---|---|---|
| Is any data being lost? | **Window capture (24 h)** and **Missed windows (24 h)** | 100 % and 0 |
| How healthy is the Bluetooth link? | **First-attempt read success (4 h)** | 90 % or more most of the time |
| Is the radio overloaded? | **RadonEye radio time share** | a few percent; a warning appears above 50 % |
| Is *k* sensible? | **Counts vs device (7 d)** | close to 1 |

**Window capture is the number that matters for your data.** First-attempt success measures the radio; it can
dip a lot while capture stays at 100 %, because every window gets several chances.

## How reads are scheduled

Each RD200 counts radon decay particles in **10-minute windows**. Each window's count can be read for the next
10 minutes, as the *previous* count. The integration therefore has a whole window's time to capture it, and
uses it:

| Read | Time after the window closed | Retries |
|---|---|---|
| A | +1:00 | +1:20, +2:00 |
| B | +6:00 | +6:20, +7:00 |
| Backup reader (optional, own radio) | +3:30, +8:30 | +20 s |

So a window is lost only if up to six attempts from Home Assistant **and** the backup reader all fail. Details
in [Configuration](configuration.md#read-schedule).

## The four numbers

### First-attempt read success (4 h)

> **What it measures:** the share of scheduled reads whose **first** attempt worked.

- **Counted:** each scheduled A and B read (a daily pull counts as an A read) while Home Assistant was
  running. Retries are not counted, in either direction.
- **Formula:** first attempts that succeeded / scheduled reads, as a percentage.
- **Blocks:** fixed 4-hour blocks of local time starting at 00:00, 04:00, 08:00, 12:00, 16:00 and 20:00. The
  sensor shows the **previous** complete block; its `block_start` attribute says which. A normal block has
  48 scheduled reads per monitor (24 windows × 2).
- **Restarts:** the counters are saved, so a restart within a block continues it. A block during which Home
  Assistant was not running at all shows `unknown`, not 0 %.
- **Only in counts mode.** In device-values-only mode there are no scheduled A/B reads, so it stays `unknown`.
- **Long-term statistics:** yes. You can graph it over months.

A failed first attempt usually means a slow or aborted connection: the read is given at most 15 seconds to
connect and read. The retry 20 seconds later normally reconnects in well under a second.

### Window capture (24 h)

> **What it measures:** of the windows that closed in the last 24 hours while Home Assistant was running,
> how many are in the archive.

Each window ends up in one of three states:

| State | Meaning | Counted as |
|---|---|---|
| **captured** | In the archive, from Home Assistant or the backup reader | captured |
| **missed** | Home Assistant was running, but every read failed and no backup copy arrived | missed |
| **not observed** | The window closed while Home Assistant was down and no backup copy arrived | excluded |

- **Formula:** captured / (captured + missed), recomputed hourly.
- **Fills later:** a missed window that the backup reader supplies later becomes captured. Home Assistant asks
  the backup reader again every hour for windows still missed.
- **Only in counts mode.** Before then the sensor stays `unknown`.

### Missed windows (24 h)

The count of *missed* windows in the last 24 hours. It is recomputed hourly.

A missed window is not a wrong value. It only shortens the time the counts-based radon is averaged over, so the
interval gets slightly wider.

### Radio time share

> **What it measures:** the share of the last hour that the integration spent with a Bluetooth connection open.

- **Counted:** the time of every connection attempt, successful or not, including daily pulls and, in a
  parallel run, `rd200_ble` refreshes. It covers all monitors together and is recomputed hourly.
- **Above 50 %:** a persistent notification *RadonEye radio load* asks you to add a Bluetooth proxy near the
  monitors. It clears itself when the share falls below 40 %.
- **Why it rises:** many monitors on one adapter, or slow connects. Failing attempts take up to 20 seconds
  each, against a few seconds for a good read.

Rough budget (an estimate, not a measurement): a good read takes about 5 to 8 seconds including the
disconnect, and there are about 12 reads per monitor per hour, so expect roughly 1 to 3 % per monitor.

## What good looks like

### The parallel run

These numbers come from a 48-hour run of this integration side by side with `rd200_ble` 0.5.3 on two
RD200V3 monitors. Both monitors were read through a single built-in Raspberry Pi Bluetooth radio, at about
-80 to -89 dBm, with no proxy.

| | Monitor on the weaker link | Monitor on the stronger link |
|---|---|---|
| **Reliable RadonEye** window capture (24 h) | **100 %** | **100 %** |
| Missed windows (24 h) | 0 | 0 |
| Windows that needed the B read or the backup reader | 13 % | 8 % |
| First-attempt success, per 4 h block | 21 % to 100 % (mean about 68 %) | 54 % to 100 % (mean about 83 %) |
| **`rd200_ble`** read success (24 h) | about **66 %** | about **99 %** |
| `rd200_ble` radon state `unknown` | about **35 %** of its radon state changes, on both monitors | |

What the run showed:

- **Data stayed complete through bad periods.** On the weaker link, first-attempt success dropped to
  21–38 % for about 8 hours. Window capture stayed at 100 % throughout, because the retries, the B read and
  the backup reader rescued every window.
- **`rd200_ble` lost readings.** Its gaps came from single attempts with no retry, and its `unknown` values
  came from a parser bug that triggers when Bluetooth aborts the first connection attempt. See
  [Migration](migration-from-rd200_ble.md#pitfalls-met-in-a-real-migration).
- **The counts agreed with the devices.** The counts-based 24 h radon was 0.716 pCi/L (interval 0.69 to 0.742)
  against the device's own 1-day level of 0.65. On the other monitor it was 0.877 (0.849 to 0.906) against 0.86.

After `rd200_ble` was removed and the night-time radio trouble passed, the weaker monitor recorded 47 of 47
first attempts in a block. The stronger one recorded 47 of 48.

### Rules of thumb

| First-attempt success | Window capture | Interpretation |
|---|---|---|
| 90–100 % | 100 % | Healthy |
| 50–90 %, or dips at certain times of day | 100 % | Marginal link. Data is complete, but improve the link if you can. |
| Below 50 % for several blocks | 100 % | Weak link. The retries are carrying it. Improve the link before it starts costing windows. |
| Any value | Below 100 %, missed > 0 | Windows are being lost. Act on the link, and consider a backup reader. |
| `unknown` | `unknown` | Not in counts mode yet. See [Troubleshooting](troubleshooting.md#counts-based-radon-stays-unavailable). |

## Improving a weak link

Work through these roughly in order of impact.

1. **Add a Bluetooth proxy near the monitor.** An ESP32 running ESPHome's `bluetooth_proxy` with active
   connections works, and so does a second supported adapter. Home Assistant automatically connects through
   whichever radio hears the monitor best. This integration needs no configuration for it. This is the
   standard fix, and it removes the dependence on a host radio that may be inside a case or behind walls.
2. **Move the radio or the monitor.** A USB adapter on an extension cable, away from USB 3 ports and disks, or
   moving the monitor a metre toward the radio, can gain several dB. Turn on the **Signal strength** sensor
   and compare before and after.
3. **Remove contention.**
   - Close the RadonEye phone app.
   - Make sure no other integration or script reads the monitor. Only the backup reader is designed to share,
     and only in its own time slots.
   - If the radio time share is high, spread monitors over more proxies.
4. **Look for a pattern in time.** If first-attempt success drops at the same hours each day, something in the
   environment is probably interfering. In one real case it dropped for 12 hours each night, two nights in a
   row, on one monitor only, and the cause was never identified. Candidates include a door closed at night, a
   device charging next to the monitor, a microwave, or heavy 2.4 GHz Wi-Fi traffic.
5. **Run the backup reader** on a second machine with its own radio
   ([extras/backup_reader](../extras/backup_reader/README.md)). It cannot improve first-attempt success, but it
   makes window capture robust, and it fills windows that close while Home Assistant restarts.
6. **Check the debug log** for the type of failure. Slow connects show as repeated
   `le-connection-abort-by-local`; a monitor that is not heard at all shows
   `not currently heard by any HA Bluetooth adapter`. See
   [Troubleshooting](troubleshooting.md#debug-logging).

What does **not** help:

- **Reading more often.** The schedule already gives each window six chances, and more reads only add radio
  load.
- **Raising timeouts.** Each connection is deliberately limited to 20 seconds (15 s to connect and read, 5 s to
  disconnect), so that one slow monitor never blocks the radio for others.
