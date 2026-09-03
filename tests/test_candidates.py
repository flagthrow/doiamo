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


# --- saying so when the request cannot be met ------------------------------

def _hilly(gain_m, distance_m, lon):
    coords = []
    for i in range(181):
        angle = 2 * math.pi * i / 180
        coords.append([
            lon + 0.014 * math.cos(angle),
            42.35 + 0.010 * math.sin(angle),
            700.0 + gain_m * (0.5 - 0.5 * math.cos(2 * angle)),
        ])
    return RawRoute(
        coordinates=coords, distance_m=distance_m, duration_s=distance_m / 2.8,
        surface={3: distance_m}, waytype={3: distance_m},
    )


def test_a_flat_request_in_a_hilly_place_says_so():
    """Scoring zero on the one thing that was asked for is a failure, not a low
    score — ranking it first in silence presents it as an answer."""
    routes = [_hilly(g, d, 13.40 + i * 0.02) for i, (g, d) in
              enumerate(((420.0, 11300.0), (380.0, 10600.0), (350.0, 10100.0)))]
    request = SearchRequest(
        lat=42.35, lon=13.40, sport="running", distance_km=10.0,
        elevation_gain_m=40.0, surface="asphalt", sights="both", mode="loop",
    )

    ranked, _, notices = candidates.rank(routes, request, CALM, {})

    assert "gain_target_unreachable" in notices
    assert ranked                       # still shows the flattest it found
    assert ranked[0].ascent_m == min(r.ascent_m for r in ranked)


def test_a_reachable_climb_target_says_nothing():
    routes = [_hilly(g, 10000.0, 13.40 + i * 0.02) for i, g in enumerate((20.0, 30.0))]
    request = SearchRequest(
        lat=42.35, lon=13.40, sport="running", distance_km=10.0,
        elevation_gain_m=40.0, surface="asphalt", sights="both", mode="loop",
    )

    _, _, notices = candidates.rank(routes, request, CALM, {})

    assert "gain_target_unreachable" not in notices


def test_wind_carries_no_weight_around_a_loop():
    """Every metre into the wind is repaid by a metre with it, so the share is
    pinned near a constant that depends on the day and not on the route."""
    assert "wind" not in config.WEIGHTS["running"]
    assert "wind" not in config.WEIGHTS["cycling"]
    assert "wind" in config.ROUTE_WEIGHTS["running"]
    assert round(sum(config.WEIGHTS["running"].values()), 6) == 1.0
    assert round(sum(config.WEIGHTS["cycling"].values()), 6) == 1.0


def test_staying_in_town_prefers_streets_over_tracks():
    """No urban boundary exists in the data we hold, but the road network is a
    decent proxy: streets and pavements are what a built-up area is made of."""
    streets = make_route(waytype={3: 8000.0, 7: 2000.0})
    tracks = make_route(waytype={5: 7000.0, 2: 3000.0}, center_lon=9.26)

    anywhere, _, _ = candidates.rank([streets, tracks], request(), CALM, {})
    in_town, _, _ = candidates.rank(
        [streets, tracks], request(area="urban"), CALM, {}
    )

    assert in_town[0].urban_share > in_town[1].urban_share
    gap_in_town = in_town[0].scores.total - in_town[1].scores.total
    gap_anywhere = abs(anywhere[0].scores.total - anywhere[1].scores.total)
    assert gap_in_town > gap_anywhere


def test_the_weights_still_sum_to_one_when_staying_in_town():
    """The urban term is taken out of the other axes, not added on top, so
    totals stay comparable between one search and the next."""
    routes = [make_route(waytype={3: 10000.0})]
    scored, _, _ = candidates.rank(routes, request(area="urban"), CALM, {})
    assert 0.0 <= scored[0].scores.total <= 1.0


# --- a stub is not a short route -------------------------------------------

def test_stub_loops_are_dropped_not_offered():
    """ORS round_trip sometimes cannot build the loop a seed asked for and
    returns a fraction of it. Three 1 km options in answer to a 10 km run are
    worse than none."""
    stubs = [make_route(distance_m=1000.0, center_lon=9.19 + i * 0.02) for i in range(3)]

    ranked, _, notices = candidates.rank(stubs, request(distance_km=10.0), CALM, {})

    assert ranked == []
    assert "no_route_of_that_length" in notices


def test_a_loose_match_is_still_offered():
    """9.2 km for a 10 km request is a real answer; the floor must not eat it."""
    close = make_route(distance_m=9200.0)

    ranked, _, notices = candidates.rank([close], request(distance_km=10.0), CALM, {})

    assert len(ranked) == 1
    assert "no_route_of_that_length" not in notices


def test_stubs_are_dropped_while_a_real_route_survives():
    good = make_route(distance_m=10100.0)
    stub = make_route(distance_m=900.0, center_lon=9.26)

    ranked, _, notices = candidates.rank(
        [good, stub], request(distance_km=10.0), CALM, {}
    )

    assert [round(r.distance_m) for r in ranked] == [10100]
    assert "some_routes_too_short" in notices


# --- when the request cannot be satisfied at all ---------------------------

def test_asking_for_climb_on_flat_ground_says_so():
    """The mirror of the hilly case, and the one that was silent: the check
    only ever fired when everything was too steep, never too flat."""
    flat = [_hilly(g, 10000.0, 9.19 + i * 0.02)
            for i, g in enumerate((15.0, 30.0, 25.0))]
    request = SearchRequest(
        lat=45.4642, lon=9.19, sport="running", distance_km=10.0,
        elevation_gain_m=500.0, surface="asphalt", sights="both", mode="loop",
    )

    ranked, _, notices = candidates.rank(flat, request, CALM, {})

    assert "climb_target_unreachable" in notices
    assert ranked


def test_an_impossible_climb_target_still_ranks_by_which_climbs_most():
    """Scoring every candidate zero against an unreachable number does not
    rank them, it only drags them down together and leaves the order to be
    decided by everything else."""
    routes = [_hilly(g, 10000.0, 9.19 + i * 0.02)
              for i, g in enumerate((10.0, 90.0, 40.0))]
    request = SearchRequest(
        lat=45.4642, lon=9.19, sport="running", distance_km=10.0,
        elevation_gain_m=500.0, surface="asphalt", sights="both", mode="loop",
    )

    ranked, _, _ = candidates.rank(routes, request, CALM, {})

    hilliest = max(ranked, key=lambda r: r.ascent_m)
    assert hilliest.scores.gain == 1.0


def test_an_impossible_request_names_both_compromises():
    """One route keeps the distance, another comes nearest the climb. Which is
    which is the reader's call, so both are labelled."""
    routes = [
        _hilly(20.0, 10000.0, 9.19),    # right distance, no climb
        _hilly(120.0, 4000.0, 9.23),    # most climb, far too short
    ]
    request = SearchRequest(
        lat=45.4642, lon=9.19, sport="running", distance_km=10.0,
        elevation_gain_m=500.0, surface="asphalt", sights="both", mode="loop",
    )

    ranked, _, notices = candidates.rank(routes, request, CALM, {})

    assert "climb_target_unreachable" in notices
    by_tag = {tag: r for r in ranked for tag in r.best_for}
    assert round(by_tag["distance"].distance_m) == 10000
    assert by_tag["gain"].ascent_m == max(r.ascent_m for r in ranked)


def test_a_request_that_can_be_met_labels_nothing():
    """The labels are a way of explaining a compromise. With nothing to
    apologise for they would just be decoration."""
    routes = [_hilly(140.0, 10000.0, 9.19 + i * 0.02) for i in range(2)]
    # Ask for what these routes actually climb, rather than guessing a number
    # from the helper's peak-height argument and asserting against the guess.
    measured = candidates.rank(
        routes,
        SearchRequest(lat=45.4642, lon=9.19, sport="running", distance_km=10.0,
                      surface="asphalt", sights="both", mode="loop"),
        CALM, {},
    )[0][0].ascent_m
    request = SearchRequest(
        lat=45.4642, lon=9.19, sport="running", distance_km=10.0,
        elevation_gain_m=measured, surface="asphalt", sights="both", mode="loop",
    )

    ranked, _, notices = candidates.rank(routes, request, CALM, {})

    assert all(r.best_for == [] for r in ranked)
    assert not [n for n in notices if "unreachable" in n]


# --- looking further out when the hills are further out --------------------

def test_a_longer_hillier_route_is_offered_beside_the_one_that_fits():
    """Bologna's hills start further out than a 10 km loop can reach. Picking
    the least-flat of a flat batch is not an answer; a different search is."""
    primary = [r.model_copy(update={"best_for": ["distance", "gain"]})
               for r in _ranked([_hilly(30.0, 10000.0, 9.19)])]
    stretched = _ranked([_hilly(600.0, 20000.0, 9.30)], distance_km=20.0)

    merged, offered = candidates.merge_stretched(primary, stretched)

    assert offered is True
    # Second, where it will actually be seen.
    assert merged[1].ascent_m == max(r.ascent_m for r in stretched)
    assert merged[1].best_for == ["gain"]
    # The short one keeps the distance and stops claiming the climb.
    assert merged[0].best_for == ["distance"]


def test_a_longer_route_that_is_no_hillier_is_not_offered():
    """Padding the page with a route twice as long for forty more metres of
    climb answers nothing."""
    primary = [r.model_copy(update={"best_for": ["distance", "gain"]})
               for r in _ranked([_hilly(30.0, 10000.0, 9.19)])]
    stretched = _ranked([_hilly(34.0, 20000.0, 9.30)], distance_km=20.0)

    merged, offered = candidates.merge_stretched(primary, stretched)

    assert offered is False
    assert merged == primary


def test_nothing_to_stretch_to_leaves_the_results_alone():
    primary = _ranked([_hilly(30.0, 10000.0, 9.19)])
    assert candidates.merge_stretched(primary, []) == (primary, False)


def _ranked(routes, distance_km=10.0):
    request = SearchRequest(
        lat=45.4642, lon=9.19, sport="running", distance_km=distance_km,
        elevation_gain_m=800.0, surface="asphalt", sights="both", mode="loop",
    )
    return candidates.rank(routes, request, CALM, {})[0]


def test_a_cyclist_prefers_the_route_with_the_bike_lane():
    """Traffic exposure already prices a cycleway as quiet, which is not the
    same as preferring one: two equally quiet routes were indistinguishable."""
    lane = make_route(waytype={6: 8000.0, 3: 2000.0})
    street = make_route(waytype={7: 8000.0, 3: 2000.0}, center_lon=9.26)

    ranked, _, _ = candidates.rank([lane, street], request(sport="cycling"), CALM, {})

    assert ranked[0].bikeway_share > ranked[1].bikeway_share


def test_a_runner_prefers_the_bike_lane_too():
    """An Italian pista ciclabile is usually ciclopedonale — a path away from
    cars, which is where you would rather run as well as ride."""
    lane = make_route(waytype={6: 8000.0, 3: 2000.0})
    street = make_route(waytype={7: 8000.0, 3: 2000.0}, center_lon=9.26)

    ranked, _, _ = candidates.rank([lane, street], request(sport="running"), CALM, {})

    assert ranked[0].bikeway_share > ranked[1].bikeway_share


def test_bike_lanes_count_for_both_sports_and_more_on_wheels():
    for table in (config.WEIGHTS, config.ROUTE_WEIGHTS):
        assert table["cycling"]["bikeway"] > table["running"]["bikeway"] > 0
        for sport in ("running", "cycling"):
            assert round(sum(table[sport].values()), 6) == 1.0
