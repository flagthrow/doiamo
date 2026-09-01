import xml.etree.ElementTree as ET

from backend.gpx import build_gpx, filename_for
from backend.models import RouteCandidate, RouteScores

NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def make_candidate(coords=None):
    return RouteCandidate(
        id="abc123def456",
        distance_m=10250.0,
        ascent_m=312.0,
        descent_m=310.0,
        duration_s=3600.0,
        coordinates=coords or [[9.19, 45.4642, 120.0], [9.20, 45.47, 135.5]],
        scores=RouteScores(total=0.81, distance=0.9, gain=0.8, surface=0.95,
                           traffic=0.7, wind=0.6, air=None),
        paved_share=0.95,
        traffic_exposure=0.3,
        headwind_share=0.4,
        step_distance_m=0.0,
        surface_breakdown=[],
        waytype_breakdown=[],
    )


def test_gpx_is_well_formed_and_namespaced():
    root = ET.fromstring(build_gpx(make_candidate()))
    assert root.tag == "{http://www.topografix.com/GPX/1/1}gpx"
    assert root.get("version") == "1.1"


def test_gpx_carries_every_point_with_elevation():
    root = ET.fromstring(build_gpx(make_candidate()))
    points = root.findall(".//gpx:trkpt", NS)
    assert len(points) == 2
    assert points[0].get("lat") == "45.464200"
    assert points[0].get("lon") == "9.190000"
    assert points[0].find("gpx:ele", NS).text == "120.0"


def test_gpx_handles_points_without_elevation():
    root = ET.fromstring(build_gpx(make_candidate(coords=[[9.19, 45.46], [9.20, 45.47]])))
    points = root.findall(".//gpx:trkpt", NS)
    assert len(points) == 2
    assert points[0].find("gpx:ele", NS) is None


def test_gpx_name_describes_the_route():
    root = ET.fromstring(build_gpx(make_candidate(), sport="cycling"))
    name = root.find(".//gpx:trk/gpx:name", NS).text
    assert "10.2km" in name
    assert "+312m" in name
    assert root.find(".//gpx:trk/gpx:type", NS).text == "cycling"


def test_description_is_xml_escaped():
    gpx = build_gpx(make_candidate(), description="pace <5:00 & hills")
    root = ET.fromstring(gpx)          # would raise if the & were unescaped
    assert root.find(".//gpx:metadata/gpx:desc", NS).text == "pace <5:00 & hills"


def test_filename_is_descriptive_and_safe():
    name = filename_for(make_candidate(), "running")
    assert name == "doiamo-running-10km-312m-abc123.gpx"
