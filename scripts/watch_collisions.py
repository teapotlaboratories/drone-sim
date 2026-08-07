#!/usr/bin/env python3
"""Witness collisions during a flight, independently of the flight node.

SITL only. Read-only: it polls the simulator and writes JSON, it never commands anything.

WHY THIS EXISTS
---------------
Until this script, NOTHING in the harness could detect that the vehicle hit something. A run
that flew into a block reported as a position error and nothing else -- the leg scoring measures
distance to the waypoint and arrival speed, and both of those look merely "bad" after an impact
rather than "invalid". A 48 m miss and a 92 s leg are exactly what a collision produces, and
exactly what poor tracking produces, and the summary could not tell them apart.

It is a SEPARATE observer on purpose. The mission node could poll this itself, but then the
thing under test would be reporting on its own crash. An independent witness costs one process
and cannot be silenced by the failure it is watching.

THE TRAP: has_collided IS NOT ENOUGH
------------------------------------
`simGetCollisionInfo` reports the vehicle's CURRENT contact, and a drone sitting on the ground is
in contact. Measured on a parked vehicle before takeoff:

    has_collided = True   object_name = Ground   impact_point z = 0.9

So a naive `if has_collided` fires on every run, before it has even armed. Two things separate a
real impact from resting on the floor:

  * `object_name` -- "Ground" (and the ground-like names below) is the floor, anything else is a
    thing the vehicle hit.
  * contact CONTINUITY -- one event lasts until a poll sees no collision (or a different
    object). `time_stamp` looks like the right key and is not: it keeps advancing while the
    vehicle DRAGS along a surface, so keying on it logged a single scrape as 56 "collisions"
    in the run that first exposed this.

GROUND NAMES ARE WORLD-SPECIFIC. Blocks calls it "Ground"; a user world may call its landscape
anything. An unrecognised name is reported as a collision rather than silently ignored -- a false
positive you can see beats a false negative you cannot.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airsim_rpc_client import Rpc

# Names treated as the floor rather than as an obstacle. Substring match, case-insensitive.
GROUND_NAMES = ("ground", "landscape", "terrain", "floor", "default_terrain")


def is_ground(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(g in n for g in GROUND_NAMES)


def main() -> int:
    ap = argparse.ArgumentParser(description="Witness collisions during a SITL flight.")
    ap.add_argument("--out", default="/tmp/collisions.json")
    ap.add_argument("--vehicle", default="PX4")
    ap.add_argument("--hz", type=float, default=20.0)
    ap.add_argument("--max-seconds", type=float, default=1800.0)
    a = ap.parse_args()

    rpc = Rpc()
    events, polls, errors = [], 0, 0
    open_evt = None
    ground_contacts = 0
    t0 = time.time()
    period = 1.0 / max(a.hz, 1.0)

    # Write immediately, and keep rewriting: a run killed mid-flight must still leave a verdict
    # behind. An empty file that appears only at the end is indistinguishable from a crash.
    def flush():
        with open(a.out, "w") as f:
            json.dump({
                "collisions": events,
                "collision_count": len(events),
                "ground_contacts": ground_contacts,
                "polls": polls,
                "rpc_errors": errors,
                "seconds": round(time.time() - t0, 1),
                "hz_requested": a.hz,
            }, f, indent=2)

    flush()
    try:
        while time.time() - t0 < a.max_seconds:
            polls += 1
            try:
                c = rpc.call("simGetCollisionInfo", a.vehicle)
            except Exception:
                errors += 1
                time.sleep(period)
                continue

            if not c.get("has_collided"):
                open_evt = None          # contact broken -- the next impact is a NEW event
            else:
                name = c.get("object_name", "")
                if is_ground(name):
                    ground_contacts += 1
                    open_evt = None
                else:
                    # ONE EVENT PER CONTACT, not one per poll. `time_stamp` alone does not
                    # do this: it keeps advancing while the vehicle DRAGS along a surface, so a
                    # single scrape logged 56 "collisions" in the run that exposed this. A new
                    # event starts only when contact with that object has actually broken --
                    # i.e. a poll saw no collision, or saw a different object.
                    now = round(time.time() - t0, 2)
                    if open_evt is not None and open_evt["object_name"] == name:
                        open_evt["t_end"] = now
                        open_evt["duration_s"] = round(now - open_evt["t"], 2)
                        open_evt["polls_in_contact"] += 1
                        open_evt["penetration_max"] = round(
                            max(open_evt["penetration_max"],
                                float(c.get("penetration_depth") or 0.0)), 4)
                    else:
                        ip = c.get("impact_point", {}) or {}
                        open_evt = {
                            "t": now, "t_end": now, "duration_s": 0.0,
                            "object_name": name,
                            "object_id": c.get("object_id"),
                            "polls_in_contact": 1,
                            "penetration_max": round(float(c.get("penetration_depth") or 0.0), 4),
                            "impact_point": [round(float(ip.get("x_val", 0.0)), 2),
                                             round(float(ip.get("y_val", 0.0)), 2),
                                             round(float(ip.get("z_val", 0.0)), 2)],
                        }
                        events.append(open_evt)
                        print(f"COLLISION t+{now}s  {name}", flush=True)
                    flush()
            time.sleep(period)
    except KeyboardInterrupt:
        pass

    flush()
    print(f"collisions={len(events)} ground_contacts={ground_contacts} "
          f"polls={polls} rpc_errors={errors}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
