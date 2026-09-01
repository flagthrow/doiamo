"""The local POI database: fast, offline, and honest about its edges."""
import sqlite3

import pytest

from backend.poi_store import LocalPoiStore


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "pois.sqlite"
    connection = sqlite3.connect(str(path))
    connection.executescript("""
        CREATE TABLE pois (id TEXT PRIMARY KEY, kind TEXT, name TEXT, lat REAL, lon REAL);
        CREATE VIRTUAL TABLE poi_bbox USING rtree(rowid, min_lat, max_lat, min_lon, max_lon);
        CREATE TABLE coverage (source TEXT, min_lat REAL, min_lon REAL,
                               max_lat REAL, max_lon REAL, built_at TEXT, count INTEGER);
    """)
    rows = [
        ("node/1", "water", "Vedovella", 45.4700, 9.1900),
        ("way/2", "green", "Parco", 45.4750, 9.1950),
        ("node/3", "monument", None, 45.5500, 9.3000),   # far away
    ]
    connection.executemany("INSERT INTO pois VALUES (?,?,?,?,?)", rows)
    connection.execute(
        "INSERT INTO poi_bbox SELECT p.rowid, p.lat, p.lat, p.lon, p.lon FROM pois p")
    connection.execute("INSERT INTO coverage VALUES ('test',45.30,9.00,45.60,9.40,'now',3)")
    connection.commit()
    connection.close()
    return LocalPoiStore(str(path))


def test_reads_its_coverage_and_count(store):
    assert store.available is True
    assert store.count == 3


def test_a_missing_database_is_simply_unavailable(tmp_path):
    absent = LocalPoiStore(str(tmp_path / "nothing.sqlite"))
    assert absent.available is False
    assert absent.covers([(45.47, 9.19)]) is False
    assert absent.near([(45.47, 9.19)], 100) == []


def test_covers_a_route_well_inside_the_extract(store):
    assert store.covers([(45.47, 9.19), (45.48, 9.20)]) is True


def test_does_not_claim_a_route_outside_the_extract(store):
    assert store.covers([(48.85, 2.35)]) is False


def test_does_not_claim_a_route_that_runs_off_the_edge(store):
    """Half-covering a route would silently drop its POIs on the far side,
    which is worse than being slow."""
    assert store.covers([(45.47, 9.19), (45.61, 9.19)]) is False


def test_refuses_a_route_inside_the_margin(store):
    """A point technically inside but within the safety margin still falls
    back, because the extract's own edge is ragged."""
    assert store.covers([(45.305, 9.19)]) is False


def test_near_returns_what_is_in_the_corridor_box(store):
    rows = store.near([(45.4700, 9.1900), (45.4750, 9.1950)], 100)
    ids = {row["id"] for row in rows}
    assert "node/1" in ids and "way/2" in ids
    assert "node/3" not in ids          # 8 km away, outside the box


def test_near_shapes_rows_like_the_overpass_path(store):
    """poi.assign consumes both sources, so they have to agree."""
    row = store.near([(45.4700, 9.1900)], 100)[0]
    assert set(row) == {"id", "kind", "name", "lat", "lon"}
    assert isinstance(row["lat"], float)


def test_an_empty_corridor_asks_nothing(store):
    assert store.near([], 100) == []
