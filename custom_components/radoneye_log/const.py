"""Constants shared by the HA side of radoneye_log."""

DOMAIN = "radoneye_log"
DEFAULT_FACTOR = 1.27                 # counts/h per Bq/m3, measured 2026-09-29/30 (spec)
RADON_UNIT = "pCi/L"                  # MUST equal statistics_meta of sensor.fr_*_radon (plan Task 0 Step 1)
SIGNAL_HUB = f"{DOMAIN}_hub"
SIGNAL_HOURLY = f"{DOMAIN}_hourly"


def signal_monitor(serial: str) -> str:
    return f"{DOMAIN}_monitor_{serial}"
