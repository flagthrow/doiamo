"""Request and response schemas."""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from .config import AREAS, MODES, SIGHTS, SPORTS, SURFACE_PREFERENCES


class SearchRequest(BaseModel):
    """A loop from one point, or a route between two.

    In loop mode the distance is the constraint the router is given. In route
    mode the distance is whatever the ground dictates, so it becomes a
    *relative* preference instead: of the alternatives, the more direct ones
    score better.
    """

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    mode: str = Field("loop")
    end_lat: Optional[float] = Field(None, ge=-90, le=90)
    end_lon: Optional[float] = Field(None, ge=-180, le=180)
    distance_km: Optional[float] = Field(None, gt=0.5, le=200)
    elevation_gain_m: Optional[float] = Field(None, ge=0, le=6000)
    sport: str = Field("running")
    surface: str = Field("asphalt")
    # Monuments or greenery is a preference, not a measure of quality.
    sights: str = Field("both")
    # "any" unless they asked to stay in town, which is a constraint on where
    # the route goes rather than on what it is paved with.
    area: str = Field("any")

    @model_validator(mode="after")
    def _check_mode(self) -> "SearchRequest":
        if self.mode not in MODES:
            raise ValueError("mode must be one of {}".format(", ".join(MODES)))
        if self.mode == "loop" and self.distance_km is None:
            raise ValueError("distance_km is required for a loop")
        if self.mode == "route" and (self.end_lat is None or self.end_lon is None):
            raise ValueError("end_lat and end_lon are required for a route")
        return self

    @property
    def is_loop(self) -> bool:
        return self.mode == "loop"

    def normalised(self) -> "SearchRequest":
        sport = self.sport if self.sport in SPORTS else "running"
        surface = self.surface if self.surface in SURFACE_PREFERENCES else "asphalt"
        sights = self.sights if self.sights in SIGHTS else "both"
        area = self.area if self.area in AREAS else "any"
        return self.model_copy(
            update={"sport": sport, "surface": surface, "sights": sights,
                    "area": area}
        )


class SurfaceBreakdown(BaseModel):
    label: str
    distance_m: float
    share: float


class RouteScores(BaseModel):
    total: float
    distance: float
    gain: float
    surface: float
    traffic: float
    wind: float
    air: Optional[float] = None


class RouteCandidate(BaseModel):
    id: str
    distance_m: float
    ascent_m: float
    descent_m: float
    duration_s: Optional[float] = None
    coordinates: List[List[float]]
    scores: RouteScores
    paved_share: float
    traffic_exposure: float
    # Share of the route on residential streets and pavements — the proxy for
    # being in a built-up area at all.
    urban_share: float = 0.0
    # Share of the route's length on state or trunk roads. Separate from the
    # weighted exposure score because a badge needs a fraction of metres, not a
    # ranking term.
    big_road_share: float = 0.0
    headwind_share: float
    step_distance_m: float
    surface_breakdown: List[SurfaceBreakdown]
    waytype_breakdown: List[SurfaceBreakdown]
    air: Optional[Dict[str, float]] = None


class WeatherContext(BaseModel):
    temperature_c: Optional[float] = None
    apparent_temperature_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    uv_index: Optional[float] = None


class AirContext(BaseModel):
    european_aqi: Optional[float] = None
    pm2_5: Optional[float] = None
    pm10: Optional[float] = None
    nitrogen_dioxide: Optional[float] = None
    ozone: Optional[float] = None
    # True when AQI actually differs across the candidate routes. When it does
    # not, air quality is city-wide context and is left out of the ranking.
    differentiates_routes: bool = False
    spread: float = 0.0


class Poi(BaseModel):
    id: str
    kind: str
    name: Optional[str] = None
    lat: float
    lon: float
    routes: List[str] = []


class PoiRequest(BaseModel):
    route_ids: List[str] = Field(..., min_length=1, max_length=8)
    sights: str = Field("both")


class PoiScores(BaseModel):
    water: float
    monuments: float
    nature: float
    # The one the preference actually selected; None when sights are not wanted.
    sights: Optional[float] = None
    bonus: float
    total: float          # the route's score after folding the bonus in


class PoiResponse(BaseModel):
    pois: List[Poi] = []
    counts: Dict[str, Dict[str, int]] = {}
    scores: Dict[str, PoiScores] = {}
    kinds: List[str] = []
    monument_kinds: List[str] = []
    nature_kinds: List[str] = []
    # False when the lookup itself failed, so the UI never reports an
    # outage as "nothing nearby".
    available: bool = True
    # True when the routes are no longer in the cache — a restart, or a page
    # left open too long. Retrying will not help; searching again will.
    expired: bool = False
    # "local", "overpass" or "cache" — useful for seeing which path answered.
    source: str = "overpass"


class InterpretRequest(BaseModel):
    sentence: str = Field(..., min_length=1, max_length=500)
    lang: str = Field("it")
    # Bias place lookup towards what the map is showing.
    lat: Optional[float] = None
    lon: Optional[float] = None


class InterpretResponse(BaseModel):
    understood: List[str] = []
    # The search this became, ready to run. None where the sentence said nothing.
    sport: Optional[str] = None
    mode: Optional[str] = None
    distance_km: Optional[float] = None
    elevation_gain_m: Optional[float] = None
    surface: Optional[str] = None
    sights: Optional[str] = None
    area: Optional[str] = None
    start: Optional["GeocodeResult"] = None
    end: Optional["GeocodeResult"] = None
    # Named but not found, so the interface can say so rather than ignore it.
    unresolved: List[str] = []
    used_model: bool = False


class GeocodeResult(BaseModel):
    label: str
    lat: float
    lon: float
    region: Optional[str] = None


class SearchResponse(BaseModel):
    query: SearchRequest
    routes: List[RouteCandidate]
    weather: WeatherContext
    air: AirContext
    notices: List[str] = []
    candidates_requested: int = 0
    candidates_returned: int = 0
    # True when the routing came from cache and cost no API calls. The scores
    # are still fresh — only the geometry is reused.
    from_cache: bool = False


InterpretResponse.model_rebuild()
