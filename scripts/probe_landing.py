#!/usr/bin/env python3
"""Watch a landing from AirSim's side: contact, physics pose, reported pose.       (SIM-27)

SITL only. Read-only — it polls the RPC and commands nothing.

WHY
---
The 10-seed gate found a landing that never terminates: the vehicle descends at exactly
`MPC_LAND_SPEED` to ~30 m below its own takeoff surface, never touches down, never disarms, and
the state times out. The first diagnosis was "it falls through the ground", asserted partly from
the flight video. **The video does not support it** — at a physics-reported 30 m below the
surface the world still renders as a normal landing, with live frames.

So AirSim's physics body and the rendered pose disagree, and nothing on the ROS 2 side can say
which is right, because everything there is downstream of the same physics.

This asks AirSim directly and logs the two poses that can disagree:

    simGetGroundTruthKinematics the physics body's position  (what every PX4 sensor derives from)
    simGetVehiclePose           the Unreal ACTOR's position  (what the cameras see)

IT DELIBERATELY DOES NOT CALL simGetCollisionInfo, and must not start.

    // RpcLibServerBase.cpp:435
    getVehicleSimApi(vehicle_name)->getCollisionInfoAndReset();
    // PawnSimApi.cpp:507 -- getCollisionInfoAndReset()
    state_.collision_info.has_collided = false;      // <- clears it ON READ

That RPC is READ-AND-RESET. `has_collided` is a one-shot flag, so every reader CONSUMES it. The
collision witness (watch_collisions.py, 20 Hz) is what decides gate PASS/FAIL, and a second
poller would silently eat impacts out from under it -- reintroducing exactly the blindness
`SIM-22` was built to remove, from inside the tool meant to diagnose `SIM-27`.

Contact is therefore the witness's job alone. This probe answers the one question the witness
cannot: whether the actor and the integrator still agree.

IT ALSO ENDS THE RUN. Since 2026-08-28 (`SIM-27` step 2) this is not only a recorder: when the
two poses split by more than a metre while the integrator is descending, it writes a
`{"fault": "pose_split", ...}` record into the same trace. run_scenario.py tails that record and
aborts the flight with a named verdict -- *landing surface rejected -- actor frozen, integrator
descending* -- instead of letting it run out the 240 s state timeout and report `timeout in
state land`, i.e. a CONTROL failure on a run whose mission flew perfectly. It does not prevent
the split; that is the ground-lock decision in FastPhysicsEngine.hpp and `SIM-27` step 3.

MEASURED baseline on a healthy landing: the two poses track to within **0.07 m**, and across 40
consecutive AUTO.LAND touchdowns the worst divergence was 0.21 m -- a single sample taken at
-2.5 m/s, i.e. skew between two RPC calls rather than a real split. So a genuine divergence would
be unmistakable.

RUN IT (inside sim-ros2, alongside a flight):

    docker cp scripts/probe_landing.py scripts/airsim_rpc_client.py sim-ros2:/tmp/
    docker exec -d sim-ros2 bash -lc 'cd /tmp && python3 probe_landing.py --out /tmp/landing.jsonl'
"""
import argparse
import json
import os
import sys
import time

# THE RPC CLIENT IS IMPORTED LAZILY, inside main().                              (SIM-27)
#
# `airsim_rpc_client` imports msgpack at module scope, and CI installs pytest and pyyaml only
# (.github/workflows/checks.yml). SplitWatch below is the harness's live fault detector, so it
# has to be importable by a test and by run_scenario.py on the host -- neither of which is
# talking to AirSim. Importing the transport here would make a pure arithmetic detector
# untestable anywhere but on the bench.


# WHEN THE TWO POSES HAVE SPLIT FOR REAL -- the thresholds, and why these numbers.  (SIM-27)
#
# 1.0 m: a healthy landing keeps actor and integrator within 0.07 m (one landing), 0.1122 m
#        (40 cold gate seeds) and 0.2104 m (worst of 40 AUTO.LAND touchdowns, a single sample
#        taken at -2.5 m/s). The failing CitySample run passed 1 m 1.4 s after touchdown and
#        reached 107.9 m. Nothing measured lives between those.
# 1.0 s AND 3 samples: every false positive on record is ONE sample of skew between the two
#        RPC calls, so a real split must persist. Both conditions, because the probe reconnects
#        after an RPC error and a gap either side of an outage could otherwise satisfy the
#        duration on two samples.
# +0.1 m/s: NED, so positive is DOWN. The fault is named "integrator descending" and this is
#        what makes that literally true rather than merely usual.
# dz > 0, not |dz|: the signature is the integrator sinking BELOW a stopped actor. The climb
#        in the failing trace shows dz reaching -0.279 at -3 m/s -- pure sampling skew, and
#        exactly what an abs() test would eventually trip on.
#
# AND THE SKEW IS SUBTRACTED, not merely waited out.                            (review)
#        The two poses come from two SEQUENTIAL RPCs, so dz carries `vz * gap` of pure
#        sampling error, where gap is the time between the calls. That error is SYSTEMATIC,
#        not transient: it persists for as long as the descent does, so neither the 1 s hold
#        nor the 3-sample count filters it. Measured typical gap is ~0.08 s (0.21 m at
#        -2.5 m/s), but a UE render hitch stretching it to half a second during a 2 m/s leg
#        would manufacture a metre of "split" out of nothing and VOID a healthy run.
#        So the probe now records the gap, and a sample only counts if it clears the
#        threshold AFTER the worst-case skew that gap could explain is removed. A trace
#        without a `gap` field (every run before 2026-08-28) is treated as gap 0, i.e.
#        uncorrected -- which is exactly how those 91 traces were scored.
SPLIT_FAULT_M = 1.0
SPLIT_FAULT_SECONDS = 1.0
SPLIT_FAULT_SAMPLES = 3
SPLIT_DESCENT_VZ = 0.1

# The verdict text, defined ONCE and imported by run_scenario.py, because it is the sentence a
# stranger debugging their own world reads. Today they get `timeout in state land` -- a control
# failure, on a run whose mission flew perfectly.
LANDING_FAULT_REASON = "landing surface rejected -- actor frozen, integrator descending"


class SplitWatch:
    """Decide, live, whether the actor and the integrator have split.            (SIM-27)

    Pure arithmetic over the samples the probe already takes: no RPC, no clock of its own, no
    imports. Feed it `update(t, dz, vz)` per sample; it returns the fault record ONCE, the
    first time the split has held long enough to be real, and None every other time.

    Latching is deliberate. The run is over the moment this fires -- what follows is evidence,
    not a second verdict -- and a detector that re-fires would rewrite the abort file under a
    controller that had already read it.
    """

    def __init__(self, threshold_m: float = SPLIT_FAULT_M,
                 hold_seconds: float = SPLIT_FAULT_SECONDS,
                 min_samples: int = SPLIT_FAULT_SAMPLES,
                 min_vz: float = SPLIT_DESCENT_VZ):
        self.threshold_m = threshold_m
        self.hold_seconds = hold_seconds
        self.min_samples = min_samples
        self.min_vz = min_vz
        self.since = None          # t of the first sample of the current qualifying run
        self.samples = 0
        self.fired = False

    def update(self, t: float, dz, vz, gap: float = 0.0) -> dict | None:
        """`gap` is the seconds between the two RPCs that produced this sample. 0 means
        unknown (older traces), and is treated as no correction."""
        if self.fired:
            return None
        # None reaches here from a real sample: the probe writes vz as null when AirSim omits
        # linear_velocity. Unknown is not "descending", so it breaks the run rather than
        # extending it -- the alternative silently treats a hole in the data as evidence.
        if dz is None or vz is None or vz < self.min_vz:
            self.since = None
            self.samples = 0
            return None
        # The most this sample's dz could owe to reading the two poses at different moments.
        # Subtracted rather than bounded by a fixed gap limit, because the quantity that
        # matters is metres of error, and that is speed times gap -- not gap alone.
        skew = abs(vz) * max(gap or 0.0, 0.0)
        if dz - skew < self.threshold_m:
            self.since = None
            self.samples = 0
            return None
        if self.since is None:
            self.since = t
        self.samples += 1
        held = t - self.since
        if held < self.hold_seconds or self.samples < self.min_samples:
            return None
        self.fired = True
        return {
            "fault": "pose_split",
            "reason": LANDING_FAULT_REASON,
            "t": round(t, 2),
            "dz": round(dz, 3),
            # What survives after the worst-case sampling skew is removed -- the number the
            # threshold was actually applied to.
            "dz_less_skew": round(dz - skew, 3),
            "rpc_gap_s": round(gap or 0.0, 3),
            "vz": round(vz, 3),
            "held_seconds": round(held, 2),
            "samples": self.samples,
            "threshold_m": self.threshold_m,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe a landing from AirSim's side (SITL only).")
    ap.add_argument("--out", required=True, help="JSON-lines, flushed every sample")
    ap.add_argument("--vehicle", default="PX4")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--max-seconds", type=float, default=1200.0)
    a = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from airsim_rpc_client import Rpc          # lazy: see the note at the top of the file

    rpc = Rpc()
    period = 1.0 / max(a.hz, 0.5)
    t0 = time.time()
    n = errs = 0
    watch = SplitWatch()

    with open(a.out, "w", buffering=1) as fh:
        # THE WHOLE LOOP, not just the RPC calls.                                (SIM-27)
        #
        # `pkill -INT` is how the runner stops this probe, and the interrupt lands wherever
        # the process happens to be -- overwhelmingly in the sleep at the bottom of the loop,
        # not in the RPC handled below. Measured on the 2026-08-28 verification flight: the
        # summary line was replaced by a traceback, so a run reported NOTHING about whether
        # the poses split. The file's own docstring claimed this line always printed.
        try:
            while time.time() - t0 < a.max_seconds:
                loop = time.time()
                try:
                    # TIMED, because the interval between these two calls is the whole error
                    # budget of `dz`: they are the physics body and the Unreal actor read one
                    # after the other, and anything moving covers ground in between.  (review)
                    call_a = time.time()
                    kin = rpc.call("simGetGroundTruthKinematics", a.vehicle)
                    pose = rpc.call("simGetVehiclePose", a.vehicle)
                    gap = time.time() - call_a
                except KeyboardInterrupt:
                    break
                except Exception as exc:
                    errs += 1
                    if errs <= 5:
                        fh.write(json.dumps({"t": round(time.time() - t0, 2),
                                             "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                    # RECONNECT rather than reuse a socket that just failed. A half-finished
                    # exchange leaves a reply unread, and a client that carries on would answer
                    # every later call with the previous one's result -- so the two poses would be
                    # identical by construction and `dz` would read 0.000 for the rest of the run.
                    # Rpc.call now discards mismatched msgids, and this closes the other half.
                    try:
                        rpc = Rpc()
                    except Exception:
                        pass
                    time.sleep(period)
                    continue

                vz = (kin.get("linear_velocity") or {}).get("z_val")
                t = round(time.time() - t0, 2)
                dz = round(kin["position"]["z_val"] - pose["position"]["z_val"], 4)
                fh.write(json.dumps({
                    "t": t,
                    # NED: positive z is BELOW the origin.
                    "phys_z": round(kin["position"]["z_val"], 3),
                    "pose_z": round(pose["position"]["z_val"], 3),
                    # If these two ever differ, the physics body and the reported pose have split,
                    # which is the whole question this probe exists to answer.
                    "dz": dz,
                    # None, not float("nan"): json.dumps emits a bare NaN, which is not valid JSON
                    # and is rejected by jq and every strict parser. Python accepts it, so it would
                    # only bite the first non-Python reader.
                    "vz": (round(vz, 3) if vz is not None else None),
                    # Seconds spanned by the two RPCs above. Recorded for every sample so the
                    # trace can be re-scored later against a different skew rule.
                    "gap": round(gap, 3),
                }) + "\n")
                n += 1

                # THE SPLIT IS ANNOUNCED IN THE TRACE, and the trace is what the runner tails.
                #
                # This file is written to /out, which sim_up.sh bind-mounts to <repo>/out, so
                # run_scenario.py reads this record from the host without a second RPC consumer and
                # without a `docker exec` per poll. The probe does not stop here: everything after
                # the fault is the evidence for what the split then did, and the 107.9 m in the
                # CitySample trace was only visible because the probe kept going.
                fault = watch.update(t, dz, vz, gap)
                if fault is not None:
                    fh.write(json.dumps(fault) + "\n")
                    print(f"probe: SPLIT at t={fault['t']}s -- {fault['reason']} "
                          f"(dz {fault['dz']} m, vz {fault['vz']} m/s)", flush=True)

                time.sleep(max(0.0, period - (time.time() - loop)))
        except KeyboardInterrupt:
            pass

    # Printed on the way out however we leave, including SIGINT -- `pkill -INT` is how the
    # runner stops this, and without the handler above a traceback replaced this line, which is
    # the ONLY place the error count is reported. "No divergence" and "the RPC was dead for 90 s"
    # must not look the same from outside.
    print(f"probe: {n} samples, {errs} errors, "
          f"split {'DETECTED' if watch.fired else 'not detected'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
