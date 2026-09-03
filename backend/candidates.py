"""Candidate generation, filtering, and multi-objective scoring.

The router hands back a dozen loops that merely start and end at the same
place. Everything that makes one of them the *right* loop happens here.
"""
from __future__ import annotations

import hashlib
import random
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config, energy, geo
from .health import grid_key
from .models import (
    AirContext,
    RouteCandidate,
    RouteScores,
    SearchRequest,
    SurfaceBreakdown,
    WeatherContext,
)
from .routing.base import RawRoute

# Points sampled per route for the air-quality lookup.
AIR_SAMPLES_PER_ROUTE = 5

# A candidate this far off the requested distance is not what was asked for.
DISTANCE_REJECT_RATIO = 0.35

# Below this fraction of what was asked for, a candidate is not a loose match,
# it is a failed generation: ORS round_trip sometimes cannot build the loop a
# seed asks for and returns a stub instead. Three 1 km options in answer to
# "una corsa" are worse than none — nobody drives to the Colosseum for a
# thousand metres — so they are dropped even when nothing else survives, and
# the page says why rather than showing them.
DISTANCE_FLOOR_RATIO = 0.5

# Geometry overlap above which two candidates are treated as the same loop.
DUPLICATE_OVERLAP = 0.7


def build_seeds(count: int, rng: Optional[random.Random] = None) -> List[Tuple[int, int]]:
    """(seed, waypoint_count) pairs spread across the router's search space.

    Varying the waypoint count as well as the seed matters: three waypoints
    gives wide, simple loops, seven gives twistier ones that fit a distance
    target in a dense city centre.
    """
    rng = rng or random.Random()
    point_options = [3, 4, 5, 6, 7]
    return [
        (rng.randint(1, 10_000_000), point_options[i % len(point_options)])
        for i in range(count)
    ]


def _weighted_share(distances: Dict[int, float], table: Dict[int, float]) -> float:
    """Distance-weighted mean of ``table`` over the classes actually travelled."""
    total = sum(distances.values())
    if total <= 0:
        return 0.5
    acc = sum(metres * table.get(cls, 0.5) for cls, metres in distances.items())
    return acc / total


def _share_of(distances: Dict[int, float], keys: Sequence[int]) -> float:
    """Plain share of the route's length spent on the given waytypes — unlike
    the exposure score this is a fraction of metres, so it can carry a ceiling."""
    total = sum(distances.values())
    if total <= 0:
        return 0.0
    return sum(distances.get(key, 0.0) for key in keys) / total


def _breakdown(
    distances: Dict[int, float], labels: Dict[int, str]
) -> List[SurfaceBreakdown]:
    total = sum(distances.values())
    if total <= 0:
        return []
    rows = [
        SurfaceBreakdown(
            label=labels.get(cls, str(cls)),
            distance_m=round(metres, 1),
            share=metres / total,
        )
        for cls, metres in distances.items()
        if metres > 0
    ]
    rows.sort(key=lambda r: r.distance_m, reverse=True)
    return rows


def _distance_score(actual_m: float, target_m: float) -> float:
    if target_m <= 0:
        return 0.0
    tolerance = 0.25 * target_m
    return max(0.0, 1.0 - abs(actual_m - target_m) / tolerance)


def _relative_score(value: float, values: List[float], lower_is_better: bool = True) -> float:
    """Rank one value against its siblings, 0..1.

    Used in route mode, where distance and climb are not targets the user set —
    they are whatever the ground dictates — so the only meaningful question is
    which of these alternatives is the more direct, or the flatter, one.
    """
    if not values:
        return 0.5
    low, high = min(values), max(values)
    if high <= low:
        return 1.0
    share = (value - low) / (high - low)
    return 1.0 - share if lower_is_better else share


def _directness_score(actual_m: float, shortest_m: float) -> float:
    """1.0 for the shortest way there, falling off as a detour grows."""
    if actual_m <= 0 or shortest_m <= 0:
        return 0.5
    return max(0.0, min(1.0, shortest_m / actual_m))


def _gain_score(actual_m: float, target_m: float) -> float:
    # Flat-city targets need an absolute floor, or asking for 50 m of climb in
    # Milan makes every candidate score zero on a 20 m difference.
    tolerance = max(60.0, 0.5 * target_m)
    return max(0.0, 1.0 - abs(actual_m - target_m) / tolerance)


def _surface_score(paved_share: float, preference: str) -> float:
    if preference == "asphalt":
        return paved_share
    if preference == "trail":
        return 1.0 - paved_share
    return 1.0 - abs(paved_share - 0.5) * 2.0


def _geometry_cells(coords: Sequence[Sequence[float]]) -> set:
    """Coarse ~100 m cells covered by a route, for duplicate detection."""
    return {(round(c[1], 3), round(c[0], 3)) for c in coords}


def _is_duplicate(cells: set, seen: List[set]) -> bool:
    for other in seen:
        union = len(cells | other)
        if union == 0:
            continue
        if len(cells & other) / union >= DUPLICATE_OVERLAP:
            return True
    return False


def _route_id(coords: Sequence[Sequence[float]]) -> str:
    digest = hashlib.sha1()
    for lon, lat in ((c[0], c[1]) for c in coords[::10]):
        digest.update("{:.5f},{:.5f};".format(lon, lat).encode())
    return digest.hexdigest()[:16]


def air_cells_for_routes(routes: Iterable[RawRoute]) -> List[Tuple[float, float]]:
    """Every distinct air-quality grid cell the candidates pass through."""
    cells = set()
    for route in routes:
        for lat, lon in geo.sample_points(route.coordinates, AIR_SAMPLES_PER_ROUTE):
            cells.add(grid_key(lat, lon))
    return sorted(cells)


class _Measured:
    """A raw route with its physical measurements taken, before scoring."""

    __slots__ = (
        "raw", "coords", "distance_m", "ascent_m", "descent_m", "paved_share",
        "traffic_exposure", "big_road_share", "urban_share", "headwind_share",
        "step_distance_m", "air", "cells",
    )

    def __init__(self, raw: RawRoute, weather: WeatherContext) -> None:
        self.raw = raw
        self.coords = raw.coordinates
        self.distance_m = raw.distance_m or geo.total_distance_m(self.coords)

        # Prefer our own smoothed figure: it is comparable across candidates and
        # is not inflated by DEM noise the way a raw per-sample sum would be.
        ascent, descent = geo.elevation_gain_m(self.coords)
        self.ascent_m = ascent
        self.descent_m = descent

        self.paved_share = _weighted_share(raw.surface, config.SURFACE_PAVEDNESS)
        self.traffic_exposure = _weighted_share(raw.waytype, config.WAYTYPE_EXPOSURE)
        self.big_road_share = _share_of(raw.waytype, config.BIG_ROAD_WAYTYPES)
        self.urban_share = _share_of(raw.waytype, config.URBAN_WAYTYPES)
        self.step_distance_m = raw.waytype.get(config.STEPS_WAYTYPE, 0.0)
        self.headwind_share = geo.headwind_exposure(
            self.coords,
            weather.wind_direction_deg or 0.0,
            weather.wind_speed_kmh or 0.0,
        )
        self.air: Optional[Dict[str, float]] = None
        self.cells: List[Tuple[float, float]] = []


def _attach_air(
    measured: List[_Measured], air_by_cell: Dict[Tuple[float, float], Dict[str, float]]
) -> AirContext:
    """Average the sampled air readings onto each route, and decide whether the
    result can separate the candidates at all."""
    aqis: List[float] = []

    for item in measured:
        readings: List[Dict[str, float]] = []
        for cell in item.cells:
            found = air_by_cell.get(cell)
            if found:
                readings.append(found)
        if not readings:
            continue
        averaged = {}
        for name in readings[0]:
            values = [r[name] for r in readings if r.get(name) is not None]
            if values:
                averaged[name] = sum(values) / len(values)
        item.air = averaged
        if "european_aqi" in averaged:
            aqis.append(averaged["european_aqi"])

    city = AirContext()
    if not aqis:
        return city

    spread = max(aqis) - min(aqis)
    pooled: Dict[str, List[float]] = {}
    for item in measured:
        for name, value in (item.air or {}).items():
            pooled.setdefault(name, []).append(value)

    city = AirContext(
        european_aqi=_mean(pooled.get("european_aqi")),
        pm2_5=_mean(pooled.get("pm2_5")),
        pm10=_mean(pooled.get("pm10")),
        nitrogen_dioxide=_mean(pooled.get("nitrogen_dioxide")),
        ozone=_mean(pooled.get("ozone")),
        spread=round(spread, 2),
        differentiates_routes=spread >= config.AIR_DIFFERENTIATION_MIN_SPREAD,
    )
    return city


def _mean(values: Optional[List[float]]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def rank(
    raw_routes: Sequence[RawRoute],
    request: SearchRequest,
    weather: WeatherContext,
    air_by_cell: Dict[Tuple[float, float], Dict[str, float]],
    limit: int = config.MAX_RESULTS,
) -> Tuple[List[RouteCandidate], AirContext, List[str]]:
    """Measure, filter, score and sort the candidates. Never returns nothing
    when the router returned something: a loose match beats an empty page."""
    notices: List[str] = []
    if not raw_routes:
        return [], AirContext(), notices

    measured = [_Measured(raw, weather) for raw in raw_routes]
    for item in measured:
        item.cells = [
            grid_key(lat, lon)
            for lat, lon in geo.sample_points(item.coords, AIR_SAMPLES_PER_ROUTE)
        ]

    air_context = _attach_air(measured, air_by_cell)

    # A target distance means the same thing in both modes: a loop of that
    # length, or a detour to B that comes out at that length. Without one, an
    # A-to-B route is as long as it is.
    has_distance_target = request.distance_km is not None
    target_m = (request.distance_km or 0.0) * 1000.0
    if has_distance_target:
        usable = [m for m in measured if m.distance_m >= DISTANCE_FLOOR_RATIO * target_m]
        if len(usable) < len(measured):
            notices.append("some_routes_too_short")
        if not usable:
            # Nothing worth showing. Saying so beats offering a stub.
            return [], air_context, ["no_route_of_that_length"]
        measured = usable

        on_target = [
            m for m in measured
            if abs(m.distance_m - target_m) <= DISTANCE_REJECT_RATIO * target_m
        ]
        if on_target:
            measured = on_target
        else:
            notices.append("no_exact_distance_match")

    # Drop the candidates that spend too much of their length beside fast
    # traffic — but only while a calmer sibling survives. In a place where every
    # way out is a state road, an unlabelled empty page helps nobody; the route
    # is kept and the badge says what it is.
    calm = [m for m in measured if m.big_road_share <= config.BIG_ROAD_REJECT_SHARE]
    if calm and len(calm) < len(measured):
        measured = calm
    elif not calm:
        notices.append("busy_roads_only")

    # Air only earns a weight when it actually varies between these routes.
    use_air = air_context.differentiates_routes
    aqi_values = [
        m.air["european_aqi"] for m in measured if m.air and "european_aqi" in m.air
    ]
    aqi_lo = min(aqi_values) if aqi_values else 0.0
    aqi_hi = max(aqi_values) if aqi_values else 0.0

    weights = dict(
        (config.WEIGHTS if request.is_loop else config.ROUTE_WEIGHTS)[request.sport]
    )
    if request.area == "urban":
        # Taken from the other axes rather than added on top, so the weights
        # still sum to one and the totals stay comparable across searches.
        share = config.URBAN_WEIGHT
        for name in weights:
            weights[name] *= 1.0 - share
        weights["urban"] = share
    if not use_air:
        weights.pop("air", None)
    # In loop mode climb only counts when it was asked for. In route mode it
    # always counts: against the target if there is one, otherwise as a live
    # comparison between the alternatives.
    has_gain_target = request.elevation_gain_m is not None
    if request.is_loop and not has_gain_target:
        weights.pop("gain", None)
    total_weight = sum(weights.values()) or 1.0

    shortest_m = min((m.distance_m for m in measured), default=0.0)
    all_ascents = [m.ascent_m for m in measured]

    scored: List[Tuple[float, _Measured, RouteScores]] = []
    for item in measured:
        parts = {
            "distance": (
                _distance_score(item.distance_m, target_m)
                if has_distance_target
                else _directness_score(item.distance_m, shortest_m)
            ),
            "surface": _surface_score(item.paved_share, request.surface),
            "traffic": 1.0 - item.traffic_exposure,
            "wind": 1.0 - item.headwind_share,
        }
        if "urban" in weights:
            # Streets and pavements count for you, farm tracks and trunk roads
            # against; everything else is neither and simply does not vote.
            rural = _share_of(item.raw.waytype, config.RURAL_WAYTYPES)
            parts["urban"] = max(0.0, min(1.0, 0.5 + (item.urban_share - rural) / 2.0))
        if "gain" in weights:
            parts["gain"] = (
                _gain_score(item.ascent_m, request.elevation_gain_m or 0.0)
                if has_gain_target
                else _relative_score(item.ascent_m, all_ascents)
            )

        air_score: Optional[float] = None
        if item.air and "european_aqi" in item.air and aqi_hi > aqi_lo:
            air_score = 1.0 - (item.air["european_aqi"] - aqi_lo) / (aqi_hi - aqi_lo)
        if "air" in weights:
            parts["air"] = air_score if air_score is not None else 0.5

        total = sum(weights[k] * parts[k] for k in weights) / total_weight

        # Steps break a run's rhythm and are simply not rideable.
        if item.distance_m > 0 and item.step_distance_m > 0:
            step_share = item.step_distance_m / item.distance_m
            total -= step_share * (2.0 if request.sport == "cycling" else 0.6)

        total = max(0.0, min(1.0, total))
        scored.append((
            total,
            item,
            RouteScores(
                total=round(total, 4),
                distance=round(parts["distance"], 4),
                gain=round(parts.get("gain", 0.0), 4),
                surface=round(parts["surface"], 4),
                traffic=round(parts["traffic"], 4),
                wind=round(parts["wind"], 4),
                air=round(air_score, 4) if air_score is not None else None,
            ),
        ))

    scored.sort(key=lambda row: row[0], reverse=True)

    # Scoring zero on the one thing that was asked for is not a low score, it
    # is a failure — and ranking it first anyway, silently, presents it as an
    # answer. Where there is no flat loop to be had (L'Aquila has hills in
    # every direction) the honest reply is to say so and show the flattest.
    if has_gain_target and scored:
        best_gain = min(item.ascent_m for _, item, _ in scored)
        asked = request.elevation_gain_m or 0.0
        if best_gain > asked + max(60.0, 0.5 * asked):
            notices.append("gain_target_unreachable")
    if has_distance_target and scored:
        closest = min(abs(item.distance_m - target_m) for _, item, _ in scored)
        if closest > 0.25 * target_m:
            notices.append("distance_target_unreachable")

    out: List[RouteCandidate] = []
    seen_geometries: List[set] = []
    for _, item, scores in scored:
        cells = _geometry_cells(item.coords)
        if _is_duplicate(cells, seen_geometries):
            continue
        seen_geometries.append(cells)
        out.append(
            RouteCandidate(
                id=_route_id(item.coords),
                distance_m=round(item.distance_m, 1),
                ascent_m=round(item.ascent_m, 1),
                descent_m=round(item.descent_m, 1),
                duration_s=item.raw.duration_s,
                coordinates=[[round(c[0], 6), round(c[1], 6)] + (
                    [round(c[2], 1)] if len(c) > 2 else []
                ) for c in item.coords],
                scores=scores,
                paved_share=round(item.paved_share, 4),
                traffic_exposure=round(item.traffic_exposure, 4),
                big_road_share=round(item.big_road_share, 4),
                urban_share=round(item.urban_share, 4),
                calories_kcal=round(energy.kcal_for_route(
                    item.coords, request.sport, request.mass_kg,
                    duration_s=item.raw.duration_s,
                ), 0),
                headwind_share=round(item.headwind_share, 4),
                step_distance_m=round(item.step_distance_m, 1),
                surface_breakdown=_breakdown(item.raw.surface, config.SURFACE_LABELS),
                waytype_breakdown=_breakdown(item.raw.waytype, config.WAYTYPE_LABELS),
                air=item.air,
            )
        )
        if len(out) >= limit:
            break

    return out, air_context, notices
