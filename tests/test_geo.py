import math

from backend import geo


def test_haversine_matches_known_distance():
    # Duomo di Milano -> Stazione Centrale, about 2.65 km as the crow flies.
    metres = geo.haversine_m(9.1919, 45.4642, 9.2044, 45.4864)
    assert 2550 < metres < 2750


def test_bearing_cardinal_directions():
    assert math.isclose(geo.bearing_deg(9.0, 45.0, 9.0, 45.1), 0.0, abs_tol=0.5)
    assert math.isclose(geo.bearing_deg(9.0, 45.0, 9.1, 45.0), 90.0, abs_tol=0.5)
    assert math.isclose(geo.bearing_deg(9.0, 45.0, 9.0, 44.9), 180.0, abs_tol=0.5)


def test_total_distance_of_a_straight_line():
    coords = [[9.0, 45.0], [9.0, 45.01], [9.0, 45.02]]
    assert math.isclose(geo.total_distance_m(coords), 2224, rel_tol=0.02)


def test_elevation_gain_ignores_dem_noise():
    """A dead-flat route sampled from a noisy DEM must not invent climb.

    This is the whole reason for the smoothing pass: summing every positive
    delta over 400 samples of +/-2 m noise reports hundreds of metres of ascent
    on ground that never rises.
    """
    noise = [0.0, 1.8, -1.5, 0.9, -2.0, 1.2, -0.7, 1.9, -1.1, 0.4]
    coords = [
        [9.0 + i * 0.0001, 45.0, 100.0 + noise[i % len(noise)]]
        for i in range(400)
    ]

    naive = sum(
        max(0.0, coords[i + 1][2] - coords[i][2]) for i in range(len(coords) - 1)
    )
    ascent, _ = geo.elevation_gain_m(coords)

    assert naive > 300          # what the obvious implementation would report
    assert ascent < 20          # what we actually report


def test_elevation_gain_survives_correlated_dem_noise():
    """The harder case than white noise.

    SRTM cells are ~30 m, so consecutive route points share a cell and the
    error is spatially correlated — it does not average out the way white
    noise does. A flat 10 km route must still report nothing.
    """
    import random

    random.seed(11)
    n_points, corr = 1200, 4
    raw = [random.gauss(0, 1) for _ in range(n_points + corr)]
    smoothed = [sum(raw[i:i + corr]) / corr for i in range(n_points)]
    scale = 3.0 / max(abs(v) for v in smoothed)

    coords = [
        [9.19 + i * 0.0001, 45.46, 120.0 + smoothed[i] * scale]
        for i in range(n_points)
    ]
    ascent, _ = geo.elevation_gain_m(coords)
    assert ascent < 10


def test_elevation_gain_keeps_a_real_climb_under_that_noise():
    import random

    random.seed(7)
    n_points, corr = 1200, 4
    raw = [random.gauss(0, 1) for _ in range(n_points + corr)]
    smoothed = [sum(raw[i:i + corr]) / corr for i in range(n_points)]
    scale = 2.5 / max(abs(v) for v in smoothed)

    coords = [
        [9.19 + i * 0.0001, 45.46, 120.0 + 300.0 * (i / (n_points - 1)) + smoothed[i] * scale]
        for i in range(n_points)
    ]
    ascent, _ = geo.elevation_gain_m(coords)
    assert 285 < ascent < 315


def test_elevation_gain_finds_a_real_climb():
    up = [[9.0 + i * 0.0002, 45.0, 100.0 + i] for i in range(101)]      # +100 m
    down = [[9.0 + (200 - i) * 0.0002, 45.0, 100.0 + (200 - i) - 100] for i in range(101)]
    ascent, descent = geo.elevation_gain_m(up + down)
    assert 85 < ascent < 105
    assert 85 < descent < 105


def test_headwind_exposure_is_highest_running_into_the_wind():
    north = [[9.0, 45.0 + i * 0.001] for i in range(20)]     # travelling north
    into_wind = geo.headwind_exposure(north, wind_from_deg=0.0, wind_speed_kmh=25.0)
    with_wind = geo.headwind_exposure(north, wind_from_deg=180.0, wind_speed_kmh=25.0)
    assert into_wind > 0.9
    assert with_wind < 0.05


def test_headwind_exposure_is_zero_without_wind():
    north = [[9.0, 45.0 + i * 0.001] for i in range(20)]
    assert geo.headwind_exposure(north, 0.0, 0.0) == 0.0


def test_sample_points_returns_requested_count_along_route():
    coords = [[9.0 + i * 0.001, 45.0] for i in range(100)]
    points = geo.sample_points(coords, 5)
    assert len(points) == 5
    assert points[0] == (45.0, 9.0)
    longitudes = [p[1] for p in points]
    assert longitudes == sorted(longitudes)


def test_bbox_contains():
    milano = [45.35, 9.02, 45.56, 9.32]
    assert geo.bbox_contains(milano, 45.4642, 9.19)
    assert not geo.bbox_contains(milano, 41.9028, 12.4964)
