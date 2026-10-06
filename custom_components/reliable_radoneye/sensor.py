"""Entities (spec "Entities"). Per monitor: device values (stale after 20 min), counts-based radon,
4 h first-attempt reliability, diagnostics (hourly, no state_class). Hub level: last pull, radio share.
New entities get TEMPORARY ids sensor.rd200_<label>_<key> (spec cutover step 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN, RADON_UNIT, SIGNAL_HOURLY, SIGNAL_HUB, signal_monitor
from .core.radon_math import Derived

M = SensorStateClass.MEASUREMENT
# Same device class as rd200_ble's radon sensors, so statistics_meta keeps unit_class radiation_concentration.
RADON_DC = getattr(SensorDeviceClass, "RADON", None)


@dataclass(frozen=True)
class Spec:
    key: str
    name: str
    unit: str | None = None
    state_class: SensorStateClass | None = None
    device_class: SensorDeviceClass | None = None
    device_value: bool = False          # stale rule applies
    diagnostic: bool = False
    hourly: bool = False                # refresh only on the hourly signal
    enabled: bool = True
    precision: int | None = None


SPECS = [
    Spec("radon", "Radon", RADON_UNIT, M, RADON_DC, device_value=True, precision=2),
    Spec("radon_1_day_level", "Radon 1-day level", RADON_UNIT, M, RADON_DC, device_value=True, precision=2),
    Spec("radon_1_month_level", "Radon 1-month level", RADON_UNIT, M, RADON_DC, device_value=True, precision=2),
    Spec("radon_peak", "Radon peak", RADON_UNIT, M, RADON_DC, device_value=True, precision=2),
    Spec("last_boot", "Last boot", device_class=SensorDeviceClass.TIMESTAMP),
    Spec("radon_counts_1h", "Radon (counts, 1 h)", RADON_UNIT, M, RADON_DC, precision=2),
    Spec("radon_counts_24h", "Radon (counts, 24 h)", RADON_UNIT, None, RADON_DC, precision=2),
    Spec("first_attempt_success_4h", "First-attempt read success (4 h)", PERCENTAGE, M, precision=1),
    Spec("window_capture_24h", "Window capture (24 h)", PERCENTAGE, diagnostic=True, hourly=True, precision=1),
    Spec("missed_windows_24h", "Missed windows (24 h)", diagnostic=True, hourly=True),
    Spec("counts_device_ratio_7d", "Counts vs device (7 d)", diagnostic=True, hourly=True, precision=3),
    Spec("rssi", "Signal strength", SIGNAL_STRENGTH_DECIBELS_MILLIWATT, device_class=SensorDeviceClass.SIGNAL_STRENGTH,
         diagnostic=True, enabled=False),
    Spec("last_good_read", "Last good read", device_class=SensorDeviceClass.TIMESTAMP, diagnostic=True, enabled=False),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = hass.data[DOMAIN]["hub"]
    mon = hub.monitors[entry.data["serial"]]
    async_add_entities([RadonEyeSensor(mon, spec) for spec in SPECS])


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities: AddEntitiesCallback,
                               discovery_info=None) -> None:
    if discovery_info is None:
        return
    hub = hass.data[DOMAIN]["hub"]
    async_add_entities([LastPullSensor(hub), RadioShareSensor(hub)])


def device_info(mon) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, mon.serial)}, connections={(CONNECTION_BLUETOOTH, mon.address)},
                      name=f"Radon {mon.label}", manufacturer="Ecosense (FTLab)", model="RD200",
                      serial_number=mon.serial)


class RadonEyeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"coverage", "factor", "block_start", "source"})

    def __init__(self, mon, spec: Spec) -> None:
        self._mon, self._spec = mon, spec
        self._attr_unique_id = f"{mon.serial}_{spec.key}"
        self._attr_name = spec.name
        self._attr_device_info = device_info(mon)
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_state_class = spec.state_class
        self._attr_device_class = spec.device_class
        self._attr_suggested_display_precision = spec.precision
        self._attr_entity_registry_enabled_default = spec.enabled
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self.entity_id = f"sensor.rd200_{slugify(mon.label)}_{spec.key}"

    async def async_added_to_hass(self) -> None:
        signal = SIGNAL_HOURLY if self._spec.hourly else signal_monitor(self._mon.serial)
        self.async_on_remove(async_dispatcher_connect(self.hass, signal, self._refresh))
        self._refresh()

    @callback
    def _refresh(self) -> None:
        values = self._mon.states(dt_util.utcnow())        # shared per monitor per signal (final review I1)
        v = values.get(self._spec.key)
        attrs: dict = {}
        if isinstance(v, Derived):
            attrs = {"lower": round(v.lower_pci, 3), "upper": round(v.upper_pci, 3),
                     "coverage": round(v.coverage, 3), "factor": values["factor"]}
            v = round(v.value_pci, 4)
        elif self._spec.key == "first_attempt_success_4h":
            attrs = {"block_start": values["reliability_block_start"]}
        if self._spec.device_value:
            self._attr_available = not values["stale"]
        elif self._spec.key.startswith("radon_counts"):
            self._attr_available = v is not None
        self._attr_native_value = v
        self._attr_extra_state_attributes = attrs
        self.async_write_ha_state()


class LastPullSensor(RestoreEntity, SensorEntity):
    _attr_name = "RadonEye log last pull"
    _attr_unique_id = "reliable_radoneye_last_pull"
    _attr_icon = "mdi:radioactive"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False

    def __init__(self, hub) -> None:
        self._hub = hub
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = datetime.fromisoformat(last.state)
            except ValueError:
                pass
            self._attr_extra_state_attributes = dict(last.attributes)
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_HUB, self._updated))

    @callback
    def _updated(self) -> None:
        pulls = self._hub.last_pull
        if pulls:
            self._attr_native_value = max(datetime.fromisoformat(p["finished"]) for p in pulls.values())
        self._attr_extra_state_attributes = {
            "all_ok": all(p.get("ok") for p in pulls.values()) if pulls else False,
            **{f"radon_{k}": ("ok" if p.get("ok") else "failed") for k, p in pulls.items()},
            **{f"radon_{k}_detail": p for k, p in pulls.items()},
        }
        self.async_write_ha_state()


class RadioShareSensor(SensorEntity):
    _attr_name = "RadonEye radio time share"
    _attr_unique_id = "reliable_radoneye_radio_share"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hub) -> None:
        self._hub = hub

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_HOURLY, self._refresh))

    @callback
    def _refresh(self) -> None:
        self._attr_native_value = self._hub.radio.share(dt_util.utcnow())
        self.async_write_ha_state()
