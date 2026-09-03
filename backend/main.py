"""Doiamo API.

POST /api/search takes a start point and a shape (distance, climb, surface),
asks the routing engine for a spread of loops, scores them against the
environmental layers, and returns the best few. GPX comes back from the cache
by route id.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import candidates, config, energy, geo, health, intent as intent_reader, poi
from .geocoding import PhotonGeocoder
from . import poi_store as poi_store_module
from .poi_store import LocalPoiStore, ensure_downloaded
from .cache import TTLCache
from .gpx import build_gpx, filename_for
from .models import (
    GeocodeResult,
    InterpretRequest,
    InterpretResponse,
    Poi,
    PoiRequest,
    PoiResponse,
    PoiScores,
    SearchRequest,
    SearchResponse,
)
from .routing import ORSEngine, RoutingError

log = logging.getLogger("doiamo")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

route_cache = TTLCache(config.ROUTE_CACHE_MAX, config.ROUTE_CACHE_TTL_S)
# Overpass fails often enough that a success is worth keeping: a retry, or a
# page reload, should reuse it rather than roll the dice again.
poi_cache = TTLCache(200, config.ROUTE_CACHE_TTL_S)

# The routing itself. In a launch where a hundred people all search "10 km from
# the Duomo", this is the difference between 1200 API calls and 6.
search_cache = TTLCache(config.SEARCH_CACHE_MAX, config.SEARCH_CACHE_TTL_S)


def _search_key(query: SearchRequest) -> str:
    """Identify a routing request, ignoring anything the router never sees.

    The climb target only affects scoring, so a search for "10 km, 300 m up"
    and one for "10 km, don't care" share the same routes and the same cache
    entry.
    """
    def bucket(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return round(value, config.SEARCH_CACHE_PRECISION)

    return "|".join(str(part) for part in (
        query.mode,
        bucket(query.lat), bucket(query.lon),
        bucket(query.end_lat), bucket(query.end_lon),
        query.distance_km,
        query.sport, query.surface,
    ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = ORSEngine()
    app.state.geocoder = PhotonGeocoder()
    # A deployment starts with no database unless POI_DB_URL is set; without
    # one everything falls back to Overpass and looks broken.
    ensure_downloaded()
    app.state.poi_store = LocalPoiStore()
    log.info(
        "POI store: %s (%s rows)",
        "local" if app.state.poi_store.available else "overpass fallback",
        app.state.poi_store.count,
    )
    app.state.http = httpx.AsyncClient(timeout=15.0)
    try:
        yield
    finally:
        await app.state.engine.aclose()
        await app.state.geocoder.aclose()
        await app.state.http.aclose()


app = FastAPI(title="Doiamo", version="0.1.0", lifespan=lifespan)


@app.get("/api/options")
async def options() -> Dict[str, object]:
    return {
        "default_view": config.DEFAULT_VIEW,
        "modes": config.MODES,
        "sports": config.SPORTS,
        "surfaces": config.SURFACE_PREFERENCES,
        "sights": config.SIGHTS,
    }


@app.get("/api/healthz")
async def healthz() -> Dict[str, object]:
    store = app.state.poi_store
    return {
        "ok": True,
        "routing_engine": app.state.engine.name,
        "routing_configured": app.state.engine.configured,
        "poi_source": "local" if store.available else "overpass",
        "poi_local_count": store.count,
        # Enough to diagnose a deployment that quietly fell back, without logs.
        "poi_db": {
            "path": store.path,
            "exists": os.path.exists(store.path),
            "url_configured": bool(poi_store_module.DOWNLOAD_URL),
            "download": poi_store_module.LAST_DOWNLOAD,
        },
        "intent_model": dict(intent_reader.spend(),
                             cache=intent_reader.cache_stats()),
        "routing_budget": app.state.engine.budget.status()
        if hasattr(app.state.engine, "budget") else None,
    }


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    query = request.normalised()
    engine = app.state.engine

    if not engine.configured:
        raise HTTPException(
            status_code=503,
            detail="ORS_API_KEY is not set. Get a free key at openrouteservice.org.",
        )

    notices: List[str] = []

    cache_key = _search_key(query)
    cached = search_cache.get(cache_key)
    from_cache = cached is not None

    if from_cache:
        raw_routes, routing_notices, requested = cached
    elif query.is_loop:
        seeds = candidates.build_seeds(config.CANDIDATE_SEEDS)
        requested = len(seeds)
        try:
            raw_routes, routing_notices = await engine.round_trips(
                lat=query.lat,
                lon=query.lon,
                length_m=(query.distance_km or 0.0) * 1000.0,
                sport=query.sport,
                surface=query.surface,
                seeds=seeds,
            )
        except RoutingError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        search_cache.set(cache_key, (raw_routes, routing_notices, requested))
    else:
        # Without a target length this is one call and the engine supplies its
        # own alternatives; with one it becomes a detour search over via points.
        length_m = (query.distance_km * 1000.0) if query.distance_km else None
        requested = (
            config.DETOUR_VIA_COUNT if length_m else config.ALTERNATIVE_TARGET_COUNT
        )
        try:
            raw_routes, routing_notices = await engine.point_to_point(
                start=(query.lat, query.lon),
                end=(query.end_lat, query.end_lon),
                sport=query.sport,
                surface=query.surface,
                length_m=length_m,
            )
        except RoutingError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        search_cache.set(cache_key, (raw_routes, routing_notices, requested))
    notices.extend(routing_notices)

    if not raw_routes:
        return SearchResponse(
            query=query,
            routes=[],
            weather=health.WeatherContext(),
            air=candidates.AirContext(),
            notices=notices + ["no_routes"],
            candidates_requested=requested,
            candidates_returned=0,
            from_cache=from_cache,
        )

    cells = candidates.air_cells_for_routes(raw_routes)
    weather, air_by_cell = await health.gather_context(
        query.lat, query.lon, cells, client=app.state.http
    )

    routes, air_context, rank_notices = candidates.rank(
        raw_routes, query, weather, air_by_cell
    )
    notices.extend(rank_notices)

    for route in routes:
        route_cache.set(route.id, (route, query.sport))

    return SearchResponse(
        query=query,
        routes=routes,
        weather=weather,
        air=air_context,
        notices=notices,
        candidates_requested=requested,
        candidates_returned=len(raw_routes),
        from_cache=from_cache,
    )


@app.post("/api/pois", response_model=PoiResponse)
async def pois(request: PoiRequest) -> PoiResponse:
    """Water, toilets, viewpoints, monuments and repair stands along the routes.

    Deliberately a second request: Overpass takes a few seconds, and the routes
    should already be on screen by the time these arrive.
    """
    routes = {}
    distances = {}
    sport = "running"
    for route_id in request.route_ids:
        entry = route_cache.get(route_id)
        if entry is not None:
            routes[route_id] = entry[0].coordinates
            distances[route_id] = entry[0].distance_m
            sport = entry[1]

    if not routes:
        # The cache is in memory, so a restart or a long-open page loses it.
        # Saying so beats returning an empty success the UI cannot explain.
        return PoiResponse(
            kinds=poi.KINDS,
            monument_kinds=config.MONUMENT_KINDS,
            nature_kinds=config.NATURE_KINDS,
            available=False,
            expired=True,
        )

    cache_key = "|".join(sorted(routes))
    cached = poi_cache.get(cache_key)
    corridor = poi.corridor(list(routes.values()))

    if cached is not None:
        found, ok, source = cached, True, "cache"
    elif app.state.poi_store.covers(corridor):
        # The local extract answers in milliseconds and cannot be too busy.
        found = app.state.poi_store.near(corridor, poi.DEFAULT_RADIUS_M)
        ok, source = True, "local"
        if found:
            poi_cache.set(cache_key, found)
    else:
        # Outside the extract, Overpass is the only option — slower, and
        # sometimes unavailable, but it covers the planet.
        found, ok = await poi.fetch(corridor, client=app.state.http)
        source = "overpass"
        # Only a result with something in it is worth keeping. Overpass
        # sometimes succeeds with nothing, and caching that would lock the
        # page into a bad answer for an hour.
        if ok and found:
            poi_cache.set(cache_key, found)
    tagged, counts = poi.assign(found, routes)

    scores = {}
    if ok:
        sights = request.sights if request.sights in config.SIGHTS else "both"
        for route_id, parts in poi.score(counts, distances, sport, sights).items():
            entry = route_cache.get(route_id)
            previous = entry[0].scores.total if entry else 0.0
            scores[route_id] = PoiScores(
                water=parts["water"],
                monuments=parts["monuments"],
                nature=parts["nature"],
                sights=parts["sights"],
                bonus=parts["bonus"],
                total=poi.blend(previous, parts["bonus"], sights),
            )

    return PoiResponse(
        pois=[Poi(**item) for item in tagged],
        counts=counts,
        scores=scores,
        kinds=poi.KINDS,
        monument_kinds=config.MONUMENT_KINDS,
        nature_kinds=config.NATURE_KINDS,
        available=ok,
        source=source,
    )


@app.post("/api/interpret", response_model=InterpretResponse)
async def interpret(request: InterpretRequest) -> InterpretResponse:
    """Turn a sentence into a search.

    Rules first — free, instant, and enough for most sentences. Claude only
    when they came back with nothing to act on, and only to fill the gaps.
    """
    # Count the call rather than diffing the answer: a model call that agreed
    # with the rules costs exactly as much as one that did not, and reporting it
    # as "the rules handled this" hides both the spend and the failures.
    calls_before = intent_reader.USAGE["calls"]
    parsed = intent_reader.interpret(request.sentence)
    used_model = intent_reader.USAGE["calls"] > calls_before

    near = (request.lat, request.lon) if request.lat is not None else None

    async def locate(intent):
        """Geocode whatever this reading called a place."""
        found: Dict[str, Optional[GeocodeResult]] = {"start": None, "end": None}
        missing: List[str] = []
        for field, text in (("start", intent.start_text), ("end", intent.end_text)):
            if not text:
                continue
            try:
                hits = await app.state.geocoder.search(
                    text, near=near, prefer_place=True
                )
            except Exception:
                hits = []
            if hits:
                found[field] = GeocodeResult(**hits[0])
            else:
                missing.append(text)
        return found, missing

    resolved, unresolved = await locate(parsed)

    # "Burn 400 calories" is a distance nobody has worked out yet. Turning it
    # into one needs a body mass, which is the largest unknown in the answer —
    # so whether it was told to us or assumed travels back with the number.
    mass_assumed = parsed.mass_kg is None and request.mass_kg is None
    mass_kg = energy.clamp_mass(parsed.mass_kg or request.mass_kg)
    distance_km = parsed.distance_km
    if parsed.calories and distance_km is None:
        distance_km = energy.distance_km_for_kcal(
            parsed.calories, parsed.sport or "running", mass_kg,
            ascent_m=parsed.elevation_gain_m,
        )
        parsed = parsed.model_copy(update={"distance_km": distance_km})

    # The rules can extract a string that is not a place: "starting from the
    # fontana di trevi at Rome" gave them "the fontana di trevi at rome",
    # which geocodes to nothing. Worse, having produced *a* string they looked
    # confident, so the model was never asked — and the model reads that
    # sentence correctly. A name that does not resolve is a gap like any
    # other, so spend the call and try again.
    if unresolved and not used_model:
        guess = intent_reader.ask_model(request.sentence)
        if guess is not None:
            used_model = True
            merged = parsed.model_dump()
            for field in ("start_text", "end_text"):
                value = getattr(guess, field)
                if value and value != merged.get(field):
                    merged[field] = value
            retry = intent_reader.Intent(**merged)
            if retry.start_text != parsed.start_text or retry.end_text != parsed.end_text:
                second, still_missing = await locate(retry)
                # Only keep the model's reading if it actually found something.
                if any(second.values()):
                    parsed, resolved, unresolved = retry, second, still_missing

    return InterpretResponse(
        understood=intent_reader.summarise(parsed, request.lang),
        sport=parsed.sport,
        mode=parsed.mode,
        distance_km=parsed.distance_km,
        elevation_gain_m=parsed.elevation_gain_m,
        surface=parsed.surface,
        sights=parsed.sights,
        area=parsed.area,
        calories=parsed.calories,
        mass_kg=mass_kg if parsed.calories else None,
        mass_assumed=mass_assumed if parsed.calories else False,
        start=resolved["start"],
        end=resolved["end"],
        unresolved=unresolved,
        used_model=used_model,
    )


@app.get("/api/intent-gaps")
async def intent_gaps(limit: int = 50) -> Dict[str, object]:
    """Sentences the rules could not read, most-asked first.

    The work list: teach the parser one of these and it never costs a model
    call again. This is how the LLM bill goes down over time rather than up.
    """
    return {
        "gaps": intent_reader.STORE.gaps(limit),
        "store": intent_reader.STORE.stats(),
    }


@app.get("/api/geocode", response_model=List[GeocodeResult])
async def geocode(
    q: str, lat: Optional[float] = None, lon: Optional[float] = None
) -> List[GeocodeResult]:
    """Place-name lookup through Photon.

    Keyless and without a daily allowance, unlike the openrouteservice
    geocoder — which matters because autocomplete spends a request per
    keystroke batch and would exhaust 1000/day long before routing ran out.

    ``lat``/``lon`` bias results towards what the user is currently looking at,
    which is what makes "via Roma" resolve to the one down the road.
    """
    text = (q or "").strip()
    if len(text) < 2:
        return []

    near = (lat, lon) if lat is not None and lon is not None else None
    results = await app.state.geocoder.search(text, near=near)
    return [GeocodeResult(**item) for item in results]


@app.get("/api/gpx/{route_id}")
async def gpx(route_id: str) -> Response:
    entry = route_cache.get(route_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="route expired, search again")
    route, sport = entry
    body = build_gpx(route, sport=sport, description="Generated by Doiamo")
    return Response(
        content=body,
        media_type="application/gpx+xml",
        headers={
            "Content-Disposition": 'attachment; filename="{}"'.format(
                filename_for(route, sport)
            )
        },
    )


@app.get("/")
async def index() -> Response:
    page = WEB_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"error": "web/index.html missing"}, status_code=500)
    return FileResponse(page, headers={"Cache-Control": "no-cache"})


class RevalidatedStatics(StaticFiles):
    """Static files the browser must check before reusing.

    Without a Cache-Control header a browser is free to invent its own
    freshness window, and Chrome will happily serve app.js and styles.css from
    memory for the rest of a session. That means a deploy can land new markup
    against last week's stylesheet — and during development a CSS change simply
    does not appear, which is how a fixed layout looked broken for an hour.
    "no-cache" is not "do not store": the file is still cached, the browser just
    has to revalidate, and an unchanged file comes back as an empty 304.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


if WEB_DIR.exists():
    app.mount("/static", RevalidatedStatics(directory=str(WEB_DIR)), name="static")
