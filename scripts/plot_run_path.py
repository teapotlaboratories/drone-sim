#!/usr/bin/env python3
"""Plot the flown ground track from a run's MCAP, against what was commanded.      (C-16)

Runs inside drone-sim/ros2 with the run directory mounted. Draws with cv2 rather than
matplotlib, which is not installed in that image and is not worth adding for four panels.

Reads `/fmu/out/vehicle_local_position` -- the same source the mission judged itself on, so a
disagreement between this picture and the verdict is informative rather than confusing. Points
whose `xy_valid` is false are drawn in red instead of being silently dropped: the whole reason
this plot exists is that outlier samples were poisoning a summary statistic, and hiding them
here would defeat it.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import rclpy.serialization
import rosbag2_py
from px4_msgs.msg import VehicleLocalPosition

BG, FG, GRID = (28, 24, 22), (225, 231, 224), (60, 66, 62)
GOOD, BAD, CMD = (120, 200, 110), (60, 70, 230), (200, 160, 60)


def read(path, topic):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=path, storage_id="mcap"),
           rosbag2_py.ConverterOptions("", ""))
    r.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    while r.has_next():
        _, raw, t = r.read_next()
        yield rclpy.serialization.deserialize_message(raw, VehicleLocalPosition), t


def text(img, s, xy, scale=0.5, col=FG):
    cv2.putText(img, s, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, col, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--size", type=int, default=760)
    a = ap.parse_args()

    xs, ys, zs, ts, ok, spd = [], [], [], [], [], []
    for m, t in read(a.bag, "/fmu/out/vehicle_local_position"):
        xs.append(m.x); ys.append(m.y); zs.append(m.z); ts.append(t)
        ok.append(bool(m.xy_valid))
        spd.append(math.hypot(m.vx, m.vy))
    if not xs:
        print("FATAL: no vehicle_local_position in the bag", file=sys.stderr)
        return 1
    t0 = ts[0]
    rel = [(t - t0) / 1e9 for t in ts]
    print(f"  {len(xs)} samples, {rel[-1]:.1f}s, xy_valid false on {ok.count(False)}")

    cmd_r = cmd_alt = None
    cmd_cx = cmd_cy = 0.0
    if a.summary and Path(a.summary).exists():
        d = json.load(open(a.summary))
        p = d.get("params", {})
        cmd_r, cmd_alt = p.get("radius"), p.get("altitude")
        # The orbit is centred on the TAKEOFF position, not on the local origin. summary.json
        # already records it; assuming (0,0) is right only while takeoff happens to be there,
        # and would silently mis-draw the reference the moment it is not -- in a plot whose
        # whole job is to make that kind of divergence visible.
        org = d.get("origin") or []
        if len(org) >= 2:
            cmd_cx, cmd_cy = float(org[0]), float(org[1])

    S = a.size
    img = np.full((S + 260, S, 3), BG, np.uint8)

    # --- ground track, equal aspect so a circle looks like a circle
    pad = 60
    cx_d, cy_d = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 1.15
    def to_px(x, y):
        return (int((y - cy_d) / span * (S - 2 * pad) + S / 2),
                int(-(x - cx_d) / span * (S - 2 * pad) + S / 2))   # north up, east right

    for g in range(-100, 101, 10):
        p1, p2 = to_px(g, -200), to_px(g, 200)
        cv2.line(img, p1, p2, GRID, 1)
        p1, p2 = to_px(-200, g), to_px(200, g)
        cv2.line(img, p1, p2, GRID, 1)

    if cmd_r:
        c = to_px(cmd_cx, cmd_cy)
        rpx = int(cmd_r / span * (S - 2 * pad))
        cv2.circle(img, c, rpx, CMD, 1, cv2.LINE_AA)
        text(img, f"commanded r={cmd_r:g} m", (c[0] - 60, c[1] - rpx - 10), 0.45, CMD)

    prev = None
    for i in range(len(xs)):
        p = to_px(xs[i], ys[i])
        if prev is not None and ok[i] and ok[i - 1]:
            cv2.line(img, prev, p, GOOD, 1, cv2.LINE_AA)
        if not ok[i]:
            cv2.circle(img, p, 3, BAD, -1)
        prev = p
    cv2.circle(img, to_px(xs[0], ys[0]), 5, (255, 255, 255), -1)
    text(img, "start", (to_px(xs[0], ys[0])[0] + 8, to_px(xs[0], ys[0])[1]), 0.45)

    text(img, "GROUND TRACK  (north up, east right)", (16, 26), 0.6)
    text(img, f"green = flown ({ok.count(True)} valid)   red = xy_valid false "
              f"({ok.count(False)})   amber = commanded", (16, 46), 0.42, (170, 180, 170))

    # --- altitude and speed traces
    def trace(y0, h, vals, label, lo=None, hi=None, ref=None):
        cv2.rectangle(img, (pad, y0), (S - pad, y0 + h), GRID, 1)
        lo = min(vals) if lo is None else lo
        hi = max(vals) if hi is None else hi
        rng = max(hi - lo, 1e-6)
        pts = [(int(pad + i / max(len(vals) - 1, 1) * (S - 2 * pad)),
                int(y0 + h - (v - lo) / rng * h)) for i, v in enumerate(vals)]
        for i in range(1, len(pts)):
            cv2.line(img, pts[i - 1], pts[i], GOOD, 1, cv2.LINE_AA)
        if ref is not None and lo <= ref <= hi:
            yr = int(y0 + h - (ref - lo) / rng * h)
            cv2.line(img, (pad, yr), (S - pad, yr), CMD, 1)
        text(img, label, (pad, y0 - 6), 0.45)
        text(img, f"{hi:.1f}", (S - pad + 4, y0 + 10), 0.38, (150, 160, 150))
        text(img, f"{lo:.1f}", (S - pad + 4, y0 + h), 0.38, (150, 160, 150))

    alt = [-z for z in zs]
    trace(S + 40, 80, alt, "ALTITUDE AGL (m)", ref=cmd_alt)
    trace(S + 160, 80, spd, "GROUND SPEED (m/s)")
    text(img, f"{rel[-1]:.0f} s", (S - pad - 40, S + 254), 0.42, (150, 160, 150))

    cv2.imwrite(a.out, img)
    print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
