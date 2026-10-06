"""RadonEye RD200 (v2/v3) BLE protocol - READ-ONLY subset: status and stored hourly history.

Adapted from the `radoneye` library (https://github.com/sormy/radoneye, v3.0.0, interface_v2.py and
util.py), MIT License, Copyright (c) 2022 Artem Butusov - see LICENSE-radoneye in this folder.
Vendored rather than pip-installed because radoneye pins bleak~=2.0.0, which must not fight Home
Assistant's own bleak. Commands that change the device (beep, alarm, unit) are deliberately left out.
Verified on RD200V3 fw V3.0.1 (history identical to the RadonEye app export, 2026-09-27).
"""

from __future__ import annotations

import asyncio
from struct import unpack_from

from bleak import BleakClient

SERVICE_UUID = "00001523-0000-1000-8000-00805f9b34fb"
CHAR_COMMAND = "00001524-0000-1000-8000-00805f9b34fb"
CHAR_STATUS = "00001525-0000-1000-8000-00805f9b34fb"
CHAR_HISTORY = "00001526-0000-1000-8000-00805f9b34fb"
COMMAND_STATUS = 0x40
COMMAND_HISTORY = 0x41


def _str(b: bytearray, off: int, n: int) -> str:
    return b[off:off + n].decode(errors="replace")


def _u8(b: bytearray, off: int) -> int:
    return b[off]


def _u16(b: bytearray, off: int) -> int:
    return unpack_from("<H", b, off)[0]


def _u32(b: bytearray, off: int) -> int:
    return unpack_from("<I", b, off)[0]


def _pci(bq: float) -> float:
    return round(bq / 37, 2)


def parse_status(data: bytearray) -> dict:
    if _u8(data, 15) == 0x06:  # v2
        serial = _str(data, 8, 3) + _str(data, 2, 6) + _str(data, 11, 4)
        model = _str(data, 16, 6)
    elif _u8(data, 14) == 0x07:  # v3
        serial = _str(data, 2, 12)
        model = _str(data, 15, 7)
    else:
        serial = model = "unknown"
    return {
        "serial": serial,
        "model": model,
        "firmware_version": _str(data, 22, 6),
        "latest_bq_m3": _u16(data, 33),
        "latest_pci_l": _pci(_u16(data, 33)),
        "day_avg_bq_m3": _u16(data, 35),
        "day_avg_pci_l": _pci(_u16(data, 35)),
        "month_avg_bq_m3": _u16(data, 37),
        "month_avg_pci_l": _pci(_u16(data, 37)),
        # Particle (alpha pulse) counts of the current and previous counting windows, as in upstream
        # KNOWLEDGE_V2.md (0x27 / 0x29). Window length and rollover are not documented - measure them.
        "counts_current": _u16(data, 39),
        "counts_previous": _u16(data, 41),
        "peak_bq_m3": _u16(data, 51),
        "peak_pci_l": _pci(_u16(data, 51)),
        "uptime_minutes": _u32(data, 43),
        # The whole packet, so fields not decoded (or decoded wrongly) can be re-read from archives
        "raw_hex": bytes(data).hex(),
    }


def _parse_history_page(data: bytearray) -> dict:
    page_count, page_no, value_count = data[1], data[2], data[3]
    body = data[4:]
    values = list(unpack_from("<" + "H" * (len(body) // 2), body, 0))
    return {"page_count": page_count, "page_no": page_no, "value_count": value_count, "values_bq_m3": values}


def _merge_history(pages: list[dict]) -> dict:
    pages = sorted(pages, key=lambda p: p["page_no"])
    if pages:
        for i, p in enumerate(pages):
            if i + 1 != p["page_no"]:
                raise ValueError("history page order mismatch")
        if pages[0]["page_count"] != len(pages):
            raise ValueError("history page count mismatch")
    bq = [v for p in pages for v in p["values_bq_m3"]]
    return {"values_bq_m3": bq, "values_pci_l": [_pci(v) for v in bq]}


def supports(client: BleakClient) -> bool:
    return bool(client.services.get_service(SERVICE_UUID))


async def _request(client: BleakClient, char: str, command: int, handle, timeout: float):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def callback(_char, data: bytearray) -> None:
        if not future.done() and data and data[0] == command:
            try:
                result = handle(bytearray(data))
            except Exception as err:  # noqa: BLE001
                future.set_exception(err)
                return
            if result is not None:
                future.set_result(result)

    await client.start_notify(char, callback)
    try:
        await client.write_gatt_char(CHAR_COMMAND, bytearray([command]))  # as upstream
        return await asyncio.wait_for(future, timeout)
    finally:
        try:
            await client.stop_notify(char)
        except Exception:  # noqa: BLE001
            pass


async def read_status(client: BleakClient, timeout: float = 10) -> dict:
    return await _request(client, CHAR_STATUS, COMMAND_STATUS, parse_status, timeout)


async def read_history(client: BleakClient, timeout: float = 180) -> dict:
    pages: list[dict] = []

    def handle(data: bytearray):
        page = _parse_history_page(data)
        pages.append(page)
        return _merge_history(pages) if page["page_count"] == page["page_no"] else None

    return await _request(client, CHAR_HISTORY, COMMAND_HISTORY, handle, timeout)
