"""A hard ceiling on routing calls per day.

The free openrouteservice allowance is 2000 directions a day, and a single loop
search spends six to twelve of them. Without a ceiling an afternoon of testing
empties the day's budget silently, and the first sign is a 403 that reads like
a broken key.

The count is persisted, because a process restart must not hand out a fresh
allowance that the upstream service does not agree exists.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Optional


class DailyBudget:
    def __init__(self, limit: int, path: Optional[str] = None) -> None:
        self.limit = limit
        self.path = path
        self._lock = threading.Lock()
        self._day = self._today()
        self._used = 0
        self._load()

    @staticmethod
    def _today() -> str:
        # openrouteservice resets on UTC, so count on the same clock.
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as handle:
                saved = json.load(handle)
        except (OSError, ValueError):
            return
        if saved.get("day") == self._day:
            self._used = int(saved.get("used", 0))

    def _save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as handle:
                json.dump({"day": self._day, "used": self._used}, handle)
        except OSError:
            pass

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._used = today, 0

    def remaining(self) -> int:
        with self._lock:
            self._roll()
            return max(0, self.limit - self._used)

    def take(self, count: int) -> bool:
        """Reserve ``count`` calls. False when the day's allowance is spent."""
        with self._lock:
            self._roll()
            if self.limit and self._used + count > self.limit:
                return False
            self._used += count
            self._save()
            return True

    def status(self) -> Dict[str, object]:
        with self._lock:
            self._roll()
            return {
                "day": self._day,
                "used": self._used,
                "limit": self.limit,
                "remaining": max(0, self.limit - self._used),
            }
