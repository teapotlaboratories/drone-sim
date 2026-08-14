#!/usr/bin/env python3
"""Apply a scenario's flight envelope to the running PX4, and prove it took.      (SIM-31)

SITL only. This reconfigures the autopilot; it commands no motion.

WHY THIS EXISTS
---------------
A scenario can say where to fly and how tightly to score it, but not how fast the aircraft may
fly getting there. Until this, the only way to bound the envelope was `px4-param set` by hand on
a live stack -- which is not reproducible, is not recorded anywhere, and disappears with the
container.

WHY THE LIMITS GO IN PX4 AND NOT IN OUR CONTROLLER
--------------------------------------------------
`offboard_control` sends POSITION setpoints; PX4's position controller is what turns those into
velocity and acceleration. A limit enforced on our side would be one the autopilot does not know
about -- it would keep planning and accelerating as if unbounded, and the number in the scenario
would describe the harness rather than the aircraft.

READ BACK EVERYTHING
--------------------
`px4-param set` on a name that does not exist is NOT an error: it prints nothing useful and exits
0. A scenario that silently failed to apply its envelope would report a flight it never flew, so
every parameter is read back and compared, and a mismatch fails the run.

MEASURED, so the numbers here are not theoretical: on CitySample, the same seed flown at the
default 12 m/s scored waypoint errors of 0.762 / 0.756 / 0.763 / 0.761 m; at 0.5 m/s it scored
0.068 / 0.062 / 0.051 m -- an order of magnitude tighter. It also took long enough that the
scenario's own `state_timeout_s` (which covers the WHOLE waypoint sequence, not one leg) ran out
at 3 of 4 waypoints. A slower envelope needs a larger budget, and both live in the scenario.
"""
import argparse
import json
import subprocess
import sys

PX4 = "sim-px4"
BUILD = "/opt/px4/build/px4_sitl_default"

# Scenario key -> the PX4 parameters it sets. One key may drive several: a "horizontal speed
# limit" is both the hard cap and the cruise speed, and asking a user to know that is asking them
# to know PX4's parameter tree.
LIMITS = {
    "velocity_xy_max_mps":   ("MPC_XY_VEL_MAX", "MPC_XY_CRUISE"),
    "velocity_up_max_mps":   ("MPC_Z_VEL_MAX_UP",),
    "velocity_down_max_mps": ("MPC_Z_VEL_MAX_DN",),
    "accel_horizontal_mps2": ("MPC_ACC_HOR",),
    "jerk_max_mps3":         ("MPC_JERK_MAX",),
    "yaw_rate_max_dps":      ("MPC_YAWRAUTO_MAX", "MC_YAWRATE_MAX"),
}


def _px4(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", PX4, "bash", "-lc", f"cd {BUILD} && {cmd}"],
                          capture_output=True, text=True, timeout=timeout)


def read_param(name: str) -> float | None:
    """The parameter's current value, or None if PX4 does not know the name."""
    r = _px4(f"./bin/px4-param show {name}")
    for line in (r.stdout or "").splitlines():
        # `x * MPC_XY_VEL_MAX [637,1114] : 0.5000`  -- the leading flags vary (used/saved/unsaved)
        if name in line and ":" in line:
            try:
                return float(line.rsplit(":", 1)[1].strip())
            except ValueError:
                continue
    return None


def apply(limits: dict, tol: float = 1e-3) -> dict:
    """Set every parameter the scenario asked for, then prove each one took."""
    unknown = sorted(set(limits) - set(LIMITS))
    if unknown:
        sys.exit(f"unknown limit key(s): {', '.join(unknown)}\n"
                 f"known: {', '.join(sorted(LIMITS))}")

    applied, bad = {}, []
    for key, value in limits.items():
        for param in LIMITS[key]:
            # A parameter PX4 has never heard of reads back as None BEFORE we try to set it,
            # which is a clearer failure than a silent no-op afterwards.
            if read_param(param) is None:
                bad.append(f"{param}: not a parameter in this PX4 build (from '{key}')")
                continue
            _px4(f"./bin/px4-param set {param} {float(value)}")
            got = read_param(param)
            if got is None or abs(got - float(value)) > tol:
                bad.append(f"{param}: asked {value}, reads {got}")
            else:
                applied[param] = got
    if bad:
        sys.exit("flight envelope NOT applied:\n  " + "\n  ".join(bad)
                 + "\nRefusing to fly: the run would report an envelope it never had.")
    return applied


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply a scenario's flight envelope to PX4 (SITL).")
    ap.add_argument("--limits", required=True,
                    help="JSON object of scenario limit keys, e.g. '{\"velocity_xy_max_mps\": 0.5}'")
    ap.add_argument("--out", help="write the APPLIED parameters here as JSON (provenance)")
    a = ap.parse_args()

    limits = json.loads(a.limits)
    if not isinstance(limits, dict):
        sys.exit("--limits must be a JSON object")
    if not limits:
        print("no limits declared; PX4 keeps its defaults")
        return 0

    applied = apply(limits)
    for param, value in sorted(applied.items()):
        print(f"  {param} = {value}")
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(applied, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
