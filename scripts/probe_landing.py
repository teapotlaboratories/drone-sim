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

This asks AirSim directly, and logs the three things that separate the possibilities:

    simGetCollisionInfo         does the SIMULATOR think it is touching anything, and what
    simGetGroundTruthKinematics the physics body's position
    simGetVehiclePose           the pose AirSim reports for the vehicle

MEASURED baseline, at rest on the ground: `has_collided=False`, `object_name='Ground'`,
`impact_point.z=0.9`, and `time_stamp` ADVANCING. So the flag is edge-triggered and the name
persists from the last contact -- neither is a live "am I touching the floor" signal. The
advancing timestamp is, which makes a descent during which it FREEZES the signature of falling
through nothing. That is why the raw fields are logged and the verdict is left to the reader.

RUN IT (inside sim-ros2, alongside a flight):

    docker cp scripts/probe_landing.py scripts/airsim_rpc_client.py sim-ros2:/tmp/
    docker exec -d sim-ros2 bash -lc 'cd /tmp && python3 probe_landing.py --out /tmp/landing.jsonl'
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airsim_rpc_client import Rpc


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe a landing from AirSim's side (SITL only).")
    ap.add_argument("--out", required=True, help="JSON-lines, flushed every sample")
    ap.add_argument("--vehicle", default="PX4")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--max-seconds", type=float, default=1200.0)
    a = ap.parse_args()

    rpc = Rpc()
    period = 1.0 / max(a.hz, 0.5)
    t0 = time.time()
    n = errs = 0
    # Remember the last object named, purely for the closing line.
    last_obj = None

    with open(a.out, "w", buffering=1) as fh:
        while time.time() - t0 < a.max_seconds:
            loop = time.time()
            try:
                kin = rpc.call("simGetGroundTruthKinematics", a.vehicle)
                pose = rpc.call("simGetVehiclePose", a.vehicle)
                col = rpc.call("simGetCollisionInfo", a.vehicle)
            except Exception as exc:
                errs += 1
                if errs <= 5:
                    fh.write(json.dumps({"t": round(time.time() - t0, 2),
                                         "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                time.sleep(period)
                continue

            obj = col.get("object_name")
            hit = bool(col.get("has_collided"))
            if obj:
                last_obj = obj

            fh.write(json.dumps({
                "t": round(time.time() - t0, 2),
                # NED: positive z is BELOW the origin.
                "phys_z": round(kin["position"]["z_val"], 3),
                "pose_z": round(pose["position"]["z_val"], 3),
                # If these two ever differ, the physics body and the reported pose have split,
                # which is the whole question this probe exists to answer.
                "dz": round(kin["position"]["z_val"] - pose["position"]["z_val"], 4),
                "vz": round(kin.get("linear_velocity", {}).get("z_val", float("nan")), 3),
                # RAW, not a derived verdict. watch_collisions.py already documents why a
                # derived one is treacherous here: `has_collided` is edge-triggered and fires at
                # rest, `object_name` persists from the LAST contact, and `time_stamp` keeps
                # advancing while contact is sustained. That last property is the signal --
                # during a genuine fall through empty space it should FREEZE. Recording the raw
                # fields lets that be checked afterwards instead of guessed at now.
                "collided": hit,
                "object": obj,
                "penetration": round(col.get("penetration_depth") or 0.0, 4),
                "col_ts": col.get("time_stamp"),
                "impact_z": round((col.get("impact_point") or {}).get("z_val", float("nan")), 3),
            }) + "\n")
            n += 1
            time.sleep(max(0.0, period - (time.time() - loop)))

    print(f"probe: {n} samples, {errs} errors, last object: {last_obj}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
