#!/usr/bin/env python3
"""Combine the chase and onboard cameras into one video, with telemetry.          (SIM-16)

Runs inside drone-sim/ros2 with the run directory mounted.

Layout: the chase camera is the base frame and the onboard view is inset picture-in-picture,
which is the conventional pairing -- the chase shot carries the motion and the inset shows what
the aircraft can actually see. A side-by-side would force both to the same height and waste the
chase camera's resolution, which is the thing being asked for at 1080p.

PLAYBACK RATE IS DERIVED FROM THE BAG, NOT ASSUMED. Imagery is recorded at whatever rate the
simulator sustains -- and that rate falls when the resolution rises, so a hardcoded fps plays
the flight back at the wrong speed. An earlier render at a fixed 20 fps turned a 252 s flight
into an 89 s video (~3x real time) without saying so. `--fps auto` uses the median interval
between frames, so one second of video is one second of flight.

The two streams are matched by timestamp rather than by index: they run at different rates, and
zipping them would drift further apart the longer the flight goes on.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import rclpy.serialization
import rosbag2_py
from cv_bridge import CvBridge
from px4_msgs.msg import VehicleLocalPosition
from sensor_msgs.msg import Image

BAND = 92


def read(path, topic, msgtype):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=path, storage_id="mcap"),
           rosbag2_py.ConverterOptions("", ""))
    r.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    while r.has_next():
        _, raw, t = r.read_next()
        yield rclpy.serialization.deserialize_message(raw, msgtype), t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chase-topic", default="/airsim_node/PX4/chase_Scene/image")
    ap.add_argument("--onboard-topic", default="/airsim_node/PX4/front_center_Scene/image")
    ap.add_argument("--pose-topic", default="/fmu/out/vehicle_local_position")
    ap.add_argument("--summary", default="")
    ap.add_argument("--fps", default="auto")
    ap.add_argument("--pip-frac", type=float, default=0.28, help="inset width as a fraction")
    a = ap.parse_args()

    bridge = CvBridge()

    # --- onboard frames, kept in memory as JPEG-free BGR at inset size later
    onboard, on_t = [], []
    for m, t in read(a.bag, a.onboard_topic, Image):
        onboard.append(bridge.imgmsg_to_cv2(m, desired_encoding="bgr8"))
        on_t.append(t)
    print(f"  onboard frames: {len(onboard)}")

    times, poses = [], []
    for m, t in read(a.bag, a.pose_topic, VehicleLocalPosition):
        times.append(t)
        poses.append((m.x, m.y, m.z, m.vx, m.vy, m.vz))
    print(f"  pose samples: {len(poses)}")

    verdict = ""
    if a.summary and Path(a.summary).exists():
        d = json.load(open(a.summary))
        if d.get("mode") == "circle":
            verdict = (f"{'PASS' if d.get('ok') else 'FAIL'}   orbit r={d['params']['radius']:g} m "
                       f"@ {d['params'].get('speed_flown', 0):g} m/s   "
                       f"radius err max {d.get('radius_error_max_m')} m")
        elif "worst_error_m" in d:
            verdict = f"{'PASS' if d.get('ok') else 'FAIL'}   worst {d['worst_error_m']} m"

    chase_t = [t for _, t in read(a.bag, a.chase_topic, Image)]
    if not chase_t:
        print(f"FATAL: no messages on {a.chase_topic}", file=sys.stderr)
        return 1
    if a.fps == "auto":
        gaps = np.diff(chase_t) / 1e9
        med = float(np.median(gaps)) if len(gaps) else 0.05
        fps = max(1.0, min(60.0, 1.0 / med))
        print(f"  derived fps: {fps:.2f} (median frame gap {med * 1000:.0f} ms) -> real time")
    else:
        fps = float(a.fps)

    writer, n, size = None, 0, None
    for m, t in read(a.bag, a.chase_topic, Image):
        base = bridge.imgmsg_to_cv2(m, desired_encoding="bgr8")
        h, w = base.shape[:2]
        canvas = np.zeros((h + BAND, w, 3), np.uint8)
        canvas[:h] = base

        # onboard inset, matched by TIMESTAMP
        if onboard:
            j = min(max(bisect.bisect_left(on_t, t), 0), len(onboard) - 1)
            iw = int(w * a.pip_frac)
            ins = cv2.resize(onboard[j], (iw, int(iw * onboard[j].shape[0] / onboard[j].shape[1])),
                             interpolation=cv2.INTER_AREA)
            ih = ins.shape[0]
            x0, y0 = w - iw - 24, 24
            cv2.rectangle(canvas, (x0 - 3, y0 - 3), (x0 + iw + 3, y0 + ih + 3), (235, 235, 235), 2)
            canvas[y0:y0 + ih, x0:x0 + iw] = ins
            cv2.putText(canvas, "ONBOARD", (x0 + 6, y0 + ih + 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (235, 235, 235), 1, cv2.LINE_AA)
        cv2.putText(canvas, "CHASE", (24, 40), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (235, 235, 235), 2, cv2.LINE_AA)

        i = min(max(bisect.bisect_left(times, t), 0), len(poses) - 1) if poses else 0
        if poses:
            x, y, z, vx, vy, vz = poses[i]
            rows = [f"x {x:+7.1f}   y {y:+7.1f}   alt {-z:6.2f} m AGL"
                    f"      ground speed {math.hypot(vx, vy):5.2f} m/s"]
        else:
            rows = ["no telemetry"]
        if verdict:
            rows.append(verdict)
        for k, s in enumerate(rows):
            cv2.putText(canvas, s, (24, h + 34 + k * 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.68 if k == 0 else 0.58,
                        (215, 240, 215) if k == 0 else (140, 220, 255), 1, cv2.LINE_AA)
        cv2.putText(canvas, "SITL - commanded over ROS 2", (w - 470, h + BAND - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1, cv2.LINE_AA)

        if writer is None:
            size = (canvas.shape[1], canvas.shape[0])
            writer = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
            if not writer.isOpened():
                print("FATAL: VideoWriter would not open", file=sys.stderr)
                return 1
        writer.write(canvas)
        n += 1

    writer.release()
    print(f"  wrote {a.out}: {n} frames, {size[0]}x{size[1]}, {n / fps:.1f}s at {fps:.2f} fps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
