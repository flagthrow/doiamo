"""Local points-of-interest lookup, built from an OpenStreetMap extract.

Overpass accounts for 96% of the time a POI lookup takes, and fails often
enough to matter. The data itself is small, so it is extracted once by
``tools/build_poi_db.py`` into SQLite with an R-tree index and read from disk
in milliseconds.

Where the extract has no coverage, the caller falls back to Overpass — so the
app stays global while the launch cities stay fast.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_PATH = os.environ.get("POI_DB", "data/pois.sqlite")

# Degrees of margin required inside the coverage box before it is trusted. A
# route that runs off the edge of the extract would silently lose its POIs
# there, which is worse than being slow.
COVERAGE_MARGIN_DEG = 0.02


class LocalPoiStore:
    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self.path = path
        self._coverage: Optional[Tuple[float, float, float, float]] = None
        self._count = 0
        if self.available:
            self._read_coverage()

    @property
    def available(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)

    def _connect(self) -> sqlite3.Connection:
        # One connection per call: SQLite reads are cheap to open and this
        # sidesteps sharing a handle across the event loop's threads.
        return sqlite3.connect("file:{}?mode=ro".format(self.path), uri=True)

    def _read_coverage(self) -> None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT min_lat, min_lon, max_lat, max_lon, count FROM coverage"
                ).fetchone()
        except sqlite3.Error:
            return
        if row:
            self._coverage = (row[0], row[1], row[2], row[3])
            self._count = row[4] or 0

    @property
    def count(self) -> int:
        return self._count

    def covers(self, points: Sequence[Tuple[float, float]]) -> bool:
        """True when every corridor point sits well inside the extract."""
        if not self._coverage or not points:
            return False
        min_lat, min_lon, max_lat, max_lon = self._coverage
        m = COVERAGE_MARGIN_DEG
        return all(
            (min_lat + m) <= lat <= (max_lat - m)
            and (min_lon + m) <= lon <= (max_lon - m)
            for lat, lon in points
        )

    def near(
        self, points: Sequence[Tuple[float, float]], radius_m: int
    ) -> List[Dict[str, object]]:
        """Everything in the corridor's bounding box.

        One box query rather than one per point: the precise distance filter
        happens in ``poi.assign`` regardless, and SQLite returns a few thousand
        rows faster than it answers hundreds of separate questions.
        """
        if not points or not self.available:
            return []

        pad = radius_m / 111_000.0
        lats = [lat for lat, _ in points]
        lons = [lon for _, lon in points]
        box = (min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad)

        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT p.id, p.kind, p.name, p.lat, p.lon
                      FROM poi_bbox b
                      JOIN pois p ON p.rowid = b.rowid
                     WHERE b.max_lat >= ? AND b.min_lat <= ?
                       AND b.max_lon >= ? AND b.min_lon <= ?
                    """,
                    (box[0], box[1], box[2], box[3]),
                ).fetchall()
        except sqlite3.Error:
            return []

        return [
            {"id": row[0], "kind": row[1], "name": row[2], "lat": row[3], "lon": row[4]}
            for row in rows
        ]
