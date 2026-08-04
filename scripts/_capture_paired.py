#!/usr/bin/env python3
"""Hold one pose and capture it BOTH ways: AirSim's simGetImages and Unreal's HighResShot.

Both captures happen inside a single pose-holding loop on purpose. Issuing them as two separate
client calls would let the vehicle drift between them under gravity, and a comparison of two
slightly different viewpoints is exactly the error this whole exercise exists to correct.

Runs inside drone-sim/airsim-client, joined to the simulator's network namespace.
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
    ap.add_argument("--shot-res", default="640x480")
    ap.add_argument("--hold-after-shot", type=float, default=6.0,
                    help="keep holding while HighResShot renders asynchronously")
    args = ap.parse_args()

    client = airsim.MultirotorClient()
    client.confirmConnection()

    pose = airsim.Pose(
        airsim.Vector3r(args.x, args.y, args.z),
        airsim.euler_to_quaternion(0.0, 0.0, float(np.deg2rad(args.yaw))),
    )
    period = 1.0 / args.reassert_hz

    # NEVER simPause() here: pausing stops the scene capture re-rendering and simGetImages
    # returns a stale frame. Holding by re-assertion keeps the view steady AND live.
    deadline = time.time() + args.settle
    while time.time() < deadline:
        client.simSetVehiclePose(pose, True, args.vehicle)
        time.sleep(period)

    client.simSetVehiclePose(pose, True, args.vehicle)
    responses = client.simGetImages(
        [airsim.ImageRequest(args.camera, airsim.ImageType.Scene, False, False)], args.vehicle
    )
    r = responses[0]
    if r.width == 0 or r.height == 0:
        raise RuntimeError(f"empty frame from camera {args.camera!r}")
    buf = np.frombuffer(r.image_data_uint8, dtype=np.uint8)
    if buf.size != r.height * r.width * 3:
        raise RuntimeError(f"frame is {buf.size} bytes, expected {r.height * r.width * 3}")
    img = buf.reshape(r.height, r.width, 3)  # RGB -- verified in _check_channel_order.py

    # Fire Unreal's own render of the SAME view, then keep holding: HighResShot is async and
    # the vehicle must not move while it renders.
    client.simRunConsoleCommand(f"HighResShot {args.shot_res}")
    deadline = time.time() + args.hold_after_shot
    while time.time() < deadline:
        client.simSetVehiclePose(pose, True, args.vehicle)
        time.sleep(period)

    import cv2

    cv2.imwrite(args.out, img[:, :, ::-1])  # cv2 wants BGR

    lum = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]).astype(np.float64)
    stats = {
        "mean": round(float(lum.mean()), 3),
        "std": round(float(lum.std()), 3),
        "p01": round(float(np.percentile(lum, 1)), 3),
        "p99": round(float(np.percentile(lum, 99)), 3),
        "r_mean": round(float(img[:, :, 0].mean()), 3),
        "g_mean": round(float(img[:, :, 1].mean()), 3),
        "b_mean": round(float(img[:, :, 2].mean()), 3),
    }
    stats["range_p01_p99"] = round(stats["p99"] - stats["p01"], 3)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
