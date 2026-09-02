"""Place-name search via Photon.

Photon is keyless and, unlike the openrouteservice geocoder, has no daily
allowance — it throttles above roughly five requests a second instead. That
matters because autocomplete spends requests fast: every debounced keystroke is
one, and the 1000/day ORS geocoding limit would run out long before the routing
quota did. Moving here leaves the whole ORS allowance for routing.

It is also built for type-ahead, which Nominatim explicitly is not: their usage
policy caps you at one request a second.
"""
from __future__ import annotations

import math
import unicodedata
from typing import Dict, List, Optional, Tuple

import httpx

from . import config
from .cache import TTLCache
from .throttle import Throttle

PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "Doiamo/0.1 (+https://doiamo.com)"
TIMEOUT_S = 10.0

# Photon throttles above ~5 requests/second, so stay just under it.
MIN_INTERVAL_S = 0.25

# Typing "sempione" sends "se", "sem", "semp"… and backspacing sends them all
# again. A short cache absorbs most of that.
CACHE_TTL_S = 900
CACHE_MAX = 500

# Photon supports only default, de, en and fr. "default" returns local names,
# which for an Italian audience is exactly right: Colosseo, Roma, Italia.


# Photon's `lat`/`lon` bias does not reorder results, it effectively restricts
# them to the neighbourhood: searching "L'Aquila" from Milan returns a dozen
# restaurants and streets and never the city, at any limit. That is right for
# type-ahead — "sempione" should find the one you can run to — and wrong for a
# place someone named in a sentence, where the whole point is that they said
# which one. So the sentence path asks without the bias and uses the location
# to break ties instead of to filter.
_SETTLEMENT_VALUES = {"city", "town", "village", "hamlet", "municipality"}


def _is_settlement(props: Dict[str, object]) -> bool:
    key = str(props.get("osm_key") or "")
    value = str(props.get("osm_value") or "")
    if key == "boundary" and value == "administrative":
        return True
    return key == "place" and value in _SETTLEMENT_VALUES


def _fold(text: object) -> str:
    """Case, accents and curly apostrophes folded away, so a typed "l'aquila"
    matches OSM's "L'Aquila"."""
    folded = unicodedata.normalize("NFD", str(text)).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(folded.replace("\u2019", "'").split())


def _tier(props: Dict[str, object], query: str) -> int:
    """How well this feature answers the name that was asked for."""
    name = _fold(props.get("name") or "")
    if not name:
        return 0
    if name == query:
        return 3 if _is_settlement(props) else 2
    return 1 if name.startswith(query) else 0


def _distance_km(near: Tuple[float, float], lat: float, lon: float) -> float:
    """Rough great-circle distance, only ever used to order two candidates."""
    lat_near, lon_near = near
    mean = math.radians((lat + lat_near) / 2.0)
    dx = (lon - lon_near) * math.cos(mean) * 111.32
    dy = (lat - lat_near) * 110.57
    return math.hypot(dx, dy)


def _label(props: Dict[str, object]) -> Tuple[str, Optional[str]]:
    """Compose a display label and a region line from Photon's parts.

    Photon returns address components rather than a single formatted string,
    so the caller has to assemble something readable.
    """
    name = props.get("name")
    street = props.get("street")
    number = props.get("housenumber")

    if name:
        primary = str(name)
    elif street:
        primary = "{} {}".format(street, number).strip() if number else str(street)
    else:
        primary = str(props.get("city") or props.get("state") or "")

    locality = props.get("city") or props.get("county")
    if locality and str(locality) != primary:
        label = "{}, {}".format(primary, locality)
    else:
        label = primary

    region = ", ".join(
        str(part) for part in (props.get("state"), props.get("country")) if part
    )
    return label, region or None


class PhotonGeocoder:
    name = "photon"

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._throttle = Throttle(MIN_INTERVAL_S)
        self._cache = TTLCache(CACHE_MAX, CACHE_TTL_S)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT_S)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        text: str,
        near: Optional[Tuple[float, float]] = None,
        prefer_place: bool = False,
    ) -> List[Dict[str, object]]:
        query = " ".join(text.split())
        if len(query) < 2:
            return []

        key = "{}|{}|{}".format(
            query.lower(),
            "{:.2f},{:.2f}".format(*near) if near else "",
            "place" if prefer_place else "near",
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        # Ask for extra, because deduping collapses a lot of them.
        params: Dict[str, object] = {"q": query, "limit": config.GEOCODE_RESULTS * 3}
        if near and not prefer_place:
            params["lat"], params["lon"] = near

        await self._throttle.wait()
        try:
            response = await self._get_client().get(
                PHOTON_URL, params=params, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        results: List[Dict[str, object]] = []
        ranked: List[Tuple[int, float, int, Dict[str, object]]] = []
        folded_query = _fold(query)
        seen = set()
        for feature in payload.get("features") or []:
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            props = feature.get("properties") or {}
            if len(coords) < 2:
                continue
            label, region = _label(props)
            if not label:
                continue
            # Photon returns a big place as a node, a way and a street, often
            # hundreds of metres apart. Deduping on the label rather than the
            # position is what matters: three identical lines in a dropdown are
            # useless whatever their coordinates. The city is part of the label,
            # so "Colosseo, Roma" and "Colosseo, Milano" stay distinct.
            identity = label.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            lat, lon = float(coords[1]), float(coords[0])
            row = {"label": label, "lat": lat, "lon": lon, "region": region}
            if prefer_place:
                ranked.append((
                    -_tier(props, folded_query),
                    _distance_km(near, lat, lon) if near else 0.0,
                    len(ranked),          # keeps Photon's order within a tie
                    row,
                ))
            else:
                results.append(row)

        if prefer_place:
            ranked.sort(key=lambda item: item[:3])
            results = [item[3] for item in ranked]

        results = results[:config.GEOCODE_RESULTS]
        self._cache.set(key, results)
        return results
