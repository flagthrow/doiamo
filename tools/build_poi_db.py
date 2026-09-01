"""Turn an OpenStreetMap extract into a local points-of-interest database.

Overpass is 96% of the time a POI lookup takes — not the network, not our
code, but their server working through a planet-scale index on every request.
It also fails often. Neither is fixable from this side.

The data we actually want is small: seven categories of node, way and relation.
Extracted once from a regional .osm.pbf it fits in a few megabytes of SQLite
with an R-tree index, and answers in milliseconds without asking anyone.

    python -m tools.build_poi_db data/nord-ovest.osm.pbf data/pois.sqlite

Extracts come from https://download.geofabrik.de. Re-run it when you want
fresher data; drinking fountains do not move weekly.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from typing import Optional, Tuple

import osmium

from backend.poi import classify


def _area_centre(area: "osmium.osm.Area") -> Optional[Tuple[float, float]]:
    """A representative point for a park or a palazzo.

    The mean of the outer ring is not the true centroid of a concave shape,
    but it is inside it often enough for a map pin, and it costs nothing.
    """
    total_lat = total_lon = 0.0
    count = 0
    try:
        for ring in area.outer_rings():
            for node in ring:
                if node.location.valid():
                    total_lat += node.location.lat
                    total_lon += node.location.lon
                    count += 1
    except (RuntimeError, osmium.InvalidLocationError):
        return None
    if not count:
        return None
    return total_lat / count, total_lon / count


class Collector(osmium.SimpleHandler):
    def __init__(self, write) -> None:
        super().__init__()
        self._write = write
        self.nodes = 0
        self.areas = 0
        self.bounds = [90.0, 180.0, -90.0, -180.0]   # min_lat, min_lon, max_lat, max_lon

    def _keep(self, identity: str, kind: str, tags, lat: float, lon: float) -> None:
        name = tags.get("name")
        self._write(identity, kind, name, lat, lon)
        self.bounds[0] = min(self.bounds[0], lat)
        self.bounds[1] = min(self.bounds[1], lon)
        self.bounds[2] = max(self.bounds[2], lat)
        self.bounds[3] = max(self.bounds[3], lon)

    def node(self, n) -> None:
        kind = classify(dict(n.tags))
        if kind is None or not n.location.valid():
            return
        self.nodes += 1
        self._keep("node/{}".format(n.id), kind, n.tags, n.location.lat, n.location.lon)

    def area(self, a) -> None:
        kind = classify(dict(a.tags))
        if kind is None:
            return
        centre = _area_centre(a)
        if centre is None:
            return
        self.areas += 1
        # from_way() tells a way-derived area from a relation-derived one.
        source = "way" if a.from_way() else "relation"
        original = a.orig_id()
        self._keep("{}/{}".format(source, original), kind, a.tags, centre[0], centre[1])


SCHEMA = """
CREATE TABLE IF NOT EXISTS pois (
    id    TEXT PRIMARY KEY,
    kind  TEXT NOT NULL,
    name  TEXT,
    lat   REAL NOT NULL,
    lon   REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS poi_bbox USING rtree(
    rowid, min_lat, max_lat, min_lon, max_lon
);
CREATE TABLE IF NOT EXISTS coverage (
    source   TEXT,
    min_lat  REAL, min_lon REAL, max_lat REAL, max_lon REAL,
    built_at TEXT,
    count    INTEGER
);
"""


def build(pbf_path: str, db_path: str) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)

    connection = sqlite3.connect(db_path)
    connection.executescript(SCHEMA)

    rows = []
    seen = set()

    def write(identity, kind, name, lat, lon):
        # A place mapped as both a node and an area appears twice; the first
        # wins, as it does in the live path.
        if identity in seen:
            return
        seen.add(identity)
        rows.append((identity, kind, name, lat, lon))

    handler = Collector(write)
    started = time.monotonic()
    print("reading {} …".format(pbf_path), flush=True)
    handler.apply_file(pbf_path, locations=True, idx="flex_mem")

    print("writing {} rows …".format(len(rows)), flush=True)
    connection.executemany("INSERT OR IGNORE INTO pois VALUES (?,?,?,?,?)", rows)
    connection.execute(
        "INSERT INTO poi_bbox SELECT p.rowid, p.lat, p.lat, p.lon, p.lon FROM pois p"
    )
    connection.execute(
        "INSERT INTO coverage VALUES (?,?,?,?,?,datetime('now'),?)",
        (os.path.basename(pbf_path), *handler.bounds, len(rows)),
    )
    connection.commit()
    connection.execute("ANALYZE")
    connection.close()

    elapsed = time.monotonic() - started
    size_mb = os.path.getsize(db_path) / 1e6
    print(
        "done in {:.0f}s — {} nodes, {} areas, {} rows, {:.1f} MB".format(
            elapsed, handler.nodes, handler.areas, len(rows), size_mb
        )
    )
    print("coverage: {:.3f},{:.3f} .. {:.3f},{:.3f}".format(*handler.bounds))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", help="an .osm.pbf extract, e.g. from Geofabrik")
    parser.add_argument("db", nargs="?", default="data/pois.sqlite")
    args = parser.parse_args()
    if not os.path.exists(args.pbf):
        sys.exit("no such file: {}".format(args.pbf))
    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    build(args.pbf, args.db)


if __name__ == "__main__":
    main()
