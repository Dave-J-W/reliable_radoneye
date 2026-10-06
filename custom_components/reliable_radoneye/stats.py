"""Exact hourly counts and windows as external statistics (spec "Raw-count archive (exact)")."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

try:  # HA >= 2025.10 describes means with mean_type
    from homeassistant.components.recorder.models import StatisticMeanType
    _MEAN = {"mean_type": StatisticMeanType.ARITHMETIC}
except ImportError:  # pragma: no cover
    _MEAN = {}


@callback
def write_hour(hass: HomeAssistant, serial: str, label: str, hour_start: datetime, counts: int, windows: int) -> None:
    """(Re)write one clock hour: counts and windows captured whose window ENDED in that hour."""
    for kind, value, unit in (("counts", counts, "counts"), ("windows", windows, "windows")):
        meta = StatisticMetaData(
            has_mean=True, has_sum=False, name=f"Radon {label} {kind} per hour", source=DOMAIN,
            statistic_id=f"{DOMAIN}:{kind}_{serial.lower()}", unit_of_measurement=unit, unit_class=None, **_MEAN)
        async_add_external_statistics(hass, meta, [StatisticData(start=hour_start, mean=value, min=value, max=value)])
