"""Points of interest along a route."""
import json
import math

import httpx
import pytest

from backend import poi


def circle(center_lon=9.19, center_lat=45.4642, radius_deg=0.014, points=180):
    return [
        [
            center_lon + radius_deg * math.cos(2 * math.pi * i / points),
            center_lat + radius_deg * math.sin(2 * math.pi * i / points) * 0.7,
            100.0,
        ]
        for i in range(points + 1)
    ]


def test_classify_recognises_each_category():
    assert poi.classify({"amenity": "drinking_water"}) == "water"
    assert poi.classify({"man_made": "water_tap"}) == "water"
    assert poi.classify({"amenity": "toilets"}) == "toilets"
    assert poi.classify({"leisure": "park"}) == "green"
    assert poi.classify({"leisure": "garden"}) == "green"
    assert poi.classify({"tourism": "viewpoint"}) == "viewpoint"
    assert poi.classify({"natural": "peak"}) == "viewpoint"
    assert poi.classify({"tourism": "artwork"}) == "art"
    assert poi.classify({"historic": "memorial"}) == "monument"
    assert poi.classify({"historic": "building"}) == "monument"
    assert poi.classify({"tourism": "attraction"}) == "monument"
    assert poi.classify({"shop": "bicycle"}) == "bike"


def test_classify_takes_historic_broadly_but_not_a_negation():
    assert poi.classify({"historic": "yes"}) == "monument"
    assert poi.classify({"historic": "no"}) is None


def test_classify_ignores_everything_else():
    assert poi.classify({"shop": "bakery"}) is None
    assert poi.classify({}) is None


def test_corridor_thins_the_geometry_and_stays_bounded():
    routes = [circle(center_lon=9.19 + i * 0.01) for i in range(5)]
    points = poi.corridor(routes)
    assert len(points) <= poi.MAX_CORRIDOR_POINTS
    assert all(len(p) == 2 for p in points)


def test_corridor_covers_every_route():
    routes = [circle(center_lon=9.19), circle(center_lon=9.40)]
    lons = [lon for _, lon in poi.corridor(routes)]
    assert min(lons) < 9.25 < max(lons)


def test_build_query_carries_radius_and_the_corridor():
    query = poi.build_query([(45.47, 9.17), (45.46, 9.19)], 75)
    assert "around:75" in query
    assert "45.47000,9.17000" in query
    assert "out center" in query


def test_build_query_asks_for_ways_and_relations_not_just_nodes():
    """A park, a palazzo or a large monument is a way or a relation in OSM.
    Asking only for nodes silently drops about a third of everything."""
    query = poi.build_query([(45.47, 9.17)], 75)
    assert "nwr(around:" in query
    assert "node(around:" not in query
    assert "out center" in query


def test_build_query_is_empty_without_points():
    assert poi.build_query([], 75) == ""


def _transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _fast_overpass(monkeypatch):
    """Shrink the retry budget so failure tests do not spend the real one.

    The production values are asserted separately, in
    test_the_retry_budget_is_bounded.
    """
    monkeypatch.setattr(poi, "OVERPASS_BUDGET_S", 3.0)
    monkeypatch.setattr(poi, "OVERPASS_ATTEMPT_TIMEOUT_S", 1.0)
    monkeypatch.setattr(poi, "OVERPASS_RETRY_DELAY_S", 0.05)


@pytest.mark.asyncio
async def test_fetch_identifies_itself():
    """Overpass answers 406 to a default library user agent — this is the
    regression guard for that."""
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={"elements": []})

    async with _transport(handler) as client:
        await poi.fetch([(45.47, 9.17)], client=client)

    assert seen["ua"] == poi.USER_AGENT
    assert "python-httpx" not in seen["ua"]


@pytest.mark.asyncio
async def test_fetch_parses_elements_and_drops_unknown_tags():
    payload = {"elements": [
        {"type": "node", "id": 1, "lat": 45.47, "lon": 9.17,
         "tags": {"amenity": "drinking_water"}},
        {"type": "node", "id": 2, "lat": 45.46, "lon": 9.18,
         "tags": {"historic": "monument", "name": "Statua"}},
        {"type": "node", "id": 3, "lat": 45.46, "lon": 9.19,
         "tags": {"shop": "bakery"}},
        {"type": "way", "id": 4, "tags": {"amenity": "toilets"}},   # no centre
    ]}

    async with _transport(lambda r: httpx.Response(200, json=payload)) as client:
        found, ok = await poi.fetch([(45.47, 9.17)], client=client)

    assert ok is True
    assert [p["kind"] for p in found] == ["water", "monument"]
    assert found[1]["name"] == "Statua"
    assert found[0]["name"] is None


@pytest.mark.asyncio
async def test_fetch_reads_the_centre_of_ways_and_relations():
    payload = {"elements": [
        {"type": "way", "id": 10, "center": {"lat": 45.4725, "lon": 9.1745},
         "tags": {"leisure": "park", "name": "Parco Sempione"}},
        {"type": "relation", "id": 11, "center": {"lat": 45.40, "lon": 9.30},
         "tags": {"historic": "castle", "name": "Castello"}},
    ]}
    async with _transport(lambda r: httpx.Response(200, json=payload)) as client:
        found, _ = await poi.fetch([(45.47, 9.17)], client=client)

    assert [p["kind"] for p in found] == ["green", "monument"]
    assert found[0]["lat"] == 45.4725
    assert found[1]["name"] == "Castello"


@pytest.mark.asyncio
async def test_a_node_and_a_way_may_share_an_id():
    """OSM ids are only unique within a type, so the dedupe key needs both."""
    payload = {"elements": [
        {"type": "node", "id": 7, "lat": 45.47, "lon": 9.17, "tags": {"amenity": "toilets"}},
        {"type": "way", "id": 7, "center": {"lat": 45.50, "lon": 9.20},
         "tags": {"leisure": "park"}},
    ]}
    async with _transport(lambda r: httpx.Response(200, json=payload)) as client:
        found, _ = await poi.fetch([(45.47, 9.17)], client=client)
    assert len(found) == 2
    assert {p["id"] for p in found} == {"node/7", "way/7"}


@pytest.mark.asyncio
async def test_the_same_place_mapped_twice_yields_one_pin():
    """A park is often a relation and its member ways all at once."""
    payload = {"elements": [
        {"type": "relation", "id": 1, "center": {"lat": 45.4725, "lon": 9.1745},
         "tags": {"leisure": "park", "name": "Sempione"}},
        {"type": "way", "id": 2, "center": {"lat": 45.47252, "lon": 9.17451},
         "tags": {"leisure": "park", "name": "Sempione"}},
    ]}
    async with _transport(lambda r: httpx.Response(200, json=payload)) as client:
        found, _ = await poi.fetch([(45.47, 9.17)], client=client)
    assert len(found) == 1


@pytest.mark.asyncio
async def test_fetch_falls_back_to_the_mirror_when_the_first_host_is_busy():
    """Overpass regularly answers 'server too busy'."""
    hits = []

    def handler(request):
        hits.append(str(request.url))
        if len(hits) == 1:
            return httpx.Response(504, text="too busy")
        return httpx.Response(200, json={"elements": [
            {"type": "node", "id": 1, "lat": 45.47, "lon": 9.17,
             "tags": {"amenity": "drinking_water"}},
        ]})

    async with _transport(handler) as client:
        found, ok = await poi.fetch([(45.47, 9.17)], client=client)

    assert ok is True
    assert len(found) == 1
    assert len(hits) == 2
    assert hits[0] != hits[1]


@pytest.mark.asyncio
async def test_a_failed_lookup_reports_unavailable_not_empty():
    """Every mirror failing is an outage, not an empty neighbourhood."""
    async with _transport(lambda r: httpx.Response(406, text="Not Acceptable")) as client:
        found, ok = await poi.fetch([(45.47, 9.17)], client=client)
    assert found == []
    assert ok is False


@pytest.mark.asyncio
async def test_a_timeout_reports_unavailable():
    def handler(request):
        raise httpx.ConnectTimeout("too slow")

    async with _transport(handler) as client:
        found, ok = await poi.fetch([(45.47, 9.17)], client=client)
    assert (found, ok) == ([], False)


def test_assign_matches_only_the_routes_a_poi_is_near():
    near_lon, far_lon = 9.19, 9.60
    routes = {"near": circle(center_lon=near_lon), "far": circle(center_lon=far_lon)}
    fountain = {"id": "1", "kind": "water", "name": None,
                "lat": 45.4642, "lon": near_lon + 0.014}

    tagged, counts = poi.assign([fountain], routes)
    assert len(tagged) == 1
    assert tagged[0]["routes"] == ["near"]
    assert counts["near"]["water"] == 1
    assert counts["far"]["water"] == 0


def test_assign_drops_a_poi_near_nothing():
    routes = {"only": circle()}
    tagged, counts = poi.assign(
        [{"id": "1", "kind": "water", "name": None, "lat": 40.0, "lon": 14.0}], routes
    )
    assert tagged == []
    assert counts["only"]["water"] == 0


def test_assign_counts_every_kind_key_even_at_zero():
    routes = {"a": circle()}
    _, counts = poi.assign([], routes)
    assert set(counts["a"]) == set(poi.KINDS)


# --- scoring ---------------------------------------------------------------

def test_water_is_scored_absolutely_not_against_the_others():
    """A 20 km run with no fountain is bad however good the alternatives are."""
    counts = {"dry": {"water": 0}, "wet": {"water": 7}}
    distances = {"dry": 20000.0, "wet": 20000.0}
    scores = poi.score(counts, distances, "running")
    assert scores["dry"]["water"] == 0.0
    assert scores["wet"]["water"] == 1.0


def test_water_target_scales_with_distance():
    # One fountain every 3 km counts as covered, so 2 on 6 km is full marks
    # while the same 2 on 30 km is not.
    short = poi.score({"a": {"water": 2}}, {"a": 6000.0}, "running")["a"]["water"]
    long_ = poi.score({"a": {"water": 2}}, {"a": 30000.0}, "running")["a"]["water"]
    assert short == 1.0
    assert long_ < 0.3


def test_monuments_and_nature_are_scored_separately():
    """The bug this replaces: summing them gave a route with forty monuments
    and no trees the same score as one with forty parks and no monuments."""
    counts = {
        "stone": {"monument": 40, "art": 10, "green": 0, "viewpoint": 0},
        "trees": {"monument": 1, "art": 0, "green": 35, "viewpoint": 4},
    }
    distances = {"stone": 10000.0, "trees": 10000.0}
    scores = poi.score(counts, distances, "running", "both")

    # Absolute, not relative: forty monuments is plenty and scores full marks,
    # while a single one is nearly nothing rather than exactly nothing — under
    # the old ranking the lowest of any pair was forced to zero whatever it had.
    assert scores["stone"]["monuments"] == 1.0
    assert scores["stone"]["nature"] == 0.0
    assert scores["trees"]["nature"] == 1.0
    assert scores["trees"]["monuments"] < 0.1


def test_a_preference_actually_separates_the_routes():
    counts = {
        "stone": {"water": 3, "monument": 40, "art": 10},
        "trees": {"water": 3, "green": 35, "viewpoint": 4},
    }
    distances = {"stone": 10000.0, "trees": 10000.0}

    wants_stone = poi.score(counts, distances, "running", "monuments")
    wants_trees = poi.score(counts, distances, "running", "nature")

    assert wants_stone["stone"]["bonus"] > wants_stone["trees"]["bonus"]
    assert wants_trees["trees"]["bonus"] > wants_trees["stone"]["bonus"]
    # And the gap is worth having, not a rounding difference.
    assert wants_stone["stone"]["bonus"] - wants_stone["trees"]["bonus"] > 0.2


def test_both_means_best_at_either_not_the_average():
    """A route full of parks should not be marked down for having no statues."""
    counts = {
        "stone": {"water": 3, "monument": 40},
        "trees": {"water": 3, "green": 40},
        "dull": {"water": 3},
    }
    distances = {k: 10000.0 for k in counts}
    scores = poi.score(counts, distances, "running", "both")

    assert scores["stone"]["sights"] == 1.0
    assert scores["trees"]["sights"] == 1.0
    assert scores["dull"]["sights"] == 0.0


def test_sights_none_leaves_only_water():
    counts = {
        "stone": {"water": 0, "monument": 40},
        "wet": {"water": 9, "monument": 0},
    }
    distances = {"stone": 10000.0, "wet": 10000.0}
    scores = poi.score(counts, distances, "running", "none")

    assert scores["stone"]["sights"] is None
    assert scores["wet"]["bonus"] > scores["stone"]["bonus"]
    assert scores["wet"]["bonus"] == scores["wet"]["water"]


def test_cycling_weights_sights_over_water_and_running_the_reverse():
    counts = {"a": {"water": 9, "monument": 0}, "b": {"water": 0, "monument": 30}}
    distances = {"a": 10000.0, "b": 10000.0}
    run = poi.score(counts, distances, "running", "monuments")
    bike = poi.score(counts, distances, "cycling", "monuments")
    assert run["a"]["bonus"] > bike["a"]["bonus"]     # runner values the water more
    assert bike["b"]["bonus"] > run["b"]["bonus"]     # cyclist values the sights more


def test_a_stated_preference_moves_the_score_more_than_water_alone():
    """Asking someone what they want and barely acting on it is worse than
    not asking."""
    from backend import config

    with_sights = poi.blend(0.70, 1.0, "monuments") - poi.blend(0.70, 0.0, "monuments")
    water_only = poi.blend(0.70, 1.0, "none") - poi.blend(0.70, 0.0, "none")
    assert round(with_sights, 4) == config.POI_SHARE
    assert round(water_only, 4) == config.POI_SHARE_WATER_ONLY
    assert with_sights > water_only


def test_blend_moves_the_score_but_does_not_dominate_it():
    from backend import config

    best = poi.blend(0.70, 1.0)
    worst = poi.blend(0.70, 0.0)
    assert best > 0.70 > worst
    # POIs arrive late, so they adjust the ranking rather than deciding it.
    assert round(best - worst, 4) == config.POI_SHARE
    assert config.POI_SHARE < 0.4


def test_blend_stays_inside_zero_and_one():
    assert poi.blend(1.0, 1.0) <= 1.0
    assert poi.blend(0.0, 0.0) >= 0.0


# --- query cost ------------------------------------------------------------

def test_corridor_circles_overlap_so_nothing_is_missed_between_them():
    """The corridor is a string of circles along the route. If the spacing
    exceeds twice the radius, the gaps between them are simply not searched —
    which is how a 10 km loop ended up with half of it invisible."""
    from backend import geo

    routes = [circle(radius_deg=0.045)]          # roughly a 10 km loop
    kept = poi.corridor(routes)
    assert len(kept) > 30

    gaps = [
        geo.haversine_m(a[1], a[0], b[1], b[0])
        for a, b in zip(kept, kept[1:])
    ]
    assert max(gaps) <= poi.DEFAULT_RADIUS_M * 2


def test_corridor_spacing_adapts_to_route_length():
    """A fixed sample count is too dense for a short route and too sparse for
    a long one."""
    short = poi.corridor([circle(radius_deg=0.004)])
    long_ = poi.corridor([circle(radius_deg=0.045)])
    assert len(long_) > len(short) * 3


def test_corridor_widens_the_spacing_rather_than_blowing_the_query_up():
    routes = [circle(radius_deg=0.14 + i * 0.001) for i in range(5)]   # ~100 km each
    kept = poi.corridor(routes)
    assert len(kept) <= poi.MAX_CORRIDOR_POINTS


def test_query_stays_cheap_enough_for_overpass_to_accept():
    """Overpass evaluates `around` once per point per clause, so the cost is
    points x clauses. Covering five 10 km routes needs a few hundred points,
    which leaves room for exactly one clause."""
    routes = [circle(radius_deg=0.021 + i * 0.001) for i in range(5)]
    points = poi.corridor(routes)
    query = poi.build_query(points, poi.DEFAULT_RADIUS_M)
    clauses = query.count("nwr(")
    assert clauses == 1
    assert len(points) * clauses < 320


def test_the_single_clause_still_reaches_every_category():
    query = poi.build_query([(45.47, 9.17)], 75)
    for value in ("drinking_water", "toilets", "park", "viewpoint", "artwork",
                  "bicycle", "monument", "memorial", "castle"):
        assert value in query


# --- how long a user can be made to wait -----------------------------------

@pytest.mark.asyncio
async def test_a_failing_lookup_gives_up_within_the_budget():
    """Retrying makes failure rarer but slower, and the two multiply. Three
    rounds over two mirrors at 25s each was 150 seconds of spinner."""
    import time

    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ConnectTimeout("no answer")

    started = time.monotonic()
    async with _transport(handler) as client:
        found, ok = await poi.fetch([(45.47, 9.17)], client=client)
    elapsed = time.monotonic() - started

    assert (found, ok) == ([], False)
    assert elapsed < 5                    # bounded, not 150 seconds
    assert len(attempts) > 1              # and it did keep trying


def test_the_retry_budget_is_bounded():
    """Asserted against the real module values, not the shrunk test ones."""
    import importlib

    fresh = importlib.reload(poi)
    try:
        assert fresh.OVERPASS_BUDGET_S <= 30
        assert fresh.OVERPASS_ATTEMPT_TIMEOUT_S < fresh.OVERPASS_BUDGET_S
    finally:
        importlib.reload(poi)


@pytest.mark.asyncio
async def test_it_still_retries_within_the_budget():
    """A bounded budget must not mean a single attempt."""
    hits = []

    def handler(request):
        hits.append(str(request.url))
        if len(hits) == 1:
            return httpx.Response(504, text="too busy")
        return httpx.Response(200, json={"elements": [
            {"type": "node", "id": 1, "lat": 45.47, "lon": 9.17,
             "tags": {"amenity": "drinking_water"}}]})

    async with _transport(handler) as client:
        found, ok = await poi.fetch([(45.47, 9.17)], client=client)

    assert ok is True and len(found) == 1
    assert len(hits) == 2
    assert hits[0] != hits[1]      # moved to the other mirror


def test_assign_does_not_miss_points_east_or_west():
    """The grid cells have to be the match radius in metres on both axes. A
    degree of longitude is ~70% of a degree of latitude in Milan, so a single
    degree-based cell size lets a POI due east fall outside the 3x3 search."""
    from backend import geo

    # A dead-straight north-south route, so anything found is found sideways.
    route = [[9.19, 45.46 + i * 0.0002, 100.0] for i in range(60)]
    routes = {"line": route}

    mid_lat = route[len(route) // 2][1]
    found_at = []
    for offset_m in (10, 40, 70, 95):
        deg = offset_m / (111_320 * math.cos(math.radians(mid_lat)))
        for direction in (1, -1):
            poi_item = {"id": "p", "kind": "water", "name": None,
                        "lat": mid_lat, "lon": 9.19 + deg * direction}
            tagged, counts = poi.assign([poi_item], routes)
            actual = geo.haversine_m(9.19, mid_lat, poi_item["lon"], poi_item["lat"])
            found_at.append((round(actual), len(tagged) == 1))

    missed = [d for d, hit in found_at if not hit]
    assert not missed, "missed POIs at {} m, all within {} m".format(
        missed, poi.MATCH_RADIUS_M
    )



def test_plenty_of_monuments_is_plenty_and_stops_buying_rank():
    """Both routes pass a fair number, so neither wins on sights and something
    that actually differs — traffic, air — gets to decide."""
    counts = {
        "busy": {"monument": 60, "art": 20},
        "quiet": {"monument": 30, "art": 10},
    }
    distances = {"busy": 10000.0, "quiet": 10000.0}

    scores = poi.score(counts, distances, "running", "monuments")

    assert scores["busy"]["sights"] == scores["quiet"]["sights"] == 1.0


def test_a_bare_route_still_loses_to_one_full_of_them():
    counts = {"rich": {"monument": 40}, "bare": {"monument": 0}}
    distances = {"rich": 10000.0, "bare": 10000.0}

    scores = poi.score(counts, distances, "running", "monuments")

    assert scores["rich"]["sights"] == 1.0
    assert scores["bare"]["sights"] == 0.0


# --- where the middle of a place is ----------------------------------------

@pytest.mark.parametrize("tags,expected", [
    ({"place": "city", "name": "Milano"}, "city"),
    ({"place": "town", "name": "Monza"}, "town"),
    ({"place": "village", "name": "Pino"}, "village"),
    ({"place": "suburb", "name": "Centro Storico"}, "historic"),
    ({"place": "quarter", "name": "Centro"}, "historic"),
    ({"place": "suburb", "name": "Old Town"}, "historic"),
    ({"place": "suburb", "name": "Navigli"}, None),
    ({"place": "suburb"}, None),
    ({"amenity": "drinking_water"}, None),
])
def test_a_centre_is_recognised_without_swallowing_every_district(tags, expected):
    assert poi.classify_place(tags) == expected


def test_a_centre_is_never_counted_as_a_point_of_interest():
    """Counted as one, every urban route would report a monument it does not
    pass — and the two are stored in different tables for the same reason."""
    for tags in ({"place": "city", "name": "Milano"},
                 {"place": "suburb", "name": "Centro Storico"}):
        assert poi.classify(tags) is None


def test_the_centre_of_a_village_is_smaller_than_the_centre_of_a_city():
    """2.5 km from the Duomo is still central Milan; 2.5 km from the middle of
    a village is the next village."""
    radii = poi.PLACE_RADIUS_M
    assert radii["village"] < radii["town"] < radii["city"]
    assert poi.PLACE_RANKS["historic"] < poi.PLACE_RANKS["city"]
