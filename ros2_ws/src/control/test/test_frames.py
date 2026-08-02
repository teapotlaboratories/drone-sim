"""Tests for the single frame-conversion point (ENU<->NED, and NWU<->ENU for Lane C).

These run off-target: no simulator, no DDS, no PX4. A frame bug found here costs seconds;
found in flight it costs a 300 s SITL run and looks like a controller problem.
"""

import math

from control.frames import (enu_to_ned, ned_to_enu, yaw_enu_to_ned, yaw_ned_to_enu,
                            nwu_to_enu, enu_to_nwu, yaw_nwu_to_enu, yaw_enu_to_nwu)


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


# ---------------------------------------------------------------------------------------
# NWU <-> ENU (C-04). Cosys-AirSim publishes NWU while its docs claim ROS-standard ENU.


def test_north_is_the_case_that_exposes_the_90_degree_error():
    """A point due North. In NWU it is +x; in ENU it is +y. Getting this backwards is the
    exact 90 deg rotation that makes an AirSim-fed planner steer into a wall."""
    assert nwu_to_enu(1.0, 0.0, 0.0) == (0.0, 1.0, 0.0)


def test_east_maps_from_negative_west():
    # NWU y is WEST, so 1 m East is y = -1.
    assert nwu_to_enu(0.0, -1.0, 0.0) == (1.0, 0.0, 0.0)


def test_up_passes_through_untouched():
    """Both frames share Up, so z is identical. This is why an OMITTED nwu_to_enu still
    looks plausible in altitude data while x/y are silently rotated 90 deg."""
    for z in (-3.0, 0.0, 10.0):
        assert nwu_to_enu(1.0, 2.0, z)[2] == z


def test_yaw_north_is_zero_in_nwu_and_ninety_in_enu():
    assert math.isclose(math.degrees(yaw_nwu_to_enu(0.0)), 90.0, abs_tol=1e-9)


def test_round_trip_is_identity():
    for v in [(3.0, -4.0, 5.0), (0.0, 0.0, 0.0), (-1.5, 2.5, -0.5)]:
        assert enu_to_nwu(*nwu_to_enu(*v)) == v


def test_yaw_round_trip_is_identity():
    for y in (0.0, 1.0, -1.0, 3.0, -3.0):
        assert math.isclose(yaw_enu_to_nwu(yaw_nwu_to_enu(y)), y, abs_tol=1e-9)


def test_this_pair_is_NOT_an_involution_unlike_enu_ned():
    """The load-bearing difference from enu<->ned, which IS its own inverse.

    ENU<->NED applied twice is the identity, which is what makes a stray double call there
    invisible. NWU->ENU is a 90 deg rotation, so twice is 180 deg: x and y both negated.
    Anyone carrying the "applying it twice is harmless" intuition across from the other
    pair will corrupt data, so pin the difference rather than leaving it to a comment."""
    v = (1.0, 2.0, 3.0)
    assert ned_to_enu(*ned_to_enu(*v)) == v, "enu<->ned should still be an involution"
    twice = nwu_to_enu(*nwu_to_enu(*v))
    assert twice != v
    assert twice == (-v[0], -v[1], v[2]), "double-applying should be a 180 deg rotation"


def test_yaw_stays_wrapped_to_pi():
    """yaw_nwu_to_enu adds a quarter turn, so inputs near +pi must wrap rather than
    exceeding it -- an unwrapped yaw silently breaks downstream angle comparisons."""
    for y in (3.0, 3.14, -3.14, math.pi):
        out = yaw_nwu_to_enu(y)
        assert -math.pi - 1e-9 <= out <= math.pi + 1e-9, out
