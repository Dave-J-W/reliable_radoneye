"""Config flow: Bluetooth discovery of FR:* adverts, confirm by connecting (refuses RD200 v1), YAML import,
options (label, factor, backup reader URL, parallel run with rd200_ble)."""

from __future__ import annotations

import asyncio

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from . import ble
from .const import DEFAULT_FACTOR, DOMAIN
from .core.schedule import CONNECT_CAP_S


class RadonEyeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None

    async def async_step_bluetooth(self, discovery_info) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address, self._name = discovery_info.address, discovery_info.name
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._address = user_input["address"]
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()
            return await self.async_step_confirm()
        current = self._async_current_ids()
        found = {i.address: f"{i.name} ({i.address})"
                 for i in bluetooth.async_discovered_service_info(self.hass, connectable=True)
                 if (i.name or "").startswith("FR:") and i.address not in current}
        if not found:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required("address"): vol.In(found)}))

    async def async_step_confirm(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                status, _, _ = await ble.session(self.hass, self._address, False, CONNECT_CAP_S)
            except ble.UnsupportedModel:
                return self.async_abort(reason="unsupported_model")
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                if status.get("model", "unknown") == "unknown":
                    return self.async_abort(reason="unsupported_model")
                label = (user_input.get("label") or status["serial"][-4:]).strip()
                return self.async_create_entry(
                    title=f"Radon {label}",
                    data={"address": self._address, "serial": status["serial"], "model": status["model"], "label": label},
                    options={"label": label, "factor": DEFAULT_FACTOR, "backup_url": "", "parallel_with_rd200_ble": False})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({vol.Optional("label"): str}),
                                    errors=errors, description_placeholders={"name": self._name or self._address})

    async def async_step_import(self, conf: dict) -> ConfigFlowResult:
        await self.async_set_unique_id(conf["address"])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Radon {conf['label']}",
            data={"address": conf["address"], "serial": conf["serial"], "model": "", "label": conf["label"],
                  "statistic_id": conf.get("statistic_id")},
            options={"label": conf["label"], "factor": float(conf.get("factor", DEFAULT_FACTOR)),
                     "backup_url": conf.get("backup_url", ""),
                     "parallel_with_rd200_ble": bool(conf.get("parallel_with_rd200_ble", False))})

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return RadonEyeOptionsFlow()


class RadonEyeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        o, d = self.config_entry.options, self.config_entry.data
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("label", default=o.get("label", d["label"])): str,
            vol.Required("factor", default=o.get("factor", DEFAULT_FACTOR)):
                vol.All(vol.Coerce(float), vol.Range(min=0.01, max=100)),
            vol.Optional("backup_url", default=o.get("backup_url", "")): str,
            vol.Optional("parallel_with_rd200_ble", default=o.get("parallel_with_rd200_ble", False)): bool,
        }))
