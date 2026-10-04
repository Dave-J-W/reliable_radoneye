"""Backup reader reachable - only for monitors with a backup reader URL (spec "Optional second poller")."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN, signal_monitor
from .sensor import device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    mon = hass.data[DOMAIN]["hub"].monitors[entry.data["serial"]]
    if mon.backup_url:
        async_add_entities([BackupReachable(mon)])


class BackupReachable(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Backup reader reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, mon) -> None:
        self._mon = mon
        self._attr_unique_id = f"{mon.serial}_backup_reachable"
        self._attr_device_info = device_info(mon)
        self.entity_id = f"binary_sensor.rd200_{slugify(mon.label)}_backup_reachable"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, signal_monitor(self._mon.serial), self._refresh))
        self._refresh()

    @callback
    def _refresh(self) -> None:
        self._attr_is_on = self._mon.backup_ok
        self.async_write_ha_state()
