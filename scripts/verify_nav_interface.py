#!/usr/bin/env python3
"""Confirm the navigation command interface, end to end over ROS 2.                   (SIM-15)

SITL ONLY. This arms and flies a SIMULATED vehicle. It must never be pointed at real hardware.

WHY THIS EXISTS
---------------
Autonomy is about to be built on top of a command interface that has only ever been exercised in
one shape: `TrajectorySetpoint.position` on a seeded square. The failure mode being guarded
against is not a crash — it is a planner emitting setpoints PX4 quietly ignores while the flight
looks "fine". So every check here asserts the VEHICLE MOVED, never that a publisher exists.

THE ARCHITECTURAL FINDING THIS ENCODES
--------------------------------------
There is no global (lat/lon) SETPOINT message. `GotoSetpoint` is local NED and
`VehicleGlobalPosition` is an estimate output, not a command. So a GPS waypoint cannot be
streamed the way a local one is:

    local waypoint / velocity  ->  stream TrajectorySetpoint at >2 Hz, mode OFFBOARD
    GPS waypoint               ->  one-shot VehicleCommand DO_REPOSITION (192), PX4's own nav mode

Those are different control paths with different failsafes. A planner cannot mix them casually,
and this script deliberately returns to a known mode between checks so one check cannot inherit
another's state and fail for the wrong reason.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from px4_msgs.msg import (
    OffboardControlMode,
    TrajectorySetpoint,
    VehicleCommand,
    VehicleGlobalPosition,
    VehicleLocalPosition,
    VehicleStatus,
)

# /fmu/out/* is BEST_EFFORT + TRANSIENT_LOCAL; a RELIABLE subscriber sees silence on a healthy
# stack. This cost a day in P1-02 and is not negotiable.
SUB_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    history=HistoryPolicy.KEEP_LAST,
    depth=5,
)
# PX4's /fmu/in subscribers are BEST_EFFORT + VOLATILE. A RELIABLE + TRANSIENT_LOCAL publisher
# matches NOTHING and every command is silently dropped against a healthy stack.
PUB_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.VOLATILE,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class NavCheck(Node):
    def __init__(self, ns: str = ""):
        super().__init__("verify_nav_interface")
        self.local: VehicleLocalPosition | None = None
        self.glob: VehicleGlobalPosition | None = None
        self.status: VehicleStatus | None = None

        self.create_subscription(VehicleLocalPosition, f"{ns}/fmu/out/vehicle_local_position",
                                 self._on_local, SUB_QOS)
        self.create_subscription(VehicleGlobalPosition, f"{ns}/fmu/out/vehicle_global_position",
                                 self._on_global, SUB_QOS)
        # vehicle_status_v1, NOT vehicle_status: PX4 v1.16 renamed it, and subscribing to the
        # old name matches nothing while looking entirely correct.
        self.create_subscription(VehicleStatus, f"{ns}/fmu/out/vehicle_status_v1",
                                 self._on_status, SUB_QOS)

        self.pub_mode = self.create_publisher(
            OffboardControlMode, f"{ns}/fmu/in/offboard_control_mode", PUB_QOS)
        self.pub_sp = self.create_publisher(
            TrajectorySetpoint, f"{ns}/fmu/in/trajectory_setpoint", PUB_QOS)
        self.pub_cmd = self.create_publisher(
            VehicleCommand, f"{ns}/fmu/in/vehicle_command", PUB_QOS)

    # -- callbacks ----------------------------------------------------------------------
    def _on_local(self, m): self.local = m
    def _on_global(self, m): self.glob = m
    def _on_status(self, m): self.status = m

    # -- plumbing -----------------------------------------------------------------------
    def spin(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.02)

    def wait_for(self, pred, timeout: float, tick=None) -> bool:
        """Spin until pred() or timeout. `tick` runs each loop — the setpoint stream lives there,
        because PX4 drops out of OFFBOARD if the stream stops for ~0.5 s."""
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.02)
            if tick:
                tick()
            if pred():
                return True
        return False

    def now_us(self) -> int:
        return int(self.get_clock().now().nanoseconds / 1000)

    def send_mode(self, position=False, velocity=False):
        m = OffboardControlMode()
        m.timestamp = self.now_us()
        m.position, m.velocity = position, velocity
        m.acceleration = m.attitude = m.body_rate = False
        self.pub_mode.publish(m)

    def send_position(self, x, y, z, yaw=float("nan")):
        s = TrajectorySetpoint()
        s.timestamp = self.now_us()
        s.position = [float(x), float(y), float(z)]
        s.velocity = [float("nan")] * 3
        s.acceleration = [float("nan")] * 3
        s.yaw = float(yaw)
        self.pub_sp.publish(s)

    def send_velocity(self, vx, vy, vz, yaw=float("nan")):
        """position MUST be NaN — a finite position with velocity set makes PX4 prefer position
        and the velocity command silently does nothing."""
        s = TrajectorySetpoint()
        s.timestamp = self.now_us()
        s.position = [float("nan")] * 3
        s.velocity = [float(vx), float(vy), float(vz)]
        s.acceleration = [float("nan")] * 3
        s.yaw = float(yaw)
        self.pub_sp.publish(s)

    def send_command(self, command, **params):
        c = VehicleCommand()
        c.timestamp = self.now_us()
        c.command = command
        for i in range(1, 8):
            setattr(c, f"param{i}", float(params.get(f"p{i}", float("nan"))))
        c.target_system = 1
        c.target_component = 1
        c.source_system = 1
        c.source_component = 1
        c.from_external = True
        self.pub_cmd.publish(c)

    # -- state helpers ------------------------------------------------------------------
    def armed(self) -> bool:
        return self.status is not None and self.status.arming_state == VehicleStatus.ARMING_STATE_ARMED

    def pos(self):
        m = self.local
        return None if m is None else (m.x, m.y, m.z)

    def vel(self):
        m = self.local
        return None if m is None else (m.vx, m.vy, m.vz)


def hdr(t):
    print(f"\n{t}\n" + "-" * len(t))


def report(ok, name, detail):
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {tag}  {name}\n        {DIM}{detail}{RESET}")
    return ok


# ---------------------------------------------------------------------------------------
# checks


def check_telemetry(n: NavCheck) -> bool:
    ok = n.wait_for(lambda: n.local is not None and n.status is not None, 30.0)
    if not ok:
        return report(False, "ROS 2 telemetry", "no /fmu/out/vehicle_local_position or vehicle_status")
    p = n.pos()
    return report(True, "ROS 2 telemetry",
                  f"local ({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) · xy_valid={n.local.xy_valid} "
                  f"z_valid={n.local.z_valid}")


def arm_and_takeoff(n: NavCheck, alt: float, timeout: float) -> bool:
    """Stream setpoints BEFORE requesting OFFBOARD: PX4 rejects the mode change unless a
    setpoint stream is already present."""
    z0 = n.pos()[2]
    target_z = z0 - alt

    for _ in range(30):
        n.send_mode(position=True)
        n.send_position(0.0, 0.0, target_z)
        n.spin(0.05)

    n.send_command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, p1=1, p2=6)   # 6 = OFFBOARD
    n.send_command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, p1=1)

    def tick():
        n.send_mode(position=True)
        n.send_position(0.0, 0.0, target_z)

    reached = n.wait_for(lambda: n.pos() is not None and abs(n.pos()[2] - target_z) < 1.0,
                         timeout, tick)
    p = n.pos()
    return report(reached and n.armed(), "arm + takeoff (offboard, local setpoint)",
                  f"armed={n.armed()} · z {z0:.2f} -> {p[2]:.2f} (target {target_z:.2f})")


def check_local_waypoint(n: NavCheck, dx: float, dy: float, tol: float, timeout: float) -> bool:
    p0 = n.pos()
    tx, ty, tz = p0[0] + dx, p0[1] + dy, p0[2]

    def tick():
        n.send_mode(position=True)
        n.send_position(tx, ty, tz)

    def near():
        p = n.pos()
        return math.dist((p[0], p[1]), (tx, ty)) < tol

    ok = n.wait_for(near, timeout, tick)
    p = n.pos()
    err = math.dist((p[0], p[1]), (tx, ty))
    return report(ok, "waypoint command (TrajectorySetpoint.position)",
                  f"commanded ({tx:.1f}, {ty:.1f}) · reached ({p[0]:.2f}, {p[1]:.2f}) · "
                  f"error {err:.2f} m (tol {tol})")


def check_velocity(n: NavCheck, vx: float, hold: float, tol: float) -> bool:
    """Velocity alone is not proof: a stationary vehicle reporting noise could satisfy it. The
    position must also integrate in the commanded direction."""
    p0 = n.pos()
    samples = []
    end = time.time() + hold

    while time.time() < end:
        n.send_mode(velocity=True)
        n.send_velocity(vx, 0.0, 0.0)
        rclpy.spin_once(n, timeout_sec=0.02)
        v = n.vel()
        if v:
            samples.append(v[0])

    p1 = n.pos()
    travelled = p1[0] - p0[0]
    # Last third only — the first two thirds contain the acceleration ramp.
    settled = samples[(2 * len(samples)) // 3:] if samples else []
    mean_vx = sum(settled) / len(settled) if settled else float("nan")
    ok = (abs(mean_vx - vx) < tol) and (travelled * vx > 0) and abs(travelled) > 0.5

    # Stop, or the next check starts from a moving vehicle.
    for _ in range(40):
        n.send_mode(velocity=True)
        n.send_velocity(0.0, 0.0, 0.0)
        n.spin(0.05)

    return report(ok, "velocity command (TrajectorySetpoint.velocity, position=NaN)",
                  f"commanded {vx:+.1f} m/s · measured {mean_vx:+.2f} m/s over {len(settled)} "
                  f"samples · travelled {travelled:+.2f} m in {hold:.0f} s")


def check_gps_waypoint(n: NavCheck, north_m: float, tol_m: float, timeout: float) -> bool:
    """A GPS waypoint is NOT a streamed setpoint. It is a one-shot VehicleCommand and PX4 flies
    it in its own nav mode, so OFFBOARD streaming must stop or the two paths fight."""
    if n.glob is None and not n.wait_for(lambda: n.glob is not None, 15.0):
        return report(False, "GPS waypoint (VehicleCommand DO_REPOSITION)",
                      "no /fmu/out/vehicle_global_position")

    lat0, lon0, alt0 = n.glob.lat, n.glob.lon, n.glob.alt
    dlat = north_m / 111_320.0
    tgt_lat, tgt_lon = lat0 + dlat, lon0

    n.send_command(VehicleCommand.VEHICLE_CMD_DO_REPOSITION,
                   p1=-1.0, p2=1.0, p3=0.0, p4=float("nan"),
                   p5=tgt_lat, p6=tgt_lon, p7=alt0)

    def dist_m():
        dn = (n.glob.lat - tgt_lat) * 111_320.0
        de = (n.glob.lon - tgt_lon) * 111_320.0 * math.cos(math.radians(n.glob.lat))
        return math.hypot(dn, de)

    ok = n.wait_for(lambda: dist_m() < tol_m, timeout)
    moved = (n.glob.lat - lat0) * 111_320.0
    return report(ok, "GPS waypoint (VehicleCommand DO_REPOSITION)",
                  f"commanded {north_m:+.0f} m north (lat {lat0:.6f} -> {tgt_lat:.6f}) · "
                  f"moved {moved:+.1f} m · remaining {dist_m():.2f} m (tol {tol_m})")


def land_and_disarm(n: NavCheck):
    n.send_command(VehicleCommand.VEHICLE_CMD_NAV_LAND)
    n.wait_for(lambda: not n.armed(), 60.0)
    n.send_command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, p1=0)
    n.spin(1.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", default="")
    ap.add_argument("--altitude", type=float, default=5.0)
    ap.add_argument("--waypoint-dx", type=float, default=10.0)
    ap.add_argument("--waypoint-tol", type=float, default=1.0)
    ap.add_argument("--velocity", type=float, default=2.0)
    # 12 s, not 5: the vehicle accelerates from rest under PX4's jerk/accel limits, and a
    # 5 s hold samples the ramp rather than the steady state. Measured: 5 s reads 1.47 m/s
    # against a commanded 2.0 (looks like a FAILURE); 14 s reads exactly 2.00.
    ap.add_argument("--velocity-hold", type=float, default=12.0)
    ap.add_argument("--velocity-tol", type=float, default=0.5)
    ap.add_argument("--gps-north", type=float, default=30.0)
    ap.add_argument("--gps-tol", type=float, default=3.0)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--skip-gps", action="store_true")
    a = ap.parse_args()

    print(f"\n  {YELLOW}navigation interface — SITL ONLY, nothing real is armed{RESET}")

    rclpy.init()
    n = NavCheck(a.namespace)
    results = {}
    try:
        hdr("0 · telemetry over ROS 2")
        results["telemetry"] = check_telemetry(n)
        if not results["telemetry"]:
            return 1

        hdr("1 · arm and take off")
        results["takeoff"] = arm_and_takeoff(n, a.altitude, a.timeout)
        if not results["takeoff"]:
            return 1

        hdr("2 · waypoint command")
        results["waypoint"] = check_local_waypoint(
            n, a.waypoint_dx, 0.0, a.waypoint_tol, a.timeout)

        hdr("3 · velocity command")
        results["velocity"] = check_velocity(n, a.velocity, a.velocity_hold, a.velocity_tol)

        if not a.skip_gps:
            hdr("4 · GPS waypoint command")
            results["gps_waypoint"] = check_gps_waypoint(n, a.gps_north, a.gps_tol, a.timeout)
    finally:
        try:
            land_and_disarm(n)
        except Exception:
            pass
        n.destroy_node()
        rclpy.shutdown()

    hdr("summary")
    for k, v in results.items():
        print(f"  {GREEN + 'PASS' + RESET if v else RED + 'FAIL' + RESET}  {k}")
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"\n  {RED}{len(failed)} check(s) failed: {', '.join(failed)}{RESET}\n")
        return 1
    print(f"\n  {GREEN}all navigation interface checks passed{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
