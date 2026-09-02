import math

from backend import candidates, config, geo
from backend.models import SearchRequest, WeatherContext
from backend.routing.base import RawRoute


def make_route(
    center_lon=9.19,
    center_lat=45.4642,
    radius_deg=0.014,
    elevation=lambda i: 120.0,
    distance_m=10000.0,
    surface=None,
    waytype=None,
    points=180,
):
    """A synthetic circular loop, so scoring can be tested without the network."""
    coords = []
    for i in range(points + 1):
        angle = 2 * math.pi * i / points
        coords.append([
            center_lon + radius_deg * math.cos(angle),
            center_lat + radius_deg * math.sin(angle) * 0.7,
            elevation(i),
        ])
    return RawRoute(
        coordinates=coords,
        distance_m=distance_m,
        duration_s=distance_m / 2.8,
        surface=surface if surface is not None else {3: distance_m},
        waytype=waytype if waytype is not None else {3: distance_m},
        seed=1,
    )


def request(**kwargs):
    base = dict(lat=45.4642, lon=9.19, distance_km=10.0, elevation_gain_m=150.0,
                sport="running", surface="asphalt")
    base.update(kwargs)
    return SearchRequest(**base)


CALM = WeatherContext(wind_speed_kmh=0.0, wind_direction_deg=0.0)


def test_build_seeds_varies_both_seed_and_waypoint_count():
    seeds = candidates.build_seeds(10)
    assert len(seeds) == 10
    assert len({s for s, _ in seeds}) == 10
    assert len({p for _, p in seeds}) > 1


def test_surface_score_follows_the_preference():
    assert candidates._surface_score(1.0, "asphalt") == 1.0
    assert candidates._surface_score(0.0, "asphalt") == 0.0
    assert candidates._surface_score(0.0, "trail") == 1.0
    assert candidates._surface_score(1.0, "trail") == 0.0
    # "mixed" peaks in the middle rather than at either extreme.
    assert candidates._surface_score(0.5, "mixed") == 1.0
    assert candidates._surface_score(1.0, "mixed") == 0.0


def test_distance_score_falls_off_outside_tolerance():
    assert candidates._distance_score(10000, 10000) == 1.0
    assert 0.0 < candidates._distance_score(11000, 10000) < 1.0
    assert candidates._distance_score(14000, 10000) == 0.0


def test_gain_score_has_an_absolute_floor_for_flat_cities():
    # Asking for 50 m of climb in Milan must not zero out every candidate that
    # comes back 20 m off.
    assert candidates._gain_score(70, 50) > 0.6


def test_asphalt_request_ranks_the_paved_loop_first():
    # Different centres, or the duplicate filter merges them before scoring.
    paved = make_route(surface={3: 10000.0})
    dirt = make_route(surface={11: 10000.0}, center_lon=9.26)
    routes, _, _ = candidates.rank([dirt, paved], request(surface="asphalt"), CALM, {})
    assert len(routes) == 2
    assert routes[0].paved_share > routes[1].paved_share


def test_trail_request_flips_the_order():
    paved = make_route(surface={3: 10000.0})
    dirt = make_route(surface={11: 10000.0}, center_lon=9.26)
    routes, _, _ = candidates.rank([paved, dirt], request(surface="trail"), CALM, {})
    assert routes[0].paved_share < routes[1].paved_share


def test_cycling_penalises_traffic_harder_than_running():
    quiet = make_route(waytype={6: 10000.0})                        # cycleway
    # Kept under BIG_ROAD_REJECT_SHARE: this test is about how the two sports
    # weight traffic, and a route over the ceiling never reaches the scorer.
    busy = make_route(waytype={1: 2500.0, 3: 7500.0}, center_lon=9.26)

    run, _, _ = candidates.rank([quiet, busy], request(sport="running"), CALM, {})
    bike, _, _ = candidates.rank([quiet, busy], request(sport="cycling"), CALM, {})

    run_gap = run[0].scores.total - run[1].scores.total
    bike_gap = bike[0].scores.total - bike[1].scores.total
    assert bike_gap > run_gap


def test_steps_are_penalised_and_sink_the_route():
    clean = make_route(waytype={3: 10000.0})
    stepped = make_route(
        waytype={3: 8000.0, config.STEPS_WAYTYPE: 2000.0}, center_lon=9.26
    )
    routes, _, _ = candidates.rank([stepped, clean], request(sport="cycling"), CALM, {})
    assert routes[0].step_distance_m == 0.0
    assert routes[1].step_distance_m == 2000.0


def test_candidates_far_off_the_requested_distance_are_dropped():
    right = make_route(distance_m=10000.0, radius_deg=0.014)
    wrong = make_route(distance_m=25000.0, radius_deg=0.030)
    routes, _, notices = candidates.rank([right, wrong], request(distance_km=10.0), CALM, {})
    assert len(routes) == 1
    assert "no_exact_distance_match" not in notices


def test_a_loose_match_still_beats_an_empty_page():
    # Nothing near the target: return the closest rather than nothing at all.
    wrong = make_route(distance_m=25000.0, radius_deg=0.030)
    routes, _, notices = candidates.rank([wrong], request(distance_km=10.0), CALM, {})
    assert len(routes) == 1
    assert "no_exact_distance_match" in notices


def test_near_identical_loops_are_deduplicated():
    a = make_route(radius_deg=0.014)
    b = make_route(radius_deg=0.01401)   # same loop, different seed
    routes, _, _ = candidates.rank([a, b], request(), CALM, {})
    assert len(routes) == 1


def test_air_is_context_only_when_it_does_not_vary():
    flat_air = {
        candidates.grid_key(45.4642, 9.19): {"european_aqi": 42.0, "pm2_5": 11.0},
    }
    routes = [make_route(radius_deg=0.014), make_route(radius_deg=0.020, distance_m=11000.0)]
    _, air, _ = candidates.rank(routes, request(), CALM, flat_air)
    assert air.european_aqi == 42.0
    assert air.differentiates_routes is False
    assert air.spread == 0.0


def test_air_enters_the_score_when_it_actually_differs():
    east = make_route(center_lon=9.19, radius_deg=0.014)
    west = make_route(center_lon=8.90, radius_deg=0.0141)

    air_by_cell = {}
    for route, aqi in ((east, 80.0), (west, 20.0)):
        for lat, lon in geo.sample_points(
            route.coordinates, candidates.AIR_SAMPLES_PER_ROUTE
        ):
            air_by_cell[candidates.grid_key(lat, lon)] = {"european_aqi": aqi}

    routes, air, _ = candidates.rank([east, west], request(), CALM, air_by_cell)
    assert air.differentiates_routes is True
    assert air.spread >= config.AIR_DIFFERENTIATION_MIN_SPREAD
    # The cleaner-air loop wins on the air component.
    cleaner = [r for r in routes if r.air and r.air["european_aqi"] == 20.0][0]
    dirtier = [r for r in routes if r.air and r.air["european_aqi"] == 80.0][0]
    assert cleaner.scores.air > dirtier.scores.air


def test_gain_is_excluded_from_weights_when_not_requested():
    route = make_route(radius_deg=0.014)
    routes, _, _ = candidates.rank([route], request(elevation_gain_m=None), CALM, {})
    assert routes[0].scores.gain == 0.0
    assert routes[0].scores.total > 0.0


# --- fast-road ceiling -----------------------------------------------------
# Motorways cannot appear at all — they are not in the foot or bike graph — so
# these cover the roads that are legal, unpleasant, and up to us to handle.

def test_route_mostly_on_state_roads_is_dropped_when_a_calmer_one_exists():
    quiet = make_route(waytype={6: 10000.0})
    busy = make_route(waytype={1: 8000.0, 3: 2000.0}, center_lon=9.26)

    ranked, _, notices = candidates.rank([quiet, busy], request(), CALM, {})

    assert [r.big_road_share for r in ranked] == [0.0]
    assert "busy_roads_only" not in notices


def test_only_busy_routes_are_still_offered_but_flagged():
    busy = make_route(waytype={1: 9000.0, 3: 1000.0})
    busier = make_route(waytype={1: 10000.0}, center_lon=9.26)

    ranked, _, notices = candidates.rank([busy, busier], request(), CALM, {})

    # An empty page helps nobody where every way out is a fast road.
    assert len(ranked) == 2
    assert "busy_roads_only" in notices


def test_big_road_share_counts_metres_not_exposure_weight():
    route = make_route(waytype={1: 2000.0, 2: 1000.0, 6: 7000.0})

    ranked, _, _ = candidates.rank([route], request(), CALM, {})

    assert ranked[0].big_road_share == 0.3
