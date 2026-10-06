# The counts method

The RD200 reports the number of alpha particles ("counts") it detected in its current and its previous
counting window. Reliable RadonEye archives every closed window's count exactly, and derives radon from those
counts with an exact Poisson uncertainty. This document explains the counting window, how a read identifies a
window, the radon formula, the Garwood interval, the coverage rules, calibration of the factor *k*, and the
live validator that decides whether a monitor's window model is the one this method assumes.

Code: `core/windows.py`, `core/radon_math.py`, `core/engine.py`. Constants are quoted from version 0.3.1.

---

## 1. The RD200 counting window

### The model

- The device counts alpha pulses in **10-minute windows**.
- A window rolls over when the **device uptime ≡ 0 (mod 10 min)**. Uptime is counted from power-up, in whole
  minutes.
- Window *k* of a boot (0-based) covers uptime [10k, 10k + 10) min.
- The status packet carries two counters (see [protocol.md](protocol.md#3-the-status-packet)):
  - `counts_current`: counts so far in the open window;
  - `counts_previous`: the final count of the window that closed last.

```
uptime (min)   0        10        20        30        40
               |---w0---|---w1---|---w2---|---w3---|
                               ^ read at u = 23
                                 counts_current  = counts so far in w2
                                 counts_previous = final count of w1 (closed at u = 20)
```

### How it was established

The protocol documentation upstream (`sormy/radoneye`, `KNOWLEDGE_V2.md`) gives the byte offsets of the two
counters but not the window length or its phase. Both were measured in a **33.5 h trial on two RD200V3 units,
firmware V3.0.1**, using an independent reader on a second machine. It read each monitor's status about every
5 minutes and logged `uptime_minutes`, `counts_current` and `counts_previous` for every read. The frozen trial
CSVs are in `tests/fixtures/pulse_counts/`, with placeholder serials.

The analysis classifies consecutive read pairs by their 10-minute uptime block, ⌊u/10⌋:

- **Same-block pairs** (both reads in one block). If the window were shorter than 10 min, or rolled over at
  any phase other than 0 mod 10, some of these pairs would contain a rollover: `counts_previous` would change,
  or `counts_current` would fall. The trial had **197 same-block pairs per monitor, and none changed**.
- **Crossing pairs** (reads in adjacent blocks). If the window were 20, 30 or 60 min, most block crossings
  would show *no* rollover (section 7). Under the 10-min model nearly every crossing showed one. Only about
  8-10 % were "silent", which is the share expected by chance when two consecutive windows happen to have
  equal counts.

Only **10 min with phase 0** fits both observations; 15, 20, 30 and 60 min were excluded. Reading every
~5 minutes captured **200 of 201 windows per monitor** as `counts_previous`. The one miss was in a scheduled
quiet period of the trial reader. The test `tests/test_windows.py::Replay` replays these files through
`windows.py`. It asserts the same missing window per unit and a passing validator.

The same trial gave the default calibration factor:

| | Unit 1 | Unit 2 |
|---|---|---|
| counts per 10 min at ~10 Bq/m³ | ~2.1-2.3 | ~2.1-2.3 |
| *k* = (counts / h) ÷ (device Bq/m³) | 1.27 | 1.28 |

`DEFAULT_FACTOR = 1.27` (`const.py`).

> All of this was verified only on RD200V3 firmware V3.0.1. For any other model or firmware the live
> validator (section 7) decides, per monitor, whether to trust the model.

---

## 2. Which window a read captures

A status read that finishes at time `t` and reports uptime `u` (whole minutes) identifies the window that
just closed (`captured_window`):

```
end_utc = t − (u mod 10) min            # last_rollover(t, u): the computed rollover
index   = ⌊u / 10⌋ − 1                  # the window that closed at that rollover
count   = counts_previous
(no window if u < 10: nothing has closed yet in this boot)
```

**The 1-minute bias.** Uptime is floored to whole minutes. If the true uptime is u + f with 0 ≤ f < 1, the true
rollover was at `t − (u mod 10) − f`, so the **computed rollover is up to 1 minute later than the true one**.
All slot offsets are measured from the computed rollover. The A read at +1:00 therefore lands between 1:00 and
2:00 after the true rollover, safely inside the window. The test
`Timing.test_aligned_reads_at_plus_one_and_plus_six` asserts that every computed rollover is within 60 s of
the true one.

The **anchor** of a monitor is the computed rollover of its latest good read. Slots are
`anchor + n·10 min + offset`.

---

## 3. Window identity and dedupe

A window's stored identity is its **end time**, `end_utc`. Two windows are the same window if their end times
differ by less than

```
SAME_WINDOW = 5 min        (half a window)
```

The end time is not exact. A computed rollover carries the seconds of the read that captured it, and it jitters
by up to a minute between reads. The backup reader computes it with a different clock. Windows are 10 minutes
apart, so a ±5 min tolerance is the widest that can never merge two real windows. The spec proposed
(boot id, window index) as the dedupe key. The code uses the end time, because the backup reader does not track
boots and an index restarts at every reboot.

`WindowLog.add(w)` returns `(changed, warning)`:

| Existing copy | New copy | Result |
|---|---|---|
| none | any | added (`changed`) |
| same count | any | ignored |
| `backup`, different count | `ha` | HA's copy replaces it, with the warning "...; kept ha" |
| `ha`, different count | `backup` | ignored, with the warning "...; kept ha" |

The engine turns a warning into a `count_conflict` notification **at most once per window**. The end times
already warned about are persisted in `conflicts_warned`, so the hourly backup re-fetch cannot repeat it. A
count conflict should never happen. If it does, it means a decoding or timing fault.

The **outcome** of each window (`captured`, `missed`, `not_observed`), kept for 25 h, feeds the diagnostics:

```
window_capture_24h = 100 × captured / (captured + missed)        (not_observed excluded)
missed_windows_24h = number of missed
```

---

## 4. Radon from counts

For a period with expected window count *E* (6 for 1 h, 144 for 24 h), let the **captured** windows have counts
n₁ … n_m. Then

```
N = Σ nᵢ                        total counts
T = m × 10/60  h                captured time (hours)
k = counts per hour per Bq/m³   (per monitor; default 1.27)

radon [Bq/m³]  = N / (T · k)
radon [pCi/L]  = N / (T · k · 37)            (1 pCi/L = 37 Bq/m³)
coverage       = m / E
```

The 68 % interval is the exact Poisson interval [L(N), U(N)] on N (section 5), scaled by the same factor
1 / (T·k·37).

**Worked example** (from `test_radon_math.py`): six windows `[2, 2, 3, 2, 2, 2]` give N = 13 and T = 1 h. With
k = 1.27 this is 13 / 1.27 = 10.24 Bq/m³ = **0.2767 pCi/L**, with interval
[9.441, 17.698] / (1.27 × 37) = **[0.2009, 0.3766] pCi/L**.

| Period | N (2 counts/window) | value | 68 % interval (pCi/L) |
|---|---|---|---|
| 1 h, 6 windows | 12 | 0.2554 | 0.1827 - 0.3524 |
| 24 h, 144 windows | 288 | 0.2554 | 0.2403 - 0.2713 |

*k* = 1.27. The interval narrows roughly as 1/√N.

### Which windows belong to "the last hour"

`_derived(now, E, minimum)` takes the windows that ended in

```
( last − E·10 min + 2 min ,  last + 2 min ]
last = anchor + ⌊(now − anchor) / 10 min⌋ · 10 min      (the latest computed rollover)
```

Before the first read after a restart, the anchor is replaced by the latest stored window. The 2-minute
tolerance absorbs end-time jitter.

The window ending at `last` is captured only by the A read at +1:00 to +2:20. Between the rollover and that read,
the 1 h value is therefore computed over 5 of 6 windows (coverage 0.833), and then over 6 of 6. This was found
in review and is unchanged; it does not bias the value, because missing windows shrink T (see below).

### Why missing windows shrink T, not N

A window that was not captured is **unknown**, not zero. Counting it as 0 would bias radon low by the missing
fraction. Leaving it out of both N and T gives an unbiased rate estimate from the windows actually observed.
The cost is a wider interval, because N is smaller. The test
`test_missing_windows_shrink_time_not_count` pins this: 4 windows of 2 counts give the same value as 6 windows
of 2 counts.

### Coverage rules

| Entity | E | minimum m | below the minimum |
|---|---|---|---|
| `Radon (counts, 1 h)` | 6 | 4 | `unavailable` |
| `Radon (counts, 24 h)` | 144 | 120 | `unavailable` |

The counts values also require counts mode (validator passed), and *k* > 0. Their attributes are:

- `lower` and `upper` (pCi/L, 3 decimals; recorded);
- `coverage` (m/E) and `factor` (*k*); these are not recorded (`_unrecorded_attributes`).

The state is rounded to 4 decimals and displayed with 2.

---

## 5. The exact (Garwood) 68 % interval

### Definition

For an observed Poisson count N, the central interval with confidence 1 − α (here 68.27 %, the ±1σ
equivalent) is

```
α/2 = (1 − 0.6826894921370859) / 2 = 0.158655…        (ONE_SIGMA_TAIL)

U(N):  P(X ≤ N   | μ = U) = α/2
L(N):  P(X ≥ N   | μ = L) = α/2,   i.e. 1 − P(X ≤ N−1 | μ = L) = α/2
L(0) = 0
```

The same bounds in chi-square form: L = ½ χ²(α/2; 2N) and U = ½ χ²(1 − α/2; 2N + 2). The interval is
"exact" in the sense that it never under-covers. This matters at the low counts typical of a 1 h value (0-30
counts), where the normal approximation √N fails badly. At N = 0 it would give an interval of zero width.

### Computation (pure Python, no SciPy)

1. **The Poisson CDF in log space** (`poisson_cdf(n, mu)`):
   - Find the largest term of the sum within [0, n]. Its index is m = min(n, ⌊μ⌋), and its log is computed
     directly as `−μ + m·ln μ − lgamma(m+1)`.
   - Add the other terms relative to it with the ratio recurrences tᵢ₋₁ = tᵢ·i/μ (downwards) and
     tᵢ = tᵢ₋₁·μ/i (upwards). Stop in each direction once a term is below 1e-17 of the running sum.
   - Return `min(1, exp(log_top) × Σ)`.

   This cannot overflow or underflow even for n in the thousands. `test_cdf_matches_direct_sum` checks it to
   1e-12 against a direct log-space sum, up to n = 3000.
2. **Bisection** (`_root`) on [0, n + 20 + 10√(n+1)]. The bracket is wide enough that the upper bound is always
   inside it. There are at most 60 halvings, and the search stops once the bracket is narrower than
   1e-12·max(1, hi).
3. **Caching.** `garwood(n)` is wrapped in `functools.lru_cache(maxsize=4096)`. The entities are computed on
   HA's event loop, and N changes at most once per window, so nearly every call is a cache hit. A cold pass
   over all n in 0…300 takes well under 1 s; this is asserted in
   `test_1000_derives_over_typical_n_under_1s_cold_cache`, which also checks that there were exactly 301
   cache misses. On a Raspberry Pi 4 a cold `garwood(288)` took about 3 ms. Before the cache and the rewritten
   CDF, it was about 120 ms per derive, recomputed by every entity every minute.

### Check against published tables

`test_against_published_table` compares the bounds with Gehrels (1986), *ApJ* 303, 336, Tables 1-2 (the
1σ, 0.8413 one-sided limits). The tolerance is 0.002 + 0.0005·n.

| N | Gehrels lower | code lower | Gehrels upper | code upper |
|---|---|---|---|---|
| 0 | 0 | 0 | 1.841 | 1.8410 |
| 1 | 0.173 | 0.1728 | 3.300 | 3.2995 |
| 2 | 0.708 | 0.7082 | 4.638 | 4.6379 |
| 3 | 1.367 | 1.3673 | 5.918 | 5.9182 |
| 10 | 6.891 | 6.8913 | 14.27 | 14.2669 |

Further values from the code: N = 100 gives [90.017, 111.033], and N = 300 gives [282.689, 318.340].

**What the interval covers:** Poisson counting noise only. The uncertainty of *k* is not included; see
section 6.

---

## 6. Calibrating *k*

*k* converts a count rate into a concentration: k = (counts per hour) / (true Bq/m³). It is a property of each
detector, and it is set per monitor in the options flow (0.01-100). The counts are stored raw, so **changing
*k* never loses or distorts data**: every derived value uses the current *k*, and any past period can be
recomputed from the CSV archive with any *k*. Every change of *k* is logged in the Store's `factor_log` as
`{at, old, new}`.

The `Counts vs device (7 d)` diagnostic is the calibration check. It uses the windows of the last 7 days that
have a device value stored (`device_bq`, the device's `latest_bq_m3` at the capturing read), and it needs at
least 144 of them:

```
counts_bq = (Σ counts) / (windows / 6 h) / k           mean counts-based Bq/m³
device_bq = mean(device_bq over the same windows)
ratio     = counts_bq / device_bq                       (3 decimals)
```

To recalibrate against the device's own value, set **k_new = k_old × ratio**. The ratio then becomes 1, because
counts_bq scales as 1/k. To calibrate against a reference instrument, use the reference value in place of
`device_bq`, with k = (counts/h) / reference_Bq.

**Precision of *k*.** A *k* estimated from N counts has a relative statistical uncertainty of about 1/√N. That
is about 2.2 % for a week at 2 counts per window (N ≈ 2000). The device's own value is probably derived from
the same counts, so agreement with the device is not an independent check of the detector's absolute
calibration. The spec treats the trial's ±5 % as an upper bound on k's statistical uncertainty.

A ratio drifting away from 1 over weeks means one of two things: a mis-set factor, or a change in the detector.

---

## 7. The window-model validator

A monitor enters **counts mode** only after its own reads have confirmed the 10-min / phase-0 model.
Counts mode means aligned A/B reads, the count archive and the counts-based radon. Until then, and
permanently if validation fails, it runs **device-values-only**: unaligned reads every 5 min, device values
shown, no count archive, no counts-based radon.

`WindowValidator.observe(u, current, previous, at)` compares each read with the one before it:

```
skip if: no previous read (after a restart the first read only primes),
         status is not pending, or u ≤ previous u (reboot or same minute)

same block   (⌊u/10⌋ == ⌊pu/10⌋):
    violation if previous ≠ pp or current < pc       else same_ok += 1

crossing     (⌊u/10⌋ == ⌊pu/10⌋ + 1) and max(pp, previous) ≥ INFORMATIVE_MIN:
    crossings += 1
    silent    += 1 if previous == pp and current ≥ pc
                    (no visible rollover)

pairs further apart than one block are ignored
```

### Thresholds

| Constant | Value | Meaning |
|---|---|---|
| `MIN_SAME` | 20 | same-block pairs needed to pass with no violation |
| `MIN_SAME_IF_VIOLATED` | 100 | same-block pairs needed if there were 1-2 violations (glitch tolerance) |
| `FAIL_VIOLATIONS` | 3 | violations that fail validation outright |
| `MIN_CROSS` | 20 | informative crossings needed to pass |
| `MAX_SILENT_SHARE` | 0.30 | maximum silent share, both to pass and to avoid failing |
| `FAIL_CROSS` | 60 | informative crossings after which a silent share > 0.30 fails |
| `INFORMATIVE_MIN` | 1 | a crossing counts only if the old or the new `previous` is ≥ 1 |
| `RETEST_AFTER` | 24 h | a failed validator restarts from scratch this long after failing |

```
silent_ok = silent ≤ 0.30 × crossings
fail  if violations ≥ 3  or  (crossings ≥ 60 and not silent_ok)
pass  if same_ok ≥ (20 if violations == 0 else 100) and crossings ≥ 20 and silent_ok
```

The spec described the pass condition as "≥ 20 same-block pairs with no change, and all observed rollovers at
uptime ≡ 0 (mod 10)". The code makes that statistical with the silent-share rule above, and adds the
informative-crossing rule.

### Same-block pairs and crossing pairs

- **A same-block pair must never show a rollover**, under the correct model. Any change in `previous`, or any
  fall in `current`, is a violation. A model with shorter windows, or with a different phase, produces
  violations quickly. The test `test_five_minute_windows_fail` covers this.
- **A crossing under the correct model is a real rollover.** It is silent only by chance: both windows had the
  same count *and* `current` did not fall. At about 2 counts per window this happens to about 10 % of
  crossings (trial: 10.4 % and 8.0 %).
- **For a window m × 10 min long** (m ≥ 2), only 1 in m block crossings is a real rollover. The other m − 1
  are always silent, because `previous` is unchanged and `current` keeps growing. The silent share therefore
  tends to (m − 1)/m: **0.50 for 20-min windows and 0.83 for 60-min windows**. Both are far above the 0.30
  limit.

### The informative-crossing rule (0 → 0 ignored)

A crossing whose two `previous` values are both 0 looks the same whether or not the device rolled over. At low
radon most windows hold 0 counts, so such crossings piled up as "silent" and failed real 10-min devices. One
live monitor reached 6 of 21 silent at near-zero radon. The rule counts a crossing, in both `crossings` and
`silent`, only when `max(old previous, new previous) ≥ 1`. It looks **only at the two `previous` values,
never at whether the crossing was silent**, so it drops crossings at the same rate under either model and
cannot bias the test towards passing.

Long-run silent share in simulation (`tests/sim.FakeRD200`, Poisson counts, 5-min free reads, 10 seeds ×
100 h):

| counts per 10 min | 10-min: all crossings | **10-min: rule ≥ 1** | 20-min: all | **20-min: rule ≥ 1** | **60-min: rule ≥ 1** |
|---|---|---|---|---|---|
| 0.2 | 0.695 | 0.078 | 0.768 | 0.453 | 0.820 |
| 0.5 | 0.462 | 0.146 | 0.638 | 0.513 | 0.831 |
| 1.0 | 0.275 | 0.163 | 0.543 | 0.503 | 0.833 |
| 2.0 | 0.100 | 0.084 | 0.503 | 0.499 | 0.833 |
| 4.0 | 0.018 | 0.018 | 0.500 | 0.500 | 0.833 |

What the rule does at each window length:

- **10-min windows:** the silent share is at most about 0.16, worst near 1 count per window, against a limit
  of 0.30.
- **20-min windows:** the share is about 0.45-0.51 down to 0.2 counts per 10 min, and it tends to 1/3 at very
  low counts. That limit follows from the rule. With λ ≪ 1 counts per window:
  - a non-rollover crossing is informative with probability ≈ λ (one window, which must be ≥ 1), and it is
    always silent;
  - a rollover crossing is informative with probability ≈ 2λ (either window), and it is almost never silent.

  For m-block windows the share therefore tends to (m − 1)/(m + 1): 1/3 for 20 min and 5/7 for 60 min. The
  margin for 20-min windows at very low counts is thin (1/3 against 0.30), but it is still a fail. The test
  `test_twenty_minute_windows_fail_at_low_counts` covers it.
- **60-min windows:** about 0.82-0.83.

Runs of 200 seeds that stopped at a verdict gave the following:

- 10-min devices passed 200 of 200 at every level from 0.2 to 1.5 counts per 10 min;
- 20-min devices failed 200 of 200;
- 60-min devices failed 40 of 40.

A stricter rule (`INFORMATIVE_MIN = 2`) was rejected for two reasons. It lets a low-count 20-min device fall to
0.34, very close to the limit. It is also slow: a 10-min device at 0.5 counts per window needs a median of about
17 h to validate, instead of about 5 h.

**At true zero radon** every crossing is 0 → 0, so the validator stays `pending` indefinitely. The monitor
then shows device values only. This is by design: such data cannot distinguish a 10-min window from a longer
one.

### Time to reach MIN_CROSS

In free mode a monitor is read every 5 min, so consecutive reads alternate between same-block pairs and
crossings: about 6 of each per hour. A crossing compares two consecutive windows with independent Poisson(μ)
counts (μ = mean counts per window), and it is informative unless both are 0:

```
informative crossings per hour ≈ 6 · (1 − e^(−2μ))
t_pass ≈ max( MIN_SAME / 6 ,  MIN_CROSS / (6 · (1 − e^(−2μ))) )   hours
       = max( 3.3 h ,  3.33 / (1 − e^(−2μ)) h )
```

This was checked against the engine simulation (20 seeds per level), with the time measured to the first
window written in counts mode:

| μ (counts / 10 min) | ≈ Bq/m³ at k = 1.27 | predicted | simulated median | simulated max |
|---|---|---|---|---|
| 0.2 | ~0.9 | 10.1 h | 10.3 h | 17.7 h |
| 0.5 | ~2.4 | 5.3 h | 5.5 h | 13.0 h |
| 1.0 | ~4.7 | 3.9 h | 4.0 h | 8.2 h |
| 2.0 | ~9.4 | 3.4 h | 3.3 h | 3.7 h |
| 4.0 | ~19 | 3.3 h | 3.3 h | 3.3 h |

The long tails come from runs of 1-count windows. A 1 → 1 crossing with `current` not falling is genuinely
silent, so the share must first fall back below 0.30.

A wrong model fails after at least 60 informative crossings, which takes about 10 h of free reads at moderate
counts. At low counts it takes longer: a 20-min device at 0.2 counts per 10 min failed within about 34 h, and
at 0.5 within about 18 h (200 seeds).

### Re-test, firmware changes and the repair issue

- **On failure** the engine emits `validation_failed`, and the hub raises the repair issue
  `window_not_recognised_<serial>`. The validator records `failed_at`.
- **24 h after `failed_at`** (`RETEST_AFTER`) the next read resets all counters and sets the status back to
  `pending`. `failed_at` is kept as a marker that this is a re-test. A pass on a re-test emits
  `validation_passed`, which deletes the repair issue, and clears `failed_at`.
- **A firmware change** (`firmware_version` differs from the stored one) replaces the validator with a fresh
  one, so the model is validated again. If the old validator had failed, the new one inherits `failed_at`
  (or the read time, for a legacy state without it). Its later pass then still clears the repair issue.
- **Validator state is persisted**, except the last read pair (`_prev`).
