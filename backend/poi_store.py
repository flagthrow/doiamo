"""Local points-of-interest lookup, built from an OpenStreetMap extract.

Overpass accounts for 96% of the time a POI lookup takes, and fails often
enough to matter. The data itself is small, so it is extracted once by
``tools/build_poi_db.py`` into SQLite with an R-tree index and read from disk
in milliseconds.

Where the extract has no coverage, the caller falls back to Overpass — so the
app stays global while the launch cities stay fast.
"""
from __future__ import annotations

import math
import os
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from . import poi as poi_module

# Resolved against the project root, not the working directory. A relative
# path here fails silently — the store reports "not available" and everything
# falls back to Overpass, which looks exactly like having no database at all.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.environ.get("POI_DB") or os.path.join(_ROOT, "data", "pois.sqlite")

# The database is built from a 576 MB extract and is far too large to commit,
# so a deployment starts without one and silently falls back to Overpass. Point
# POI_DB_URL at a copy and it is fetched once on startup instead.
DOWNLOAD_URL = os.environ.get("POI_DB_URL", "")
DOWNLOAD_TIMEOUT_S = float(os.environ.get("POI_DB_TIMEOUT_S", "120"))

# Coverage is a grid of cells that actually hold data, not a bounding box. A
# box cannot describe a region: the north-west extract's box spans Bologna,
# Verona and Parma, none of which are in it, and claiming them would return no
# points of interest at all with nothing to say why.
COVERAGE_CELL_DEG = 0.1


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth distance. Only ever used over a few tens of kilometres, where
    the error is far smaller than the question being asked."""
    mean = math.radians((lat1 + lat2) / 2.0)
    dx = (lon2 - lon1) * math.cos(mean) * 111_320.0
    dy = (lat2 - lat1) * 110_570.0
    return math.hypot(dx, dy)


class LocalPoiStore:
    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self.path = path
        self._cells: set = set()
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
                self._count = sum(
                    row[0] or 0
                    for row in connection.execute("SELECT count FROM coverage")
                )
                self._cells = {
                    (row[0], row[1])
                    for row in connection.execute(
                        "SELECT cell_lat, cell_lon FROM coverage_cells"
                    )
                }
        except sqlite3.Error:
            return

    @property
    def count(self) -> int:
        return self._count

    def covers(self, points: Sequence[Tuple[float, float]]) -> bool:
        """True when every corridor point falls in a cell that holds data.

        Deliberately strict: the point's own cell must have something in it,
        not merely a neighbour. Being generous at the edge is exactly where
        lying is worse than being slow — a lenient version claimed Verona,
        eight kilometres past where the extract's data actually stops, and
        would have answered "no fountains here" when the truth was "this
        database has never heard of here".

        A route through a genuinely empty cell falls back to Overpass. That
        costs time; it does not cost the truth.
        """
        if not self._cells or not points:
            return False
        return all(
            (int(lat // COVERAGE_CELL_DEG), int(lon // COVERAGE_CELL_DEG)) in self._cells
            for lat, lon in points
        )

    @property
    def cells(self) -> int:
        return len(self._cells)

    def nearest_place(
        self, lat: float, lon: float, within_km: float = 25.0
    ) -> Optional[Dict[str, object]]:
        """The middle of the nearest settlement, or None.

        Returns None rather than raising when the database predates this table:
        a deployment running last week's asset should quietly lose the feature,
        not every search. A named centro storico beats a city node at the same
        distance, because it is the thing people actually mean by "in centro".
        """
        span = within_km / 111.0
        lon_span = span / max(0.2, math.cos(math.radians(lat)))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT p.kind, p.name, p.lat, p.lon
                    FROM place_bbox b JOIN places p ON p.rowid = b.rowid
                    WHERE b.min_lat >= ? AND b.max_lat <= ?
                      AND b.min_lon >= ? AND b.max_lon <= ?
                    """,
                    (lat - span, lat + span, lon - lon_span, lon + lon_span),
                ).fetchall()
        except sqlite3.Error:
            return None

        found = []
        for kind, name, plat, plon in rows:
            metres = _distance_m(lat, lon, plat, plon)
            found.append({
                "kind": kind, "name": name, "lat": plat, "lon": plon,
                "distance_m": metres,
                "radius_m": poi_module.PLACE_RADIUS_M.get(kind, 1500),
            })
        if not found:
            return None

        # Distance decides. Rank only breaks a tie between nodes describing the
        # same place: standing at the Duomo, the answer is Milan, not a centro
        # storico five kilometres away in another town — which is exactly what
        # ranking first produced.
        found.sort(key=lambda place: place["distance_m"])
        nearest = found[0]

        # Unless you are actually standing inside a named historic centre, in
        # which case that is the thing you meant by "in centro".
        inside = [
            place for place in found
            if place["kind"] == "historic"
            and place["distance_m"] <= place["radius_m"]
        ]
        if inside:
            nearest = min(inside, key=lambda place: place["distance_m"])

        # Too far away to be anybody's centre.
        return nearest if nearest["distance_m"] <= within_km * 1000 else None

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


# Filled in by ensure_downloaded so /api/healthz can say what went wrong. A
# deployment that silently falls back to Overpass is indistinguishable from a
# working one until someone notices the POIs are missing.
LAST_DOWNLOAD: Dict[str, object] = {"attempted": False, "ok": None, "error": None}


def ensure_downloaded(
    path: str = DEFAULT_PATH,
    url: str = "",
    timeout_s: float = DOWNLOAD_TIMEOUT_S,
) -> bool:
    """Fetch the POI database if it is missing and a URL was configured.

    Returns True when a usable file is in place. Failure is not fatal: the app
    falls back to Overpass, which is slower but works.

    Downloads to a temporary name and renames on success, so an interrupted
    download never leaves a half-written database that SQLite would then open
    and quietly report as empty.
    """
    if os.path.exists(path):
        LAST_DOWNLOAD.update(attempted=False, ok=True, error="already present")
        return True
    url = url or DOWNLOAD_URL
    if not url:
        LAST_DOWNLOAD.update(attempted=False, ok=False, error="POI_DB_URL is not set")
        return False

    import shutil
    import tempfile
    import urllib.request

    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".write-probe")
        with open(probe, "w") as check:
            check.write("")
        os.remove(probe)
    except OSError as problem:
        LAST_DOWNLOAD.update(
            attempted=False, ok=False,
            error="cannot write to {}: {}".format(directory, problem),
        )
        return False

    handle, staging = tempfile.mkstemp(dir=directory, suffix=".part")
    os.close(handle)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Doiamo/0.1"})
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            with open(staging, "wb") as out:
                shutil.copyfileobj(response, out)
        if os.path.getsize(staging) < 1024:
            raise OSError("downloaded file is too small to be a database")
        os.replace(staging, path)
        LAST_DOWNLOAD.update(attempted=True, ok=True, error=None)
        return True
    except Exception as problem:
        if os.path.exists(staging):
            os.remove(staging)
        LAST_DOWNLOAD.update(
            attempted=True, ok=False,
            error="{}: {}".format(type(problem).__name__, problem),
        )
        return False
