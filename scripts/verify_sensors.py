#!/usr/bin/env python3
"""Verify the drone actually provides what navigation needs, over the ROS 2 link.

SITL only. Nothing real is armed or flown.

WHY THIS IS NOT `ros2 topic list`
---------------------------------
Every failure this project has hit here looked healthy from the outside:

  * `/fmu/out/*` topics listed while publishing nothing, because the subscriber QoS did not
    match (P1-02) and because /dev/shm was not shared (D-02).
  * The IMU published at 1501 Hz and looked pristine on `ros2 topic hz` -- while 78% of the
    messages were the same sample republished.
  * `camera_info` published a frame_id no TF-aware node could resolve.
  * A stale EKF origin reported 35 m of altitude with `z_valid: true`, rock steady, on a
    vehicle sitting on the ground.

So this checks VALUES, and prefers a check that can fail. An all-black camera and a working
camera both "publish an image"; only one has pixel variance. A dead IMU and a live one both
produce Vector3; only one reads ~9.81 m/s^2 at rest.

Exit 0 only if every REQUIRED check passes. Advisory checks report but do not fail the run.
"""
from __future__ import annotations

import math
import sys
import time
from collections import defaultdict

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

from sensor_msgs.msg import (Image, CameraInfo, PointCloud2, NavSatFix, Imu,
                             MagneticField)
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from tf2_msgs.msg import TFMessage

V = "PX4"
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


# Anything at or beyond this reads as "no return" rather than a measured distance. AirSim's
# depth cameras report a huge finite value for sky/void rather than inf or NaN -- observed
# 16312.0 m in the Blocks environment.
DEPTH_NO_RETURN_M = 1000.0
# At least this fraction of finite samples must be a BOUNDED distance for the frame to have
# seen geometry. Deliberately low: a camera legitimately pointed mostly at sky should still
# pass, but an all-sentinel frame must not.
DEPTH_MIN_BOUNDED_FRAC = 0.01


def depth_is_usable(values: list[float]) -> tuple[bool, str]:
    """Pure decision: does this depth sample contain real geometry?

    WHY THIS IS NOT `max(v) > 0.5`. That was the first version, and it CANNOT FAIL the way
    it claims to: a frame where every pixel is the 16312 m no-return sentinel satisfies both
    "some positive value" and "max above half a metre", so a broken depth capture returning
    all-sentinel -- or a camera staring at empty sky -- read as healthy. That is precisely
    the "topics exist so it must work" error this whole script exists to avoid, reproduced
    one level down.

    A usable depth frame needs BOUNDED returns, not merely positive ones.
    """
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return False, f"0 finite values of {len(values)} sampled - unusable"
    bounded = [v for v in finite if 0.0 < v < DEPTH_NO_RETURN_M]
    frac = len(bounded) / len(finite)
    if not bounded:
        # Distinguish the two ways this happens, because they mean different things: a
        # sentinel-filled frame is a camera seeing nothing, whereas a zero/negative-filled
        # frame is a broken capture. Reporting "all >= 1000 m" for a frame of zeros would be
        # a false statement in the very message meant to explain the failure.
        sentinel = sum(1 for v in finite if v >= DEPTH_NO_RETURN_M)
        nonpos = sum(1 for v in finite if v <= 0.0)
        return False, (f"{len(finite)} finite samples, none usable: {sentinel} at/above the "
                       f"{DEPTH_NO_RETURN_M:g} m no-return sentinel, {nonpos} non-positive "
                       f"- the frame contains no measurable geometry")
    if frac < DEPTH_MIN_BOUNDED_FRAC:
        return False, (f"only {len(bounded)}/{len(finite)} ({100*frac:.2f}%) samples are "
                       f"bounded distances - below the {100*DEPTH_MIN_BOUNDED_FRAC:g}% floor")
    return True, (f"{len(bounded)}/{len(finite)} ({100*frac:.0f}%) bounded, "
                  f"range {min(bounded):.2f}..{max(bounded):.2f} m "
                  f"(>= {DEPTH_NO_RETURN_M:g} m treated as no-return)")


def best_effort(depth=50):
    q = QoSProfile(depth=depth)
    q.reliability = ReliabilityPolicy.BEST_EFFORT
    q.durability = DurabilityPolicy.VOLATILE
    return q


def tf_qos():
    q = QoSProfile(depth=50)
    q.reliability = ReliabilityPolicy.RELIABLE
    q.durability = DurabilityPolicy.TRANSIENT_LOCAL   # /tf_static is latched
    return q


class Collector(Node):
    def __init__(self):
        super().__init__("sensor_verify")
        self.msgs = defaultdict(list)
        be = best_effort()
        sub = self.create_subscription

        sub(Image,        f"/airsim_node/{V}/front_center_Scene/image",             self._k("rgb"), be)
        sub(CameraInfo,   f"/airsim_node/{V}/front_center_Scene/camera_info",       self._k("rgb_info"), be)
        sub(Image,        f"/airsim_node/{V}/front_center_DepthPlanar/image",       self._k("depth"), be)
        sub(CameraInfo,   f"/airsim_node/{V}/front_center_DepthPlanar/camera_info", self._k("depth_info"), be)
        sub(PointCloud2,  f"/airsim_node/{V}/gpulidar/points/gpulidar",             self._k("lidar"), be)
        sub(NavSatFix,    f"/airsim_node/{V}/gps/gps",                              self._k("gps"), be)
        sub(Imu,          f"/airsim_node/{V}/imu/imu",                              self._k("imu"), be)
        sub(MagneticField, f"/airsim_node/{V}/magnetometer/magnetometer",           self._k("mag"), be)
        sub(Odometry,     f"/airsim_node/{V}/odom_local",                           self._k("odom"), be)
        sub(Clock,        "/clock",                                                 self._k("clock"), be)
        sub(TFMessage,    "/tf_static",                                             self._k("tf_static"), tf_qos())

    def _k(self, key):
        def cb(m):
            self.msgs[key].append((time.time(), m))
        return cb


results = []


def check(name, required, ok, detail):
    results.append((name, required, bool(ok), detail))


def rate(samples):
    if len(samples) < 2:
        return 0.0
    span = samples[-1][0] - samples[0][0]
    return (len(samples) - 1) / span if span > 0 else 0.0


def main():
    rclpy.init()
    n = Collector()
    t0 = time.time()
    while time.time() - t0 < SECS:
        rclpy.spin_once(n, timeout_sec=0.05)
    m = n.msgs

    # ---- RGB ------------------------------------------------------------------------
    rgb = m["rgb"]
    if rgb:
        img = rgb[-1][1]
        px = bytes(img.data)
        distinct = len(set(px[:6000]))
        check("RGB image", True,
              img.width > 0 and img.height > 0 and len(px) > 0,
              f"{img.width}x{img.height} {img.encoding}, {rate(rgb):.1f} Hz")
        # A camera pointed at a wall and a camera returning a blank buffer both have data.
        check("RGB is not a blank frame", True, distinct > 8,
              f"{distinct} distinct byte values in the first 6 kB "
              f"({'looks like real imagery' if distinct > 8 else 'SUSPECT: near-uniform'})")
    else:
        check("RGB image", True, False, "no messages")

    # ---- Depth ----------------------------------------------------------------------
    dep = m["depth"]
    if dep:
        d = dep[-1][1]
        import struct
        n_px = d.width * d.height
        vals = []
        if d.encoding in ("32FC1",) and len(d.data) >= n_px * 4:
            raw = bytes(d.data)
            step = max(1, n_px // 2000)
            for i in range(0, n_px, step):
                vals.append(struct.unpack_from("<f", raw, i * 4)[0])
        check("Depth image", True, d.width > 0 and len(d.data) > 0,
              f"{d.width}x{d.height} {d.encoding}, {rate(dep):.1f} Hz")
        ok_d, why_d = depth_is_usable(vals)
        check("Depth contains measurable geometry", True, ok_d, why_d)
    else:
        check("Depth image", True, False, "no messages")

    # ---- LiDAR ----------------------------------------------------------------------
    lid = m["lidar"]
    if lid:
        pc = lid[-1][1]
        npts = pc.width * pc.height
        import struct
        raw = bytes(pc.data)
        nonzero = 0
        checked = 0
        for i in range(0, min(npts, 1500)):
            off = i * pc.point_step
            if off + 12 <= len(raw):
                x, y, z = struct.unpack_from("<fff", raw, off)
                checked += 1
                if (x * x + y * y + z * z) > 1e-6:
                    nonzero += 1
        check("LiDAR point cloud", True, npts > 0,
              f"{npts} points, point_step {pc.point_step}, {rate(lid):.1f} Hz")
        check("LiDAR returns are not all origin", True, nonzero > checked * 0.2,
              f"{nonzero}/{checked} sampled points non-zero")
    else:
        check("LiDAR point cloud", True, False, "no messages")

    # ---- GPS ------------------------------------------------------------------------
    gps = m["gps"]
    if gps:
        g = gps[-1][1]
        plausible = (-90 <= g.latitude <= 90 and -180 <= g.longitude <= 180
                     and g.latitude != 0.0 and g.longitude != 0.0)
        check("GPS fix", True, plausible,
              f"lat {g.latitude:.6f} lon {g.longitude:.6f} alt {g.altitude:.2f} m, "
              f"status {g.status.status}, {rate(gps):.1f} Hz")
    else:
        check("GPS fix", True, False, "no messages")

    # ---- IMU ------------------------------------------------------------------------
    imu = m["imu"]
    if imu:
        i = imu[-1][1]
        a = i.linear_acceleration
        mag = math.sqrt(a.x**2 + a.y**2 + a.z**2)
        stamps = [msg.header.stamp.sec * 10**9 + msg.header.stamp.nanosec for _, msg in imu]
        distinct = len(set(stamps))
        dup_pct = 100.0 * (len(stamps) - distinct) / len(stamps)
        check("IMU", True, len(imu) > 0, f"{rate(imu):.0f} Hz published")
        # At rest the accelerometer must read ~1 g. This is what distinguishes a live IMU
        # from a zero-filled message that still deserialises fine.
        check("IMU reads ~1 g at rest", True, 8.5 < mag < 11.0,
              f"|accel| = {mag:.3f} m/s^2 (expect ~9.81 at rest)")
        check("IMU duplicate rate", False, dup_pct < 20.0,
              f"{dup_pct:.1f}% duplicate timestamps, {distinct} distinct of {len(stamps)} "
              f"-> ~{distinct/SECS:.0f} Hz of real data. KNOWN: polled snapshot, see SIM-04 trap 3")
    else:
        check("IMU", True, False, "no messages")

    # ---- Magnetometer ---------------------------------------------------------------
    mg = m["mag"]
    if mg:
        f = mg[-1][1].magnetic_field
        fmag = math.sqrt(f.x**2 + f.y**2 + f.z**2)
        check("Magnetometer", True, fmag > 1e-9,
              f"|B| = {fmag:.4f}, {rate(mg):.1f} Hz")
    else:
        check("Magnetometer", True, False, "no messages")

    # ---- Odometry -------------------------------------------------------------------
    od = m["odom"]
    if od:
        o = od[-1][1]
        p = o.pose.pose.position
        finite = all(math.isfinite(v) for v in (p.x, p.y, p.z))
        check("Odometry", True, finite,
              f"({p.x:+.2f}, {p.y:+.2f}, {p.z:+.2f}) frame '{o.header.frame_id}'"
              f" child '{o.child_frame_id}', {rate(od):.0f} Hz")
    else:
        check("Odometry", True, False, "no messages")

    # ---- camera_info / TF consistency ------------------------------------------------
    tf = m["tf_static"]
    frames = set()
    for _, msg in tf:
        for tr in msg.transforms:
            frames.add(tr.child_frame_id)
    ci = m["rgb_info"]
    if ci and frames:
        cif = ci[-1][1].header.frame_id
        check("camera_info frame_id resolves in TF", True, cif in frames,
              f"camera_info '{cif}' {'found in' if cif in frames else 'NOT IN'} tf_static "
              f"({len(frames)} frames)")
    else:
        check("camera_info frame_id resolves in TF", True, False,
              f"camera_info msgs={len(ci)} tf_static frames={len(frames)}")

    # ---- clock ----------------------------------------------------------------------
    ck = m["clock"]
    if len(ck) >= 2:
        first = ck[0][1].clock.sec + ck[0][1].clock.nanosec / 1e9
        last = ck[-1][1].clock.sec + ck[-1][1].clock.nanosec / 1e9
        check("/clock advancing", True, last > first,
              f"advanced {last - first:.2f} s over {SECS:.0f} s wall, {rate(ck):.0f} Hz")
    else:
        check("/clock advancing", True, False,
              f"{len(ck)} messages - use_sim_time consumers would freeze at zero")

    n.destroy_node()
    rclpy.shutdown()

    # ---- report ---------------------------------------------------------------------
    print(f"\n  sensor verification - {SECS:.0f} s sample, SITL only\n")
    req_fail = 0
    for name, required, ok, detail in results:
        if ok:
            mark, colour = "PASS", GREEN
        elif required:
            mark, colour = "FAIL", RED
            req_fail += 1
        else:
            mark, colour = "WARN", YELLOW
        tag = "" if required else f" {DIM}(advisory){RESET}"
        print(f"  {colour}{mark}{RESET}  {name}{tag}\n        {DIM}{detail}{RESET}")
    print()
    if req_fail:
        print(f"  {RED}{req_fail} required check(s) failed{RESET}\n")
        return 1
    print(f"  {GREEN}all required checks passed{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
