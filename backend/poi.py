"""Points of interest along a route, from OpenStreetMap via Overpass.

Free and keyless, but slow enough (a few seconds) that this is deliberately not
part of the search request: the frontend asks for POIs once the routes are
already on screen.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

from . import config, geo

# Overpass is free and genuinely unreliable: measured from here the main
# instance answered about two requests in three, failing with a server-side
# timeout rather than a rate limit. Retrying is the defence.
#
# Regional instances are deliberately excluded. overpass.osm.ch responds to
# every request but only holds Swiss data, so it answers an Italian query with
# a cheerful, empty, wrong result — worse than an error.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_TIMEOUT_S = 25.0
OVERPASS_ROUNDS = 3
OVERPASS_RETRY_DELAY_S = 1.5

# Overpass answers 406 to a default library user agent, and its usage policy
# asks callers to identify themselves. Both reasons to send a real one.
USER_AGENT = "Doiamo/0.1 (+https://doiamo.com)"

# The corridor is a string of circles along the route. They have to overlap, or
# the gaps between them are simply not searched — a fixed number of samples per
# route made a 10 km loop into 75 m circles spaced 300 m apart, so half of it
# was invisible. Sample by distance instead, and keep the radius above half the
# spacing so consecutive circles meet.
CORRIDOR_SPACING_M = 150.0
DEFAULT_RADIUS_M = 130

# What actually counts as "on your route" once the results are back. Tighter
# than the fetch radius, which only exists to guarantee coverage.
MATCH_RADIUS_M = 100

# Above this the query gets too expensive for Overpass, so the spacing is
# widened until it fits.
MAX_CORRIDOR_POINTS = 300
MAX_RESULTS = 400

# kind -> Overpass tag selectors. Queried as `nwr`, not `node`: a park, a
# palazzo or a large monument is a way or a relation in OSM, and asking only
# for nodes silently drops about a third of everything worth seeing.
CATEGORIES: List[Tuple[str, List[str]]] = [
    ("water", [
        '["amenity"~"^(drinking_water|water_point)$"]',
        '["man_made"="water_tap"]["drinking_water"!="no"]',
    ]),
    ("toilets", ['["amenity"="toilets"]']),
    ("green", ['["leisure"~"^(park|garden|nature_reserve)$"]']),
    ("viewpoint", ['["tourism"="viewpoint"]', '["natural"="peak"]']),
    ("monument", ['["historic"]', '["tourism"="attraction"]']),
    ("art", ['["tourism"="artwork"]']),
    ("bike", [
        '["amenity"~"^(bicycle_repair_station|compressed_air)$"]',
        '["shop"="bicycle"]',
    ]),
]

KIND_BY_TAG: Dict[Tuple[str, str], str] = {
    ("amenity", "drinking_water"): "water",
    ("amenity", "water_point"): "water",
    ("man_made", "water_tap"): "water",
    ("amenity", "toilets"): "toilets",
    ("leisure", "park"): "green",
    ("leisure", "garden"): "green",
    ("leisure", "nature_reserve"): "green",
    ("tourism", "viewpoint"): "viewpoint",
    ("natural", "peak"): "viewpoint",
    ("tourism", "artwork"): "art",
    ("tourism", "attraction"): "monument",
    ("amenity", "bicycle_repair_station"): "bike",
    ("amenity", "compressed_air"): "bike",
    ("shop", "bicycle"): "bike",
}

KINDS = [name for name, _ in CATEGORIES]


def classify(tags: Dict[str, str]) -> Optional[str]:
    """Name the category an element belongs to, or None if we do not want it."""
    for (key, value), kind in KIND_BY_TAG.items():
        if tags.get(key) == value:
            return kind
    # Any `historic=*` counts: monument, memorial, castle, building, ruins.
    if tags.get("historic") and tags.get("historic") != "no":
        return "monument"
    return None


def corridor(
    routes: Sequence[Sequence[Sequence[float]]],
    spacing_m: float = CORRIDOR_SPACING_M,
) -> List[Tuple[float, float]]:
    """A thinned set of (lat, lon) covering every candidate route.

    Thinning matters more than it looks: candidates share their start, so the
    raw samples are full of duplicates, and any two points closer together than
    the search radius cover almost the same ground. Every extra point is
    repeated in every clause of the query, and an oversized `around` polyline
    is what makes Overpass answer "too busy".
    """
    def sample_at(spacing: float) -> List[Tuple[float, float]]:
        raw: List[Tuple[float, float]] = []
        for coords in routes:
            length = geo.total_distance_m(coords)
            count = max(4, int(length / spacing) + 1)
            raw.extend(geo.sample_points(coords, count))

        # Dedupe only: candidates share their start, so the raw samples repeat.
        # The threshold has to sit well below the sampling spacing, or it
        # deletes every other point and re-opens the gaps it was meant to close.
        threshold = spacing * 0.5
        kept: List[Tuple[float, float]] = []
        for lat, lon in raw:
            if any(
                geo.haversine_m(lon, lat, k_lon, k_lat) < threshold
                for k_lat, k_lon in kept
            ):
                continue
            kept.append((lat, lon))
        return kept

    # Widen the spacing until the query is affordable rather than truncating,
    # which would leave a silent hole in one part of the route.
    spacing = spacing_m
    points = sample_at(spacing)
    while len(points) > MAX_CORRIDOR_POINTS and spacing < 2000:
        spacing *= 1.4
        points = sample_at(spacing)
    return points


# Overpass evaluates `around` once per point per clause, so cost is
# points x clauses — not query length. Covering five 10 km routes needs a few
# hundred points, which leaves room for exactly one clause. So `historic` is
# enumerated rather than matched by presence: it costs a longer value list
# instead of a second pass over the whole polyline.
BROAD_KEYS = "amenity|leisure|tourism|natural|shop|man_made|historic"
BROAD_VALUES = "|".join([
    "drinking_water", "water_point", "water_tap", "toilets",
    "park", "garden", "nature_reserve",
    "viewpoint", "peak", "artwork", "attraction",
    "bicycle_repair_station", "compressed_air", "bicycle",
    # the long tail of historic=* that actually occurs
    "monument", "memorial", "castle", "ruins", "archaeological_site",
    "building", "church", "yes", "city_gate", "tower", "wayside_shrine",
    "manor", "fort", "aqueduct", "citywalls", "tomb", "chapel",
])


def build_query(points: Sequence[Tuple[float, float]], radius_m: int) -> str:
    if not points:
        return ""
    line = ",".join("{:.5f},{:.5f}".format(lat, lon) for lat, lon in points)
    around = "(around:{},{})".format(radius_m, line)
    clauses = ['  nwr{}[~"^({})$"~"^({})$"];'.format(around, BROAD_KEYS, BROAD_VALUES)]
    # `out center` gives ways and relations a single representative point.
    return "[out:json][timeout:50];\n(\n{}\n);\nout center {};".format(
        "\n".join(clauses), MAX_RESULTS
    )


async def fetch(
    points: Sequence[Tuple[float, float]],
    radius_m: int = DEFAULT_RADIUS_M,
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[List[Dict[str, object]], bool]:
    """Query Overpass for POIs in the corridor.

    Returns (pois, ok). A failure degrades to an empty list — a missing water
    fountain must never take the whole page down — but ``ok`` says whether the
    lookup actually ran, so "none nearby" is never confused with "it broke".
    """
    query = build_query(points, radius_m)
    if not query:
        return [], True

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_S)
    try:
        payload = None
        for attempt in range(OVERPASS_ROUNDS):
            for url in OVERPASS_URLS:
                try:
                    response = await client.post(
                        url,
                        data={"data": query},
                        headers={"User-Agent": USER_AGENT},
                        timeout=OVERPASS_TIMEOUT_S,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    break
                except (httpx.HTTPError, ValueError):
                    # "Too busy" is transient and uncorrelated between hosts,
                    # so the next mirror — or the next round — often works.
                    continue
            if payload is not None:
                break
            if attempt + 1 < OVERPASS_ROUNDS:
                await asyncio.sleep(OVERPASS_RETRY_DELAY_S)
        if payload is None:
            return [], False
    finally:
        if owns_client:
            await client.aclose()

    return _parse_elements(payload), True


def _parse_elements(payload: Dict[str, object]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    seen_ids = set()
    seen_places = set()

    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        kind = classify(tags)
        if kind is None:
            continue

        # A way and a node can share an id, so the type has to be part of the key.
        identity = "{}/{}".format(element.get("type"), element.get("id"))
        if identity in seen_ids:
            continue

        # Ways and relations carry their representative point in `center`.
        if "lat" in element and "lon" in element:
            lat, lon = element["lat"], element["lon"]
        else:
            centre = element.get("center") or {}
            if "lat" not in centre or "lon" not in centre:
                continue
            lat, lon = centre["lat"], centre["lon"]

        # A park mapped as both a relation and its member ways would otherwise
        # land three pins on the same lawn.
        place = (kind, round(float(lat), 4), round(float(lon), 4))
        if place in seen_places:
            continue

        seen_ids.add(identity)
        seen_places.add(place)
        out.append({
            "id": identity,
            "kind": kind,
            "name": tags.get("name") or None,
            "lat": float(lat),
            "lon": float(lon),
        })
    return out


def assign(
    pois: Iterable[Dict[str, object]],
    routes: Dict[str, Sequence[Sequence[float]]],
    radius_m: int = MATCH_RADIUS_M,
) -> Tuple[List[Dict[str, object]], Dict[str, Dict[str, int]]]:
    """Tag each POI with the routes it sits near, and count them per route.

    The Overpass corridor covers every candidate at once, so a fountain found
    for one route has to be matched back to the routes it actually serves.
    """
    # Dense enough that a POI beside the route is never missed between samples.
    sampled = {
        route_id: geo.sample_points(
            coords, max(20, int(geo.total_distance_m(coords) / 40))
        )
        for route_id, coords in routes.items()
    }
    counts: Dict[str, Dict[str, int]] = {
        route_id: {kind: 0 for kind in KINDS} for route_id in routes
    }

    kept: List[Dict[str, object]] = []
    for poi in pois:
        near: List[str] = []
        for route_id, points in sampled.items():
            for lat, lon in points:
                if geo.haversine_m(poi["lon"], poi["lat"], lon, lat) <= radius_m:
                    near.append(route_id)
                    counts[route_id][poi["kind"]] += 1
                    break
        if near:
            item = dict(poi)
            item["routes"] = near
            kept.append(item)
    return kept, counts


def score(
    counts: Dict[str, Dict[str, int]],
    distances_m: Dict[str, float],
    sport: str,
) -> Dict[str, Dict[str, float]]:
    """Turn POI counts into per-route water and scenery scores.

    Water is judged on an absolute scale — a 20 km run with no fountain is bad
    however good the alternatives are — while scenery is comparative, because
    "worth looking at" only means anything next to the other options.
    """
    weights = config.POI_WEIGHTS.get(sport, config.POI_WEIGHTS["running"])

    water: Dict[str, float] = {}
    density: Dict[str, float] = {}
    for route_id, per_kind in counts.items():
        km = max(0.1, distances_m.get(route_id, 0.0) / 1000.0)
        wanted = max(1.0, km / config.WATER_INTERVAL_KM)
        water[route_id] = min(1.0, per_kind.get("water", 0) / wanted)
        scenic = sum(per_kind.get(kind, 0) for kind in config.SCENERY_KINDS)
        density[route_id] = scenic / km

    low = min(density.values()) if density else 0.0
    high = max(density.values()) if density else 0.0

    out: Dict[str, Dict[str, float]] = {}
    for route_id in counts:
        scenery = 1.0 if high <= low else (density[route_id] - low) / (high - low)
        out[route_id] = {
            "water": round(water[route_id], 4),
            "scenery": round(scenery, 4),
            "bonus": round(
                weights["water"] * water[route_id] + weights["scenery"] * scenery, 4
            ),
        }
    return out


def blend(previous_total: float, bonus: float) -> float:
    """Fold the POI bonus into a score that was already computed without it."""
    share = config.POI_SHARE
    return round(max(0.0, min(1.0, previous_total * (1 - share) + bonus * share)), 4)
