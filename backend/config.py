"""Configuration and domain constants for Doiamo."""
from __future__ import annotations

import os
from typing import Dict, List

ORS_BASE_URL = os.environ.get("ORS_BASE_URL", "https://api.openrouteservice.org")
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")

# Free ORS tier: 2000 directions/day, 40/min. Stay under both.
ORS_MAX_CONCURRENCY = int(os.environ.get("ORS_MAX_CONCURRENCY", "4"))
ORS_MIN_INTERVAL_S = float(os.environ.get("ORS_MIN_INTERVAL_S", "1.6"))
ORS_TIMEOUT_S = float(os.environ.get("ORS_TIMEOUT_S", "25"))

# How many loop candidates we ask the router for per search.
CANDIDATE_SEEDS = int(os.environ.get("CANDIDATE_SEEDS", "12"))
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "5"))

# Cached routes live long enough for the user to click "download GPX".
ROUTE_CACHE_TTL_S = 3600
ROUTE_CACHE_MAX = 2000

# Routing is the expensive, slow-changing half of a search; weather and air are
# the cheap, fast-changing half. So the router's answer is cached and the
# scoring is always recomputed against live conditions.
SEARCH_CACHE_TTL_S = int(os.environ.get("SEARCH_CACHE_TTL_S", 6 * 3600))
SEARCH_CACHE_MAX = int(os.environ.get("SEARCH_CACHE_MAX", "500"))

# Start points are bucketed to this many decimals (~110 m) so that everyone
# setting off "from the Duomo" shares one set of routes.
SEARCH_CACHE_PRECISION = int(os.environ.get("SEARCH_CACHE_PRECISION", "3"))

GEOCODE_RESULTS = int(os.environ.get("GEOCODE_RESULTS", "6"))

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"

# The CAMS air-quality grid is ~11 km. Snapping sample points to this grid
# collapses a whole city's samples into a handful of unique API lookups.
AIR_GRID_DEG = 0.1

# Below this spread in European AQI across candidates, air quality is city-wide
# context rather than something that can separate one route from another.
AIR_DIFFERENTIATION_MIN_SPREAD = 4.0

# Where the map opens before the user has said anything. A starting view, not
# a limit: routing, weather, air and POIs are all global.
DEFAULT_VIEW = {"center": [45.4642, 9.1900], "zoom": 13}

# --- OpenRouteService extra_info encodings -------------------------------
# https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/extra-info/

# How "paved" each ORS surface class is, 0 = soft ground, 1 = tarmac.
SURFACE_PAVEDNESS: Dict[int, float] = {
    0: 0.50,   # unknown
    1: 1.00,   # paved
    2: 0.00,   # unpaved
    3: 1.00,   # asphalt
    4: 0.95,   # concrete
    5: 0.60,   # cobblestone
    6: 0.80,   # metal
    7: 0.50,   # wood
    8: 0.40,   # compacted gravel
    9: 0.30,   # fine gravel
    10: 0.20,  # gravel
    11: 0.05,  # dirt
    12: 0.05,  # ground
    13: 0.00,  # ice
    14: 0.85,  # paving stones
    15: 0.00,  # sand
    16: 0.10,  # woodchips
    17: 0.00,  # grass
    18: 0.30,  # grass paver
}

SURFACE_LABELS: Dict[int, str] = {
    0: "unknown", 1: "paved", 2: "unpaved", 3: "asphalt", 4: "concrete",
    5: "cobblestone", 6: "metal", 7: "wood", 8: "compacted gravel",
    9: "fine gravel", 10: "gravel", 11: "dirt", 12: "ground", 13: "ice",
    14: "paving stones", 15: "sand", 16: "woodchips", 17: "grass",
    18: "grass paver",
}

# Motor-traffic exposure per ORS waytype, 0 = traffic-free, 1 = trunk road.
# This is the layer that actually varies street by street, which makes it a
# better proxy for what you breathe than a city-wide AQI number.
WAYTYPE_EXPOSURE: Dict[int, float] = {
    0: 0.50,   # unknown
    1: 1.00,   # state road
    2: 0.75,   # road
    3: 0.45,   # street
    4: 0.05,   # path
    5: 0.10,   # track
    6: 0.10,   # cycleway
    7: 0.10,   # footway
    8: 0.15,   # steps
    9: 0.00,   # ferry
    10: 0.60,  # construction
}

WAYTYPE_LABELS: Dict[int, str] = {
    0: "unknown", 1: "state road", 2: "road", 3: "street", 4: "path",
    5: "track", 6: "cycleway", 7: "footway", 8: "steps", 9: "ferry",
    10: "construction",
}

# Steps are miserable on a run and unrideable on a bike.
STEPS_WAYTYPE = 8

# --- Scoring --------------------------------------------------------------
# A cyclist weights traffic proximity far more heavily than a runner; a runner
# weights surface (impact load) more. These are hand-tuned starting points to
# be replaced once there is real feedback.
# Point-to-point weights. Distance and climb are no longer targets the user
# set, so they become relative preferences across the alternatives: the more
# direct route and the flatter route each score better than their siblings.
ROUTE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "running": {
        "distance": 0.22,
        "gain": 0.13,
        "surface": 0.25,
        "traffic": 0.25,
        "wind": 0.05,
        "air": 0.10,
    },
    "cycling": {
        "distance": 0.20,
        "gain": 0.13,
        "surface": 0.16,
        "traffic": 0.36,
        "wind": 0.10,
        "air": 0.05,
    },
}

WEIGHTS: Dict[str, Dict[str, float]] = {
    "running": {
        "distance": 0.20,
        "gain": 0.20,
        "surface": 0.25,
        "traffic": 0.20,
        "wind": 0.05,
        "air": 0.10,
    },
    "cycling": {
        "distance": 0.18,
        "gain": 0.18,
        "surface": 0.18,
        "traffic": 0.31,
        "wind": 0.10,
        "air": 0.05,
    },
}

MODES: List[str] = ["loop", "route"]
# Points of interest arrive after the routes (Overpass is slow), so they adjust
# an already-computed score rather than being one of its terms.
#
# Water and sights are different kinds of thing. Nobody wants fewer drinking
# fountains, so water is a universal good on an absolute scale. Monuments
# versus greenery is not a quality axis at all — it is a destination axis, and
# a runner heading for parks and one touring the centro storico want opposite
# routes. Averaging them scores neither.
POI_SHARE = 0.25

# When the sights are not wanted, only water is left to adjust, and it should
# not swing the ranking as far.
POI_SHARE_WATER_ONLY = 0.12

# Split within the POI share. A cyclist carries bottles; a runner mostly does
# not, so water matters more on foot and the view matters more on wheels.
POI_WEIGHTS: Dict[str, Dict[str, float]] = {
    "running": {"water": 0.50, "sights": 0.50},
    "cycling": {"water": 0.35, "sights": 0.65},
}

# One drinking fountain every this many kilometres counts as fully covered.
WATER_INTERVAL_KM = 3.0

# The two destination axes, scored separately so a preference can pick one.
MONUMENT_KINDS = ["monument", "art"]
NATURE_KINDS = ["green", "viewpoint"]

# What the user says they want to see. "none" is not offered in the UI but
# remains a valid request: it drops sights from the score and leaves water.
SIGHTS: List[str] = ["both", "monuments", "nature", "none"]

MODES: List[str] = ["loop", "route"]

SPORTS: List[str] = ["running", "cycling"]
SURFACE_PREFERENCES: List[str] = ["asphalt", "mixed", "trail"]

# ORS profile per (sport, surface preference). Picking the right profile does
# half the surface work before scoring even starts.
# ORS caps alternatives at three per request, which is also the whole cost of a
# point-to-point search: one call, against roughly a dozen for a loop.
ALTERNATIVE_TARGET_COUNT = 3
ALTERNATIVE_WEIGHT_FACTOR = 1.6
ALTERNATIVE_SHARE_FACTOR = 0.6

# Detour search for "get me from A to B, but make it N km". Via points are
# placed on an ellipse whose axis is the straight-line budget; roads wander, so
# the budget is the requested length divided by this factor.
DETOUR_ROAD_FACTOR = float(os.environ.get("DETOUR_ROAD_FACTOR", "1.18"))
DETOUR_VIA_COUNT = int(os.environ.get("DETOUR_VIA_COUNT", "8"))

# Below this multiple of the straight-line distance there is no detour to make;
# the request is really just "the direct way".
DETOUR_MIN_RATIO = 1.15

ORS_PROFILES: Dict[str, Dict[str, str]] = {
    "running": {
        "asphalt": "foot-walking",
        "mixed": "foot-hiking",
        "trail": "foot-hiking",
    },
    "cycling": {
        "asphalt": "cycling-road",
        "mixed": "cycling-regular",
        "trail": "cycling-mountain",
    },
}
