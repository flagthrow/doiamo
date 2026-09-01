"""OpenRouteService adapter.

Free tier limits are the binding constraint here: 2000 directions/day and 40
per minute, against roughly a dozen requests per user search. Requests are
throttled and failures degrade to a partial result rather than an error page.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .. import config, geo
from .base import RawRoute, RoutingEngine, RoutingError


def _extras_to_distance(extras: Optional[Dict[str, Any]], key: str) -> Dict[int, float]:
    """Turn an ORS extra_info summary into {class value: metres}."""
    if not extras or key not in extras:
        return {}
    out: Dict[int, float] = {}
    for row in extras[key].get("summary", []) or []:
        try:
            value = int(row["value"])
            distance = float(row.get("distance", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        out[value] = out.get(value, 0.0) + distance
    return out


def _forbidden_reason(response: httpx.Response) -> str:
    """Tell a spent quota apart from a bad key."""
    try:
        error = (response.json() or {}).get("error")
    except ValueError:
        error = None
    text = error if isinstance(error, str) else (error or {}).get("message", "")
    if "quota" in str(text).lower():
        return "OpenRouteService daily quota exhausted"
    return "OpenRouteService rejected the API key"


class _Throttle:
    """Serialises request starts so we stay under the per-minute quota."""

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval = min_interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._min_interval - (now - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class ORSEngine(RoutingEngine):
    name = "openrouteservice"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.ORS_API_KEY
        self.base_url = (base_url or config.ORS_BASE_URL).rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.ORS_MAX_CONCURRENCY)
        self._throttle = _Throttle(config.ORS_MIN_INTERVAL_S)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=config.ORS_TIMEOUT_S)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _profile(self, sport: str, surface: str) -> str:
        return config.ORS_PROFILES.get(sport, config.ORS_PROFILES["running"]).get(
            surface, "foot-walking"
        )

    async def round_trips(
        self,
        lat: float,
        lon: float,
        length_m: float,
        sport: str,
        surface: str,
        seeds: Sequence[Tuple[int, int]],
    ) -> Tuple[List[RawRoute], List[str]]:
        if not self.configured:
            raise RoutingError("ORS_API_KEY is not set")

        profile = self._profile(sport, surface)
        tasks = [
            self._one_round_trip(profile, lat, lon, length_m, seed, points)
            for seed, points in seeds
        ]
        settled = await asyncio.gather(*tasks, return_exceptions=True)

        routes: List[RawRoute] = []
        failures: Dict[str, int] = {}
        for item in settled:
            if isinstance(item, RawRoute):
                routes.append(item)
            elif isinstance(item, BaseException):
                reason = type(item).__name__ if not str(item) else str(item)
                failures[reason] = failures.get(reason, 0) + 1

        if not routes and failures:
            reason = max(failures.items(), key=lambda kv: kv[1])[0]
            raise RoutingError(reason)

        notices = [
            "{} of {} candidate requests failed ({})".format(
                count, len(seeds), reason
            )
            for reason, count in failures.items()
        ]
        return routes, notices

    async def _one_round_trip(
        self,
        profile: str,
        lat: float,
        lon: float,
        length_m: float,
        seed: int,
        points: int,
    ) -> RawRoute:
        body = {
            "coordinates": [[lon, lat]],
            "elevation": True,
            "instructions": False,
            "extra_info": ["surface", "waytype", "steepness"],
            "options": {
                "round_trip": {
                    "length": int(length_m),
                    "points": int(points),
                    "seed": int(seed),
                }
            },
        }
        return self._parse(await self._post_directions(profile, body), seed)

    async def _post_directions(self, profile: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = "{}/v2/directions/{}/geojson".format(self.base_url, profile)
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        }

        async with self._semaphore:
            await self._throttle.wait()
            response = await self._get_client().post(url, json=body, headers=headers)

        if response.status_code == 429:
            raise RoutingError("OpenRouteService rate limit reached")
        if response.status_code == 403:
            # 403 covers both "your key is wrong" and "you have used up today's
            # allowance", and telling a user the first when it is the second
            # sends them hunting for a problem that is not there.
            raise RoutingError(_forbidden_reason(response))
        if response.status_code >= 400:
            detail = ""
            try:
                error = (response.json() or {}).get("error") or {}
                detail = error.get("message") or str(error.get("code") or "")
            except ValueError:
                detail = ""
            raise RoutingError(
                "routing error {}{}".format(
                    response.status_code, ": " + detail if detail else ""
                )
            )

        return response.json()

    @staticmethod
    def _parse_feature(feature: Dict[str, Any], seed: Optional[int]) -> RawRoute:
        coordinates = feature.get("geometry", {}).get("coordinates") or []
        if len(coordinates) < 2:
            raise RoutingError("degenerate route")

        properties = feature.get("properties", {}) or {}
        summary = properties.get("summary", {}) or {}
        extras = properties.get("extras", {}) or {}

        return RawRoute(
            coordinates=[list(c) for c in coordinates],
            distance_m=float(summary.get("distance", 0.0)),
            duration_s=summary.get("duration"),
            ascent_m=properties.get("ascent"),
            descent_m=properties.get("descent"),
            surface=_extras_to_distance(extras, "surface"),
            waytype=_extras_to_distance(extras, "waytype"),
            seed=seed,
        )

    @classmethod
    def _parse(cls, payload: Dict[str, Any], seed: int) -> RawRoute:
        features = payload.get("features") or []
        if not features:
            raise RoutingError("no route found")
        return cls._parse_feature(features[0], seed)

    @classmethod
    def _parse_all(cls, payload: Dict[str, Any]) -> List[RawRoute]:
        """Every alternative ORS returned, skipping any that came back broken."""
        routes: List[RawRoute] = []
        for index, feature in enumerate(payload.get("features") or []):
            try:
                routes.append(cls._parse_feature(feature, index))
            except RoutingError:
                continue
        if not routes:
            raise RoutingError("no route found")
        return routes

    async def point_to_point(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        sport: str,
        surface: str,
        length_m: Optional[float] = None,
    ) -> Tuple[List[RawRoute], List[str]]:
        if not self.configured:
            raise RoutingError("ORS_API_KEY is not set")

        profile = self._profile(sport, surface)

        straight_m = geo.haversine_m(start[1], start[0], end[1], end[0])
        wants_detour = (
            length_m is not None
            and straight_m > 0
            and length_m > straight_m * config.DETOUR_MIN_RATIO
        )
        if wants_detour:
            routes, notices = await self._detour_routes(
                profile, start, end, length_m, straight_m
            )
            if routes:
                return routes, notices
            # Nothing threaded through; fall back to the direct alternatives
            # rather than returning an empty page.
            notices = notices + ["detour_unavailable"]
            direct, more = await self._alternative_routes(profile, start, end)
            return direct, notices + more

        notices = [] if length_m is None else ["distance_below_direct"]
        routes, more = await self._alternative_routes(profile, start, end)
        return routes, notices + more

    async def _detour_routes(
        self,
        profile: str,
        start: Tuple[float, float],
        end: Tuple[float, float],
        length_m: float,
        straight_m: float,
    ) -> Tuple[List[RawRoute], List[str]]:
        """Route A -> via -> B for a spread of via points on the length ellipse."""
        budget = max(length_m / config.DETOUR_ROAD_FACTOR, straight_m * 1.05)
        vias = geo.ellipse_via_points(start, end, budget, config.DETOUR_VIA_COUNT)
        if not vias:
            return [], []

        async def one(index: int, via: Tuple[float, float]) -> RawRoute:
            body = {
                "coordinates": [
                    [start[1], start[0]],
                    [via[1], via[0]],
                    [end[1], end[0]],
                ],
                "elevation": True,
                "instructions": False,
                "extra_info": ["surface", "waytype", "steepness"],
            }
            return self._parse(await self._post_directions(profile, body), index)

        settled = await asyncio.gather(
            *[one(i, via) for i, via in enumerate(vias)], return_exceptions=True
        )

        routes: List[RawRoute] = []
        failures: Dict[str, int] = {}
        for item in settled:
            if isinstance(item, RawRoute):
                routes.append(item)
            elif isinstance(item, BaseException):
                reason = str(item) or type(item).__name__
                failures[reason] = failures.get(reason, 0) + 1

        notices = [
            "{} of {} detour candidates failed ({})".format(count, len(vias), reason)
            for reason, count in failures.items()
        ]
        return routes, notices

    async def _alternative_routes(
        self,
        profile: str,
        start: Tuple[float, float],
        end: Tuple[float, float],
    ) -> Tuple[List[RawRoute], List[str]]:
        body = {
            "coordinates": [[start[1], start[0]], [end[1], end[0]]],
            "elevation": True,
            "instructions": False,
            "extra_info": ["surface", "waytype", "steepness"],
            "alternative_routes": {
                "target_count": config.ALTERNATIVE_TARGET_COUNT,
                "weight_factor": config.ALTERNATIVE_WEIGHT_FACTOR,
                "share_factor": config.ALTERNATIVE_SHARE_FACTOR,
            },
        }

        try:
            payload = await self._post_directions(profile, body)
        except RoutingError as exc:
            # Alternatives are refused on some geometries (very short hops, or
            # when no sufficiently distinct second path exists). One route is a
            # better answer than an error page.
            if "alternative" not in str(exc).lower() and "2010" not in str(exc):
                raise
            body.pop("alternative_routes")
            payload = await self._post_directions(profile, body)
            return self._parse_all(payload), ["alternatives_unavailable"]

        return self._parse_all(payload), []

    async def geocode(
        self, text: str, near: Optional[Tuple[float, float]] = None
    ) -> List[Dict[str, object]]:
        if not self.configured:
            raise RoutingError("ORS_API_KEY is not set")

        params: Dict[str, Any] = {
            "api_key": self.api_key,
            "text": text,
            "size": config.ORS_GEOCODE_SIZE,
        }
        if near:
            params["focus.point.lat"] = near[0]
            params["focus.point.lon"] = near[1]

        url = "{}/geocode/autocomplete".format(self.base_url)
        async with self._semaphore:
            response = await self._get_client().get(url, params=params)

        if response.status_code == 429:
            raise RoutingError("OpenRouteService rate limit reached")
        if response.status_code >= 400:
            raise RoutingError("geocoding error {}".format(response.status_code))

        out: List[Dict[str, object]] = []
        for feature in (response.json() or {}).get("features") or []:
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            props = feature.get("properties") or {}
            if len(coords) < 2 or not props.get("label"):
                continue
            out.append({
                "label": props["label"],
                "lat": float(coords[1]),
                "lon": float(coords[0]),
                "region": props.get("region") or props.get("county"),
            })
        return out
