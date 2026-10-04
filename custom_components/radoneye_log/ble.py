"""One BLE session with an RD200 through HA's Bluetooth manager (read-only: 0x40, optionally 0x41)."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from . import protocol
from .core.schedule import DISCONNECT_CAP_S
from .core.timing import capped_then_close


class UnsupportedModel(Exception):
    """Not an RD200 v2/v3 (e.g. RD200 v1) - refused at setup."""


async def session(hass: HomeAssistant, address: str, with_history: bool, cap_s: float,
                  disconnect_cap_s: float = DISCONNECT_CAP_S) -> tuple[dict, dict | None, int | None]:
    """Connect + read within cap_s; the disconnect is bounded separately and never discards the read."""
    from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise RuntimeError("not currently heard by any HA Bluetooth adapter")
    info = bluetooth.async_last_service_info(hass, address, connectable=True)
    rssi = info.rssi if info else None
    client = None

    async def work() -> tuple[dict, dict | None, int | None]:
        nonlocal client
        client = await establish_connection(
            BleakClientWithServiceCache, ble_device, address, max_attempts=1,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(hass, address, connectable=True)
            or ble_device)
        if not protocol.supports(client):
            raise UnsupportedModel("RadonEye v2/v3 service not found (RD200 v1 or another product)")
        status = await protocol.read_status(client, timeout=8)
        history = await protocol.read_history(client, timeout=25) if with_history else None
        return status, history, rssi

    async def close() -> None:
        if client is not None:
            await client.disconnect()

    return await capped_then_close(work, close, cap_s, disconnect_cap_s)
