# Changelog

## 0.4.0

- With a backup reader, the device-value sensors (radon, 1-day, 1-month, peak) stay available while Home Assistant's own Bluetooth reads fail. They take the backup reader's latest good read and show `source: backup` (`ha` otherwise). Counts still come only through the window path.
- A backup value is accepted only if it is newer than any read so far, under 20 minutes old, and no more than 2 minutes in the future (clock-skew guard). It never feeds the counts, the archive or the reliability numbers.
- The backup reader keeps each monitor's latest good read in `latest.json` and serves it at `GET /latest?serial=` (404 if none). Home Assistant asks once its own last read is more than 7 minutes old, at most every 5 minutes.
- The "has not been read" notification still follows Home Assistant's own reads, and now says when the backup is covering.
- Backup windows get `boot_utc` in the counts CSV when their window index fits the current boot.
- Failed reads are logged at WARNING once per failure streak: on the first failed attempt after a success and on the first success after failures (`read OK again after N failed attempts over M min`). Other failures stay at DEBUG.
- A saved device read dated in the future is dropped on load, and the stored device read time keeps full precision.
- Backup reader: a failed save of `ring.json` or `latest.json` is logged and never fails a good read.

## 0.3.2

- The daily stored-log pull now also runs when the counting window check is pending or failed (it rides on the first free read slot after 06:00).
- The pull log (`pull_log.csv`) attempts column is filled with the job's attempt number.
- A changed factor k is written to the logbook once, and saved immediately so a crash cannot log it twice.
- New diagnostic sensor "Counting window check" (pending, passed or failed).
- Free-slot pulls no longer count toward read reliability, and a pull that fails every slot writes one FAILED logbook entry per day.
- A static undefined-name check (`scripts/check_names.py`) runs in CI after the tests.

## 0.3.1

- The Bluetooth time cap now covers connect + read only; the disconnect is bounded separately (5 s) and can no longer discard a good read. Status reads are capped at 15 s and the daily pull at 23 s, so a connection is never held longer than 20 s / 28 s.
- The window-model validator ignores uninformative 0 to 0 crossings, fixing a false validation failure at very low radon levels.
- The count-conflict warning is raised at most once per window, including across restarts and the hourly backup re-fetch.
- A firmware change while validation had failed now still clears the Repair issue.
- More tests: capped_then_close cancellation and bounds, low-count longer windows, the crossing rule.

## 0.3.0

- Reads RD200 v2/v3 over Home Assistant's Bluetooth about every 5 minutes, aligned to the monitor's 10-minute counting window, with retries and a 15 s cap per connection.
- One integration-wide read queue (one Bluetooth connection at a time) for any number of monitors; radio-time share diagnostic and notification.
- Exact archive of raw particle counts: per-window CSV files plus hourly external statistics (counts and windows captured).
- Counts-based radon (1 h and 24 h) with a 68 % Garwood interval, coverage rules, and a configurable factor k (default 1.27 counts/h per Bq/m3) whose changes are logged.
- Reliability reporting: first-attempt read success per 4 h block, window capture, missed windows, counts-vs-device ratio.
- Live per-monitor window-model validator, re-tested every 24 h; device-values-only fallback and a Repair issue when the counting window is not recognised.
- Honest staleness: device values become unavailable after 20 minutes without a good read.
- Daily stored-log pull (after 06:00 local) that fills missing long-term radon statistics hours without overwriting; `reliable_radoneye.pull` service.
- Bluetooth discovery and config flow; RD200 v1 refused; options for label, k, backup reader URL and parallel run; one-time YAML import for migration from `rd200_ble`.
- Integration domain is `reliable_radoneye` (matches the repository); archive directory is `/config/reliable_radoneye/`; external statistic ids are `reliable_radoneye:counts_<serial>`.
- Persistent store of measurements, so restarts blank nothing that was measured.
- Optional backup reader (`extras/backup_reader`) that fills windows Home Assistant missed.
