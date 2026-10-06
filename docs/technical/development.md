# Development

This document covers the repository layout, running the tests, the simulation harness, mutation checking,
CI, releasing, and testing against a real Home Assistant. Read [architecture.md](architecture.md) first. The
most important rule is that **every decision lives in the pure `core/` package and comes with a test**.

---

## 1. Repository layout

```
reliable_radoneye/
├── custom_components/reliable_radoneye/
│   ├── __init__.py            setup, the `pull` service, YAML import
│   ├── config_flow.py         discovery, confirm-by-connect, options
│   ├── hub.py                 the queue loop; executes jobs and actions; owns the Store
│   ├── ble.py                 one BLE session (cap + separately bounded disconnect)
│   ├── protocol.py            read-only RD200 v2/v3 protocol (vendored from sormy/radoneye)
│   ├── pull.py                daily log pull, pull files, statistics backfill
│   ├── stats.py               hourly external statistics
│   ├── sensor.py, binary_sensor.py
│   ├── const.py, manifest.json, services.yaml, translations/en.json
│   ├── LICENSE-radoneye       MIT licence of the vendored protocol code
│   └── core/                  PURE: stdlib only, no Home Assistant imports
│       ├── engine.py          per-monitor state machine
│       ├── schedule.py        slots, retries, caps, Job, JobQueue
│       ├── windows.py         window identity, dedupe, boots, validator (no relative imports!)
│       ├── radon_math.py      Poisson CDF, Garwood interval, derive()
│       ├── reliability.py     4 h blocks, radio time share
│       ├── timing.py          capped_then_close
│       └── archive.py         count CSV writer
├── extras/backup_reader/      optional second reader (rd200_counts.py, systemd unit, example config)
├── tests/
│   ├── sim.py                 FakeRD200, poisson_counts, simulate (not a test module)
│   ├── fixtures/pulse_counts/ the frozen trial CSVs (placeholder serials)
│   └── test_*.py
├── docs/                      user guide; docs/technical/ = this reference
├── .github/workflows/validate.yml
├── hacs.json, CHANGELOG.md, LICENSE, README.md, CONTRIBUTING.md
```

`core/windows.py` must keep **no relative imports**. The backup reader copies it next to itself and imports it
as the top-level module `windows`. `tests/test_backup_reader.py` does the same, which keeps this honest.

---

## 2. Running the tests

The suite uses only the standard library's `unittest`. From the repository root:

```sh
python -m pip install bleak            # only protocol.py imports it
python -m unittest discover -s tests -t .
```

- `-t .` makes `tests` a package, which `test_engine.py` relies on (`from tests.sim import ...`).
- Without `bleak`, `test_radoneye_protocol` and `test_backup_reader` fail to import (2 errors). The other
  modules still run.
- To run one module or one test:

  ```sh
  python -m unittest tests.test_windows -v
  python -m unittest tests.test_engine.LowRadonValidation.test_low_radon_ten_minute_device_passes -v
  ```

- The suite has 101 tests and runs in a few seconds. CI uses Python 3.13. The core must not depend on
  version-specific features, because it also runs on the backup reader's Python (3.11 or later).

| Module | What it covers |
|---|---|
| `test_radoneye_protocol.py` | Status decoding against two real RD200V3 packets (counts, uptime, averages, `raw_hex` round trip) |
| `test_windows.py` | Capture, the compact Store row, dedupe and merge, reboot detection, the validator (pass, fail, re-test, informative crossings), and a **replay of the trial CSVs** |
| `test_radon_math.py` | Garwood against Gehrels' table, the CDF against a direct sum, `derive`, coverage, a cold-cache timing guard |
| `test_schedule.py` | A/B slots, retries per kind, EDF order, `has_chain` |
| `test_reliability.py` | 4 h blocks (including DST and late slots), radio share |
| `test_timing.py` | `capped_then_close`: slow close, close timeout or error, work timeout or error, worst-case bound, cancellation |
| `test_archive.py` | CSV round trip across UTC midnight, `hour_totals` |
| `test_engine.py` | Engine simulations: timing, capture, guards, restarts, reboots, pulls, staleness, five monitors, validation, re-test, backup refetch, factor change, persistence and size, the parallel run, low-radon validation, conflicts, firmware change |
| `test_backup_reader.py` | The backup reader's self-aligned plan, `parse_since`, and the ring (corrupt file, dedupe, prune) |

---

## 3. The simulation harness (`tests/sim.py`)

### `FakeRD200`

```python
FakeRD200(serial, boot_utc, counts_for=lambda w: 2, model="RD200V3", firmware="V3.0.1", window_min=10)
```

`status(now)` returns a `parse_status`-shaped dict for a device that booted at `boot_utc`:

- `uptime_minutes = ⌊(now − boot_utc) / 1 min⌋`;
- the window is `w = ⌊uptime / window_min⌋`;
- `counts_previous = counts_for(w − 1)`, or 0 for `w = 0`;
- `counts_current = ⌊counts_for(w) × fraction of window elapsed⌋`, so it grows during the window and resets at
  the rollover;
- fixed device values: `latest_bq_m3 = 10`, `latest_pci_l = 0.27`, and so on.

`window_min` lets a test model a device whose window is **not** 10 minutes (20 or 60, for example), so that the
validator can be checked against wrong models.

### `poisson_counts(seed, mean)`

This returns `counts_for(w)`, a deterministic, memoised Poisson(`mean`) draw per window (Knuth's method on
`random.Random(seed)`). Repeat reads of one window agree, and a seed fully determines the sequence.

### `simulate(engines, devices, start, end, fails=..., read_s=5.0)`

This runs one or more `MonitorEngine`s against their devices on **one radio**, using the real `JobQueue`:

- it starts from each engine's `initial_jobs(start)`;
- it repeatedly jumps the clock to the next due job and pops the job (EDF);
- each read "takes" `read_s` seconds. `read_s` can be a callable `read_s(i)`, which varies the duration;
- `fails(serial, t, job)` returns `True` to make an attempt fail (`ReadResult(ok=False, error="TimeoutError")`);
- the result is fed to `on_result`, and the returned jobs are queued;
- `parallel` jobs are recorded but not executed.

It returns `(actions, executed_jobs)`. There is no real time, no asyncio and no HA. A simulated day takes
milliseconds.

### Writing an engine simulation test

```python
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tests.sim import FakeRD200, poisson_counts, simulate
from core.engine import MonitorEngine, WriteWindow          # sim.py put the package dir on sys.path

TZ = ZoneInfo("UTC")
START = datetime(2026, 10, 1, 17, 0, 7, tzinfo=timezone.utc)
PASSED = {"validator": {"status": "passed", "same_ok": 30, "violations": 0, "crossings": 30, "silent": 3}}


class EveryOtherAFails(unittest.TestCase):
    def test_b_rescues_the_window(self):
        e = MonitorEngine("A1", "A1", 1.27, TZ, START, state=PASSED)        # start in counts mode
        d = FakeRD200("A1", START - timedelta(minutes=5003, seconds=20), poisson_counts(seed=1, mean=2))
        fails = lambda serial, t, job: job.kind == "A" and job.attempt == 1
        acts, done = simulate({"A1": e}, {"A1": d}, START, START + timedelta(hours=6), fails)

        idx = [a.window.index for a in acts if isinstance(a, WriteWindow)]
        self.assertEqual(idx, list(range(idx[0], idx[0] + len(idx))))       # no gaps, no duplicates
        self.assertEqual(e.entity_states(START + timedelta(hours=6))["missed_windows_24h"], 0)
```

Some guidelines for these tests:

- **Choose the starting state on purpose.** Use `state={}` for a fresh monitor (validator pending, free
  reads), or `PASSED` to start in counts mode.
- **Offset the boot by seconds** (`seconds=20` above), so that the computed rollovers are not exactly on the
  minute. This exercises the 1-minute uptime bias.
- **Test a restart** with `e.to_state(at)`, then `json.loads(json.dumps(...))` (that round trip is what the
  Store gives back), then a new `MonitorEngine(..., state=..., now=at)`.
- **Assert on actions and on `entity_states`**, not on private fields, where you can.
- **For statistical properties, loop over seeds** with `self.subTest(seed=...)`, and assert something that
  holds for every seed, or a robust quantile. `LowRadonValidation` is an example of both.

---

## 4. Mutation-checking practice

A test that passes before and after a change proves nothing about that change. For every new rule or fix:

1. **Red first.** Write the test against the old code and see it fail, for the reason you expect.
2. **Green.** Implement, and see it pass.
3. **Mutate.** Deliberately break the rule the test is meant to guard, and confirm that the test fails. Then
   revert the mutation, and check with `git diff` or `grep` that it is gone. Examples from this project:
   - `INFORMATIVE_MIN = 9` makes `test_longer_windows_fail_at_moderate_counts` fail. This proves the test
     depends on the crossing filter.
   - Temporarily adding a rejected variant of the crossing rule (also ignoring crossings where `current` is 0
     at both reads) made `test_twenty_minute_windows_fail_at_low_counts` report **false passes** for 20-minute
     devices. That is how the variant was rejected.
   - The cold-cache timing test asserts exactly 301 cache misses. An earlier draft used a stride of 7, which
     covered only 43 values because 301 = 7 × 43, and so measured the warm path.
4. **Guard tests** (ones that pass both before and after a change) are welcome, but say so in the commit or
   the PR, and mutation-check them.

Record the red output, the green output and the mutation result in the PR description.

---

## 5. Continuous integration

`.github/workflows/validate.yml` runs on every push and pull request, weekly (Monday 03:00 UTC) and on manual
dispatch:

| Job | What it checks |
|---|---|
| `hassfest` | `home-assistant/actions/hassfest`: manifest, translations, services, config-flow conventions |
| `hacs` | `hacs/action` with `category: integration`: HACS repository requirements (`hacs.json`, README, structure) |
| `tests` | Python 3.13, `pip install bleak`, then `python -m unittest discover -s tests -t .` |

All three must pass before a merge.

---

## 6. Releasing

The version lives in `custom_components/reliable_radoneye/manifest.json` (`"version"`). HACS shows the
repository's releases, and `hacs.json` declares the minimum Home Assistant version (`2026.9.0`).

1. Choose the version (semantic versioning):
   - **patch** for fixes that change no stored format and no entity;
   - **minor** for new features, entities or options;
   - **major** for breaking changes to entities, files or the Store.

   A change to the Store format that an older build cannot read must be called out in the changelog, together
   with its rollback procedure.
2. Add a `## X.Y.Z` section at the top of `CHANGELOG.md`, written for users: what changed and what it means
   for them.
3. Bump `manifest.json` `version` to the same `X.Y.Z`.
4. Commit (`Release X.Y.Z: changelog and manifest version`) and make sure CI is green.
5. Tag the commit `vX.Y.Z` (annotated) and create a GitHub release from the tag, with the changelog section
   as its notes.

No tags exist yet in the repository. 0.3.0 and 0.3.1 were recorded in the changelog and the manifest only.

---

## 7. Testing against a real Home Assistant

The HA layer (`hub.py`, `ble.py`, `pull.py`, `stats.py`, the platforms and the config flow) is not covered by
unit tests. It is verified by evidence on a running instance.

1. **Install.** Copy `custom_components/reliable_radoneye/` to `/config/custom_components/` (or install the
   branch through HACS as a custom repository), and **restart** HA. Python changes always need a restart.
   Remove files on the target that the branch deleted, because a plain copy leaves them behind.
2. **Add a monitor.** Use Bluetooth discovery, or *Add integration → Reliable RadonEye*. The confirm step
   connects and reads one status.
3. **Turn on debug logging** for a short window, through the integration's "Enable debug logging", or
   with:

   ```yaml
   logger:
     logs:
       custom_components.reliable_radoneye: debug
       bleak_retry_connector: debug
   ```

   About 11 minutes covers one full window: free reads every 5 min, or A and B at +1:00 and +6:00 in counts
   mode.
4. **Check, in order:**
   - **Entities exist.** Device values are available within one read.
   - **`.storage/reliable_radoneye.state`** appears within about 10 min of the first read.
   - **Validator.** It passes after about 3.3 h at moderate radon, and later at low radon (see
     [counts-method.md](counts-method.md#time-to-reach-min_cross)). In the meantime `Radon (counts, …)`
     stays unavailable, and no count CSV is written.
   - **In counts mode:**
     - one row per 10 min in `counts_<serial>_<label>_<date>.csv`, with `read_at_utc − window_end_utc` about
       1 min (or about 6 min when read B captured the window);
     - `Developer tools → Statistics` lists `reliable_radoneye:counts_…` and `…windows_…`.
   - **The diagnostics:** `Window capture (24 h)` near 100 %, `First-attempt read success (4 h)` (updates
     every 4 h), and `RadonEye radio time share`.
   - **The pull.** `reliable_radoneye.pull` with `immediate: true` produces a JSON file, a `_latest.csv`, a
     `pull_log.csv` row and a logbook entry "RadonEye log pull".
5. **Restart test.** Restart HA, and confirm:
   - the counts values are available straight away;
   - the device values are unavailable until the first read;
   - windows from the downtime are `not_observed`, not `missed`;
   - or, with a backup reader, those windows are filled with `source=backup`.
6. **Turn debug logging off** again.

Never test against a monitor while another reader holds long connections to it. Keep in mind that the official
app and the backup reader also need the radio.
