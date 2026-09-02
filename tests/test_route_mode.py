"""Point-to-point mode: scoring is relative, not against a target."""
import math

import pytest
from fastapi.testclient import TestClient

from backend import candidates, config, main
from backend.models import SearchRequest, WeatherContext
from backend.routing.base import RawRoute, RoutingEngine

CALM = WeatherContext(wind_speed_kmh=0.0, wind_direction_deg=0.0)


def line(start=(45.46, 9.19), end=(45.50, 9.25), bow=0.0, distance_m=6000.0,
         climb=0.0, surface=None, waytype=None):
    """A synthetic A-to-B path, bowed sideways so alternatives differ."""
    coords = []
    points = 120
    for i in range(points + 1):
        f = i / points
        lat = start[0] + (end[0] - start[0]) * f + bow * math.sin(math.pi * f)
        lon = start[1] + (end[1] - start[1]) * f + bow * math.sin(math.pi * f)
        coords.append([lon, lat, 100.0 + climb * math.sin(math.pi * f)])
    return RawRoute(
        coordinates=coords,
        distance_m=distance_m,
        duration_s=distance_m / 2.8,
        surface=surface or {3: distance_m},
        waytype=waytype or {3: distance_m},
    )


def request(**kwargs):
    base = dict(lat=45.46, lon=9.19, mode="route", end_lat=45.50, end_lon=9.25,
                sport="running", surface="asphalt")
    base.update(kwargs)
    return SearchRequest(**base)


def test_shortest_alternative_wins_on_directness():
    direct = line(distance_m=6000.0)
    detour = line(bow=0.03, distance_m=9000.0)
    routes, _, _ = candidates.rank([detour, direct], request(), CALM, {})
    assert len(routes) == 2
    assert routes[0].distance_m < routes[1].distance_m
    assert routes[0].scores.distance == 1.0


def test_directness_falls_off_with_the_detour():
    assert candidates._directness_score(6000, 6000) == 1.0
    assert candidates._directness_score(12000, 6000) == 0.5
    assert candidates._directness_score(0, 6000) == 0.5


def test_climb_is_compared_between_alternatives_not_to_a_target():
    flat = line(bow=0.0, climb=0.0, distance_m=6000.0)
    hilly = line(bow=0.03, climb=300.0, distance_m=6000.0)
    routes, _, _ = candidates.rank([hilly, flat], request(), CALM, {})
    flattest = min(routes, key=lambda r: r.ascent_m)
    steepest = max(routes, key=lambda r: r.ascent_m)
    assert flattest.scores.gain > steepest.scores.gain
    assert flattest.scores.gain == 1.0


def test_climb_counts_even_though_no_target_was_given():
    # In loop mode an absent elevation_gain_m drops the factor entirely; in
    # route mode it must stay, because it is a live comparison.
    routes, _, _ = candidates.rank([line(), line(bow=0.03, climb=200.0)],
                                   request(), CALM, {})
    assert any(r.scores.gain > 0 for r in routes)


def test_relative_score_handles_identical_values():
    assert candidates._relative_score(5.0, [5.0, 5.0]) == 1.0
    assert candidates._relative_score(5.0, []) == 0.5


def test_no_distance_filter_in_route_mode():
    # A 25 km alternative to a 6 km route would be dropped by the loop-mode
    # distance filter. Here it is merely ranked last.
    routes, _, notices = candidates.rank(
        [line(distance_m=6000.0), line(bow=0.05, distance_m=25000.0)],
        request(), CALM, {},
    )
    assert len(routes) == 2
    assert "no_exact_distance_match" not in notices


def test_route_mode_weights_traffic_higher_for_cycling():
    quiet = line(waytype={6: 6000.0})
    # Under BIG_ROAD_REJECT_SHARE — over it the route is filtered out before
    # scoring, which is a different behaviour with its own test.
    busy = line(bow=0.03, waytype={1: 1500.0, 3: 4500.0})
    run, _, _ = candidates.rank([quiet, busy], request(sport="running"), CALM, {})
    bike, _, _ = candidates.rank([quiet, busy], request(sport="cycling"), CALM, {})
    assert (bike[0].scores.total - bike[1].scores.total) > (
        run[0].scores.total - run[1].scores.total
    )


# --- API ------------------------------------------------------------------

class StubEngine(RoutingEngine):
    name = "stub"
    configured = True

    def __init__(self):
        self.point_calls = []

    async def point_to_point(self, start, end, sport, surface, length_m=None):
        self.point_calls.append((start, end, sport, surface, length_m))
        if length_m:
            return [
                line(distance_m=length_m * 0.98),
                line(bow=0.03, distance_m=length_m * 1.06, climb=180.0),
            ], []
        return [line(distance_m=6000.0), line(bow=0.03, distance_m=7500.0, climb=180.0)], []

    async def round_trips(self, lat, lon, length_m, sport, surface, seeds):
        return [line(distance_m=10000.0)], []

    async def aclose(self):
        return None


@pytest.fixture
def client(monkeypatch):
    async def fake_context(lat, lon, cells, client=None):
        return WeatherContext(temperature_c=20.0, wind_speed_kmh=5.0,
                              wind_direction_deg=90.0), {}

    monkeypatch.setattr(main.health, "gather_context", fake_context)
    with TestClient(main.app) as test_client:
        test_client.app.state.engine = StubEngine()
        yield test_client


def route_body(**kwargs):
    body = {"lat": 45.4642, "lon": 9.19, "mode": "route",
            "end_lat": 45.4780, "end_lon": 9.1750,
            "sport": "running", "surface": "asphalt"}
    body.update(kwargs)
    return body


def test_route_search_returns_ranked_alternatives(client):
    response = client.post("/api/search", json=route_body())
    assert response.status_code == 200
    data = response.json()
    assert data["query"]["mode"] == "route"
    assert len(data["routes"]) == 2
    scores = [r["scores"]["total"] for r in data["routes"]]
    assert scores == sorted(scores, reverse=True)


def test_route_search_passes_both_endpoints_to_the_engine(client):
    client.post("/api/search", json=route_body())
    start, end, sport, surface, length_m = client.app.state.engine.point_calls[0]
    assert start == (45.4642, 9.19)
    assert end == (45.4780, 9.1750)
    assert sport == "running"
    assert length_m is None


def test_a_direct_route_costs_one_call_not_a_dozen(client):
    data = client.post("/api/search", json=route_body()).json()
    assert data["candidates_requested"] == config.ALTERNATIVE_TARGET_COUNT


def test_a_target_length_turns_it_into_a_detour_search(client):
    data = client.post("/api/search", json=route_body(distance_km=15)).json()
    assert data["candidates_requested"] == config.DETOUR_VIA_COUNT
    length_m = client.app.state.engine.point_calls[0][4]
    assert length_m == 15000.0
    # And the routes come back near the requested length, not near the direct one.
    assert all(13000 < r["distance_m"] < 17000 for r in data["routes"])


def test_route_without_an_endpoint_is_rejected(client):
    body = route_body()
    del body["end_lat"]
    assert client.post("/api/search", json=body).status_code == 422


def test_loop_without_a_distance_is_rejected(client):
    assert client.post("/api/search", json={
        "lat": 45.4642, "lon": 9.19, "mode": "loop"}).status_code == 422


def test_unknown_mode_is_rejected(client):
    assert client.post("/api/search", json=route_body(mode="teleport")).status_code == 422


def test_gpx_download_works_for_a_route(client):
    route_id = client.post("/api/search", json=route_body()).json()["routes"][0]["id"]
    response = client.get("/api/gpx/" + route_id)
    assert response.status_code == 200
    assert "<trkpt" in response.text


def test_geocode_returns_places(client, monkeypatch):
    async def fake_search(text, near=None):
        fake_search.calls.append((text, near))
        return [{"label": "Parco Sempione, Milano", "lat": 45.4725, "lon": 9.1745,
                 "region": "Lombardia, Italia"}]
    fake_search.calls = []
    monkeypatch.setattr(client.app.state.geocoder, "search", fake_search)

    data = client.get(
        "/api/geocode", params={"q": "sempione", "lat": 45.4642, "lon": 9.19}
    ).json()
    assert data[0]["label"] == "Parco Sempione, Milano"
    assert data[0]["lat"] == 45.4725
    text, near = fake_search.calls[0]
    assert text == "sempione"
    assert near == (45.4642, 9.19)   # biased towards what the map is showing


def test_geocode_ignores_too_short_a_query(client, monkeypatch):
    called = []

    async def fake_search(text, near=None):
        called.append(text)
        return []
    monkeypatch.setattr(client.app.state.geocoder, "search", fake_search)

    assert client.get("/api/geocode", params={"q": "a"}).json() == []
    assert called == []


def test_geocode_without_a_viewpoint_sends_no_focus(client, monkeypatch):
    seen = []

    async def fake_search(text, near=None):
        seen.append(near)
        return []
    monkeypatch.setattr(client.app.state.geocoder, "search", fake_search)

    client.get("/api/geocode", params={"q": "duomo"})
    assert seen[0] is None



# --- detour geometry ------------------------------------------------------

def test_via_points_land_on_the_requested_length():
    from backend import geo

    start, end = (45.4642, 9.19), (45.4986, 9.2494)
    vias = geo.ellipse_via_points(start, end, sum_m=12000.0, count=8)
    assert len(vias) == 8
    for lat, lon in vias:
        legs = (
            geo.haversine_m(start[1], start[0], lon, lat)
            + geo.haversine_m(lon, lat, end[1], end[0])
        )
        assert abs(legs - 12000.0) < 60


def test_via_points_spread_to_both_sides_of_the_line():
    from backend import geo

    start, end = (45.4642, 9.19), (45.4986, 9.2494)
    vias = geo.ellipse_via_points(start, end, sum_m=12000.0, count=8)
    # Cross product sign says which side of A->B each point falls on.
    sides = set()
    for lat, lon in vias:
        cross = (end[1] - start[1]) * (lat - start[0]) - (end[0] - start[0]) * (lon - start[1])
        sides.add(cross > 0)
    assert sides == {True, False}


def test_no_via_points_when_the_target_barely_beats_the_straight_line():
    from backend import geo

    start, end = (45.4642, 9.19), (45.4986, 9.2494)
    straight = geo.haversine_m(start[1], start[0], end[1], end[0])
    assert geo.ellipse_via_points(start, end, sum_m=straight * 1.001, count=8) == []


def test_target_distance_is_scored_the_same_way_in_both_modes():
    # A 15 km A-to-B detour is judged against 15 km, exactly as a loop would be.
    on_target = line(distance_m=15000.0)
    too_long = line(bow=0.04, distance_m=22000.0)
    routes, _, _ = candidates.rank(
        [too_long, on_target], request(distance_km=15), CALM, {}
    )
    assert len(routes) == 1                 # the 22 km candidate is filtered out
    assert routes[0].scores.distance == 1.0


def test_climb_target_applies_in_route_mode_too():
    close = line(climb=300.0, distance_m=15000.0)
    far = line(bow=0.03, climb=20.0, distance_m=15000.0)
    routes, _, _ = candidates.rank(
        [far, close], request(distance_km=15, elevation_gain_m=300), CALM, {}
    )
    best = max(routes, key=lambda r: r.scores.gain)
    assert best.ascent_m > 200
