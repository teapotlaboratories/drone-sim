#!/usr/bin/env python3
"""Record a flight to mp4 while it happens, independently of the flight node.

SITL only. Read-only: it pulls frames over the AirSim RPC and writes a file. It commands
nothing and it is never in the control path.

WHY RPC AND NOT ROS 2
---------------------
The obvious way to record would be to subscribe to the camera topics. That does not work for a
gate run: `airsim_node`, the wrapper that publishes imagery, is NOT started by `sim_up.sh` --
it is built and launched separately -- so during a gate run the camera topics do not exist at
all. Adding them to `record_topics` would have recorded nothing, silently.

`simGetImages` needs none of that. It talks to the simulator directly, so it works on exactly
the stack a gate run brings up.

A SEPARATE PROCESS, for the same reason the collision witness is one: the thing under test
should not be the thing reporting on it, and an observer that dies should not take the flight
with it.

WHAT IT COSTS
-------------
Measured on a 4-waypoint mission: 887 frames of 2 cameras at 10 fps, 3840x1558 -> 38 MB raw
mp4v, 5.3 MB after h264. Per seed. A 10-seed gate is therefore ~380 MB of raw video, which is
fine on the 7 TB volume `out/` points at and is NOT fine on the internal NVMe.

It also shares the GPU with the renderer it is photographing. `--hz` is deliberately low.
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airsim_rpc_client import Rpc

CAMS = ["front_center", "bottom_center"]
SCALE = 3          # native capture is small; upscale so burned-in text is legible
BAND = 46          # telemetry strip height


def frame_of(resp) -> np.ndarray:
    buf = resp.get("image_data_uint8") or b""
    h, w = resp["height"], resp["width"]
    if not buf or h == 0 or w == 0:
        return np.zeros((max(h, 1), max(w, 1), 3), np.uint8)
    return np.frombuffer(buf, np.uint8).reshape(h, w, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description="Record a SITL flight to mp4 over the AirSim RPC.")
    ap.add_argument("--out", default="/tmp/flight.mp4")
    ap.add_argument("--vehicle", default="PX4")
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--max-seconds", type=float, default=900.0)
    a = ap.parse_args()

    rpc = Rpc()
    # Probe first: the frame size decides the writer's geometry, and a writer opened at the
    # wrong size silently produces a file no player will read.
    try:
        probe = rpc.images(CAMS, vehicle=a.vehicle)
    except Exception as exc:
        print(f"video: cannot reach the simulator RPC ({exc}) -- not recording", flush=True)
        return 1
    h, w = probe[0]["height"], probe[0]["width"]
    if h == 0 or w == 0:
        print("video: simulator returned an empty frame -- not recording", flush=True)
        return 1

    tw, th = w * SCALE, h * SCALE
    W, H = tw * len(CAMS), th + BAND
    vw = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), a.hz, (W, H))
    if not vw.isOpened():
        print("video: VideoWriter would not open -- not recording", flush=True)
        return 1

    t0, n, errs = time.time(), 0, 0
    period = 1.0 / max(a.hz, 1.0)
    try:
        while time.time() - t0 < a.max_seconds:
            loop = time.time()
            try:
                resp = rpc.images(CAMS, vehicle=a.vehicle)
                tiles = [cv2.resize(frame_of(r), (tw, th), interpolation=cv2.INTER_NEAREST)
                         for r in resp]
                gt = rpc.call("simGetGroundTruthKinematics", a.vehicle)["position"]["z_val"]
            except Exception:
                errs += 1
                time.sleep(period)
                continue

            canvas = np.zeros((H, W, 3), np.uint8)
            canvas[:th] = np.hstack(tiles)
            canvas[th:] = (22, 20, 18)
            for i, c in enumerate(CAMS):
                cv2.putText(canvas, c, (i * tw + 8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (235, 235, 235), 1, cv2.LINE_AA)
            # Ground truth, not the EKF's belief: this is the recording you reach for when the
            # question is "where was it REALLY", and the estimator is what you are doubting.
            cv2.putText(canvas, f"SITL  t+{time.time()-t0:6.1f}s   AirSim truth z = {gt:+7.2f} m",
                        (10, th + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (140, 255, 140), 1,
                        cv2.LINE_AA)
            vw.write(canvas)
            n += 1
            time.sleep(max(0.0, period - (time.time() - loop)))
    except KeyboardInterrupt:
        pass

    vw.release()
    print(f"video: wrote {a.out}  frames={n}  {W}x{H} @ {a.hz:g}fps  rpc_errors={errs}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
