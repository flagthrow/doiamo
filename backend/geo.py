"""Geometry helpers: distances, bearings, and honest elevation gain."""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

EARTH_RADIUS_M = 6371008.8

# Coordinates arrive from ORS as [lon, lat] or [lon, lat, elevation].
Coord = Sequence[float]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two lon/lat points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial compass bearing from point 1 to point 2, in degrees [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def segment_lengths_m(coords: Sequence[Coord]) -> List[float]:
    """Length of each segment; the returned list is one shorter than coords."""
    out: List[float] = []
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        out.append(haversine_m(a[0], a[1], b[0], b[1]))
    return out


def total_distance_m(coords: Sequence[Coord]) -> float:
    return sum(segment_lengths_m(coords))


def _moving_average(values: Sequence[float], window: int) -> List[float]:
    """Centred moving average, clamped at the ends."""
    if window <= 1 or len(values) <= window:
        return list(values)
    half = window // 2
    out: List[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def elevation_gain_m(
    coords: Sequence[Coord],
    smoothing_window: int = 5,
    threshold_m: float = 3.0,
) -> Tuple[float, float]:
    """Cumulative ascent and descent in metres.

    Naively summing every positive difference between consecutive SRTM samples
    inflates gain badly, because the DEM noise floor is a couple of metres and
    a 10 km route has thousands of samples. So: smooth first, then only commit
    a climb once it clears ``threshold_m`` above the last committed reference.
    Returns (ascent, descent), both positive.
    """
    elevations = [c[2] for c in coords if len(c) >= 3]
    if len(elevations) < 2:
        return 0.0, 0.0

    smoothed = _moving_average(elevations, smoothing_window)

    ascent = descent = 0.0
    reference = smoothed[0]
    for value in smoothed[1:]:
        delta = value - reference
        if delta >= threshold_m:
            ascent += delta
            reference = value
        elif delta <= -threshold_m:
            descent += -delta
            reference = value
    return ascent, descent


def headwind_exposure(
    coords: Sequence[Coord],
    wind_from_deg: float,
    wind_speed_kmh: float,
) -> float:
    """Share of the route spent riding or running into the wind, 0..1.

    ``wind_from_deg`` follows the meteorological convention (the direction the
    wind blows *from*), so heading towards it means a headwind. Weighted by
    distance and scaled by wind speed, since a 3 km/h breeze is not a factor.
    A closed loop always takes some headwind; routes with long straights lined
    up against the wind take noticeably more.
    """
    if wind_speed_kmh is None or wind_speed_kmh <= 0 or len(coords) < 2:
        return 0.0

    speed_factor = min(1.0, wind_speed_kmh / 25.0)
    weighted = 0.0
    total = 0.0
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        length = haversine_m(a[0], a[1], b[0], b[1])
        if length <= 0:
            continue
        travel = bearing_deg(a[0], a[1], b[0], b[1])
        component = math.cos(math.radians(travel - wind_from_deg))
        weighted += length * max(0.0, component)
        total += length

    if total <= 0:
        return 0.0
    return (weighted / total) * speed_factor


def sample_points(coords: Sequence[Coord], count: int) -> List[Tuple[float, float]]:
    """``count`` points spaced evenly along the route, as (lat, lon)."""
    if not coords:
        return []
    if count <= 1 or len(coords) <= count:
        return [(c[1], c[0]) for c in coords[:count]] or [(coords[0][1], coords[0][0])]

    lengths = segment_lengths_m(coords)
    total = sum(lengths)
    if total <= 0:
        return [(coords[0][1], coords[0][0])]

    targets = [total * i / (count - 1) for i in range(count)]
    out: List[Tuple[float, float]] = []
    walked = 0.0
    idx = 0
    for target in targets:
        while idx < len(lengths) and walked + lengths[idx] < target:
            walked += lengths[idx]
            idx += 1
        point = coords[min(idx, len(coords) - 1)]
        out.append((point[1], point[0]))
    return out


def ellipse_via_points(
    start: Tuple[float, float],
    end: Tuple[float, float],
    sum_m: float,
    count: int = 8,
) -> List[Tuple[float, float]]:
    """Waypoints that stretch an A-to-B route to a chosen length.

    Every point on an ellipse with foci A and B has |AV| + |VB| equal to the
    major axis, so routing through one lengthens the trip by a predictable
    amount. Sampling around that ellipse gives detours of roughly the right
    size pointing in different directions — the A-to-B answer to the loop
    search's seeds. Both are (lat, lon); the return is (lat, lon) too.
    """
    lat1, lon1 = start
    lat2, lon2 = end
    straight = haversine_m(lon1, lat1, lon2, lat2)
    if count < 1 or straight <= 0:
        return []

    # A degenerate ellipse (the target barely exceeds the straight line) has no
    # width to sample, so there is nothing useful to offer.
    semi_major = sum_m / 2.0
    semi_focal = straight / 2.0
    if semi_major <= semi_focal * 1.02:
        return []
    semi_minor = math.sqrt(semi_major ** 2 - semi_focal ** 2)

    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0
    metres_per_deg_lat = 111132.0
    metres_per_deg_lon = 111320.0 * math.cos(math.radians(mid_lat))
    if metres_per_deg_lon <= 0:
        return []

    # Local frame with x pointing from A to B.
    axis = math.atan2(
        (lat2 - lat1) * metres_per_deg_lat,
        (lon2 - lon1) * metres_per_deg_lon,
    )
    cos_a, sin_a = math.cos(axis), math.sin(axis)

    out: List[Tuple[float, float]] = []
    for i in range(count):
        # Skip the poles of the ellipse: a via point in line with A and B adds
        # length without adding any variety.
        theta = math.pi * (0.5 + i) / count
        side = 1.0 if i % 2 == 0 else -1.0
        x = semi_major * math.cos(theta)
        y = semi_minor * math.sin(theta) * side

        east = x * cos_a - y * sin_a
        north = x * sin_a + y * cos_a
        out.append((
            mid_lat + north / metres_per_deg_lat,
            mid_lon + east / metres_per_deg_lon,
        ))
    return out


def bbox_contains(bbox: Sequence[float], lat: float, lon: float) -> bool:
    """bbox is [min_lat, min_lon, max_lat, max_lon]."""
    return bbox[0] <= lat <= bbox[2] and bbox[1] <= lon <= bbox[3]


def centroid(coords: Sequence[Coord]) -> Tuple[float, float]:
    """Mean (lat, lon) of the geometry."""
    if not coords:
        return (0.0, 0.0)
    lat = sum(c[1] for c in coords) / len(coords)
    lon = sum(c[0] for c in coords) / len(coords)
    return (lat, lon)
