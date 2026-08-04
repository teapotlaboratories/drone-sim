#!/usr/bin/env python3
"""Render an mp4 from a recorded run's MCAP, with telemetry burned in.             (SIM-16)

Runs inside drone-sim/ros2 with the run directory mounted. Reads the bag rather than the live
simulator, which matters for three reasons: the video is derived from the SAME evidence the
verdict came from, it can be re-rendered later without re-flying, and it cannot drift from what
was actually recorded.

COLOUR, AND A BUG IN THE OLDER RECORDER
--------------------------------------
`scripts/record_flight.py:36` reshapes AirSim's raw buffer and hands it straight to
`cv2.VideoWriter`, which expects BGR -- but Cosys-AirSim returns **RGB**. The first flight
videos recorded that way have red and blue swapped. This reads through `cv_bridge` with an
explicit `desired_encoding`, so the `Image.encoding` field decides rather than an assumption.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import rclpy.serialization
import rosbag2_py
from cv_bridge import CvBridge
from px4_msgs.msg import VehicleLocalPosition
from sensor_msgs.msg import Image

BAND = 96          # telemetry band height, px


def read_bag(path: str, topics: list[str]):
    """Yield (topic, raw_bytes, t_ns) for `topics`, in log order."""
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=path, storage_id="mcap"),
                rosbag2_py.ConverterOptions("", ""))
    reader.set_filter(rosbag2_py.StorageFilter(topics=topics))
    while reader.has_next():
        yield reader.read_next()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True, help="directory containing the .mcap")
    ap.add_argument("--out", required=True)
    ap.add_argument("--image-topic", default="/airsim_node/PX4/front_center_Scene/image")
    ap.add_argument("--pose-topic", default="/fmu/out/vehicle_local_position")
    ap.add_argument("--summary", default="", help="summary.json, for the verdict caption")
    # "auto" derives the rate from the bag timestamps so playback is REAL TIME.
    # A hardcoded rate silently rescales the flight: 15 fps turned a 252 s flight
    # into an 89 s video (~3x) with nothing on screen saying so.
    ap.add_argument("--fps", default="auto")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()

    # Pose first, so each frame can be matched to the nearest telemetry sample by timestamp.
    times, poses = [], []
    for topic, raw, t in read_bag(a.bag, [a.pose_topic]):
        m = rclpy.serialization.deserialize_message(raw, VehicleLocalPosition)
        times.append(t)
        poses.append((m.x, m.y, m.z, m.vx, m.vy, m.vz))
    print(f"  pose samples: {len(poses)}")

    verdict = ""
    if a.summary and Path(a.summary).exists():
        d = json.load(open(a.summary))
        if "worst_error_m" in d:
            verdict = (f"{'PASS' if d.get('ok') else 'FAIL'}  "
                       f"worst {d['worst_error_m']} m  mean {d['mean_error_m']} m  "
                       f"{len(d.get('legs', []))} legs")

    if a.fps == "auto":
        img_t = [t for _, _, t in read_bag(a.bag, [a.image_topic])]
        if len(img_t) > 1:
            med = float(np.median(np.diff(img_t)) / 1e9)
            fps = max(1.0, min(60.0, 1.0 / med)) if med > 0 else 15.0
        else:
            fps = 15.0
        print(f"  derived fps: {fps:.2f} -> real time")
    else:
        fps = float(a.fps)

    bridge = CvBridge()
    writer, n, size = None, 0, None
    for topic, raw, t in read_bag(a.bag, [a.image_topic]):
        msg = rclpy.serialization.deserialize_message(raw, Image)
        # Let the encoding field decide; do NOT assume the buffer layout.
        frame = bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        if a.scale != 1.0:
            frame = cv2.resize(frame, None, fx=a.scale, fy=a.scale,
                               interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]

        canvas = np.zeros((h + BAND, w, 3), np.uint8)
        canvas[:h] = frame

        i = bisect.bisect_left(times, t)
        if poses:
            i = min(max(i, 0), len(poses) - 1)
            x, y, z, vx, vy, vz = poses[i]
            spd = float(np.hypot(vx, vy))
            rows = [
                f"x {x:+8.2f}  y {y:+8.2f}  z {z:+7.2f} m (NED)",
                f"alt {-z:6.2f} m AGL     ground speed {spd:5.2f} m/s",
            ]
        else:
            rows = ["no telemetry in bag"]
        if verdict:
            rows.append(verdict)

        for k, text in enumerate(rows):
            cv2.putText(canvas, text, (12, h + 26 + k * 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (210, 235, 210) if k < 2 else (140, 220, 255), 1, cv2.LINE_AA)
        cv2.putText(canvas, "SITL - ROS 2 interface", (w - 330, h + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1, cv2.LINE_AA)

        if writer is None:
            size = (canvas.shape[1], canvas.shape[0])
            writer = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
            if not writer.isOpened():
                print("FATAL: VideoWriter would not open", file=sys.stderr)
                return 1
        writer.write(canvas)
        n += 1

    if writer is None:
        print(f"FATAL: no messages on {a.image_topic} — nothing to render", file=sys.stderr)
        return 1
    writer.release()
    print(f"  wrote {a.out}: {n} frames, {size[0]}x{size[1]}, {n / fps:.1f}s at {fps:.2f} fps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
