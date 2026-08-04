#!/usr/bin/env python3
"""Grab one Scene frame from a running AirSim, from an exactly specified pose.

Runs INSIDE drone-sim/airsim-client, joined to the simulator's network namespace. Kept
separate from the orchestrator so the pose/capture discipline lives in one auditable place.

Two hard-won rules are encoded here, both of which silently corrupted earlier measurements:

1. NEVER simPause() before simGetImages(). Pausing stops the scene-capture component
   re-rendering, so the RPC returns the LAST frame it happened to have. This was proven the
   expensive way: captures at 300 m, 120 m and 9 m returned byte-identical statistics.
2. simSetVehiclePose() does not HOLD the vehicle -- gravity drags it under between the set
   and the grab. So the pose is re-asserted in a tight loop right up to the capture, which
   gives a steady view without freezing the renderer.
"""
import argparse
import json
import sys
import time

import cosysairsim as airsim
import numpy as np


def grab(client, camera, vehicle, pose, settle_s, reassert_hz):
    """Re-assert `pose` continuously for settle_s, then capture without ever pausing."""
    deadline = time.time() + settle_s
    period = 1.0 / reassert_hz
    while time.time() < deadline:
        client.simSetVehiclePose(pose, True, vehicle)
        time.sleep(period)

    # One final assert immediately before the grab, then capture on the very next tick.
    client.simSetVehiclePose(pose, True, vehicle)
    responses = client.simGetImages(
        [airsim.ImageRequest(camera, airsim.ImageType.Scene, False, False)], vehicle
    )
    if not responses:
        raise RuntimeError("simGetImages returned nothing")
    r = responses[0]
    if r.width == 0 or r.height == 0:
        raise RuntimeError("simGetImages returned an empty frame (camera name wrong?)")

    buf = np.frombuffer(r.image_data_uint8, dtype=np.uint8)
    expected = r.height * r.width * 3
    if buf.size != expected:
        raise RuntimeError(f"frame is {buf.size} bytes, expected {expected} (h*w*3)")
    # Cosys-AirSim returns the uncompressed buffer as RGB, NOT BGR. Verified against AirSim's
    # own PNG encoder on an identical frame (scripts/_check_channel_order.py): interpreting it
    # as BGR differs from the encoder by 19.59 mean absolute, and swapping matches it EXACTLY
    # (0.00). Reading it as BGR swaps red and blue -- which does not look like a bug, it looks
    # like a plausible cyan colour cast, and was misread as one for a full day.
    return buf.reshape(r.height, r.width, 3)  # RGB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera", default="front_center")
    ap.add_argument("--vehicle", default="PX4")
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--z", type=float, required=True)
    ap.add_argument("--yaw", type=float, default=0.0, help="degrees")
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--reassert-hz", type=float, default=20.0)
    args = ap.parse_args()

    client = airsim.MultirotorClient()
    client.confirmConnection()

    # Cosys-AirSim 3.4.1 has no `to_quaternion`; it exports euler_to_quaternion, and the
    # argument order is (roll, pitch, yaw) in RADIANS -- not the (pitch, roll, yaw) of
    # classic AirSim. Getting this wrong swaps two axes silently rather than raising.
    pose = airsim.Pose(
        airsim.Vector3r(args.x, args.y, args.z),
        airsim.euler_to_quaternion(0.0, 0.0, float(np.deg2rad(args.yaw))),
    )
    img = grab(client, args.camera, args.vehicle, pose, args.settle, args.reassert_hz)

    import cv2

    cv2.imwrite(args.out, img[:, :, ::-1])  # cv2 writes BGR; img is RGB

    # Stats on the LUMINANCE, not the raw byte soup: the washout is a tone problem, and
    # per-channel means hide it when one channel carries a colour cast.
    lum = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]).astype(np.float64)
    stats = {
        "mean": round(float(lum.mean()), 3),
        "std": round(float(lum.std()), 3),
        "p01": round(float(np.percentile(lum, 1)), 3),
        "p99": round(float(np.percentile(lum, 99)), 3),
        "frac_below_16": round(float((lum < 16).mean()), 5),
        "frac_above_240": round(float((lum > 240).mean()), 5),
        "r_mean": round(float(img[:, :, 0].mean()), 3),
        "g_mean": round(float(img[:, :, 1].mean()), 3),
        "b_mean": round(float(img[:, :, 2].mean()), 3),
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
    }
    # Dynamic range actually used: a washed-out image is not just bright, it is COMPRESSED.
    stats["range_p01_p99"] = round(stats["p99"] - stats["p01"], 3)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
