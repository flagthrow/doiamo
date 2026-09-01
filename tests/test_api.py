"""End-to-end tests against a stub routing engine — no network, no API key."""
import math

import pytest
from fastapi.testclient import TestClient

from backend import health, main
from backend.models import WeatherContext
from backend.routing.base import RawRoute, RoutingEngine, RoutingError


def circle(center_lon=9.19, center_lat=45.4642, radius_deg=0.014,
           distance_m=10000.0, surface=None, waytype=None, climb=0.0):
    coords = []
    points = 180
    for i in range(points + 1):
        angle = 2 * math.pi * i / points
        elevation = 100.0 + climb * math.sin(angle) ** 2
        coords.append([
            center_lon + radius_deg * math.cos(angle),
            center_lat + radius_deg * math.sin(angle) * 0.7,
            elevation,
        ])
    return RawRoute(
        coordinates=coords,
        distance_m=distance_m,
        duration_s=distance_m / 2.8,
        surface=surface or {3: distance_m},
        waytype=waytype or {3: distance_m},
        seed=1,
    )


class StubEngine(RoutingEngine):
    name = "stub"

    def __init__(self, routes=None, error=None):
        self.routes = routes if routes is not None else [
            circle(),
            circle(center_lon=9.26, waytype={1: 10000.0}),
            circle(center_lon=9.12, surface={11: 10000.0}),
        ]
        self.error = error
        self.calls = []

    @property
    def configured(self):
        return True

    async def round_trips(self, lat, lon, length_m, sport, surface, seeds):
        self.calls.append((lat, lon, length_m, sport, surface, len(seeds)))
        if self.error:
            raise self.error
        return list(self.routes), []

    async def aclose(self):
        return None


@pytest.fixture
def client(monkeypatch):
    async def fake_context(lat, lon, cells, client=None):
        return WeatherContext(
            temperature_c=24.0, wind_speed_kmh=12.0,
            wind_direction_deg=270.0, uv_index=5.0,
        ), {cell: {"european_aqi": 40.0, "pm2_5": 10.0} for cell in cells}

    monkeypatch.setattr(health, "gather_context", fake_context)
    monkeypatch.setattr(main.health, "gather_context", fake_context)

    with TestClient(main.app) as test_client:
        test_client.app.state.engine = StubEngine()
        yield test_client


def search_body(**kwargs):
    body = {"lat": 45.4642, "lon": 9.19, "distance_km": 10,
            "elevation_gain_m": 150, "sport": "running", "surface": "asphalt"}
    body.update(kwargs)
    return body


def test_options_endpoint_describes_the_choices(client):
    data = client.get("/api/options").json()
    assert data["sports"] == ["running", "cycling"]
    assert data["modes"] == ["loop", "route"]
    assert data["surfaces"] == ["asphalt", "mixed", "trail"]
    assert "center" in data["default_view"]


def test_search_returns_scored_routes(client):
    response = client.post("/api/search", json=search_body())
    assert response.status_code == 200
    data = response.json()

    assert len(data["routes"]) >= 2
    scores = [r["scores"]["total"] for r in data["routes"]]
    assert scores == sorted(scores, reverse=True)
    assert data["weather"]["temperature_c"] == 24.0


def test_search_passes_the_query_through_to_the_engine(client):
    client.post("/api/search", json=search_body(sport="cycling", distance_km=40))
    lat, lon, length_m, sport, surface, seed_count = client.app.state.engine.calls[0]
    assert (lat, lon) == (45.4642, 9.19)
    assert length_m == 40000
    assert sport == "cycling"
    assert seed_count > 1


def test_uniform_air_is_reported_as_context_not_as_a_ranking_signal(client):
    data = client.post("/api/search", json=search_body()).json()
    assert data["air"]["european_aqi"] == 40.0
    assert data["air"]["differentiates_routes"] is False
    assert all(r["scores"]["air"] is None for r in data["routes"])


def test_anywhere_in_the_world_is_served_without_a_warning(client):
    """Routing, weather, air and POIs are all global — there is no supported
    region, and a runner in Paris must not be told they are out of bounds."""
    data = client.post("/api/search", json=search_body(lat=48.8566, lon=2.3522)).json()
    assert data["routes"]
    assert data["notices"] == []


def test_gpx_download_round_trips_from_the_search_result(client):
    route_id = client.post("/api/search", json=search_body()).json()["routes"][0]["id"]

    response = client.get("/api/gpx/" + route_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/gpx+xml")
    assert ".gpx" in response.headers["content-disposition"]
    assert "<trkpt" in response.text


def test_gpx_for_an_unknown_route_is_a_404(client):
    assert client.get("/api/gpx/deadbeef").status_code == 404


def test_routing_failure_surfaces_as_502(client, monkeypatch):
    client.app.state.engine = StubEngine(error=RoutingError("rate limit reached"))
    response = client.post("/api/search", json=search_body())
    assert response.status_code == 502
    assert "rate limit" in response.json()["detail"]


def test_missing_api_key_is_a_clear_503(client):
    class Unconfigured(StubEngine):
        @property
        def configured(self):
            return False

    client.app.state.engine = Unconfigured()
    response = client.post("/api/search", json=search_body())
    assert response.status_code == 503
    assert "ORS_API_KEY" in response.json()["detail"]


def test_invalid_input_is_rejected(client):
    assert client.post("/api/search", json=search_body(distance_km=0)).status_code == 422
    assert client.post("/api/search", json=search_body(lat=200)).status_code == 422


def test_unknown_sport_falls_back_instead_of_failing(client):
    data = client.post("/api/search", json=search_body(sport="swimming")).json()
    assert data["query"]["sport"] == "running"


def test_healthz(client):
    data = client.get("/api/healthz").json()
    assert data["ok"] is True


def test_a_spent_quota_is_not_reported_as_a_bad_key():
    """ORS answers 403 for both, and sending someone to check a key that is
    fine wastes their evening."""
    import httpx

    from backend.routing.ors import _forbidden_reason

    quota = httpx.Response(403, json={"error": "Quota exceeded"})
    assert "quota" in _forbidden_reason(quota).lower()

    nested = httpx.Response(403, json={"error": {"message": "Daily quota reached"}})
    assert "quota" in _forbidden_reason(nested).lower()

    bad_key = httpx.Response(403, json={"error": {"message": "Access denied"}})
    assert "key" in _forbidden_reason(bad_key).lower()

    unparseable = httpx.Response(403, text="<html>nope</html>")
    assert "key" in _forbidden_reason(unparseable).lower()


# --- search cache ----------------------------------------------------------

def test_an_identical_search_costs_no_routing_calls(client):
    """A hundred people searching '10 km from the Duomo' should cost one
    search, not a hundred."""
    first = client.post("/api/search", json=search_body()).json()
    calls_after_first = len(client.app.state.engine.calls)

    second = client.post("/api/search", json=search_body()).json()

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert len(client.app.state.engine.calls) == calls_after_first
    assert [r["id"] for r in second["routes"]] == [r["id"] for r in first["routes"]]


def test_the_climb_target_does_not_split_the_cache(client):
    """The router never sees the climb target — it only affects scoring — so
    '10 km, 300 m up' and '10 km, don't care' share one routing result."""
    client.post("/api/search", json=search_body(elevation_gain_m=150))
    calls = len(client.app.state.engine.calls)

    other = client.post("/api/search", json=search_body(elevation_gain_m=None)).json()

    assert other["from_cache"] is True
    assert len(client.app.state.engine.calls) == calls


def test_a_different_start_is_a_different_search(client):
    client.post("/api/search", json=search_body())
    calls = len(client.app.state.engine.calls)

    far = client.post("/api/search", json=search_body(lat=45.50, lon=9.25)).json()

    assert far["from_cache"] is False
    assert len(client.app.state.engine.calls) == calls + 1


def test_nearby_starts_share_one_cache_entry(client):
    """Bucketed to ~110 m, so two people standing on opposite corners of the
    same piazza do not pay twice."""
    client.post("/api/search", json=search_body(lat=45.4642, lon=9.1900))
    calls = len(client.app.state.engine.calls)

    nudged = client.post("/api/search", json=search_body(lat=45.46421, lon=9.19004)).json()

    assert nudged["from_cache"] is True
    assert len(client.app.state.engine.calls) == calls


def test_a_different_sport_or_surface_is_a_different_search(client):
    """Both pick the routing profile, so they genuinely change the geometry."""
    client.post("/api/search", json=search_body())
    calls = len(client.app.state.engine.calls)

    client.post("/api/search", json=search_body(sport="cycling", distance_km=30))
    client.post("/api/search", json=search_body(surface="trail"))

    assert len(client.app.state.engine.calls) == calls + 2


def test_cached_routes_still_work_when_the_quota_is_gone(client):
    """The nicest property of the cache: a spent quota stops new searches, not
    the ones already made."""
    from backend.routing.base import RoutingError

    first = client.post("/api/search", json=search_body()).json()

    client.app.state.engine = StubEngine(
        error=RoutingError("OpenRouteService daily quota exhausted")
    )
    again = client.post("/api/search", json=search_body()).json()

    assert again["from_cache"] is True
    assert [r["id"] for r in again["routes"]] == [r["id"] for r in first["routes"]]


def test_scores_are_recomputed_even_on_a_cache_hit(client, monkeypatch):
    """Geometry is reused; weather and air are not. A cached route must not
    carry yesterday's wind."""
    from backend import main as main_module
    from backend.models import WeatherContext

    client.post("/api/search", json=search_body())

    async def colder(lat, lon, cells, client=None):
        return WeatherContext(temperature_c=-5.0, wind_speed_kmh=40.0,
                              wind_direction_deg=0.0), {}

    monkeypatch.setattr(main_module.health, "gather_context", colder)
    second = client.post("/api/search", json=search_body()).json()

    assert second["from_cache"] is True
    assert second["weather"]["temperature_c"] == -5.0
