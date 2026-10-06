# Frequently asked questions

[README](../README.md) > FAQ

## Calibration and the factor k

**What is k?** The number of particle counts per hour that the monitor's detector produces for each Bq/m³
of radon. The counts-based radon is `(counts per hour) / k` in Bq/m³, divided by 37 for pCi/L.

**Where does 1.27 come from?** It was measured on two RD200V3 units (firmware V3.0.1) over about 33 hours,
by dividing their counts per hour by the devices' own Bq/m³ values: 1.27 on one unit and 1.28 on the other.
The device very probably derives its own value from the same counts, so this is a **consistency check, not
an independent calibration**. Treat 1.27 as a sensible starting point.

**How do I calibrate against a reference instrument?**

1. Put the RD200 next to a calibrated reference (a professional monitor, or an approved test device) for
   several days. Longer is better: at indoor levels each window has only a few counts.
2. Over the same period, add up the counts from the hourly statistic `reliable_radoneye:counts_<serial>`
   (see [Entities](entities.md#hourly-counts-statistics)) and the number of hours.
3. `k = (counts per hour) / (reference average in Bq/m³)`. If the reference is in pCi/L, multiply it by 37
   first.
4. Enter k under **Configure** for that monitor.

**How do I make the counts value agree with the display?** Look at **Counts vs device (7 d)**. It is the
counts-based value divided by the device's own value. Set `new k = old k × ratio`; for example, with k = 1.27
and a ratio of 1.10, use 1.40. This makes the two consistent; it does not make either more accurate.

**Does changing k lose data?** No. The raw counts are stored, so a new k re-scales every derived value at once
and leaves the archive and the hourly counts statistics untouched. Each change is recorded in the
integration's stored state.

Details of the method: [the counts method](technical/counts-method.md).

## Why does the counts-based value differ from the device display?

Several honest reasons, usually together:

- **Different averaging periods.** *Radon (counts, 1 h)* covers the last 6 windows and *(counts, 24 h)* the
  last 144. The device's latest value, 1-day and 1-month levels each use their own (undocumented) averaging.
  Compare the 24 h counts value with the **1-day level**, not with the latest value.
- **Counting noise.** At about 10 Bq/m³ a 10-minute window holds about 2 counts, so an hour has about a dozen.
  That is why the 1 h value has a wide `lower`/`upper` interval. A difference inside the interval is not a
  disagreement.
- **The factor k.** If k is a few percent off for your unit, the counts value is a few percent off too. See
  above.
- **Missing windows** only widen the interval; they never pull the value toward zero.

In a real parallel run the 24 h counts value was 0.877 pCi/L (interval 0.849 to 0.906) against a device 1-day
level of 0.86, and 0.716 (0.69 to 0.742) against 0.65 on a second unit.

## Is it really read-only?

Yes. The integration's Bluetooth code only sends two **request** commands to the monitor: `0x40` (send the
status) and `0x41` (send the stored log). Commands that change the device (beep, alarm, unit) are deliberately
not included in the code at all. It never pairs, never changes settings, and never clears the stored log. The
backup reader is the same, and sends only `0x40`. See [the protocol](technical/protocol.md).

## Does frequent reading drain a battery or interfere with other Bluetooth devices?

- **Battery:** the RD200 runs from its power adapter, not a battery (as on the units tested), so frequent
  reads cost nothing in battery life.
- **The monitor itself:** it accepts one connection at a time. While Home Assistant reads it (a few seconds,
  about 12 times an hour), the phone app cannot connect, and the other way round. Close the app when you are
  not using it.
- **Your Bluetooth radio:** every read occupies one connection on the adapter or proxy for a few seconds.
  `sensor.radoneye_radio_time_share` shows how much. With a few monitors it is a few percent, and the
  integration warns above 50 %. Passive devices that only advertise (thermometers and the like) are not
  affected by connections in practice; other devices that need connections share the radio.
- **Wi-Fi:** Bluetooth and 2.4 GHz Wi-Fi share the band. Heavy Wi-Fi traffic near a marginal link can make
  connects slower; it is not caused by this integration.

## Does any data leave my home?

No. Everything is local:

- the monitors are read over Bluetooth by your own Home Assistant (or proxy);
- the archive files stay under `/config/reliable_radoneye/` and the statistics in your recorder database;
- the only network requests the integration ever makes are to a **backup reader URL that you configure**
  yourself, on your own network. With the option empty, it makes none.

There is no cloud service, account, telemetry or update check. (HACS itself contacts GitHub to download and
update the integration.)

## Can I use an RD200 v1?

No. The RD200 v1 uses a different Bluetooth protocol, and the setup flow refuses it with *Not supported: only
RadonEye RD200 v2/v3*. The check is made by connecting once and looking for the v2/v3 service, so nothing is
written to the device. RD200 v2 is supported by the protocol code but was not tested on real hardware; the
RD200 v3 was.

## Can I use other firmware versions?

Probably. The status read is the same; what may differ is the counting window. Each monitor is checked live,
and if its counts do not follow a 10-minute window it simply runs with the device's own values and a Repair
issue explains why. See [Troubleshooting](troubleshooting.md#repair-counting-window-not-recognised).

## Why pCi/L and not Bq/m³?

The integration reports pCi/L, the unit of the sensors it was built to continue. 1 pCi/L = 37 Bq/m³. The
archive keeps raw counts, which have no unit problem at all.

## Do I need the backup reader?

No. Every feature works without it. It adds robustness: windows missed while Home Assistant restarts, or
while its radio has a bad spell, can be filled from a second machine with its own radio. Without it those
windows are recorded as *not observed* or *missed*, which only shortens the averaging time. See
[the backup reader](../extras/backup_reader/README.md).

## Can I still use the RadonEye phone app?

Yes, but not at the same moment as a read. If the app holds the connection, Home Assistant's reads fail and
retry; close the app when you are done. Reading while the app is open in the background can make the device
values go `unavailable` for a while.

## Is this a certified radon measurement?

No. It reports what a consumer monitor measures, with honest statistics. For decisions about mitigation,
follow your national radon guidance.
