"""RadonEye RD200 (v2/v3) over Home Assistant's Bluetooth - spec docs/superpowers/specs/2026-09-30-radoneye-b-design.md.

Each monitor is read ~every 5 min (aligned to its 10-min counting window, 3 attempts per read), its raw
particle counts are archived exactly (/config/radoneye_logs/counts_*.csv + hourly external statistics),
and radon is derived from the counts with a Garwood interval. The stored hourly log is pulled once a day
in a read slot after 06:00 and fills missing radon statistics hours (never overwrites). READ-ONLY.

YAML (radoneye_log: devices:) is imported once into config entries, then a repair asks to remove it.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICES, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, discovery, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .hub import Hub

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

DEVICE_SCHEMA = vol.Schema({
    vol.Required("address"): cv.string,
    vol.Required("serial"): cv.string,
    vol.Required("label"): cv.string,
    vol.Optional("statistic_id"): cv.entity_id,
    vol.Optional("factor"): vol.Coerce(float),
    vol.Optional("backup_url"): cv.string,
    vol.Optional("parallel_with_rd200_ble"): cv.boolean,
})
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA])})},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hub = hass.data.setdefault(DOMAIN, {}).setdefault("hub", Hub(hass))

    async def handle_pull(call: ServiceCall) -> ServiceResponse:
        return {"queued": hub.queue_pull(call.data.get("immediate", False)),
                "immediate": call.data.get("immediate", False)}

    hass.services.async_register(DOMAIN, "pull", handle_pull,
                                 schema=vol.Schema({vol.Optional("immediate", default=False): cv.boolean}),
                                 supports_response=SupportsResponse.OPTIONAL)
    hass.async_create_task(discovery.async_load_platform(hass, Platform.SENSOR, DOMAIN, {}, config))
    if DOMAIN in config:
        for dev in config[DOMAIN][CONF_DEVICES]:
            hass.async_create_task(hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT},
                                                                       data=dict(dev)))
        ir.async_create_issue(hass, DOMAIN, "remove_yaml", is_fixable=False, severity=ir.IssueSeverity.WARNING,
                              translation_key="remove_yaml")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub: Hub = hass.data[DOMAIN]["hub"]
    await hub.async_add(entry)
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await hass.data[DOMAIN]["hub"].async_remove(entry.data["serial"])
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
