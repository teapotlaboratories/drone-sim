#!/usr/bin/env python3
"""Capture one scene with optional anti-aliasing interventions, and measure what they cost.

Every lever here is available on STOCK Cosys-AirSim -- no plugin patch:

  --console CMD     run an r.* cvar through simRunConsoleCommand before capturing. Note this
                    changes the WHOLE renderer, so the native HighResShot reference moves too;
                    that is why the native frame is re-taken per variant rather than reused.
  --downsample N    the capture was requested at N x the target size in settings.json; box-filter
                    it down here. Supersampling is single-frame anti-aliasing, so unlike TSR it
                    does not need the temporal history the capture never accumulates.

Throughput is measured in the same run because the fix and its cost have to be judged together:
The graph already claims 31 Hz RGB, and an intervention that renders every camera every frame can
take that away. The rate measured is the achievable simGetImages rate at this resolution, not
the simulator's internal frame rate.
"""
import argparse
import json
import sys
import time

import cosysairsim as airsim
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera", default="fpv")
    ap.add_argument("--vehicle", default="Drone")
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--z", type=float, required=True)
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--reassert-hz", type=float, default=20.0)
    ap.add_argument("--console", action="append", default=[])
    ap.add_argument("--downsample", type=int, default=1)
    ap.add_argument("--fps-samples", type=int, default=20)
    ap.add_argument("--shot-res", default="1920x1080")
    ap.add_argument("--hold-after-shot", type=float, default=6.0)
    args = ap.parse_args()

    client = airsim.MultirotorClient()
    client.confirmConnection()

    for cmd in args.console:
        client.simRunConsoleCommand(cmd)
    if args.console:
        time.sleep(2.0)  # let the renderer settle after a cvar change

    pose = airsim.Pose(
        airsim.Vector3r(args.x, args.y, args.z),
        airsim.euler_to_quaternion(0.0, 0.0, float(np.deg2rad(args.yaw))),
    )
    period = 1.0 / args.reassert_hz
    req = [airsim.ImageRequest(args.camera, airsim.ImageType.Scene, False, False)]

    # Hold, never pause. With ForceUpdate the capture renders every frame, so this settle window
    # is also what lets temporal accumulation converge -- the thing being tested.
    deadline = time.time() + args.settle
    while time.time() < deadline:
        client.simSetVehiclePose(pose, True, args.vehicle)
        time.sleep(period)

    client.simSetVehiclePose(pose, True, args.vehicle)
    r = client.simGetImages(req, args.vehicle)[0]
    if r.width == 0:
        raise RuntimeError(f"empty frame from camera {args.camera!r}")
    buf = np.frombuffer(r.image_data_uint8, dtype=np.uint8)
    if buf.size != r.height * r.width * 3:
        raise RuntimeError(f"frame is {buf.size} bytes, expected {r.height * r.width * 3}")
    img = buf.reshape(r.height, r.width, 3)  # RGB

    client.simRunConsoleCommand(f"HighResShot {args.shot_res}")
    deadline = time.time() + args.hold_after_shot
    while time.time() < deadline:
        client.simSetVehiclePose(pose, True, args.vehicle)
        time.sleep(period)

    # Throughput: back-to-back grabs with the pose held, so the cost measured is the capture's
    # and not the vehicle's.
    t0 = time.time()
    for _ in range(args.fps_samples):
        client.simGetImages(req, args.vehicle)
    elapsed = time.time() - t0
    fps = args.fps_samples / elapsed if elapsed > 0 else float("nan")

    import cv2

    out = img
    if args.downsample > 1:
        n = args.downsample
        h, w = out.shape[0] // n * n, out.shape[1] // n * n
        out = out[:h, :w].reshape(h // n, n, w // n, n, 3).mean(axis=(1, 3)).astype(np.uint8)
    cv2.imwrite(args.out, out[:, :, ::-1])

    lum = (0.299 * out[:, :, 0] + 0.587 * out[:, :, 1] + 0.114 * out[:, :, 2]).astype(np.float64)
    p01, p99 = np.percentile(lum, 1), np.percentile(lum, 99)
    print(json.dumps({
        "mean": round(float(lum.mean()), 3),
        "std": round(float(lum.std()), 3),
        "range_p01_p99": round(float(p99 - p01), 3),
        "captured_w": int(r.width), "captured_h": int(r.height),
        "out_w": int(out.shape[1]), "out_h": int(out.shape[0]),
        "capture_fps": round(float(fps), 2),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
