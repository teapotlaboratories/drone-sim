#!/usr/bin/env python3
"""Assert PX4's EKF local origin agrees with its own GPS before a run counts.

WHY THIS EXISTS (SIM-10, from SIM-09):

PX4 sets its EKF local origin ONCE. If it initialises before the simulated vehicle has
settled at its final altitude, `ref_alt` is frozen at the wrong height and every altitude
PX4 reports is offset by the difference -- for the whole session, with no warning.

That is not hypothetical. It cost a full debugging session in SIM-09:

    ref_alt          88.113 m     <- origin, set too early
    altitude_msl_m  123.280 m     <- GPS, correct all along
    vehicle_local_position.z  =  -35.167 m, constant to 2 mm, while ON THE GROUND

The controller targets an ABSOLUTE altitude, so a vehicle that reports +35 m while grounded
gets commanded to descend into the ground. It never moves, PX4 auto-disarms via
COM_DISARM_PRFLT, and the controller times out. Every symptom points at the controller, and
the controller is fine -- the identical node scored 10/10 on the retired Gazebo baseline.

THE POINT: this failure is silent, order-dependent, and mimics a control bug. A run against a
mis-initialised origin must be VOID -- not scored as a failure -- because scoring it would
blame the flight code for a bring-up defect and would poison a success-rate gate. Same
distinction the retired Gazebo gate drew: a void run is not a failed run.

Exit codes:
    0  origin sane        -> the run may proceed and count
    2  origin STALE       -> VOID; fix the bring-up ordering, do not score this run
    3  could not tell     -> VOID; telemetry missing (also not a failure)
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys

# One metre. The observed good case agreed to ~1 mm (ref_alt 123.280 vs GPS 123.28) and the
# observed bad case was off by 35.167 m, so anything in between is comfortably separated.
# Deliberately not tighter: baro/GPS noise and a settling vehicle make sub-metre agreement a
# flaky assertion rather than a stricter one.
DEFAULT_TOLERANCE_M = 1.0

VOID_STALE = 2
VOID_UNKNOWN = 3


def origin_is_sane(ref_alt: float | None, gps_alt: float | None,
                   tolerance_m: float = DEFAULT_TOLERANCE_M) -> tuple[bool, str]:
    """Pure decision, so it is testable without a simulator or a ROS graph.

    Returns (ok, human-readable reason). `None` for either input means "could not tell",
    which is NOT sane -- absence of evidence must not read as a pass.
    """
    if ref_alt is None or gps_alt is None:
        missing = [n for n, v in (("ref_alt", ref_alt), ("gps_alt", gps_alt)) if v is None]
        return False, f"could not read {' and '.join(missing)}"
    # NaN MUST be rejected explicitly. abs(nan - x) is nan and `nan > tol` is False, so a
    # NaN would otherwise fall through to "sane" -- and PX4 publishes ref_alt as NaN before
    # the EKF has established an origin at all, which is precisely the unsafe state this
    # check exists to catch. Caught by running the cold start: the first version of this
    # function reported "OK: ref_alt nan m ... = nan m apart". Same class as the gate's
    # test_nan_error_must_not_pass, which this repo already had.
    bad = [n for n, v in (("ref_alt", ref_alt), ("gps_alt", gps_alt)) if not math.isfinite(v)]
    if bad:
        return False, (f"{' and '.join(bad)} is not finite ({ref_alt} / {gps_alt}) -- the EKF "
                       f"has not established an origin yet. VOID, not a pass.")
    delta = abs(ref_alt - gps_alt)
    if delta > tolerance_m:
        return False, (f"EKF origin is STALE: ref_alt {ref_alt:.3f} m vs GPS {gps_alt:.3f} m "
                       f"= {delta:.3f} m apart (tolerance {tolerance_m:g} m). PX4 initialised "
                       f"its origin before the vehicle settled; restart PX4 after the sim is "
                       f"up. This run is VOID, not a failure.")
    return True, (f"EKF origin sane: ref_alt {ref_alt:.3f} m vs GPS {gps_alt:.3f} m "
                  f"= {delta:.3f} m apart (tolerance {tolerance_m:g} m)")


def _echo_field(topic: str, field: str, timeout_s: int) -> float | None:
    """One sample of a numeric field. /fmu/out/* publishers are BEST_EFFORT (P1-02): a
    default RELIABLE subscription matches nothing and reads as silence on a healthy stack."""
    cmd = ["ros2", "topic", "echo", "--qos-reliability", "best_effort",
           "--qos-durability", "volatile", "--once", "--field", field, topic]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_s).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    for line in out.splitlines():
        # `ros2 topic echo` interleaves "---" separators, and a leading "-" is NOT enough to
        # identify a number -- "---" passes that test and then raises. Parse per line.
        try:
            return float(line.strip())
        except ValueError:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Assert PX4's EKF origin agrees with GPS (SIM-10). "
                    "A mis-initialised origin makes a run VOID, not failed.")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_M,
                    help="metres of allowed disagreement (default: %(default)s)")
    ap.add_argument("--timeout", type=int, default=20,
                    help="seconds to wait for each topic sample (default: %(default)s)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    ref_alt = _echo_field("/fmu/out/vehicle_local_position", "ref_alt", args.timeout)
    gps_alt = _echo_field("/fmu/out/vehicle_gps_position", "altitude_msl_m", args.timeout)

    ok, reason = origin_is_sane(ref_alt, gps_alt, args.tolerance)
    if not args.quiet:
        print(("OK: " if ok else "VOID: ") + reason)
    if ok:
        return 0
    # A non-finite origin is "no origin established yet", which is UNKNOWN rather than
    # STALE -- the distinction matters because a caller may reasonably wait and retry on
    # UNKNOWN, whereas STALE means the ordering is already wrong and needs a PX4 restart.
    unknown = (ref_alt is None or gps_alt is None
               or not math.isfinite(ref_alt) or not math.isfinite(gps_alt))
    return VOID_UNKNOWN if unknown else VOID_STALE


if __name__ == "__main__":
    sys.exit(main())
