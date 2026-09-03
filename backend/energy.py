"""What a route costs to move through, in kilocalories.

Two different problems, because the physiology is different.

Running is dominated by the cost of moving your own mass, which barely changes
with speed but changes a great deal with gradient. Minetti et al. (2002)
measured that cost across gradients from -45% to +45% and fitted a fifth-order
polynomial; it is the basis of every grade-adjusted pace on the market, and it
is what makes a hilly 10 km honestly more expensive than a flat one instead of
identical. Because we hold the elevation profile point by point, the gradient
is applied where it actually occurs rather than smeared across the route as an
average, which matters: a route that climbs 200 m and gives it all back costs
more than a flat one, and averaging the two away would say it costs the same.

Cycling is dominated by air, which scales with the cube of speed, so a power
model is the only honest option and an assumed speed has to come with it.

Every number here is an estimate, and the largest error is not the model — it
is the body mass, which nobody tells us and everybody differs on.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from . import config, geo

J_PER_KCAL = 4184.0
GRAVITY = 9.80665

# Minetti's polynomial: the net metabolic cost of running, in joules per
# kilogram of body mass per metre travelled, as a function of gradient.
# At zero gradient it gives 3.6 J/kg/m, which is the familiar "about one
# kilocalorie per kilogram per kilometre".
MINETTI = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)

# Beyond the range Minetti measured the polynomial turns back on itself and
# starts predicting less cost for more gradient, which is nonsense. Clamp to
# the edges of the data rather than extrapolating a fitted curve.
GRADIENT_LIMIT = 0.45

# Cycling. A road bike on the hoods, on tarmac, in still air at sea level.
CDA_M2 = 0.32            # frontal area x drag coefficient
CRR = 0.005              # rolling resistance
AIR_DENSITY = 1.225      # kg/m3
DRIVETRAIN_EFFICIENCY = 0.97
BIKE_MASS_KG = 9.0

# Muscle turns roughly a quarter of the energy it burns into forward motion.
CYCLING_EFFICIENCY = 0.24

# What the body spends just being alive, which a watch includes in its total
# and which therefore has to be here too or our figure will read low.
RESTING_KCAL_PER_KG_H = 1.0

# Nobody tells us, and it is the single largest source of error in the answer.
# Shown as an assumption rather than folded in silently.
DEFAULT_MASS_KG = 70.0
MIN_MASS_KG = 30.0
MAX_MASS_KG = 250.0


def running_cost_j_per_kg_m(gradient: float) -> float:
    """Minetti's cost of running one metre, per kilogram, at this gradient."""
    i = max(-GRADIENT_LIMIT, min(GRADIENT_LIMIT, gradient))
    total = 0.0
    for coefficient in MINETTI:
        total = total * i + coefficient
    # The fit dips slightly below zero on steep descents, where the real cost
    # is small but never negative: you do not gain energy by running downhill.
    return max(0.3, total)


# Elevation samples carry a couple of metres of DEM noise, and a route has
# thousands of them. Taken raw, every one of those wobbles reads as a little
# climb, and since going up costs more than going down saves, the cost only
# ever inflates — a flat 5.8 km came out at 1.28 kcal/kg/km against a true
# 0.96. The ascent figure already smooths for exactly this reason; so does
# this. The window is wider because gradient is a derivative, and derivatives
# amplify noise worse than the sums do.
SMOOTHING_WINDOW = 9

# Gradient is a derivative, and a derivative taken between two points a metre
# apart is mostly noise: half a metre of DEM error over one metre of ground
# reads as a fifty percent slope, which Minetti prices like a mountain. A
# router samples a line as densely as it likes — corners especially — so
# smoothing by sample index is not enough. Measuring the rise over a fixed
# distance of ground instead makes the answer independent of how the geometry
# happened to be cut up.
MIN_BASELINE_M = 25.0


def _segments(coords: Sequence[Sequence[float]]):
    """(length, gradient) over chunks of at least MIN_BASELINE_M of ground."""
    heights = [c[2] if len(c) > 2 else 0.0 for c in coords]
    if len(heights) >= 2:
        heights = geo._moving_average(heights, SMOOTHING_WINDOW)

    run = 0.0
    start = None
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        if start is None:
            start = heights[i]
        step = geo.haversine_m(a[0], a[1], b[0], b[1])
        if step <= 0:
            continue
        run += step
        if run >= MIN_BASELINE_M:
            yield run, (heights[i + 1] - start) / run
            run, start = 0.0, None
    if run > 0 and start is not None:
        yield run, (heights[-1] - start) / run


def running_kcal(
    coords: Sequence[Sequence[float]], mass_kg: float, duration_s: Optional[float]
) -> float:
    joules = 0.0
    metres = 0.0
    for run, gradient in _segments(coords):
        joules += running_cost_j_per_kg_m(gradient) * mass_kg * run
        metres += run
    if metres <= 0:
        return 0.0
    hours = (duration_s or metres / (config.KMH_RUNNING * 1000 / 3600.0)) / 3600.0
    return joules / J_PER_KCAL + RESTING_KCAL_PER_KG_H * mass_kg * hours


def cycling_kcal(
    coords: Sequence[Sequence[float]], mass_kg: float, duration_s: Optional[float]
) -> float:
    """Power against air, rolling resistance and gravity, integrated over time.

    Descents are not credited back: freewheeling costs nothing, but it does not
    repay what the climb took either.
    """
    total_mass = mass_kg + BIKE_MASS_KG
    metres = sum(run for run, _ in _segments(coords))
    if metres <= 0:
        return 0.0

    speed_ms = (
        metres / duration_s if duration_s and duration_s > 0
        else config.KMH_CYCLING * 1000 / 3600.0
    )
    speed_ms = max(1.5, speed_ms)

    joules = 0.0
    for run, gradient in _segments(coords):
        rolling = CRR * total_mass * GRAVITY * math.cos(math.atan(gradient))
        drag = 0.5 * AIR_DENSITY * CDA_M2 * speed_ms * speed_ms
        climbing = total_mass * GRAVITY * math.sin(math.atan(gradient))
        # Gravity gives energy back on the way down, but the rider cannot
        # store it: coasting is free, not profitable.
        force = max(0.0, rolling + drag + climbing)
        joules += force * run / DRIVETRAIN_EFFICIENCY

    hours = (metres / speed_ms) / 3600.0
    return joules / CYCLING_EFFICIENCY / J_PER_KCAL + (
        RESTING_KCAL_PER_KG_H * mass_kg * hours
    )


def kcal_for_route(
    coords: Sequence[Sequence[float]],
    sport: str,
    mass_kg: float = DEFAULT_MASS_KG,
    duration_s: Optional[float] = None,
) -> float:
    mass_kg = clamp_mass(mass_kg)
    if sport == "cycling":
        return cycling_kcal(coords, mass_kg, duration_s)
    return running_kcal(coords, mass_kg, duration_s)


def clamp_mass(mass_kg: Optional[float]) -> float:
    if not mass_kg or mass_kg <= 0:
        return DEFAULT_MASS_KG
    return max(MIN_MASS_KG, min(MAX_MASS_KG, float(mass_kg)))


def distance_km_for_kcal(
    kcal: float,
    sport: str,
    mass_kg: float = DEFAULT_MASS_KG,
    ascent_m: Optional[float] = None,
) -> float:
    """The other direction: how far to go to spend this much.

    There is no route yet, so this assumes an even gradient — the requested
    climb spread over the distance, half up and half down on a loop. It sets
    the target the search then aims at; what the cards report is measured from
    the route that actually came back.
    """
    mass_kg = clamp_mass(mass_kg)
    kcal = max(1.0, float(kcal))

    # Solve by bisection rather than algebra: the cycling model is cubic in
    # speed and the running one is a fifth-order polynomial in gradient, and
    # neither inverts cleanly. Twelve metres of resolution over 200 km is
    # plenty for something that ends up on a slider.
    low, high = 0.1, 200.0
    for _ in range(40):
        middle = (low + high) / 2.0
        if _kcal_for_even_route(middle, sport, mass_kg, ascent_m) < kcal:
            low = middle
        else:
            high = middle
    return round((low + high) / 2.0, 1)


def _kcal_for_even_route(
    distance_km: float, sport: str, mass_kg: float, ascent_m: Optional[float]
) -> float:
    metres = distance_km * 1000.0
    climb = ascent_m or 0.0
    # A loop climbs and descends the same amount, so half the distance goes up
    # at the average gradient and half comes back down it.
    gradient = (climb / (metres / 2.0)) if metres > 0 and climb else 0.0

    if sport == "cycling":
        speed_ms = config.KMH_CYCLING * 1000 / 3600.0
        total_mass = mass_kg + BIKE_MASS_KG
        joules = 0.0
        for sign in (1.0, -1.0):
            angle = math.atan(gradient * sign)
            rolling = CRR * total_mass * GRAVITY * math.cos(angle)
            drag = 0.5 * AIR_DENSITY * CDA_M2 * speed_ms * speed_ms
            climbing = total_mass * GRAVITY * math.sin(angle)
            joules += max(0.0, rolling + drag + climbing) * (metres / 2.0)
        joules /= DRIVETRAIN_EFFICIENCY
        hours = (metres / speed_ms) / 3600.0
        return joules / CYCLING_EFFICIENCY / J_PER_KCAL + (
            RESTING_KCAL_PER_KG_H * mass_kg * hours
        )

    joules = (metres / 2.0) * mass_kg * (
        running_cost_j_per_kg_m(gradient) + running_cost_j_per_kg_m(-gradient)
    )
    hours = (metres / (config.KMH_RUNNING * 1000 / 3600.0)) / 3600.0
    return joules / J_PER_KCAL + RESTING_KCAL_PER_KG_H * mass_kg * hours
