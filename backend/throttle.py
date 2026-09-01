"""Shared request pacing for the free upstream services."""
from __future__ import annotations

import asyncio
import time


class Throttle:
    """Serialises request starts so a shared quota is never overrun.

    Every caller waits its turn, so this paces the process as a whole rather
    than each request independently.
    """

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval = min_interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._min_interval - (now - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()
