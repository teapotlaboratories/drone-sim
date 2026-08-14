#!/usr/bin/env python3
"""Did the aircraft actually respect the envelope the scenario asked for?         (SIM-31)

Reads an MCAP bag and reports what the vehicle DID, next to what the scenario asked for.

WHY THIS EXISTS
---------------
`apply_px4_params.py` proves a parameter was accepted and reads it back. That is necessary and
NOT sufficient: it shows PX4 stored a number, not that the number changed the flight. Five of the
six limit keys shipped having never been flown -- only set and read back -- which is precisely
the class of claim this project has been caught making before.

WHAT IT MEASURES, and from what
-------------------------------
Everything here comes from `/fmu/out/vehicle_local_position`, which the scenarios already record:

    climb / descent   vz          NED, so NEGATIVE vz is UP
    horizontal speed  hypot(vx, vy)
    horizontal accel  d/dt of that speed, from the message timestamps
    yaw rate          d/dt of heading, unwrapped across the +/-pi seam

NO SINGLE STATISTIC IS RIGHT FOR EVERY LIMIT, which is why both are printed.

    SUSTAINED (p95) suits a PHASE -- a climb, a descent, a cruise. The peak there is a
    transition overshoot: asking for 0.6 m/s descent gave a 0.688 peak lasting 1.8 s while the
    sustained rate was 0.572.

    PEAK suits a MANOEUVRE OF FIXED SIZE -- a turn. Measured on a 180 deg yaw: unlimited gave
    p95 4.5 / peak 31.7, and limiting to 10 deg/s gave p95 10.2 / peak 15.3. The sustained
    figure went UP under the limit, because a slower turn spends far longer turning, so a larger
    share of the flight sits at the bound. The peak halved, which is the real effect.

    So the verdict column is computed from the sustained value and is meaningful for phases; for
    a manoeuvre, read the peaks against each other. A verdict is not a substitute for looking.

READ THE COMPARISON HONESTLY. A limit is an upper bound: measuring UNDER it proves nothing on its
own, because a gentle mission may never reach the bound. The useful signal is the pair -- the same
mission flown limited and unlimited -- and a measurement that EXCEEDS its limit is a real failure.
Accelerations differentiated from a 5-10 Hz estimate are noisy, so a small overshoot is the
measurement, not the aircraft; the tolerance below says so out loud rather than hiding it.
"""
import argparse
import json
import math
import sys

TOPIC = "/fmu/out/vehicle_local_position"

# Scenario key -> (what to measure, human label, unit)
CHECKS = {
    "velocity_up_max_mps":   ("climb", "max climb rate", "m/s"),
    "velocity_down_max_mps": ("descent", "max descent rate", "m/s"),
    "velocity_xy_max_mps":   ("speed", "max horizontal speed", "m/s"),
    "accel_horizontal_mps2": ("accel", "max horizontal accel", "m/s^2"),
    "yaw_rate_max_dps":      ("yawrate", "max yaw rate", "deg/s"),
    # jerk_max_mps3 is deliberately absent: differentiating a 5-10 Hz position estimate three
    # times is noise, not measurement. Claiming a jerk number from this data would be inventing
    # precision the source does not have.
}


def read_series(path: str) -> list:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from px4_msgs.msg import VehicleLocalPosition

    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=path, storage_id="mcap"),
           rosbag2_py.ConverterOptions("", ""))
    r.set_filter(rosbag2_py.StorageFilter(topics=[TOPIC]))
    out = []
    while r.has_next():
        _, data, t = r.read_next()
        m = deserialize_message(data, VehicleLocalPosition)
        # m.timestamp, not the bag's receive time. DDS delivery jitter and batching would
        # otherwise land straight in the dt divisor of every derivative below. (review, PR 53)
        out.append((m.timestamp * 1e-6, m.vx, m.vy, m.vz, m.heading,
                    bool(m.v_xy_valid), bool(m.v_z_valid),
                    bool(m.heading_good_for_control)))
    return out


# Differentiate over a WINDOW, not between adjacent samples. The estimate arrives at ~110 Hz
# (measured dt ~0.009 s), and dividing sample-to-sample noise by 0.009 manufactures accelerations
# and yaw rates that never happened.
WINDOW_S = 0.20


def measure(series: list) -> dict:
    """Maxima the vehicle actually reached, using only samples PX4 vouches for.

    THE VALIDITY FLAGS ARE NOT OPTIONAL. The first version of this ignored them and reported a
    max yaw rate of 848 deg/s -- a number no multirotor produces. Every one of the top samples
    had `heading_good_for_control = False`: PX4 was saying the heading estimate could not be
    trusted, and the script differentiated it anyway. 1633 of 10723 samples in that run were so
    flagged. A measurement taken from data its own producer disowns is not evidence.
    """
    if len(series) < 3:
        return {}
    # KEEP EVERY SAMPLE, not a running max. A maximum is the wrong statistic for this question:
    # PX4's parameters bound the SETPOINT, and the vehicle overshoots on transitions. Measured --
    # asking for a 0.6 m/s descent gave a peak of 0.688 lasting 1.8 s as the vehicle entered the
    # descent from 20 m (1.7% of the flight), while the sustained rate was 0.572. Judging on the
    # peak calls that a violation; judging on the sustained value calls it what it is.
    vals = {"climb": [], "descent": [], "speed": [], "accel": [], "yawrate": []}
    n_used = n_total = 0
    for i, (t, vx, vy, vz, h, vxy_ok, vz_ok, hdg_ok) in enumerate(series):
        n_total += 1
        # v_xy_valid / v_z_valid, not xy_valid. The earlier version gated VELOCITY samples on
        # the POSITION estimate's flag, and gated vz on nothing at all -- so a window where PX4
        # vouched for position but disowned velocity still contributed numbers. (review, PR 53)
        if not (vxy_ok or vz_ok):
            continue
        n_used += 1
        s = math.hypot(vx, vy)
        if vxy_ok:
            vals['speed'].append(s)
        if vz_ok:
            vals['climb'].append(-vz)    # NED: negative vz is UP
            vals['descent'].append(vz)

        # Find the earliest sample at least WINDOW_S back, and difference against that.
        j = i
        while j > 0 and t - series[j - 1][0] < WINDOW_S:
            j -= 1
        if j == i:
            continue
        t0, vx0, vy0, _, h0, vxy0, _vz0, hdg0 = series[j]
        dt = t - t0
        if not (0.05 < dt < 1.0):
            continue
        if vxy_ok and vxy0:
            # THE VECTOR, not the change in speed. MPC_ACC_HOR bounds the acceleration vector's
            # magnitude, and |v| - |v0| is blind to direction: a 90-degree corner flown at
            # constant speed has large real horizontal acceleration and measured ~0 here -- so
            # the check read "within" on exactly the manoeuvre most likely to breach it.
            #                                                                  (review, PR 53)
            vals['accel'].append(math.hypot(vx - vx0, vy - vy0) / dt)
        if hdg_ok and hdg0:
            dh = h - h0
            while dh > math.pi:
                dh -= 2 * math.pi
            while dh < -math.pi:
                dh += 2 * math.pi
            vals['yawrate'].append(abs(math.degrees(dh) / dt))
    def p95(xs):
        if not xs:
            return 0.0
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(0.95 * len(xs)))]

    # NOTHING USABLE IS NOT COMPLIANCE. With every list empty, p95 and max both return 0.0 and
    # the dict is still non-empty -- so main() skipped its guard, printed 0.000 against every
    # limit and called each one "within". A run with no usable data reported as a run that
    # respected its envelope.                                                  (review, PR 53)
    if n_used == 0 or not any(vals.values()):
        return {}
    out = {k: (p95(v), max(v) if v else 0.0) for k, v in vals.items()}
    out["_samples"] = f"{n_used}/{n_total} used"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bag", help="the run's MCAP directory")
    ap.add_argument("--limits", default="{}", help="JSON of the scenario's limits, to compare")
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="fractional overshoot tolerated before a check FAILS (default 0.15)")
    a = ap.parse_args()

    got = measure(read_series(a.bag))
    if got:
        print(f"  samples: {got.pop('_samples')}   (window {WINDOW_S:g}s, "
              f"invalid-estimate samples excluded)")
    if not got:
        print("no usable samples in the bag", file=sys.stderr)
        return 2
    limits = json.loads(a.limits)

    print(f"  {'measured':<24}{'sustained':>10}{'peak':>9}   {'limit':>8}   verdict")
    failed = 0
    for key, (field, label, unit) in CHECKS.items():
        value, peak = got[field]
        limit = limits.get(key)
        if limit is None:
            print(f"  {label:<24}{value:>10.3f}{peak:>9.3f}   {'-':>8}   (no limit declared)")
            continue
        # Judged on the SUSTAINED value; the peak is shown because a large gap between them is
        # itself information (a transition overshoot, not a violated envelope).
        over = value > float(limit) * (1 + a.tolerance)
        failed += over
        verdict = "OVER" if over else "within"
        print(f"  {label:<24}{value:>10.3f}{peak:>9.3f}   {float(limit):>8.3f}   {verdict}  ({unit})")
    if failed:
        print(f"\n  {failed} measurement(s) exceeded their limit by more than "
              f"{a.tolerance:.0%} -- the envelope was not respected in flight.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
