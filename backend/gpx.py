"""GPX 1.1 output.

Written by hand rather than pulled from a library: the document is small, and
every watch and head unit that matters reads this shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence
from xml.sax.saxutils import escape

from .models import RouteCandidate

GPX_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<gpx version="1.1" creator="Doiamo" '
    'xmlns="http://www.topografix.com/GPX/1/1" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.topografix.com/GPX/1/1 '
    'http://www.topografix.com/GPX/1/1/gpx.xsd">\n'
)


def _track_name(route: RouteCandidate, sport: str) -> str:
    return "Doiamo {} {:.1f}km +{:.0f}m".format(
        sport, route.distance_m / 1000.0, route.ascent_m
    )


def build_gpx(
    route: RouteCandidate,
    sport: str = "running",
    description: Optional[str] = None,
) -> str:
    name = escape(_track_name(route, sport))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    parts = [GPX_HEADER, "  <metadata>\n"]
    parts.append("    <name>{}</name>\n".format(name))
    if description:
        parts.append("    <desc>{}</desc>\n".format(escape(description)))
    parts.append("    <time>{}</time>\n".format(stamp))
    parts.append("  </metadata>\n")
    parts.append("  <trk>\n    <name>{}</name>\n".format(name))
    parts.append("    <type>{}</type>\n".format(escape(sport)))
    parts.append("    <trkseg>\n")

    for point in route.coordinates:
        lon, lat = point[0], point[1]
        if len(point) > 2:
            parts.append(
                '      <trkpt lat="{:.6f}" lon="{:.6f}"><ele>{:.1f}</ele></trkpt>\n'.format(
                    lat, lon, point[2]
                )
            )
        else:
            parts.append(
                '      <trkpt lat="{:.6f}" lon="{:.6f}"/>\n'.format(lat, lon)
            )

    parts.append("    </trkseg>\n  </trk>\n</gpx>\n")
    return "".join(parts)


def filename_for(route: RouteCandidate, sport: str) -> str:
    return "doiamo-{}-{:.0f}km-{:.0f}m-{}.gpx".format(
        sport, route.distance_m / 1000.0, route.ascent_m, route.id[:6]
    )
