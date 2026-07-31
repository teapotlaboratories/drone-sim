"""Tests for the single ENU<->NED conversion point.

These run off-target: no simulator, no DDS, no PX4. A frame bug found here costs seconds;
found in flight it costs a 300 s SITL run and looks like a controller problem.
"""

import math

from control.frames import enu_to_ned, ned_to_enu, yaw_enu_to_ned, yaw_ned_to_enu


def test_altitude_sign_is_the_one_that_matters():
    """10 m up in ENU must be -10 in NED. Getting this backwards commands flight into
    the ground, which is the specific accident this module exists to prevent."""
    assert enu_to_ned(0.0, 0.0, 10.0) == (0.0, 0.0, -10.0)


def test_axes_swap_not_just_relabel():
    """ENU x is East, NED y is East. A pure z-negation without the swap passes an
    altitude-only test and silently transposes every horizontal waypoint."""
    north, east, down = enu_to_ned(3.0, 5.0, 0.0)
    assert (north, east, down) == (5.0, 3.0, 0.0)


def test_round_trip_is_identity():
    for v in [(1.0, 2.0, 3.0), (-4.5, 0.0, 12.25), (0.0, 0.0, 0.0)]:
        assert ned_to_enu(*enu_to_ned(*v)) == v


def test_transform_is_its_own_involution():
    """Applying the conversion twice returns the input — which is exactly why a stray
    second call is invisible on inspection. Pinned so the property is deliberate rather
    than accidental."""
    v = (1.0, 2.0, 3.0)
    assert enu_to_ned(*enu_to_ned(*v)) == v


def test_yaw_cardinal_directions():
    """ENU yaw 0 is East; NED yaw 0 is North. East in NED is +pi/2."""
    assert math.isclose(yaw_enu_to_ned(0.0), math.pi / 2.0)
    # ENU pi/2 is North -> NED 0.
    assert math.isclose(yaw_enu_to_ned(math.pi / 2.0), 0.0, abs_tol=1e-12)


def test_yaw_stays_in_px4_range():
    """PX4 documents TrajectorySetpoint.yaw as -PI..+PI. Feeding it 3*pi/2 because a
    wrap was skipped is accepted by the message and misbehaves in the controller."""
    for deg in range(-720, 721, 15):
        out = yaw_enu_to_ned(math.radians(deg))
        assert -math.pi <= out <= math.pi


def _angular_diff(a: float, b: float) -> float:
    """Smallest signed difference between two angles.

    Needed because the round trip is only an identity *modulo 2pi*: `_wrap_pi` maps onto
    [-pi, pi), so an input of exactly +pi legitimately comes back as -pi. Comparing raw
    floats flags that as a failure when it is the same heading."""
    return abs((a - b + math.pi) % (2.0 * math.pi) - math.pi)


def test_yaw_round_trip():
    for deg in range(-180, 181, 30):
        rad = math.radians(deg)
        assert _angular_diff(yaw_ned_to_enu(yaw_enu_to_ned(rad)), rad) < 1e-9


def test_pi_and_minus_pi_are_the_same_heading():
    """Pins the wrap boundary explicitly, so a future change to the wrap range is a
    deliberate decision rather than a surprise in a round-trip test."""
    assert math.isclose(yaw_enu_to_ned(math.pi), yaw_enu_to_ned(-math.pi))
