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

IT STAYS OFF THE COLLISION RPC NEAR THE GROUND                          (SIM-27, 2026-08-31)

This witness used to poll `has_collided`, and that flag is ONE-SHOT: whoever reads it clears it.
Upstream cleared it inside the simGetCollisionInfo RPC, so this witness -- an observer -- was
clearing a flag the PHYSICS ENGINE depends on to run collision response. It starved the engine
of touchdowns: no collision response, no ground lock, and the integrator descended through the
world while the Unreal actor rested on the surface. Measured on CitySample: **10 pose splits in
12 runs with this witness polling, 0 in 7 with nothing polling.** The instrument was causing the
fault the gate then reported as a flight-control failure.

THE FIX IS HERE, NOT IN THE SIMULATOR. The first attempt patched Cosys-AirSim so the RPC
stopped consuming. That was the wrong shape: the simulator behaves correctly for its intended
use -- nothing upstream polls collisions at 20 Hz mid-flight -- and WE introduced the poller.
Patching a vendored tree so our own tool stops breaking it inverts this project's primary rule.
So the change is here:

  * `simGetVehiclePose` is a PLAIN GETTER and consumes nothing. Poll it freely.
  * The collision RPC is touched ONLY while the vehicle is airborne (--min-altitude-m, default
    2 m above its resting height). On or near the ground -- takeoff, touchdown, resting -- this
    witness does not call it at all, so it cannot take the flag the ground lock depends on.
  * Detection keys on `collision_count`, which is MONOTONIC. An increment between polls means
    Unreal reported new contacts; no increment means contact has broken.

THE BLIND SPOT, STATED PLAINLY: contacts below the altitude gate are NOT observed. A strike in
the last two metres of a descent is invisible to this witness. That is a deliberate trade -- the
alternative is being blind to nothing and breaking every landing instead, which is what the 20 Hz
version did: 10 pose splits in 12 CitySample runs with it polling, 0 in 7 with it off.

Reading the renderer log instead would avoid the RPC entirely, and does not work: AirSim's
collision message reaches only the on-screen debug display. The `UE_LOG` calls in
`UAirBlueprintLib::LogMessage` are COMMENTED OUT upstream, which is why the count is burned into
the chase video and appears in none of the flight logs.

THE TRAP: has_collided IS NOT ENOUGH
------------------------------------
`simGetCollisionInfo` reports the vehicle's CURRENT contact, and a drone sitting on the ground is
in contact. Measured on a parked vehicle before takeoff:

    has_collided = True   object_name = Ground   impact_point z = 0.9

So a naive `if has_collided` fires on every run, before it has even armed. Two things separate a
real impact from resting on the floor:

  * `object_name` -- "Ground" (and the ground-like names below) is the floor, anything else is a
    thing the vehicle hit.
  * contact CONTINUITY -- one event lasts until a poll sees NO NEW CONTACTS (or a different
    object). `time_stamp` looks like the right key and is not: it keeps advancing while the
    vehicle DRAGS along a surface, so keying on it logged a single scrape as 56 "collisions"
    in the run that first exposed this. The counter behaves the same way and is grouped the
    same way.

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
    # THE COLLISION RPC IS ONLY TOUCHED ABOVE THIS ALTITUDE.                   (SIM-27)
    #
    # metres above the resting height, NED. See the header: reading the RPC consumes a flag
    # the physics engine needs, and the moment that matters is the touchdown. Staying off it
    # near the ground keeps the witness out of the landing entirely.
    ap.add_argument("--min-altitude-m", type=float, default=2.0)
    a = ap.parse_args()

    rpc = Rpc()
    polls, errors = 0, 0
    t0 = time.time()
    period = 1.0 / max(a.hz, 1.0)

    # Write immediately, and keep rewriting: a run killed mid-flight must still leave a verdict
    # behind. An empty file that appears only at the end is indistinguishable from a crash.
    def flush():
        # CONTACTS WHILE AIRBORNE, or None for "not measured".                     (SIM-27)
        #
        # None is not zero, and the distinction is the reason this file exists: a flight that
        # never crossed the altitude gate, or died before coming back down, has NOT been shown
        # to be clean. Callers treat None as UNKNOWN and must not score it as a pass.
        airborne_contacts = (None if (baseline is None or final_count is None)
                             else max(0, final_count - baseline))
        with open(a.out, "w") as f:
            json.dump({
                "airborne_contacts": airborne_contacts,
                "collision_count": airborne_contacts,   # scored field, kept under its old name
                "last_object": final_object or None,
                "baseline_count": baseline,
                "final_count": final_count,
                "measured": airborne_contacts is not None,
                "min_altitude_m": a.min_altitude_m,
                "polls": polls,
                "rpc_collision_reads": (0 if baseline is None else 1) + (0 if final_count is None else 1),
                "rpc_errors": errors,
                "seconds": round(time.time() - t0, 1),
                "hz_requested": a.hz,
                # WHAT THIS WITNESS CANNOT SEE, stated in its own output so a reader of the
                # artifact does not have to know the history: contacts below the altitude gate
                # (takeoff and touchdown), and any per-event detail -- object names, timing,
                # durations. Two samples cannot produce a story, only a count.
                "blind_to": ["contacts below min_altitude_m",
                             "per-event object, timing and duration"],
            }, f, indent=2)

    was_airborne = False
    baseline = None
    final_count = None
    final_object = ""
    # Resting altitude, captured from the first pose sample. The vehicle is on the ground when
    # this starts, so it is the datum "airborne" is measured against -- an absolute z would be
    # wrong on any world whose ground is not at zero, which is every world we fly.
    rest_z = None

    flush()
    try:
        while time.time() - t0 < a.max_seconds:
            polls += 1
            try:
                # POSE FIRST, AND IT IS FREE. simGetVehiclePose is a plain getter -- it
                # consumes nothing -- so it can be polled as fast as we like and used to decide
                # whether touching the collision RPC is safe at all.
                pose = rpc.call("simGetVehiclePose", a.vehicle)
                z = float((pose.get("position") or {}).get("z_val", 0.0))
                if rest_z is None:
                    rest_z = z                    # first sample: the vehicle is on the ground
                # NED: z INCREASES downward, so "above the ground" is a MORE NEGATIVE z.
                airborne = (rest_z - z) >= a.min_altitude_m
                # EXACTLY TWO READS PER FLIGHT, AND NEITHER IS DURING ONE.      (SIM-27)
                #
                # Reading this RPC consumes a flag the physics engine needs, and there is no
                # non-consuming way to ask -- the log route is closed too (upstream commented
                # out the UE_LOG calls). So the witness cannot watch a flight; it can only
                # BRACKET one. It samples the counter as the vehicle crosses the altitude gate
                # going up, and again as it crosses back down, and reports the difference:
                # contacts that happened while airborne, which is what the gate scores on.
                #
                # Polling anywhere in between is what broke landings (10 splits in 12 runs) and
                # then, at a 2 m gate, broke the cruise instead: an actor frozen at 4.82 m while
                # physics flew the whole mission 25 m away, and the gate scored it PASS.
                #
                # FLUSHED AT EACH TRANSITION, not once at the end. The rewrite dropped the
                # per-event flush, leaving the file written exactly twice -- so a witness
                # killed between the two (SIGKILL, a container teardown, or a closing RPC
                # that takes longer than the runner's 1 s wait) left the INITIAL all-None
                # record on disk and the run scored UNKNOWN. Precisely when the simulator is
                # stalled, which is the case this witness exists to catch. (review)
                #
                # Two writes per flight, not per poll: the file only changes here.
                if airborne and not was_airborne:
                    baseline = int((rpc.call("simGetCollisionInfo", a.vehicle)
                                    ).get("collision_count") or 0)
                    was_airborne = True
                    flush()
                elif was_airborne and not airborne and final_count is None:
                    c = rpc.call("simGetCollisionInfo", a.vehicle)
                    final_count = int(c.get("collision_count") or 0)
                    final_object = c.get("object_name", "")
                    flush()
                time.sleep(period)
                continue
            except Exception:
                errors += 1
                time.sleep(period)
                continue
    except KeyboardInterrupt:
        pass

    # STOPPED BEFORE COMING BACK DOWN? Take the closing sample now.               (SIM-27)
    #
    # The runner stops this witness with pkill -INT after the flight has ended, so by here the
    # aircraft is down and one more read cannot steal a touchdown from anything. Without this,
    # a run that landed but was stopped before the descent crossed the gate would report
    # UNKNOWN forever -- and unknown blocks a gate, correctly but needlessly.
    if baseline is not None and final_count is None:
        try:
            c = rpc.call("simGetCollisionInfo", a.vehicle)
            final_count = int(c.get("collision_count") or 0)
            final_object = c.get("object_name", "")
        except Exception:
            errors += 1

    flush()
    contacts = (None if (baseline is None or final_count is None)
                else max(0, final_count - baseline))
    print(f"airborne_contacts={contacts} baseline={baseline} final={final_count} "
          f"polls={polls} rpc_errors={errors}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
