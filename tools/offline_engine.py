"""A routing engine that invents plausible routes, for working without a key.

Everything else in the app stays real in this mode: weather, air quality and
points of interest all come from free APIs that need no key and have no
meaningful quota. Only the routing — the expensive, metered part — is faked,
which is enough to work on the results page indefinitely.

Geometry is deliberately staircased onto a rough street grid rather than drawn
as smooth curves: a perfect circle on a map looks obviously wrong and hides
layout problems that real routes would show.
"""
from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence, Tuple

from backend.routing.base import RawRoute, RoutingEngine

# Surface and waytype mixes that read like different kinds of city route.
SURFACE_MIXES = [
    {3: 1.0},                       # all asphalt
    {3: 0.72, 10: 0.28},            # some gravel
    {11: 0.55, 3: 0.45},            # mostly dirt
    {14: 0.5, 3: 0.5},              # paving stones
]
WAYTYPE_MIXES = [
    {3: 0.8, 7: 0.2},               # streets and footways
    {1: 0.4, 3: 0.6},               # a lot of main road
    {6: 0.7, 4: 0.3},               # cycleway and path
    {3: 0.5, 2: 0.5},               # streets and roads
]

BLOCK_M = 110.0     # rough city block, the step the staircase moves in


def _staircase(
    points: Sequence[Tuple[float, float]], lat: float
) -> List[List[float]]:
    """Turn a smooth line into axis-aligned steps, the way streets run."""
    lon_scale = 1 / math.cos(math.radians(lat))
    step_lat = BLOCK_M / 111132.0
    step_lon = step_lat * lon_scale

    # The first vertex has to be the point that was asked for, or the route
    # starts a block away from the address the user typed.
    out: List[List[float]] = [[points[0][1], points[0][0]]]
    for (lat_a, lon_a), (lat_b, lon_b) in zip(points, points[1:]):
        d_lat, d_lon = lat_b - lat_a, lon_b - lon_a
        n_lat = max(1, int(abs(d_lat) / step_lat))
        n_lon = max(1, int(abs(d_lon) / step_lon))
        # Alternate which axis leads, so the result is not a single L shape.
        if len(out) % 2 == 0:
            for i in range(n_lon):
                out.append([lon_a + d_lon * (i + 1) / n_lon, lat_a])
            for i in range(n_lat):
                out.append([lon_b, lat_a + d_lat * (i + 1) / n_lat])
        else:
            for i in range(n_lat):
                out.append([lon_a, lat_a + d_lat * (i + 1) / n_lat])
            for i in range(n_lon):
                out.append([lon_a + d_lon * (i + 1) / n_lon, lat_b])
    return out


def _with_elevation(coords: List[List[float]], climb_m: float) -> List[List[float]]:
    n = max(1, len(coords) - 1)
    return [
        [lon, lat, 100.0 + (climb_m / 2) * (1 - math.cos(2 * math.pi * i / n))]
        for i, (lon, lat) in enumerate(coords)
    ]


def _loop_outline(
    lat: float, lon: float, distance_m: float, index: int
) -> List[Tuple[float, float]]:
    """A wandering closed outline that starts and ends on the given point."""
    radius = (distance_m / (2 * math.pi)) / 111320.0
    lon_scale = 1 / math.cos(math.radians(lat))
    bearing = 2 * math.pi * index / 8
    centre_lat = lat + radius * math.sin(bearing)
    centre_lon = lon + radius * math.cos(bearing) * lon_scale
    start = bearing + math.pi

    points: List[Tuple[float, float]] = []
    steps = 26
    for k in range(steps + 1):
        a = start + 2 * math.pi * k / steps
        wobble = 1 + 0.14 * math.sin(3 * a + index) + 0.07 * math.cos(5 * a)
        points.append((
            centre_lat + radius * wobble * math.sin(a),
            centre_lon + radius * wobble * math.cos(a) * lon_scale,
        ))
    points[0] = (lat, lon)
    points[-1] = (lat, lon)
    return points


def _path_outline(
    start: Tuple[float, float], end: Tuple[float, float], bow: float
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    steps = 18
    for k in range(steps + 1):
        f = k / steps
        swell = math.sin(math.pi * f)
        points.append((
            start[0] + (end[0] - start[0]) * f + bow * swell,
            start[1] + (end[1] - start[1]) * f - bow * swell * 1.4,
        ))
    points[0], points[-1] = start, end
    return points


class OfflineEngine(RoutingEngine):
    name = "offline"
    configured = True

    async def round_trips(self, lat, lon, length_m, sport, surface, seeds):
        rng = random.Random(7)
        routes = []
        for i, _ in enumerate(seeds[:8]):
            distance = length_m * rng.uniform(0.9, 1.12)
            coords = _with_elevation(
                _staircase(_loop_outline(lat, lon, distance, i), lat),
                rng.uniform(15, 90),
            )
            routes.append(self._raw(coords, distance, i))
        return routes, []

    async def point_to_point(self, start, end, sport, surface, length_m=None):
        straight = math.hypot(
            (end[0] - start[0]) * 111320,
            (end[1] - start[1]) * 111320 * math.cos(math.radians(start[0])),
        )
        if length_m:
            plan = [(0.020, 0.96, 25), (-0.030, 1.02, 95), (0.040, 1.09, 50),
                    (-0.015, 0.99, 35), (0.030, 1.05, 70)]
            base = length_m
        else:
            plan = [(0.0, 1.18, 20), (0.012, 1.32, 80), (-0.009, 1.26, 45)]
            base = straight

        routes = []
        for i, (bow, factor, climb) in enumerate(plan):
            distance = max(base * factor, 500)
            coords = _with_elevation(
                _staircase(_path_outline(start, end, bow), start[0]), climb
            )
            routes.append(self._raw(coords, distance, i))
        return routes, []

    async def geocode(self, text, near=None):
        """Nominatim: free, keyless, and real — so the search box behaves."""
        import httpx

        params = {"q": text, "format": "jsonv2", "limit": 6, "addressdetails": 1}
        if near:
            params["viewbox"] = "{},{},{},{}".format(
                near[1] - 0.3, near[0] + 0.3, near[1] + 0.3, near[0] - 0.3
            )
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params=params,
                    headers={"User-Agent": "Doiamo-dev/0.1 (+https://doiamo.com)"},
                )
                response.raise_for_status()
                rows = response.json()
        except Exception:
            return []

        out = []
        for row in rows:
            address = row.get("address") or {}
            out.append({
                "label": row.get("display_name", "").split(",")[0]
                         + (", " + address["city"] if address.get("city") else ""),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "region": address.get("state") or address.get("county"),
            })
        return out

    @staticmethod
    def _raw(coords, distance_m: float, index: int) -> RawRoute:
        surface = SURFACE_MIXES[index % len(SURFACE_MIXES)]
        waytype = WAYTYPE_MIXES[index % len(WAYTYPE_MIXES)]
        return RawRoute(
            coordinates=coords,
            distance_m=distance_m,
            duration_s=distance_m / 2.8,
            surface={k: v * distance_m for k, v in surface.items()},
            waytype={k: v * distance_m for k, v in waytype.items()},
            seed=index,
        )

    async def aclose(self) -> None:
        return None
