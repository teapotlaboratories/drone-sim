#!/usr/bin/env python3
"""Hammer the GPU-LiDAR readback path for a soak. Read-only; commands nothing.  (SIM-23)

WHY THIS ARM EXISTS
-------------------
The 2026-08-03 soak (arm C, `soak_full_stack.sh`) survived 74,253 RPC calls and was read as
evidence against the segfault hypothesis. It was aimed at the **image** path -- `simGetImages`
with `compress=true`. The crash actually lives on the **GPU-LiDAR** path
(`ALidarCamera::ProcessCapturedBuffers`), which that soak never drove directly. So it could not
have reproduced the fault it was built to reproduce.

This is the missing arm: pull `getGPULidarData` continuously so `ProcessCapturedBuffers` is
running as hot as the sensor allows, while the image load and MAVLink run alongside it.

WHAT IT IS LOOKING FOR
----------------------
Not a crash -- patch 0006 is expected to prevent that. It is looking for the **condition**: a
readback that comes back empty. With 0006 the simulator logs

    GPU-LiDAR readback incomplete (depth 0 px, need 262144), dropping frame

and keeps flying. Observing that line naturally, rather than under fault injection, is the one
piece of evidence the fix does not yet have.

A short point count is worth recording too: `point_cloud` arrives as 5 floats per point, and a
partial cloud means a scan was dropped somewhere upstream of the API.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airsim_rpc_client import Rpc


def main() -> int:
    ap = argparse.ArgumentParser(description="Soak the GPU-LiDAR path (SITL only).")
    ap.add_argument("--progress", required=True, help="JSON-lines file, flushed every sample")
    ap.add_argument("--sensor", default="gpulidar")
    ap.add_argument("--vehicle", default="PX4")
    ap.add_argument("--max-seconds", type=float, default=5400.0)
    ap.add_argument("--stats-every", type=int, default=50)
    a = ap.parse_args()

    rpc = Rpc()
    t0 = time.time()
    n = errs = 0
    short = 0            # clouds smaller than the largest seen -- a dropped scan, not an error
    best = 0
    last_stamp = None
    stale = 0            # same time_stamp twice: the sensor did not produce a new cloud

    with open(a.progress, "w", buffering=1) as fh:
        while time.time() - t0 < a.max_seconds:
            try:
                d = rpc.call("getGPULidarData", a.sensor, a.vehicle)
                pts = len(d.get("point_cloud") or []) // 5
                stamp = d.get("time_stamp")
                if stamp is not None and stamp == last_stamp:
                    stale += 1
                last_stamp = stamp
                best = max(best, pts)
                if pts < best:
                    short += 1
                n += 1
            except Exception as exc:
                errs += 1
                # An RPC timeout here is itself interesting: the pre-0006 symptom set included
                # `rpc::timeout ... getGPULidarData`, which is the same path stalling.
                if errs <= 5:
                    fh.write(json.dumps({"t": round(time.time() - t0, 1),
                                         "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                time.sleep(0.2)
                continue

            if n % a.stats_every == 0:
                fh.write(json.dumps({"t": round(time.time() - t0, 1), "n": n, "errs": errs,
                                     "points": pts, "best": best, "short": short,
                                     "stale": stale}) + "\n")
        fh.write(json.dumps({"t": round(time.time() - t0, 1), "n": n, "errs": errs,
                             "best": best, "short": short, "stale": stale,
                             "final": True}) + "\n")

    print(f"gpulidar soak: {n} calls, {errs} errors, best={best} pts, "
          f"{short} short clouds, {stale} stale stamps", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
