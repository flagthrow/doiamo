"""A tiny TTL cache so a search result survives until the GPX download click."""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    def __init__(self, max_items: int, ttl_s: float) -> None:
        self._max = max_items
        self._ttl = ttl_s
        self._items: "OrderedDict[str, tuple]" = OrderedDict()

    def set(self, key: str, value: Any) -> None:
        self._items[key] = (time.monotonic() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def get(self, key: str) -> Optional[Any]:
        entry = self._items.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value
