"""The seam between Doiamo and whatever computes the routes.

Today that is the OpenRouteService public API. When the free tier's 40
requests/minute stops being enough, a self-hosted GraphHopper implementing this
same interface drops in without touching the scoring or the API layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


class RoutingError(RuntimeError):
    """The routing backend could not answer at all."""


@dataclass
class RawRoute:
    """One route as returned by a routing engine, before any scoring.

    ``surface`` and ``waytype`` map an encoded class to metres travelled on it.
    ``coordinates`` are [lon, lat, elevation] triples.
    """

    coordinates: List[List[float]]
    distance_m: float
    duration_s: Optional[float] = None
    ascent_m: Optional[float] = None
    descent_m: Optional[float] = None
    surface: Dict[int, float] = field(default_factory=dict)
    waytype: Dict[int, float] = field(default_factory=dict)
    seed: Optional[int] = None


class RoutingEngine:
    """Interface every routing backend implements."""

    name = "base"

    async def round_trips(
        self,
        lat: float,
        lon: float,
        length_m: float,
        sport: str,
        surface: str,
        seeds: Sequence[Tuple[int, int]],
    ) -> Tuple[List[RawRoute], List[str]]:
        """Generate loop candidates from a start point.

        ``seeds`` is a sequence of (seed, waypoint_count) pairs; each yields one
        candidate loop. Returns the routes that succeeded plus human-readable
        notices about the ones that did not, so a partial answer still ships.
        """
        raise NotImplementedError

    async def point_to_point(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        sport: str,
        surface: str,
        length_m: Optional[float] = None,
    ) -> Tuple[List[RawRoute], List[str]]:
        """Generate alternative routes between two points.

        ``start`` and ``end`` are (lat, lon). With no ``length_m`` this is a
        single call and the engine returns its own alternatives. With one, it
        becomes a detour search: routes are threaded through via points chosen
        to stretch the trip to roughly that length.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
