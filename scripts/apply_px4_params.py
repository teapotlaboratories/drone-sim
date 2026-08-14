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
    # MPC_XY_CRUISE is NOT set here, though it looks like it belongs. Its declared range is
    # [3.0, 20.0], so it cannot express a slow envelope: setting it to 0.5 stores an
    # out-of-range value that param set neither clamps nor rejects, and the read-back then
    # "confirms" a number PX4 itself considers invalid. MPC_XY_VEL_MAX is the cap that acts on
    # offboard position setpoints, which is what this harness flies.           (review, PR 53)
    "velocity_xy_max_mps":   ("MPC_XY_VEL_MAX",),
    "velocity_up_max_mps":   ("MPC_Z_VEL_MAX_UP",),
    # MPC_LAND_SPEED too, or the key cannot do what its name says. The LANDING descent is
    # floored by MPC_LAND_SPEED (default 0.7, min 0.6), not by MPC_Z_VEL_MAX_DN -- measured:
    # asking for 0.6 produced 0.689 m/s, which is MPC_LAND_SPEED's default and not a coincidence.
    # Setting only the max would leave `velocity_down_max_mps` unable to slow the one descent
    # every mission performs.
    "velocity_down_max_mps": ("MPC_Z_VEL_MAX_DN", "MPC_LAND_SPEED"),
    "accel_horizontal_mps2": ("MPC_ACC_HOR",),
    "jerk_max_mps3":         ("MPC_JERK_MAX",),
    "yaw_rate_max_dps":      ("MPC_YAWRAUTO_MAX", "MC_YAWRATE_MAX"),
}


class Px4Unreachable(RuntimeError):
    """`docker exec` into PX4 failed -- which is NOT the same as a parameter not existing."""


def _px4(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    r = subprocess.run(["docker", "exec", PX4, "bash", "-lc", f"cd {BUILD} && {cmd}"],
                       capture_output=True, text=True, timeout=timeout)
    # CHECK THE RETURN CODE. Without this, a stopped or still-booting sim-px4 gives empty
    # stdout, read_param returns None, and the operator is told
    # "MPC_XY_VEL_MAX: not a parameter in this PX4 build" -- sent to hunt a naming or build
    # problem that does not exist.                                             (review, PR 53)
    if r.returncode != 0:
        raise Px4Unreachable(
            f"cannot reach PX4 in container '{PX4}' (exit {r.returncode}): "
            f"{(r.stderr or r.stdout or '').strip()[:200]}")
    return r


def param_ranges() -> dict:
    """Declared min/max for every parameter, from the running build's own metadata.

    `param set` neither clamps nor rejects an out-of-range value, so reading a value back
    proves it is STORED, not that PX4 considers it legal. parameters.json ships inside the
    image and is the authority.                                                (review, PR 53)
    """
    r = _px4(f"cat {BUILD}/parameters.json")
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}
    return {p["name"]: p for p in doc.get("parameters", [])}


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

    ranges = param_ranges()
    applied, bad = {}, []
    for key, value in limits.items():
        for param in LIMITS[key]:
            meta = ranges.get(param, {})
            lo, hi = meta.get("min"), meta.get("max")
            if lo is not None and float(value) < float(lo):
                bad.append(f"{param}: {value} is below PX4's declared minimum {lo} (from '{key}')")
                continue
            if hi is not None and float(value) > float(hi):
                bad.append(f"{param}: {value} is above PX4's declared maximum {hi} (from '{key}')")
                continue
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


def reset_defaults() -> dict:
    """Put every parameter this script manages back to the build's declared default."""
    ranges = param_ranges()
    out = {}
    for params in LIMITS.values():
        for param in params:
            default = ranges.get(param, {}).get("default")
            if default is None:
                continue
            _px4(f"./bin/px4-param set {param} {float(default)}")
            got = read_param(param)
            if got is not None:
                out[param] = got
    return out


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
        # RESTORE, do not merely skip.                                         (review, PR 53)
        #
        # On a reused stack (`--no-restart`, or `run_gate --reuse`) nothing resets PX4 between
        # runs. Flying square-10m-slow and then the baseline would have flown the baseline at
        # 0.5 m/s while its result recorded `applied_limits: null` -- and its waypoint errors
        # would then be compared against numbers gathered at 12 m/s. Cold starts were safe only
        # because teardown() recreates the container.
        restored = reset_defaults()
        print("no limits declared; restored PX4 defaults for the managed parameters")
        for param, value in sorted(restored.items()):
            print(f"  {param} = {value}")
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
