"""Tests for the example mission's pure logic (`SIM-16`).

Off-target: no simulator, no ROS 2 runtime. This file exists because `park_tour.py` was the
most defect-dense code in its PR -- four bugs, every one found by flying rather than by
testing, and three of them reproducible without a simulator:

  * arrival was distance-only, so at speed the tolerance sphere was crossed on the way THROUGH
  * the ramp-out scaled rate by angle REMAINING, so it converged without ever arriving (Zeno)
  * a jump filter did not advance its reference on rejection, so rejections cascaded

`park_tour` imports rclpy and px4_msgs, which are not present off-target, so the geometry and
validation are re-expressed here against the same formulas. That is a deliberate trade: a test
that pins the ARITHMETIC is worth more than no test at all, and the alternative is a ROS 2
container in CI. The docstrings name the source lines so drift is visible.
"""
import math

import pytest


# --- mirrors ParkTour.waypoints() -------------------------------------------------------
def waypoints(x0, y0, legs, radius, alt):
    pts = [(x0 + radius * math.cos(2 * math.pi * i / legs),
            y0 + radius * math.sin(2 * math.pi * i / legs)) for i in range(legs)]
    out = []
    for i, (x, y) in enumerate(pts):
        nx, ny = pts[(i + 1) % len(pts)]
        out.append((x, y, -alt, math.atan2(ny - y, nx - x)))
    out.append((x0, y0, -alt, out[0][3]))
    return out


def smoothstep(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def test_circuit_closes_on_the_start_point():
    w = waypoints(0, 0, 4, 25.0, 8.0)
    assert w[-1][0] == pytest.approx(0.0, abs=1e-9)
    assert w[-1][1] == pytest.approx(0.0, abs=1e-9)


def test_altitude_is_negative_because_z_is_ned():
    """The single most expensive sign convention in this project: negative z is UP."""
    w = waypoints(0, 0, 4, 25.0, 8.0)
    assert all(p[2] == -8.0 for p in w)


def test_every_corner_is_on_the_circle():
    w = waypoints(10.0, -5.0, 6, 30.0, 12.0)
    for x, y, _, _ in w[:-1]:
        assert math.dist((x, y), (10.0, -5.0)) == pytest.approx(30.0, abs=1e-6)


def test_yaw_faces_the_next_corner():
    w = waypoints(0, 0, 4, 25.0, 8.0)
    for i in range(4):
        x, y, _, yaw = w[i]
        nx, ny = w[(i + 1) % 4][0], w[(i + 1) % 4][1]
        assert yaw == pytest.approx(math.atan2(ny - y, nx - x), abs=1e-9)


# --- the ramp ---------------------------------------------------------------------------
def test_smoothstep_has_zero_slope_at_both_ends():
    """Why smoothstep and not a linear ramp: a linear ramp is continuous in RATE but steps in
    ACCELERATION, and that step is the lurch at lap start."""
    eps = 1e-6
    assert (smoothstep(eps) - smoothstep(0)) / eps < 1e-3
    assert (smoothstep(1.0) - smoothstep(1 - eps)) / eps < 1e-3
    assert smoothstep(0) == 0.0 and smoothstep(1) == 1.0


def test_ramp_out_without_a_rate_floor_never_terminates():
    """Regression for the Zeno bug: scaling w by the angle REMAINING drives w->0 as theta->
    total, so the orbit converges without ever arriving. It sat 6e-5 rad short at 6e-9 rad/s."""
    r, v, laps, ramp_s, dt = 35.0, 4.0, 2.0, 8.0, 0.05
    omega, total = v / r, 2 * math.pi * laps
    ramp_ang = omega * ramp_s * 0.5

    theta = el = 0.0
    for _ in range(200_000):                     # ~10,000 s of simulated time
        frac = min(smoothstep(el / ramp_s), smoothstep((total - theta) / ramp_ang))
        theta += omega * frac * dt
        el += dt
        if theta >= total:
            break
    assert theta < total, "expected the un-floored ramp to stall short of the target"

    theta = el = 0.0
    done = False
    for _ in range(200_000):
        frac = min(smoothstep(el / ramp_s), smoothstep((total - theta) / ramp_ang))
        theta += omega * max(frac, 0.03) * dt    # W_FLOOR
        el += dt
        if theta >= total:
            done = True
            break
    assert done, "the rate floor must guarantee termination"


# --- the acceleration cap ---------------------------------------------------------------
def v_cap(max_accel, radius):
    return math.sqrt(max(max_accel, 0.01) * radius)


def test_speed_is_capped_by_the_acceleration_budget():
    """Circular motion costs a = v^2/r CONTINUOUSLY, so a tight fast circle is an acceleration
    demand the vehicle cannot meet -- visible as a wobble, not as an error."""
    assert v_cap(2.0, 35.0) == pytest.approx(math.sqrt(70.0))
    assert min(4.0, v_cap(2.0, 35.0)) == 4.0          # comfortably inside budget
    assert min(12.0, v_cap(1.0, 20.0)) == pytest.approx(math.sqrt(20.0))   # clamped


@pytest.mark.parametrize("radius,speed", [(35.0, 0.0), (0.0, 4.0), (-10.0, 4.0)])
def test_degenerate_parameters_would_break_the_arithmetic(radius, speed):
    """These are exactly what ParkTour.validate() must reject BEFORE anything arms: each one
    raises partway through a mission with the vehicle already airborne."""
    with pytest.raises((ZeroDivisionError, ValueError)):
        omega = min(speed, v_cap(2.0, radius)) / radius
        _ = (2 * math.pi) / omega
