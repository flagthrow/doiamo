"""Environmental context from Open-Meteo (free, no API key)."""
from __future__ import annotations

import asyncio
from typing import Dict, Iterable, List, Optional, Tuple

import httpx

from . import config
from .models import WeatherContext

AIR_VARIABLES = ["european_aqi", "pm2_5", "pm10", "nitrogen_dioxide", "ozone"]
WEATHER_VARIABLES = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "uv_index",
]

GridKey = Tuple[float, float]


def grid_key(lat: float, lon: float) -> GridKey:
    """Snap a point to the ~11 km CAMS cell it falls in.

    Sampling five points on each of a dozen candidate loops in Milan yields
    sixty lookups but only a handful of distinct cells, so dedupe before asking.
    """
    step = config.AIR_GRID_DEG
    return (round(lat / step) * step, round(lon / step) * step)


def _first(values, index: int):
    if isinstance(values, list):
        return values[index] if index < len(values) else None
    return values if index == 0 else None


async def fetch_weather(
    client: httpx.AsyncClient, lat: float, lon: float
) -> WeatherContext:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(WEATHER_VARIABLES),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }
    try:
        response = await client.get(config.OPEN_METEO_FORECAST, params=params)
        response.raise_for_status()
        current = (response.json() or {}).get("current", {}) or {}
    except (httpx.HTTPError, ValueError):
        return WeatherContext()

    return WeatherContext(
        temperature_c=current.get("temperature_2m"),
        apparent_temperature_c=current.get("apparent_temperature"),
        precipitation_mm=current.get("precipitation"),
        wind_speed_kmh=current.get("wind_speed_10m"),
        wind_direction_deg=current.get("wind_direction_10m"),
        uv_index=current.get("uv_index"),
    )


async def fetch_air_by_cell(
    client: httpx.AsyncClient, cells: Iterable[GridKey]
) -> Dict[GridKey, Dict[str, float]]:
    """One batched Open-Meteo call for every distinct grid cell."""
    unique: List[GridKey] = sorted(set(cells))
    if not unique:
        return {}

    params = {
        "latitude": ",".join("{:.4f}".format(c[0]) for c in unique),
        "longitude": ",".join("{:.4f}".format(c[1]) for c in unique),
        "current": ",".join(AIR_VARIABLES),
    }
    try:
        response = await client.get(config.OPEN_METEO_AIR, params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}

    # Open-Meteo returns a bare object for one location and a list for several.
    entries = payload if isinstance(payload, list) else [payload]

    out: Dict[GridKey, Dict[str, float]] = {}
    for cell, entry in zip(unique, entries):
        current = (entry or {}).get("current", {}) or {}
        readings = {
            name: current[name]
            for name in AIR_VARIABLES
            if current.get(name) is not None
        }
        if readings:
            out[cell] = readings
    return out


async def gather_context(
    lat: float,
    lon: float,
    cells: Iterable[GridKey],
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[WeatherContext, Dict[GridKey, Dict[str, float]]]:
    """Weather at the start point plus air quality for every sampled cell."""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        weather, air = await asyncio.gather(
            fetch_weather(client, lat, lon),
            fetch_air_by_cell(client, cells),
        )
        return weather, air
    finally:
        if owns_client:
            await client.aclose()
