# Contributing to Reliable RadonEye

Thank you for helping. This integration has one purpose: **read RadonEye RD200 monitors reliably, archive their
raw particle counts exactly, and derive radon honestly**. Contributions that serve that purpose are welcome.
Please read [docs/technical/architecture.md](docs/technical/architecture.md) before changing code.

## Scope

**In scope:**

- read reliability, scheduling and Bluetooth robustness;
- correctness of the counts method and its statistics;
- support for further RD200 models or firmware, backed by evidence (packet captures, a validator pass);
- diagnostics, documentation and tests.

**Out of scope:**

- **Any command that changes a monitor.** That includes beep, alarm, unit and any setting. The integration is
  read-only by design (see [docs/technical/protocol.md](docs/technical/protocol.md#5-the-read-only-guarantee)),
  and pull requests that add write commands will not be accepted.
- Sessions longer than 30 s, or more than one connection at a time.
- Automations or fan control built on the radon values. Those belong in your Home Assistant configuration.
- New runtime dependencies. The integration uses only what Home Assistant ships.

## Reporting an issue

Please include:

1. **Versions:** Reliable RadonEye (`manifest.json`), Home Assistant, and the monitor's model and firmware.
   The firmware is shown in the device page, or in `firmware_version` in a pull JSON file.
2. **What you expected, and what happened**, with times in UTC if you can.
3. **Debug log** covering at least 11 minutes, which is one full counting window:

   ```yaml
   logger:
     logs:
       custom_components.reliable_radoneye: debug
       bleak_retry_connector: debug
   ```

   For connection problems, add `bleak.backends.bluezdbus.client: debug` and `habluetooth: debug`.
4. **Diagnostic sensor values:**
   - `First-attempt read success (4 h)`;
   - `Window capture (24 h)` and `Missed windows (24 h)`;
   - `Counts vs device (7 d)`;
   - `RadonEye radio time share`;
   - `Signal strength`.
5. **For counting or validation issues:**
   - the monitor's `validator` object from `/config/.storage/reliable_radoneye.state`;
   - a few rows of `counts_*.csv` around the problem;
   - and, if relevant, the `raw_hex` of a status packet from a pull JSON file.
6. **Your Bluetooth setup:** the onboard adapter or a USB dongle, any ESPHome proxies, the distance to each
   monitor, and other readers of the same monitors (the official app, `rd200_ble`, a backup reader).

**Scrub your identifiers before posting.** Replace serial numbers, Bluetooth addresses, IP addresses,
usernames and labels that identify you with placeholders such as `XX01RE000001` and `AA:BB:CC:DD:EE:01`. Serials
appear in file names, in entity ids, in the Store and in `raw_hex` (bytes 2-13).

## Pull requests

- **Decisions go in `core/`.** Anything that decides something belongs in the pure `core/` package, which
  imports nothing from Home Assistant: when to read, what a read means, what to store and what to show.
  HA modules only execute jobs and actions. If your change needs logic in `hub.py`, consider whether it should
  be an action or a job returned by the engine instead.
- **Tests first.** Write the test, show it failing on the current code, then make it pass. Bug fixes need a
  regression test. Behaviour across time (schedules, restarts, statistics) needs an engine simulation with
  `tests/sim.py`; see [docs/technical/development.md](docs/technical/development.md#3-the-simulation-harness-testssimpy).
- **Mutation-check new tests.** Break the rule on purpose, show that the test fails, and revert.
- **Put evidence in the description:** red and green output, the mutation result, and, for HA-layer changes,
  what you verified on a real instance and how.
- **Keep the stored formats compatible.** The Store, the CSV columns and the statistic ids are public
  contracts. A new Store key must load with a default when it is absent. If an older build cannot read a
  change, say so, and describe the rollback in `CHANGELOG.md`.
- **Keep every number traceable.** A new constant needs a comment saying where it came from (a measurement, a
  simulation or a rule). If you change a number, update `docs/technical/` in the same pull request.
- **Keep pull requests small, one concern each.** CI (hassfest, HACS and the unit tests) must be green.
- **Add a changelog line** under an unreleased heading, written for users.

## Code style

- Python 3.11+ syntax, with `from __future__ import annotations` and type hints on public functions.
- Line length up to about 120 characters. Follow the existing formatting.
- `core/` uses the standard library only. `core/windows.py` must have **no relative imports**, because the
  backup reader imports it standalone.
- Use frozen dataclasses for value records (`Window`, `Job`, `Derived`), and plain dicts for persisted state
  (`to_dict` / `to_state`).
- No clock calls inside `core/`: pass `now` in.
- A broad `except Exception` needs a `# noqa: BLE001 - <reason>` comment that says why it is safe.
- Docstrings say *what* and *why*, and cite the measurement or review finding behind a rule. Write comments and
  user-facing messages in plain, short sentences.
- User-facing strings go in `translations/en.json` where Home Assistant supports it.

## Licence

By contributing you agree that your contribution is licensed under the repository's MIT licence. The vendored
protocol code keeps its own MIT notice (`custom_components/reliable_radoneye/LICENSE-radoneye`).
