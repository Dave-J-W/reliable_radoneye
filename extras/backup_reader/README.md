# Optional backup reader

[README](../../README.md) > Backup reader

A small stand-alone program that reads the same RD200 monitors from a **second machine with its own Bluetooth
radio**, keeps the last 24 hours of captured 10-minute windows, and serves them over HTTP. Home Assistant
fetches any window it missed, for example during a restart or a bad radio spell. It is read-only on the
monitors.

You do not need it: every feature of Reliable RadonEye works without it. Without it, windows that close
while Home Assistant is down are recorded as *not observed*, and windows that all of Home Assistant's reads
missed stay *missed*. See [Reliability](../../docs/reliability.md).

## How it works

- **Self-aligning.** Each read's uptime tells it when the monitor's window rolls over. It then reads at
  **+3:30** and **+8:30** of each window, between Home Assistant's reads at +1:00 and +6:00, and never starts
  a read after **+9:00**, so the two readers do not collide. Until a monitor's first good read it reads every
  5 minutes.
- **One retry** 20 seconds after a failed read (if still before +9:00).
- **Each read** scans for the monitor for up to 6 seconds, then connects, reads the status (`0x40` only) and
  disconnects within a 15-second cap (the scan is not part of the cap, so one read takes at most about
  21 seconds).
- **A 24-hour ring** of captured windows per monitor, saved to `~/radon_counts/ring.json` of the service
  user after each new window, so it survives restarts. A corrupt ring file is renamed to `ring.json.bad` and
  a new one started.
- **An HTTP server** on port **8765**, all interfaces, with two read-only endpoints (see [API](#api)).
- No CSV files; Home Assistant keeps the archive.

In a real deployment on a Raspberry Pi's built-in radio, the backup reader completed about 1,500 reads over
three days with no failures, and supplied 12 to 15 windows per monitor over two days that Home Assistant's own
reads had missed.

## Requirements

| Requirement | Notes |
|---|---|
| A Linux machine with Bluetooth | For example a Raspberry Pi. Its radio must be able to connect to the monitors (signal around -80 dBm or better is comfortable). It should **not** be the Home Assistant host: the point is a second radio. |
| BlueZ | The standard Linux Bluetooth stack, running (`systemctl status bluetooth`). |
| Python 3.11 or newer | With `venv`. |
| `bleak` | Installed into a virtual environment, below. |
| Network | Home Assistant must reach the machine on TCP port 8765. |

## Install (systemd)

From a copy of this repository on the backup machine, in `extras/backup_reader/`:

```sh
# 1. A home for the program and a virtual environment with bleak
sudo mkdir -p /opt/rd200 && sudo chown "$USER" /opt/rd200
python3 -m venv /opt/rd200/venv
/opt/rd200/venv/bin/pip install bleak

# 2. The program plus the two modules it shares with the integration
cp rd200_counts.py monitors.example.json /opt/rd200/
cp ../../custom_components/reliable_radoneye/protocol.py \
   ../../custom_components/reliable_radoneye/core/windows.py /opt/rd200/

# 3. Your monitors
cp /opt/rd200/monitors.example.json /opt/rd200/monitors.json
nano /opt/rd200/monitors.json          # see Configuration below

# 4. The service (runs as you; adjust paths if you installed elsewhere)
sed "s/<USER>/$USER/" rd200-counts.service | sudo tee /etc/systemd/system/rd200-counts.service
sudo systemctl daemon-reload
sudo systemctl enable --now rd200-counts

# 5. Check
curl http://localhost:8765/status
```

The user running the service needs access to Bluetooth over D-Bus; on most distributions a normal user can
use BlueZ, and on some you need to add it to the `bluetooth` group.

Copy `protocol.py` and `windows.py` again whenever you update the integration, so both readers parse the
monitor the same way. Then `sudo systemctl restart rd200-counts`.

## Configuration

### monitors.json

A list of monitors, in the same folder as `rd200_counts.py`:

```json
[
  {"label": "upstairs", "serial": "XX01RE000001", "address": "AA:BB:CC:DD:EE:01"},
  {"label": "basement", "serial": "XX01RE000002", "address": "AA:BB:CC:DD:EE:02"}
]
```

| Key | Meaning |
|---|---|
| `address` | The monitor's Bluetooth address as this machine sees it (compare `bluetoothctl scan on`, where RD200s show as `FR:...`). |
| `serial` | The monitor's serial **exactly as Home Assistant has it**, in upper case (shown on the device page). Home Assistant asks for windows by this serial. |
| `label` | A name for logs and `/status`. It does not need to match Home Assistant's label. |

Double-check that each serial is paired with the right address. Swapped entries make the two readers disagree
on every window and produce [count-conflict notifications](../../docs/troubleshooting.md#count-conflict-notification).

Restart the service after editing: `sudo systemctl restart rd200-counts`.

### Home Assistant

For each monitor, open **Settings > Devices & services > Reliable RadonEye > (entry) > Configure** and set
**Backup reader URL** to `http://<backup-machine>:8765` (a host name or IP address, no path). A **Backup
reader reachable** binary sensor then appears for that monitor. See
[Configuration](../../docs/configuration.md#backup-reader-url).

Home Assistant then:

- asks `/status` once an hour (the binary sensor);
- asks `/windows` when a window is missed, after a restart gap, and hourly for any window still missed in the
  last 24 hours;
- times out each request after 5 seconds, and never lets a backup failure affect its own reads.

Windows that arrive from the backup reader are archived with `source = backup`. If Home Assistant later reads
the same window itself with a different count, its own copy replaces the backup copy.

### Fixed settings

These are constants at the top of `rd200_counts.py`; there are no command-line options.

| Setting | Value |
|---|---|
| Port | 8765, all interfaces |
| Read slots | +3:30 and +8:30 after the computed rollover; no start after +9:00 |
| Retry | once, 20 s after a failure |
| Before the first good read | every 5 minutes |
| Scan before connect | up to 6 s |
| Connection cap | 15 s |
| Ring span | 24 hours |
| Data folder | `~/radon_counts/` (ring file `ring.json`) |

## API

Both endpoints are `GET` only and return JSON. Anything else returns `404` with `{"ok": false}`.

### `GET /windows?serial=<serial>&since=<UTC ISO time>`

The windows in the ring for one monitor whose end is **after** `since`, oldest first.

| Parameter | Required | Notes |
|---|---|---|
| `serial` | yes | Matched case-insensitively (upper-cased). Unknown serials return `[]`. |
| `since` | no | ISO 8601. `Z`, an offset, or no offset (taken as UTC) are accepted; a `+` that arrived as a space is repaired. Default: everything. An unparsable value returns `400` with `{"ok": false, "error": "bad since: ..."}`. |

```sh
curl "http://backup-host.local:8765/windows?serial=XX01RE000001&since=2026-10-05T12:00:00Z"
```

```json
[
  {"end_utc": "2026-10-05T12:12:00+00:00", "index": 4412, "count": 3, "source": "backup",
   "read_at_utc": "2026-10-05T12:15:31+00:00", "device_bq": null},
  {"end_utc": "2026-10-05T12:22:00+00:00", "index": 4413, "count": 1, "source": "backup",
   "read_at_utc": "2026-10-05T12:25:30+00:00", "device_bq": null}
]
```

| Field | Meaning |
|---|---|
| `end_utc` | Computed end of the window (up to 1 minute after the true end, because uptime has 1-minute resolution) |
| `index` | Window number since the monitor's last power-up (0 = first 10 minutes) |
| `count` | Particle count in the window |
| `source` | Always `backup` |
| `read_at_utc` | When the backup reader read it |
| `device_bq` | Always `null` here (Home Assistant records the device value with its own reads) |

### `GET /status`

The configured monitors, the last result per monitor and the next planned read.

```json
{
  "monitors": [{"label": "upstairs", "serial": "XX01RE000001", "address": "AA:BB:CC:DD:EE:01"}],
  "last": {"upstairs": {"at": "2026-10-05T12:25:30+00:00", "ok": true, "attempt": 1, "rssi": -78,
                        "uptime_minutes": 44135, "error": null}},
  "pending": {"XX01RE000001": "2026-10-05T12:30:30+00:00"}
}
```

`last` is empty until the first read after a start. `error` holds the failure text of a failed read.

## Security

- **No authentication and no encryption.** Anyone who can reach port 8765 can read the windows and the
  status, which includes your monitors' **serials and Bluetooth addresses**.
- It listens on **all interfaces**. Keep the machine on a trusted LAN, do **not** forward the port from the
  internet, and, if your machine has a firewall, allow 8765/TCP only from the Home Assistant host. For
  example, with `ufw`: `sudo ufw allow from <ha-host-ip> to any port 8765 proto tcp`.
- It is read-only towards everything: the HTTP server cannot change anything, and towards the monitors it only
  sends the status request `0x40`.
- Run it as an ordinary user, as the provided service file does, not as root.

## Operations

| Task | Command |
|---|---|
| Is it running? | `systemctl status rd200-counts` |
| Recent log (one line per read) | `journalctl -u rd200-counts -n 50` |
| Follow the log | `journalctl -u rd200-counts -f` |
| Restart (after editing `monitors.json` or updating files) | `sudo systemctl restart rd200-counts` |
| Stop / disable | `sudo systemctl disable --now rd200-counts` |
| What it has | `curl http://localhost:8765/status` |

A log line looks like `upstairs attempt 1 ok=True rssi=-78`. A failure shows the error, for example
`not heard within 6 s` (monitor out of range or busy) or a Bluetooth timeout.

- **Restarts.** systemd restarts the service 30 seconds after a crash. The ring is reloaded from disk; it
  realigns to each monitor within one read.
- **Disk.** The ring holds at most 24 hours (about 144 windows per monitor), so it stays small. Old trial CSVs
  in `~/radon_counts/`, if any, are not touched.
- **Clock.** Window times come from the backup machine's clock. Keep it synchronised (NTP, which most
  distributions enable by default); a wrong clock would misplace windows.
- **Updating.** Copy the new `rd200_counts.py`, `protocol.py` and `windows.py` into `/opt/rd200/` and restart.
- **Uninstalling.** `sudo systemctl disable --now rd200-counts`, delete
  `/etc/systemd/system/rd200-counts.service`, `/opt/rd200` and `~/radon_counts`, and clear the **Backup reader
  URL** option in Home Assistant.

**Troubleshooting from Home Assistant's side:** see
[Backup reader unreachable](../../docs/troubleshooting.md#backup-reader-unreachable).

## Tests

The backup reader's scheduling, query parsing and ring handling are tested in the repository's
`tests/test_backup_reader.py`. See [development](../../docs/technical/development.md).
