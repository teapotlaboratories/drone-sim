"""ENU <-> NED conversion — the project's single conversion point.

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

__all__ = ["enu_to_ned", "ned_to_enu", "yaw_enu_to_ned", "yaw_ned_to_enu"]


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
