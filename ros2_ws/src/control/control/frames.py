"""Frame conversion — the project's single conversion point (ENU <-> NED, NWU -> ENU).

`docs/lane-a/conventions.md` §3 freezes the rule: our interfaces are ROS REP-103 (ENU
world, FLU body), PX4 is NED, and the conversion happens here and nowhere else. Every
extra conversion site is an opportunity for a sign error, and the failure mode is silent —
a double conversion is the identity on x and a sign flip on z, so the vehicle accepts the
command and flies into the ground.

The transform is its own involution: applying it twice returns the input. That is what
makes a stray second call so hard to spot by reading, and it is why `test_frames.py`
asserts it explicitly.
"""

import math

__all__ = ["enu_to_ned", "ned_to_enu", "yaw_enu_to_ned", "yaw_ned_to_enu",
           "nwu_to_enu", "enu_to_nwu", "yaw_nwu_to_enu", "yaw_enu_to_nwu"]


def enu_to_ned(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ENU (x=East, y=North, z=Up) -> NED (x=North, y=East, z=Down).

    Swap x/y and negate z. A 10 m altitude in ENU (z=+10) becomes NED z=-10, which is
    what `TrajectorySetpoint.position[2]` expects.
    """
    return (y, x, -z)


def ned_to_enu(x: float, y: float, z: float) -> tuple[float, float, float]:
    """NED -> ENU. Identical swap-and-negate; the transform is its own inverse."""
    return (y, x, -z)


def yaw_enu_to_ned(yaw: float) -> float:
    """ENU yaw (CCW from East) -> NED yaw (CW from North), wrapped to [-pi, pi].

    Position and heading do NOT share a conversion: the position transform is a
    reflection, so the rotation sense flips too. Handling only the axes and leaving yaw
    alone yields a vehicle that arrives at the right place pointing the wrong way, which
    reads as a controller tuning problem rather than a frame bug.
    """
    return _wrap_pi(math.pi / 2.0 - yaw)


def yaw_ned_to_enu(yaw: float) -> float:
    """NED yaw -> ENU yaw. Also its own inverse."""
    return _wrap_pi(math.pi / 2.0 - yaw)


def _wrap_pi(angle: float) -> float:
    """Wrap to [-pi, pi) — the range PX4 documents for `TrajectorySetpoint.yaw`."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


# ---------------------------------------------------------------------------------------
# NWU <-> ENU — Lane C only.  (C-04)
#
# Cosys-AirSim's ROS 2 wrapper publishes NWU, NOT ENU, despite its docs claiming "the
# right-handed coordinate frame of the ROS standard". `convert_tf_msg_to_enu()` exists at
# airsim_ros_wrapper.cpp:1600 and is NEVER CALLED; all four call sites use
# `convert_tf_msg_to_ros()`, which negates only y and z (NED -> NWU).
#
# Measured 2026-08-02 against AirSim ground truth: the published yaw missed an ENU
# prediction by 97.3 deg and an NWU prediction by 7.3 deg.
#
# These live here rather than in a Lane C node because conventions.md freezes the rule that
# conversion happens in ONE place. Lane C does not get to invent a second convention -- but
# it does have to REACH the frozen one, and NWU is where it starts.
#
# UNLIKE enu <-> ned, THIS PAIR IS NOT AN INVOLUTION. ENU <-> NED is its own inverse, which
# is what makes a stray double call there invisible. NWU <-> ENU is a 90 deg rotation about
# z, so calling it twice yields a 180 deg rotation -- x and y both negated. That is a
# different failure: less silent (a vehicle heading due south instead of north is obvious),
# but it means you CANNOT reason about it with the "applying it twice is safe" intuition the
# rest of this module earns. Hence separate names and separate tests.


def nwu_to_enu(x: float, y: float, z: float) -> tuple[float, float, float]:
    """NWU (x=North, y=West, z=Up) -> ENU (x=East, y=North, z=Up).

    East is -West and North is North, so this is a +90 deg rotation about z. Up is shared,
    which is why z passes through untouched -- the one thing this conversion has in common
    with a no-op, and the reason an omitted call still looks plausible in altitude data.
    """
    return (-y, x, z)


def enu_to_nwu(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ENU -> NWU. The inverse of `nwu_to_enu`, i.e. -90 deg about z."""
    return (y, -x, z)


def yaw_nwu_to_enu(yaw: float) -> float:
    """NWU yaw (CCW from North) -> ENU yaw (CCW from East).

    A heading of due North is yaw 0 in NWU and +pi/2 in ENU, so this adds a quarter turn.
    """
    return _wrap_pi(yaw + math.pi / 2.0)


def yaw_enu_to_nwu(yaw: float) -> float:
    """ENU yaw -> NWU yaw."""
    return _wrap_pi(yaw - math.pi / 2.0)
