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
        self, text: str, near: Optional[Tuple[float, float]] = None
    ) -> List[Dict[str, object]]:
        query = " ".join(text.split())
        if len(query) < 2:
            return []

        key = "{}|{}".format(
            query.lower(),
            "{:.2f},{:.2f}".format(*near) if near else "",
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        # Ask for extra, because deduping collapses a lot of them.
        params: Dict[str, object] = {"q": query, "limit": config.GEOCODE_RESULTS * 3}
        if near:
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
            results.append({
                "label": label,
                "lat": float(coords[1]),
                "lon": float(coords[0]),
                "region": region,
            })

        results = results[:config.GEOCODE_RESULTS]
        self._cache.set(key, results)
        return results
