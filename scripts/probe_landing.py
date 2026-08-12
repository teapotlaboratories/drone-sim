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

    with open(a.out, "w", buffering=1) as fh:
        while time.time() - t0 < a.max_seconds:
            loop = time.time()
            try:
                kin = rpc.call("simGetGroundTruthKinematics", a.vehicle)
                pose = rpc.call("simGetVehiclePose", a.vehicle)
            except Exception as exc:
                errs += 1
                if errs <= 5:
                    fh.write(json.dumps({"t": round(time.time() - t0, 2),
                                         "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                time.sleep(period)
                continue

            fh.write(json.dumps({
                "t": round(time.time() - t0, 2),
                # NED: positive z is BELOW the origin.
                "phys_z": round(kin["position"]["z_val"], 3),
                "pose_z": round(pose["position"]["z_val"], 3),
                # If these two ever differ, the physics body and the reported pose have split,
                # which is the whole question this probe exists to answer.
                "dz": round(kin["position"]["z_val"] - pose["position"]["z_val"], 4),
                "vz": round(kin.get("linear_velocity", {}).get("z_val", float("nan")), 3),
            }) + "\n")
            n += 1
            time.sleep(max(0.0, period - (time.time() - loop)))

    print(f"probe: {n} samples, {errs} errors", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
