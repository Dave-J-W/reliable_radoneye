"""Pure asyncio timing for one BLE session: the cap bounds connect+read only, close is bounded separately."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
_LOGGER = logging.getLogger(__name__)


async def capped_then_close(work: Callable[[], Awaitable[T]], close: Callable[[], Awaitable[None]],
                            cap_s: float, close_cap_s: float) -> T:
    """Run work() for at most cap_s, then ALWAYS run close() for at most close_cap_s.

    A result already obtained is returned even if close() times out or fails (logged at debug). If work
    times out or fails its exception propagates, after close() has still run. Worst case total time is
    cap_s + close_cap_s.
    """
    try:
        return await asyncio.wait_for(work(), cap_s)
    finally:
        try:
            await asyncio.wait_for(close(), close_cap_s)
        except Exception as e:  # noqa: BLE001 - includes TimeoutError; a failed disconnect never loses a read
            _LOGGER.debug("BLE disconnect did not complete cleanly: %s: %s", type(e).__name__, e)
