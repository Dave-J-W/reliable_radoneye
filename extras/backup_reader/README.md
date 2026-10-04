# Optional backup reader

A stand-alone script that reads the same RD200 monitors from a **second machine with its own Bluetooth
radio** (any Linux box, for example a Raspberry Pi) and serves the windows it captured over HTTP. Home
Assistant can then fetch any window it missed (a failed read, or a restart). Read-only on the monitors.

- Reads each monitor at +3:30 and +8:30 of its 10-minute window (Home Assistant uses +1:00 and +6:00) and never
  starts a read after +9:00, so the two readers do not collide.
- Keeps a 24 h ring of captured windows (`~/radon_counts/ring.json`, survives reboots).
- `GET :8765/windows?serial=<serial>&since=<UTC ISO>` returns the ring; `GET :8765/status` shows monitors and
  pending slots.
- The server has **no authentication** and listens on all interfaces: keep it on a trusted LAN.

## Install (generic systemd)

Needs Python 3.11+ and a working BlueZ stack.

```sh
sudo mkdir -p /opt/rd200 && sudo chown "$USER" /opt/rd200
python3 -m venv /opt/rd200/venv && /opt/rd200/venv/bin/pip install bleak
cp rd200_counts.py monitors.example.json /opt/rd200/
cp ../../custom_components/radoneye_log/protocol.py ../../custom_components/radoneye_log/core/windows.py /opt/rd200/
cp /opt/rd200/monitors.example.json /opt/rd200/monitors.json   # then edit: your addresses, serials, labels
sed "s/<USER>/$USER/" rd200-counts.service | sudo tee /etc/systemd/system/rd200-counts.service
sudo systemctl daemon-reload && sudo systemctl enable --now rd200-counts
curl http://localhost:8765/status
```

`monitors.json` lists each monitor: `address` (Bluetooth MAC), `serial` (as shown in Home Assistant, e.g. the
device's serial number) and `label`. In Home Assistant, set the integration option **Backup reader URL** to
`http://<that-machine>:8765`.

Tests live in the repository's `tests/test_backup_reader.py`.
