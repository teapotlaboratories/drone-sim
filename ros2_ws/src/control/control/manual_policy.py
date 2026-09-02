"""What a hand-flown command may do, and from where.                            (SIM-45)

THE POINT OF THIS FILE IS THAT IT IMPORTS NOTHING. No rclpy, no px4_msgs, no
drone_interfaces. `tests/` runs off-target -- no simulator, no ROS 2 runtime -- and the
convention in `tests/test_park_tour.py` is to RE-EXPRESS logic there, with a docstring
naming the source lines so drift is visible. That trade is acceptable for arithmetic. It is
not acceptable for the table that decides whether a browser is allowed to arm an aircraft:
a copy that drifts would leave the test asserting a policy nobody is running.

So the policy lives here as plain strings, `offboard_control` maps it onto its `State` enum
at import time (and fails loudly if a name stops resolving), and the tests import THIS.

The strings are the same tokens `MissionCommand.msg` declares as constants.
`tests/test_manual_flight.py` parses that .msg and asserts the two agree, which is the one
join this file cannot make on its own without importing a generated message.
"""

import math

COMMAND_TAKEOFF = "takeoff"
COMMAND_LAND = "land"
COMMAND_HOLD = "hold"

# State names, matching `offboard_control.State` values.
IDLE = "idle"
HOVER = "hover"
STREAM_SETPOINTS = "stream_setpoints"
REQUEST_OFFBOARD = "request_offboard"
ARM = "arm"
TAKEOFF = "takeoff"

# The states in which waiting forever is CORRECT, so the per-state timeout is suppressed.
# Both are waits on a human. Every other state keeps the timeout that stops a controller
# hanging a CI job for its whole budget.
UNTIMED = frozenset({IDLE, HOVER})

ALLOWED_FROM: dict = {
    # Arming is reachable from exactly one state, and it is the one in which the aircraft is
    # on the ground and the operator has had to have seen it land.
    COMMAND_TAKEOFF: frozenset({IDLE}),
    # LAND from the whole climb, not just from HOVER. An operator watching a drone rise into
    # a tree must not have to wait for it to finish arriving before being allowed to stop it
    # -- that is the SIM-44 failure, and it is a large part of why this interface exists.
    COMMAND_LAND: frozenset({HOVER, TAKEOFF, STREAM_SETPOINTS, REQUEST_OFFBOARD, ARM}),
    COMMAND_HOLD: frozenset({HOVER}),
}

# Commands that put energy INTO the aircraft, and so must re-check the SITL interlock before
# they are obeyed. LAND and HOLD are deliberately absent: refusing to land an aircraft
# because a simulator stopped answering would be a safety check that causes the accident it
# exists to prevent.
REQUIRES_INTERLOCK = frozenset({COMMAND_TAKEOFF})

COMMAND_MOVE = "move"

# MOVE is allowed from HOVER only -- so it is reachable only through a TAKEOFF that already
# satisfied the SITL interlock. Added to ALLOWED_FROM and REQUIRES_INTERLOCK below rather than
# in the literals above, so the diff that introduced it is legible.
ALLOWED_FROM[COMMAND_MOVE] = frozenset({HOVER})

# MOVE re-checks the interlock, unlike LAND and HOLD. The asymmetry is deliberate and it is the
# same rule as before, applied honestly: refusing a command must never be the dangerous option.
# Refusing to MOVE leaves the aircraft holding station, which is safe. Refusing to LAND leaves
# it airborne, which is not.
REQUIRES_INTERLOCK = frozenset({COMMAND_TAKEOFF, COMMAND_MOVE})


# --- THE LEASH ------------------------------------------------------------------ (SIM-47)
#
# An unbounded delta from a browser is how an aircraft flies into a building nobody could see.
#
# ENFORCED IN THE NODE, NEVER IN THE PAGE. The page cannot be the thing that keeps the aircraft
# safe, because `ros2 topic pub` does not run the page -- the same argument that put the SITL
# interlock in the node that arms rather than in the transport.
#
# CLAMPED, NOT REFUSED. A refusal at the boundary makes a held key do nothing with no
# explanation; a clamp gives the operator a fence they can feel. Every clamp is reported so
# that "it stopped moving" is never mysterious.
#
# These are DEFAULTS. `offboard_control` exposes each as a ROS parameter, so a scenario that
# genuinely needs a wider envelope sets it deliberately, at launch, where it is recorded --
# rather than a browser widening it at run time.
DEFAULT_LIMITS = {
    "step_max_m": 5.0,        # one command may not teleport the hold point across the map
    "radius_max_m": 50.0,     # nor drift far from where the operator watched it take off
    "alt_min_m": 2.0,         # descending into the ground is not a translation
    "alt_max_m": 60.0,        # nor is climbing out of sight
    "yaw_step_max_rad": math.pi / 4.0,
}


def clamp_move(current_enu, home_enu, delta_enu, limits=None):
    """Where the hold point may actually go, and what had to be given up to get there.

    `current_enu` is the present hold point, `home_enu` the (x, y) the aircraft took off
    from, `delta_enu` an ALREADY-ROTATED world-frame delta (see frames.flu_to_enu).

    Returns `(target_enu, notes)`. `notes` is a list of human-readable strings, one per bound
    that bit -- empty when nothing was clamped. The caller logs them; the operator reads them.

    Pure, and deliberately dependency-free, so tests/test_manual_flight.py exercises the exact
    arithmetic the aircraft runs rather than a re-expression of it.

    ORDER MATTERS, and it is: step, then altitude, then radius.
      * step first, because it bounds the request itself -- a 10 km delta must not be allowed
        to reach the radius check and be "clamped" into a legal but wildly unintended place.
      * radius last, because altitude clamping changes nothing horizontal, while clamping the
        radius must be the final word on where the aircraft ends up.
    """
    lim = dict(DEFAULT_LIMITS)
    if limits:
        lim.update({k: v for k, v in limits.items() if v is not None})
    notes = []

    dx, dy, dz = float(delta_enu[0]), float(delta_enu[1]), float(delta_enu[2])

    # 1. per-step cap, applied to the horizontal delta and the vertical one separately.
    #    Separately because they are different kinds of mistake: a big sideways nudge is a
    #    misjudged distance, a big vertical one is usually a stuck key.
    step = float(lim["step_max_m"])
    horiz = math.hypot(dx, dy)
    if horiz > step:
        scale = step / horiz
        dx, dy = dx * scale, dy * scale
        notes.append(f"step {horiz:.1f} m -> {step:.1f} m")
    if abs(dz) > step:
        notes.append(f"climb {dz:+.1f} m -> {math.copysign(step, dz):+.1f} m")
        dz = math.copysign(step, dz)

    x = float(current_enu[0]) + dx
    y = float(current_enu[1]) + dy
    z = float(current_enu[2]) + dz

    # 2. altitude band -- A FENCE, NOT A MAGNET.
    #
    # The band may only stop the target moving FURTHER out of it. It must never pull a target
    # that is already outside back to the boundary, because the delta that triggered the clamp
    # may not have been vertical at all.
    #
    # THE BUG THIS FIXES (found in review, never flown): the first version clamped the
    # ABSOLUTE target on every MOVE. Hovering at 100 m -- the page allows a take-off to 120 --
    # a pure YAW nudge arrives with delta (0, 0, 0), the ceiling of 60 m bites, and one
    # keypress commands a 40 m descent. A fence built to keep the aircraft safe was itself a
    # way to command a dive.
    #
    # So the effective band is widened to include wherever the aircraft already is. At 100 m
    # with a 60 m ceiling: climbing is refused, yawing changes nothing, descending works
    # normally -- and once back under 60 m the real ceiling applies again.
    lo, hi = float(lim["alt_min_m"]), float(lim["alt_max_m"])
    z_start = float(current_enu[2])
    lo_eff, hi_eff = min(lo, z_start), max(hi, z_start)
    if z < lo_eff:
        notes.append(f"altitude {z:.1f} m -> floor {lo_eff:.1f} m")
        z = lo_eff
    elif z > hi_eff:
        # Named differently when the aircraft was already above the ceiling, because "ceiling
        # 60.0 m" would be a lie about where it stopped.
        edge = "ceiling" if hi_eff == hi else f"no higher than {hi_eff:.1f} m"
        notes.append(f"altitude {z:.1f} m -> {edge}"
                     if hi_eff != hi else f"altitude {z:.1f} m -> ceiling {hi:.1f} m")
        z = hi_eff

    # 3. radius around HOME, measured horizontally only -- the altitude band already owns the
    #    vertical, and a spherical bound would shrink the usable area as the aircraft climbed,
    #    which is the opposite of what a survey wants.
    hx, hy = float(home_enu[0]), float(home_enu[1])
    r = math.hypot(x - hx, y - hy)
    rmax = float(lim["radius_max_m"])
    if r > rmax:
        scale = rmax / r
        x = hx + (x - hx) * scale
        y = hy + (y - hy) * scale
        notes.append(f"radius {r:.1f} m -> fence {rmax:.1f} m")

    return (x, y, z), notes


def clamp_yaw_step(delta_rad, limits=None):
    """Clamp one yaw nudge. Returns (delta, note-or-None)."""
    lim = dict(DEFAULT_LIMITS)
    if limits:
        lim.update({k: v for k, v in limits.items() if v is not None})
    cap = float(lim["yaw_step_max_rad"])
    d = float(delta_rad)
    if abs(d) > cap:
        return math.copysign(cap, d), (
            f"yaw step {math.degrees(d):+.0f} deg -> {math.degrees(math.copysign(cap, d)):+.0f} deg")
    return d, None
