#!/usr/bin/env python3
"""Hammer simGetImages until something breaks, and record exactly where.          (SIM-04 soak)

Runs INSIDE drone-sim/airsim-client, joined to the simulator's network namespace.

WHAT THIS IS TESTING
--------------------
A simulator segfault was seen once after ~57 minutes:

    Array index out of bounds: 18823 into an array of size 0

The candidate mechanism is in the capture path. `setupRenderResource()` writes
`result->width`/`height` BEFORE `ReadSurfaceData` fills `bmp`, and the consumer guard checks
only the dimensions:

    if (results[i]->width != 0 && results[i]->height != 0)          <- never bmp.Num()
        CompressImageArray(width, height, results[i]->bmp, ...)     <- indexes by width*height

Upstream itself comments, four lines above that read, that it "seems to segfault every 2000 or
so calls". That is a COUNT, not a duration — so if the hypothesis holds, driving captures hard
should reproduce in minutes rather than an hour.

The discriminator is `compress`: only `compress=True` reaches `CompressImageArray`. The ROS 2
wrapper uses `compress=False` and takes an iterate-not-index branch, which is safe on an empty
buffer. So compress=True crashing while compress=False survives confirms the mechanism; both
crashing refutes it and sends the search elsewhere.

SECOND DEFECT THIS ALSO WATCHES FOR
-----------------------------------
The "safe" branches do `SetNumUninitialized(w*h*3)` and then fill only `bmp.Num()` entries. On
an empty buffer that publishes a FULLY UNINITIALISED image -- garbage pixels, no error. That is
silent corruption on the path the graph actually uses, and nothing currently detects it. Every
frame's statistics are recorded so an outlier is visible after the fact.

PROGRESS IS FLUSHED EVERY CALL. When the simulator dies it takes this container's network
namespace with it, so the process cannot report its own death -- the file on disk is the only
record of how far it got.
"""
import argparse
import json
import sys
import time

import cosysairsim as airsim
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", required=True, help="JSON-lines file, flushed every call")
    ap.add_argument("--compress", choices=("true", "false"), required=True)
    ap.add_argument("--camera", default="front_center")
    ap.add_argument("--vehicle", default="Drone")
    ap.add_argument("--max-calls", type=int, default=200000)
    ap.add_argument("--max-seconds", type=float, default=5400.0)
    ap.add_argument("--stats-every", type=int, default=1)
    args = ap.parse_args()

    compress = args.compress == "true"
    client = airsim.MultirotorClient()
    client.confirmConnection()

    req = [airsim.ImageRequest(args.camera, airsim.ImageType.Scene, False, compress)]
    prog = open(args.progress, "w", buffering=1)          # line buffered
    t0 = time.time()
    n = 0
    anomalies = 0
    running_mean = None

    def emit(rec):
        prog.write(json.dumps(rec) + "\n")
        prog.flush()

    emit({"event": "start", "compress": compress, "t": 0.0})

    try:
        while n < args.max_calls and (time.time() - t0) < args.max_seconds:
            n += 1
            r = client.simGetImages(req, args.vehicle)[0]
            buf = np.frombuffer(r.image_data_uint8, dtype=np.uint8)

            rec = {"n": n, "t": round(time.time() - t0, 2),
                   "w": int(r.width), "h": int(r.height), "bytes": int(buf.size)}

            # Empty or short buffer is the silent-corruption case: dimensions say one thing and
            # the payload says another.
            expected = r.height * r.width * (0 if compress else 3)
            if buf.size == 0:
                rec["anomaly"] = "empty_buffer"
                anomalies += 1
            elif not compress and buf.size != expected:
                rec["anomaly"] = f"short_buffer expected {expected}"
                anomalies += 1
            else:
                m = float(buf.mean())
                s = float(buf.std())
                rec["mean"], rec["std"] = round(m, 2), round(s, 2)
                # A fully uninitialised buffer reads as near-uniform random: mean ~127, std ~74.
                # Flag a large jump from the running mean rather than an absolute threshold, so
                # this works whatever the scene happens to look like.
                if running_mean is None:
                    running_mean = m
                elif abs(m - running_mean) > 40.0:
                    rec["anomaly"] = f"mean jumped {running_mean:.1f} -> {m:.1f}"
                    anomalies += 1
                else:
                    running_mean = 0.98 * running_mean + 0.02 * m

            if n % args.stats_every == 0 or "anomaly" in rec:
                emit(rec)
    except Exception as e:
        emit({"event": "client_exception", "n": n, "t": round(time.time() - t0, 2),
              "error": f"{type(e).__name__}: {e}"})
        prog.close()
        return 2

    emit({"event": "completed", "n": n, "t": round(time.time() - t0, 2), "anomalies": anomalies})
    prog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
