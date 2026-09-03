"""What a route costs to move through."""
import math

import pytest

from backend import energy, geo


def line(km, climb_m=0.0, lat=45.46, points=400):
    """A straight run of exactly km, climbing climb_m and giving it back."""
    deg = 1.0 / (111.320 * math.cos(math.radians(lat)))
    out = []
    for i in range(points + 1):
        f = i / points
        rise = climb_m * (2 * f if f < 0.5 else 2 * (1 - f))
        out.append([9.19 + km * deg * f, lat, 100.0 + rise])
    return out


def test_the_synthetic_route_is_the_length_it_claims():
    """Every number below rests on this; a bad generator hides a good model."""
    assert geo.total_distance_m(line(10.0)) == pytest.approx(10000, rel=0.01)


def test_flat_running_costs_about_a_kcal_per_kg_per_km():
    """The rule of thumb everyone knows, and what Minetti gives at zero grade."""
    kcal = energy.kcal_for_route(line(10.0), "running", 70.0)
    assert kcal / 70.0 / 10.0 == pytest.approx(1.0, abs=0.12)


def test_minetti_at_zero_gradient_is_the_published_value():
    assert energy.running_cost_j_per_kg_m(0.0) == pytest.approx(3.6, abs=0.01)


def test_running_uphill_costs_more_than_running_down():
    assert energy.running_cost_j_per_kg_m(0.10) > energy.running_cost_j_per_kg_m(-0.10)


def test_no_gradient_ever_returns_free_running():
    """The fitted polynomial dips negative off the end of the measured range;
    you do not gain energy by running downhill."""
    for gradient in (-0.9, -0.45, -0.3, -0.1, 0.0, 0.3, 0.9):
        assert energy.running_cost_j_per_kg_m(gradient) > 0


def test_a_hilly_route_costs_more_than_the_same_distance_flat():
    flat = energy.kcal_for_route(line(10.0), "running", 70.0)
    hilly = energy.kcal_for_route(line(10.0, climb_m=300.0), "running", 70.0)
    assert hilly > flat


def test_heavier_bodies_spend_more_over_the_same_ground():
    light = energy.kcal_for_route(line(10.0), "running", 55.0)
    heavy = energy.kcal_for_route(line(10.0), "running", 95.0)
    assert heavy > light * 1.4


def test_cycling_is_cheaper_per_kilometre_than_running():
    ride = energy.kcal_for_route(line(10.0), "cycling", 70.0)
    run = energy.kcal_for_route(line(10.0), "running", 70.0)
    assert ride < run


def test_climbing_costs_a_cyclist_proportionally_more_than_a_runner():
    """A descent refunds a runner most of the climb and a cyclist almost none:
    you cannot bank a freewheel."""
    def ratio(sport):
        flat = energy.kcal_for_route(line(20.0), sport, 70.0)
        hilly = energy.kcal_for_route(line(20.0, climb_m=400.0), sport, 70.0)
        return hilly / flat

    assert ratio("cycling") > ratio("running")


@pytest.mark.parametrize("kcal", [200, 400, 800, 1500])
@pytest.mark.parametrize("sport", ["running", "cycling"])
def test_asking_for_calories_gives_back_a_distance_that_costs_them(kcal, sport):
    km = energy.distance_km_for_kcal(kcal, sport, 70.0)
    measured = energy.kcal_for_route(line(km), sport, 70.0)
    assert measured == pytest.approx(kcal, rel=0.06)


def test_the_same_target_is_further_for_a_lighter_person():
    light = energy.distance_km_for_kcal(400, "running", 55.0)
    heavy = energy.distance_km_for_kcal(400, "running", 95.0)
    assert light > heavy


def test_an_unknown_mass_falls_back_rather_than_dividing_by_nothing():
    for bad in (None, 0, -5):
        assert energy.clamp_mass(bad) == energy.DEFAULT_MASS_KG


def test_absurd_masses_are_pulled_into_range():
    assert energy.clamp_mass(2.0) == energy.MIN_MASS_KG
    assert energy.clamp_mass(900.0) == energy.MAX_MASS_KG


def test_a_route_with_no_length_costs_nothing_rather_than_crashing():
    assert energy.kcal_for_route([[9.19, 45.46, 100.0]], "running", 70.0) == 0.0
    assert energy.kcal_for_route([], "cycling", 70.0) == 0.0


def test_dem_noise_does_not_inflate_the_estimate():
    """Elevation samples carry a couple of metres of noise and a route has
    thousands of them. Taken raw, every wobble reads as a small climb, and
    since going up costs more than coming down saves, the error only ever
    accumulates upward — a flat 5.8 km read 1.28 kcal/kg/km against a true
    0.96 before the profile was smoothed."""
    import random

    random.seed(7)
    lat, points, km = 45.46, 600, 10.0
    deg = 1.0 / (111.320 * math.cos(math.radians(lat)))

    def noisy(amplitude):
        return [
            [9.19 + km * deg * (i / points), lat,
             100.0 + random.uniform(-amplitude, amplitude)]
            for i in range(points + 1)
        ]

    clean = energy.kcal_for_route(noisy(0.0), "running", 70.0)
    rough = energy.kcal_for_route(noisy(3.0), "running", 70.0)
    assert rough == pytest.approx(clean, rel=0.03)
