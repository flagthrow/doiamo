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
        CREATE TABLE coverage_cells (cell_lat INTEGER, cell_lon INTEGER,
                                     PRIMARY KEY (cell_lat, cell_lon)) WITHOUT ROWID;
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
    connection.execute(
        "INSERT INTO coverage_cells SELECT DISTINCT "
        "CAST(FLOOR(lat/0.1) AS INTEGER), CAST(FLOOR(lon/0.1) AS INTEGER) FROM pois")
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


def test_covers_a_route_where_there_is_data(store):
    assert store.covers([(45.47, 9.19), (45.475, 9.195)]) is True


def test_does_not_claim_a_route_outside_the_extract(store):
    assert store.covers([(48.85, 2.35)]) is False


def test_does_not_claim_a_route_that_runs_off_the_edge(store):
    """Half-covering a route would silently drop its POIs on the far side,
    which is worse than being slow."""
    assert store.covers([(45.47, 9.19), (45.90, 9.19)]) is False


def test_a_bounding_box_would_have_claimed_an_empty_hole(store):
    """The reason coverage is a grid and not a rectangle.

    45.4, 9.3 sits inside the rectangle spanned by the data but has nothing in
    it. A box-based check claimed exactly this shape of gap — Bologna, Verona
    and Parma all fall inside the north-west extract's rectangle and none of
    them are in it."""
    inside_the_box = (45.40, 9.30)
    assert store.near([inside_the_box], 2000) == []
    assert store.covers([inside_the_box]) is False


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


# --- several extracts in one database --------------------------------------

@pytest.fixture
def two_regions(tmp_path):
    """Milan and Rome: two distant boxes with a large gap between them."""
    path = tmp_path / "pois.sqlite"
    connection = sqlite3.connect(str(path))
    connection.executescript("""
        CREATE TABLE pois (id TEXT PRIMARY KEY, kind TEXT, name TEXT, lat REAL, lon REAL);
        CREATE VIRTUAL TABLE poi_bbox USING rtree(rowid, min_lat, max_lat, min_lon, max_lon);
        CREATE TABLE coverage (source TEXT, min_lat REAL, min_lon REAL,
                               max_lat REAL, max_lon REAL, built_at TEXT, count INTEGER);
        CREATE TABLE coverage_cells (cell_lat INTEGER, cell_lon INTEGER,
                                     PRIMARY KEY (cell_lat, cell_lon)) WITHOUT ROWID;
    """)
    connection.executemany("INSERT INTO pois VALUES (?,?,?,?,?)", [
        ("node/1", "water", None, 45.4700, 9.1900),
        ("node/2", "water", None, 41.9000, 12.4900),
    ])
    connection.execute(
        "INSERT INTO poi_bbox SELECT p.rowid, p.lat, p.lat, p.lon, p.lon FROM pois p")
    connection.executemany("INSERT INTO coverage VALUES (?,?,?,?,?,'now',?)", [
        ("nord-ovest", 44.00, 6.50, 46.60, 11.40, 1),
        ("centro", 41.30, 11.40, 43.60, 14.00, 1),
    ])
    connection.execute(
        "INSERT INTO coverage_cells SELECT DISTINCT "
        "CAST(FLOOR(lat/0.1) AS INTEGER), CAST(FLOOR(lon/0.1) AS INTEGER) FROM pois")
    connection.commit()
    connection.close()
    return LocalPoiStore(str(path))


def test_counts_add_up_across_extracts(two_regions):
    assert two_regions.count == 2
    assert two_regions.cells == 2


def test_each_region_is_covered(two_regions):
    # Each fixture has one POI per region, so coverage is that POI's own cell.
    assert two_regions.covers([(45.47, 9.19), (45.48, 9.18)]) is True     # Milan
    assert two_regions.covers([(41.90, 12.49)]) is True                   # Rome


def test_the_gap_between_two_regions_is_not_claimed(two_regions):
    """Merging the boxes would swallow Bologna, where there is no data — and a
    route there would come back empty with nothing saying why."""
    assert two_regions.covers([(44.49, 11.34)]) is False                  # Bologna


def test_a_route_spanning_the_gap_is_not_claimed(two_regions):
    assert two_regions.covers([(45.47, 9.19), (44.49, 11.34)]) is False


def test_each_region_keeps_its_own_data(two_regions):
    assert two_regions.near([(45.47, 9.19)], 500)[0]["id"] == "node/1"
    assert two_regions.near([(41.90, 12.49)], 500)[0]["id"] == "node/2"


def test_the_default_path_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    """A relative default fails silently: the store reports unavailable and
    everything falls back to Overpass, which looks identical to having no
    database at all."""
    import os

    from backend import poi_store

    monkeypatch.chdir(tmp_path)
    assert os.path.isabs(poi_store.DEFAULT_PATH)
    assert poi_store.DEFAULT_PATH.endswith(os.path.join("data", "pois.sqlite"))


# --- fetching the database on a fresh deployment ---------------------------

def test_download_is_skipped_when_the_file_is_already_there(tmp_path):
    from backend.poi_store import ensure_downloaded

    existing = tmp_path / "pois.sqlite"
    existing.write_bytes(b"x" * 4096)
    assert ensure_downloaded(str(existing), url="http://unused.invalid") is True
    assert existing.read_bytes() == b"x" * 4096          # untouched


def test_no_url_means_no_download(tmp_path):
    from backend.poi_store import ensure_downloaded

    assert ensure_downloaded(str(tmp_path / "pois.sqlite"), url="") is False


def test_a_failed_download_leaves_nothing_behind(tmp_path):
    """A half-written file would be opened by SQLite and reported as empty —
    coverage would claim nothing and every route would fall back anyway, but
    silently and for the wrong reason."""
    from backend.poi_store import ensure_downloaded

    target = tmp_path / "pois.sqlite"
    assert ensure_downloaded(str(target), url="http://127.0.0.1:9/nope") is False
    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_a_truncated_download_is_rejected(tmp_path, monkeypatch):
    import backend.poi_store as store

    class FakeResponse:
        def read(self, *a):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: FakeResponse()
    )
    target = tmp_path / "pois.sqlite"
    assert store.ensure_downloaded(str(target), url="http://example.invalid/db") is False
    assert not target.exists()


def test_a_real_download_lands_at_the_target(tmp_path):
    """Served over a real socket, so the whole path is exercised."""
    import http.server
    import threading

    from backend.poi_store import ensure_downloaded

    payload = b"SQLite format 3\x00" + b"y" * 5000
    source = tmp_path / "served.sqlite"
    source.write_bytes(payload)

    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    import os
    os.chdir(tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        target = tmp_path / "fetched.sqlite"
        url = "http://127.0.0.1:{}/served.sqlite".format(server.server_port)
        assert ensure_downloaded(str(target), url=url) is True
        assert target.read_bytes() == payload
    finally:
        server.shutdown()


def test_healthz_can_explain_a_missing_database(tmp_path, monkeypatch):
    """A deployment that silently fell back to Overpass looks identical to a
    working one until someone notices the POIs are gone. It has to say why."""
    import importlib

    import backend.poi_store as store

    monkeypatch.setenv("POI_DB", str(tmp_path / "absent" / "pois.sqlite"))
    monkeypatch.delenv("POI_DB_URL", raising=False)
    importlib.reload(store)

    assert store.ensure_downloaded() is False
    assert store.LAST_DOWNLOAD["error"] == "POI_DB_URL is not set"


def test_a_read_only_directory_is_reported_not_swallowed(tmp_path, monkeypatch):
    import importlib
    import os

    import backend.poi_store as store

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        monkeypatch.setenv("POI_DB", str(locked / "sub" / "pois.sqlite"))
        monkeypatch.setenv("POI_DB_URL", "http://example.invalid/db")
        importlib.reload(store)
        assert store.ensure_downloaded() is False
        assert "cannot write" in str(store.LAST_DOWNLOAD["error"])
    finally:
        os.chmod(locked, 0o700)
        importlib.reload(store)


# --- finding the middle of town --------------------------------------------

def _with_places(tmp_path, rows):
    path = str(tmp_path / "places.sqlite")
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE places (id TEXT PRIMARY KEY, kind TEXT, name TEXT,
                             lat REAL, lon REAL);
        CREATE VIRTUAL TABLE place_bbox USING rtree(
            rowid, min_lat, max_lat, min_lon, max_lon);
        CREATE TABLE coverage (source TEXT, min_lat REAL, min_lon REAL,
                               max_lat REAL, max_lon REAL, built_at TEXT,
                               count INTEGER);
        CREATE TABLE coverage_cells (cell_lat INTEGER, cell_lon INTEGER);
        """
    )
    connection.executemany("INSERT INTO places VALUES (?,?,?,?,?)", rows)
    connection.execute(
        "INSERT INTO place_bbox SELECT p.rowid, p.lat, p.lat, p.lon, p.lon FROM places p"
    )
    connection.commit()
    connection.close()
    return LocalPoiStore(path)


def test_a_database_without_places_loses_the_feature_not_the_search(tmp_path):
    """A deployment still serving last week's asset must degrade quietly."""
    path = str(tmp_path / "old.sqlite")
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE pois (id TEXT PRIMARY KEY, kind TEXT, name TEXT,
                           lat REAL, lon REAL);
        CREATE TABLE coverage (source TEXT, min_lat REAL, min_lon REAL,
                               max_lat REAL, max_lon REAL, built_at TEXT,
                               count INTEGER);
        CREATE TABLE coverage_cells (cell_lat INTEGER, cell_lon INTEGER);
        """
    )
    connection.commit()
    connection.close()

    assert LocalPoiStore(path).nearest_place(45.4642, 9.19) is None


def test_the_nearest_centre_is_found(tmp_path):
    store = _with_places(tmp_path, [
        ("node/1", "city", "Milano", 45.4642, 9.1900),
        ("node/2", "city", "Torino", 45.0703, 7.6869),
    ])

    found = store.nearest_place(45.47, 9.20)

    assert found["name"] == "Milano"
    assert found["radius_m"] == 2500


def test_a_named_centro_storico_beats_the_city_node(tmp_path):
    """Standing inside one, it is the thing people mean by "in centro" — even
    though the city node sits a little closer."""
    store = _with_places(tmp_path, [
        ("node/1", "city", "Bologna", 44.4949, 11.3426),
        ("node/2", "historic", "Centro Storico", 44.4940, 11.3450),
    ])

    found = store.nearest_place(44.4949, 11.3426)

    assert found["kind"] == "historic"
    assert found["radius_m"] < 2500


def test_a_centro_storico_in_another_town_does_not_win(tmp_path):
    """Ranking historic nodes above city nodes outright answered "Milano
    Duomo" with a centro storico five kilometres away in Cantalupa. Rank only
    settles a tie between nodes describing the same place."""
    store = _with_places(tmp_path, [
        ("node/1", "city", "Milano", 45.4642, 9.1900),
        ("node/2", "historic", "Cantalupa Centro Storico", 45.5100, 9.1600),
    ])

    found = store.nearest_place(45.4642, 9.1900)

    assert found["name"] == "Milano"


def test_a_centre_on_the_other_side_of_the_country_is_not_your_centre(tmp_path):
    store = _with_places(tmp_path, [("node/1", "city", "Milano", 45.4642, 9.19)])
    assert store.nearest_place(41.9028, 12.4964) is None
