# Changelog

## 0.3.0

- Reads RD200 v2/v3 over Home Assistant's Bluetooth about every 5 minutes, aligned to the monitor's 10-minute counting window, with retries and a 15 s cap per connection.
- One integration-wide read queue (one Bluetooth connection at a time) for any number of monitors; radio-time share diagnostic and notification.
- Exact archive of raw particle counts: per-window CSV files plus hourly external statistics (counts and windows captured).
- Counts-based radon (1 h and 24 h) with a 68 % Garwood interval, coverage rules, and a configurable factor k (default 1.27 counts/h per Bq/m3) whose changes are logged.
- Reliability reporting: first-attempt read success per 4 h block, window capture, missed windows, counts-vs-device ratio.
- Live per-monitor window-model validator, re-tested every 24 h; device-values-only fallback and a Repair issue when the counting window is not recognised.
- Honest staleness: device values become unavailable after 20 minutes without a good read.
- Daily stored-log pull (after 06:00 local) that fills missing long-term radon statistics hours without overwriting; `radoneye_log.pull` service.
- Bluetooth discovery and config flow; RD200 v1 refused; options for label, k, backup reader URL and parallel run; one-time YAML import for migration from `rd200_ble`.
- Persistent store of measurements, so restarts blank nothing that was measured.
- Optional backup reader (`extras/backup_reader`) that fills windows Home Assistant missed.
