# Migration from rd200_ble

[README](../README.md) > Migration from rd200_ble

This guide moves your monitors from the community `rd200_ble` integration to Reliable RadonEye while
keeping your **long-term radon history in one unbroken series**, under the entity IDs your dashboards
already use. It is the procedure used in a real migration of two monitors, with the problems found along the
way.

Plan for about **2 to 3 days**:

1. a few minutes to install;
2. a parallel run of a day or two;
3. a 15-minute takeover;
4. a couple of weeks with `rd200_ble` kept available for rollback.

## Overview

| Step | What | Reversible? |
|---|---|---|
| 1 | Install Reliable RadonEye and import the monitors | yes |
| 2 | Parallel run: both integrations, one connection at a time | yes |
| 3 | Take a full backup | - |
| 4 | Check that the statistics metadata matches | read-only |
| 5 | Delete the `rd200_ble` config entries | yes, by re-adding (see [Rollback](#rollback)) |
| 6 | Rename the new radon entities onto the old entity IDs | yes, by renaming back |
| 7 | Turn the parallel option off and check continuity | yes |
| 8 | Uninstall `rd200_ble` after a couple of weeks | yes (reinstall) |

In the examples, the monitor's serial is `XX01RE000001` and its label is `upstairs`. `rd200_ble` names its
entities after the Bluetooth name, so its radon sensor is `sensor.fr_xx01re000001_radon`, together with
`_radon_1_day_level`, `_radon_1_month_level` and `_radon_peak`. Check yours under the `rd200_ble` device.

## 1. Install and import

[Install](installation.md) Reliable RadonEye. You can add the monitors through the UI as usual. The YAML import
is more convenient for a migration because it sets the parallel-run option and the backfill target in one go:

```yaml
reliable_radoneye:
  devices:
    - address: "AA:BB:CC:DD:EE:01"
      serial: "XX01RE000001"
      label: "upstairs"
      statistic_id: sensor.fr_xx01re000001_radon
      parallel_with_rd200_ble: true
```

Restart Home Assistant. The monitor appears under **Settings > Devices & services > Reliable RadonEye** as
*Radon upstairs*. A Repair asks you to remove the YAML block. Do so once you have checked the entry; the entry
stays. All keys are described in [Configuration](configuration.md#yaml-import).

The new entities get the standard IDs, such as `sensor.radon_upstairs_radon`. The old ones keep theirs.

### The `statistic_id` key and the backfill

Each day, the integration reads the monitor's stored hourly log. It fills hours that are **missing** from one
long-term statistic and never overwrites an hour that exists.

- **With `statistic_id`:** it fills the statistic you name, typically the old `rd200_ble` radon sensor. During
  the parallel run this fills the old series' gaps, such as the hours where `rd200_ble` showed `unknown`.
- **Without it:** it fills this integration's own Radon sensor, whatever that sensor's entity ID currently is.
  After the takeover rename that is the old ID anyway, so the key mostly matters during the parallel run.

The key is only available in YAML. It is stored with the entry when it is first imported. If the named
statistic does not exist, the backfill is skipped with a warning in the log.

## 2. Parallel run (recommended)

**A monitor accepts only one Bluetooth connection at a time.** Two integrations polling independently will
collide, so:

1. **Disable polling** on each `rd200_ble` config entry: **Settings > Devices & services > rd200_ble >
   (entry) > three-dot menu > System options > Enable polling for updates: off**. Its entities stay, but it no
   longer connects on its own.
2. **Turn on the parallel option** on each Reliable RadonEye entry: **Configure > Parallel run: read rd200_ble
   in its own slot**. The YAML above already did this. Reliable RadonEye then asks `rd200_ble` to refresh at
   +4:30 of each window, between its own reads, by updating `sensor.fr_<serial>_radon_uptime`. If your
   `rd200_ble` uptime entity has a different ID, the request is skipped and `rd200_ble` shows no new values.

Run both for **24 to 48 hours**. Note that during this time Reliable RadonEye is still validating its counting
window (the first 3 to 5 hours), and the 24 h counts value needs 120 captured windows.

### How to compare

| Check | Where | What to expect |
|---|---|---|
| Data completeness | `sensor.radon_upstairs_window_capture_24_h` | 100 % |
| Old integration's gaps | History of `sensor.fr_xx01re000001_radon` | Count the `unknown` stretches. In the real run they made up about 35 % of its state changes. |
| Same device value | Compare `sensor.radon_upstairs_radon` with `sensor.fr_xx01re000001_radon` **only where the two reads are less than 2 minutes apart** | Identical. Reads several minutes apart differ slightly, because the device value itself moves between reads. |
| Counts vs device | `sensor.radon_upstairs_radon_counts_24_h` (and its `lower`/`upper`) against the device's 1-day level | Close: within about one interval width |
| Link quality | `sensor.radon_upstairs_first_attempt_read_success_4_h` | See [Reliability](reliability.md#what-good-looks-like) |

The real parallel run (two monitors, 48 h):

- **Reliable RadonEye:** captured 100 % of windows on both monitors.
- **`rd200_ble`:** succeeded on about 66 % and 99 % of reads.
- **Matching device values:** where the two read at nearly the same moment, the values matched exactly. A naive
  comparison of all reads within ±10 minutes "failed", at 92–94 % identical, but only because the device value
  drifts between reads that are minutes apart. That was a flaw in the comparison, not a decoding difference.

## 3. Take a full backup

**Settings > System > Backups > Back up now**, with the database included. This is the backstop for every step
below.

## 4. Check that the statistics metadata matches

Long-term statistics continue across the rename only if the old and new sensors describe their statistics the
same way. Compare each pair:

- `sensor.radon_upstairs_radon` with `sensor.fr_xx01re000001_radon`
- the same for `_1_day_level`, `_1_month_level` and `_peak`

| Field | Reliable RadonEye | Must match on the old sensor |
|---|---|---|
| Unit | `pCi/L` | `pCi/L` |
| Unit class | `radiation_concentration` | the same |
| Mean type | arithmetic mean (`mean_type` 1), no sum | the same |

There are two ways to check:

- **Quick:** in **Developer tools > Statistics**, both sensors should be listed with unit pCi/L and no issue
  shown.
- **Exact:** run the WebSocket command `recorder/list_statistic_ids` with `statistic_type: mean`, for example
  from the browser developer console or a WebSocket tool. Compare `statistics_unit_of_measurement`,
  `unit_class` and `mean_type` for each pair. In the real migration all four pairs were identical.

**If they differ, stop.** For example, if your old sensors were in Bq/m³, a rename would put pCi/L values into
a Bq/m³ series. This case was not tested. Keep the new entities under their own IDs instead.

## 5. Delete the rd200_ble entries

**Settings > Devices & services > rd200_ble**: for each entry, open the three-dot menu and choose **Delete**.

- This removes the `rd200_ble` devices and **all** their entities from the entity registry, so their entity IDs
  become free. Their recorded history and statistics stay in the database.
- No restart is needed.
- Home Assistant's Bluetooth discovery will soon offer the monitors to `rd200_ble` again, as *Discovered*
  cards. **Ignore them** (do not click Add). They are your quick rollback path. They disappear when you
  uninstall `rd200_ble`.
- If Home Assistant shows a Repair offering to delete statistics, **do not accept it** for the `sensor.fr_*`
  IDs. In the real migration no such offer appeared.

## 6. Rename the new entities onto the old IDs

Do this straight after step 5, so the gap is a few seconds. For each of the four device-value sensors, open
**Settings > Entities**, click the entity, open its settings (gear icon), change the **Entity ID** and click
**Update**:

| From (Reliable RadonEye) | To (old `rd200_ble` ID) |
|---|---|
| `sensor.radon_upstairs_radon` | `sensor.fr_xx01re000001_radon` |
| `sensor.radon_upstairs_radon_1_day_level` | `sensor.fr_xx01re000001_radon_1_day_level` |
| `sensor.radon_upstairs_radon_1_month_level` | `sensor.fr_xx01re000001_radon_1_month_level` |
| `sensor.radon_upstairs_radon_peak` | `sensor.fr_xx01re000001_radon_peak` |

Leave the other new entities (counts, reliability, diagnostics) under their new IDs. `rd200_ble` has no
equivalent for them.

**Expect these log messages for each renamed ID.** They are harmless:

- an ERROR from the recorder: *Cannot rename statistic_id `sensor.radon_upstairs_radon` to
  `sensor.fr_xx01re000001_radon` because the new statistic_id is already in use*;
- a WARNING: *Cannot migrate history ... new entity_id is already in use*.

These messages are how the series continues. See the next section.

### Why the statistics continue

This was rehearsed on disposable test sensors before the real migration (Home Assistant 2026.9), and then
confirmed on the real one:

- **Nothing is merged or overwritten.** Renaming an entity onto an entity ID that already has statistics and
  history is a no-op in the database. The recorder logs the messages above and leaves every existing row alone.
- **The old series simply continues.** From the moment of the rename, the old ID records the new sensor's
  values. Its hourly statistics have a row for every hour with no gap. The hour of the rename is a blend of old
  and new values.
- **No new metadata row is created.** There is still exactly one statistics entry per ID, with the same unit
  and unit class.
- **The renamed-from rows are orphaned.** The parallel-run history of `sensor.radon_upstairs_radon` stays under
  that ID: neither lost nor merged. Home Assistant may later list those IDs in **Developer tools > Statistics**
  as having no entity. You may delete those orphans (the `sensor.radon_upstairs_*` ones). **Never delete the
  `sensor.fr_*` ones.**
- **Dashboards keep working.** Cards and automations that use the old IDs now show the new values. A card that
  you pointed at the *new* IDs during the parallel run needs to be repointed to the old IDs.

## 7. Turn off the parallel option and check

1. On each Reliable RadonEye entry, **Configure** and switch **Parallel run** off. Saving reloads the entry.
   (If you used YAML, remove the block now. Editing it has no effect after the first import.)
2. After an hour or two, check:
   - `sensor.fr_xx01re000001_radon` updates every few minutes;
   - its graph (**History**, or a statistics graph over a week) shows no gap at the takeover hour;
   - the logs show no errors from `reliable_radoneye`.
3. The next morning, check that the daily log pull ran: `sensor.radoneye_log_last_pull` has `all_ok: true`.

## 8. Keep rd200_ble for a while, then uninstall

Leave `rd200_ble` installed, with no entries, for a couple of weeks. That is your rollback path. Then remove it
in HACS and restart.

## Rollback

**Quick rollback** (keeps the history continuous):

1. Rename the four Reliable RadonEye entities back to their own IDs, for example
   `sensor.fr_xx01re000001_radon` back to `sensor.radon_upstairs_radon`. This frees the old IDs.
2. Delete the Reliable RadonEye entries, or keep them and make sure the **Parallel run** option is **off**.
   Otherwise the option starts triggering `rd200_ble` again once it is back.
3. Add `rd200_ble` again from the *Discovered* cards (or **Add integration > rd200_ble**). Its entities get
   their usual `sensor.fr_<serial>_*` IDs, so they continue the same statistics, by the same mechanism as the
   takeover.
4. Re-enable polling on the `rd200_ble` entries if you had disabled it.

**Full rollback:** restore the backup from step 3. Everything since the backup, in every integration, is lost,
so use this only if something has gone badly wrong.

## Pitfalls met in a real migration

1. **Deploy tooling that did not ship subdirectories.** The first restart with the new integration failed,
   because the sync script copied only the integration's top-level files. The `core/` folder was missing, so the
   integration could not import its own modules. `rd200_ble` was rolled back within minutes and the script
   fixed. If you install by copying files, copy the **whole** folder, including `core/` and `translations/`,
   and check after the restart that the integration loaded. Sync scripts also often leave deleted files
   behind on the target.
2. **`rd200_ble`'s early-disconnect parser bug.** `rd200_ble` 0.5.3 showed `unknown` for radon, 1-day and
   1-month (but not peak or uptime) after many reads. Cause:
   - When Bluetooth aborts a connection attempt and retries internally (`le-connection-abort-by-local`),
     `rd200_ble`'s disconnect handler fires before the connection completes.
   - Its first command, the radon read, is then cancelled, and the error is swallowed.
   - The result was about 35 % of its radon states `unknown` on a marginal link.
   
   It is a bug in `rd200_ble` and does not affect Reliable RadonEye. During the parallel run it makes
   `rd200_ble` look worse than its connection success alone suggests. Its option *keep last valid value* hides
   the `unknown`s, but it also re-shows old values, which would bias a comparison.
3. **The device class and unit class must match.** Statistics continue only if the new sensors have the same
   unit, unit class and mean type as the old ones. Reliable RadonEye deliberately uses the same radon device
   class as `rd200_ble` and pCi/L, so the unit class is `radiation_concentration` on both sides. Check this
   (step 4) before you rename. A mismatch would leave you with a unit-change repair on your main radon series.
4. **`rd200_ble` holding the radio after a restart.** Right after one restart during the parallel run,
   `rd200_ble`'s start-up retries held a monitor for about 13 minutes. For a while Reliable RadonEye's reads
   then failed with `BleakCharacteristicNotFoundError`. It recovered by itself. See
   [Troubleshooting](troubleshooting.md#bleakcharacteristicnotfounderror).
5. **Dashboards pointed at the temporary IDs.** A chart created during the parallel run used
   `sensor.radon_<label>_radon`. After the rename it pointed at an ID that no longer existed, so check your
   dashboards.
