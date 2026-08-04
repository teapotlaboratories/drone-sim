#!/usr/bin/env python3
"""Fly a closed circuit of the world, using ONLY the ROS 2 interface.              (C-16)

SITL ONLY. This arms and flies a SIMULATED vehicle. Never point it at real hardware.

This is an EXAMPLE, and is meant to be read start to finish. It flies a fixed geometric
circuit -- takeoff, N legs, return, land -- with no obstacle avoidance and no perception in the
loop. Its job is to be the reference for *how you drive this vehicle from ROS 2*, so it is
deliberately plain: one node, one state machine, no planner.

    ros2 run control park_tour --ros-args -p legs:=4 -p radius:=25.0 -p altitude:=8.0

WHAT IT DEMONSTRATES, AND THE TRAPS IT ENCODES
----------------------------------------------
Everything below was measured on 2026-08-03 by `scripts/verify_nav_interface.py`; each is a
thing that fails SILENTLY if you get it wrong.

1. **QoS must match or nothing moves.** PX4's `/fmu/in` subscribers are BEST_EFFORT + VOLATILE.
   A RELIABLE publisher matches nothing and every command is dropped against a healthy stack.
   `/fmu/out` is BEST_EFFORT + TRANSIENT_LOCAL, and a default RELIABLE subscription sees silence.

2. **It is `vehicle_status_v1`, not `vehicle_status`.** PX4 v1.16 renamed it; the old name
   matches nothing while looking entirely correct.

3. **Setpoints must stream before OFFBOARD is requested, and keep streaming.** PX4 rejects the
   mode change unless a stream already exists, and drops out of OFFBOARD if it stops for ~0.5 s.

4. **Z is NED — negative is UP.** An altitude of 8 m is `z = -8`.

5. **Position and velocity are mutually exclusive per setpoint.** For velocity control the
   position fields must be NaN, or PX4 prefers position and the velocity is silently ignored.

Yaw is commanded to face along each leg, which is what a camera-carrying vehicle wants: the
imagery in the recording then sweeps the world instead of strafing sideways through it.
"""
from __future__ import annotations

import json
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
    VehicleLocalPosition,
    VehicleStatus,
)

SUB_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     history=HistoryPolicy.KEEP_LAST, depth=5)
PUB_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                     durability=DurabilityPolicy.VOLATILE,
                     history=HistoryPolicy.KEEP_LAST, depth=10)


class ParkTour(Node):
    def __init__(self):
        super().__init__("park_tour")
        self.declare_parameter("legs", 4)
        self.declare_parameter("radius", 25.0)
        self.declare_parameter("altitude", 8.0)
        self.declare_parameter("tolerance", 2.0)
        self.declare_parameter("arrive_speed", 0.7)
        self.declare_parameter("mode", "waypoints")   # "waypoints" | "circle"
        self.declare_parameter("speed", 4.0)          # circle: tangential speed, m/s
        self.declare_parameter("laps", 1.0)           # circle: how many times round
        self.declare_parameter("yaw_mode", "inward")  # circle: inward | tangent
        self.declare_parameter("max_accel", 2.0)      # m/s^2 cap; clamps speed if needed
        self.declare_parameter("ramp_s", 6.0)         # smoothstep ramp in/out
        self.declare_parameter("leg_timeout", 90.0)
        self.declare_parameter("settle_s", 2.0)
        self.declare_parameter("summary", "")
        self.declare_parameter("namespace", "")

        self.legs = int(self.get_parameter("legs").value)
        self.radius = float(self.get_parameter("radius").value)
        self.alt = float(self.get_parameter("altitude").value)
        self.tol = float(self.get_parameter("tolerance").value)
        self.arrive_speed = float(self.get_parameter("arrive_speed").value)
        self.mode = str(self.get_parameter("mode").value)
        self.speed = float(self.get_parameter("speed").value)
        self.laps = float(self.get_parameter("laps").value)
        self.yaw_mode = str(self.get_parameter("yaw_mode").value)
        self.max_accel = float(self.get_parameter("max_accel").value)
        self.ramp_s = float(self.get_parameter("ramp_s").value)
        self.leg_timeout = float(self.get_parameter("leg_timeout").value)
        self.settle_s = float(self.get_parameter("settle_s").value)
        self.summary_path = str(self.get_parameter("summary").value)
        ns = str(self.get_parameter("namespace").value)

        self.local: VehicleLocalPosition | None = None
        self.status: VehicleStatus | None = None
        self.create_subscription(VehicleLocalPosition, f"{ns}/fmu/out/vehicle_local_position",
                                 self._on_local, SUB_QOS)
        self.create_subscription(VehicleStatus, f"{ns}/fmu/out/vehicle_status_v1",
                                 self._on_status, SUB_QOS)
        self.pub_mode = self.create_publisher(
            OffboardControlMode, f"{ns}/fmu/in/offboard_control_mode", PUB_QOS)
        self.pub_sp = self.create_publisher(
            TrajectorySetpoint, f"{ns}/fmu/in/trajectory_setpoint", PUB_QOS)
        self.pub_cmd = self.create_publisher(
            VehicleCommand, f"{ns}/fmu/in/vehicle_command", PUB_QOS)

    # -- io -----------------------------------------------------------------------------
    def _on_local(self, m): self.local = m
    def _on_status(self, m): self.status = m

    def now_us(self) -> int:
        return int(self.get_clock().now().nanoseconds / 1000)

    def pos(self):
        m = self.local
        return None if m is None else (m.x, m.y, m.z)

    def speed_xy(self) -> float:
        m = self.local
        return float("inf") if m is None else math.hypot(m.vx, m.vy)

    def armed(self) -> bool:
        return (self.status is not None
                and self.status.arming_state == VehicleStatus.ARMING_STATE_ARMED)

    def stream(self, x, y, z, yaw, vel=None, acc=None):
        """One tick of the OFFBOARD stream. Both messages, every tick — see trap 3.

        `vel` is an optional FEED-FORWARD velocity. Position and velocity together is not the
        pure-velocity case from trap 5: with both finite and both flags set, PX4 treats velocity
        as feed-forward and tracks a moving setpoint smoothly instead of repeatedly chasing a
        stationary one. Trap 5's "position must be NaN" applies only to velocity-ONLY control.
        """
        m = OffboardControlMode()
        m.timestamp = self.now_us()
        m.position = True
        m.velocity = vel is not None
        m.acceleration = acc is not None
        m.attitude = m.body_rate = False
        self.pub_mode.publish(m)

        s = TrajectorySetpoint()
        s.timestamp = self.now_us()
        s.position = [float(x), float(y), float(z)]
        s.velocity = [float(v) for v in vel] if vel is not None else [float("nan")] * 3
        s.acceleration = [float(v) for v in acc] if acc is not None else [float("nan")] * 3
        s.yaw = float(yaw)
        self.pub_sp.publish(s)

    def command(self, cmd, **p):
        c = VehicleCommand()
        c.timestamp = self.now_us()
        c.command = cmd
        for i in range(1, 8):
            setattr(c, f"param{i}", float(p.get(f"p{i}", float("nan"))))
        c.target_system = c.target_component = 1
        c.source_system = c.source_component = 1
        c.from_external = True
        self.pub_cmd.publish(c)

    def spin_until(self, pred, timeout, tick=None):
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.02)
            if tick:
                tick()
            if pred():
                return True
        return False

    # -- mission ------------------------------------------------------------------------
    def waypoints(self):
        """A closed polygon around the start point, at `radius`, `legs` corners.

        Yaw faces the NEXT corner, so the camera looks where the vehicle is going rather than
        strafing sideways through the scene -- which is what makes the recording watchable.
        """
        x0, y0, _ = self.origin
        pts = []
        for i in range(self.legs):
            a = 2.0 * math.pi * i / self.legs
            pts.append((x0 + self.radius * math.cos(a), y0 + self.radius * math.sin(a)))
        out = []
        for i, (x, y) in enumerate(pts):
            nx, ny = pts[(i + 1) % len(pts)]
            out.append((x, y, -self.alt, math.atan2(ny - y, nx - x)))
        out.append((x0, y0, -self.alt, out[0][3]))          # close the circuit
        return out

    def run_circle(self) -> dict:
        """Orbit the start point at constant tangential speed, streaming a MOVING setpoint.

        The waypoint mission stops at every corner, which is right for a waypoint test and looks
        terrible: accelerate, brake, rotate, repeat. Here the setpoint itself travels around the
        circle at `speed`, with the tangential velocity supplied as feed-forward, so the vehicle
        flies a continuous arc.

        Angular rate is ramped in and out over `RAMP` seconds. Starting at full rate would demand
        a step change in velocity that the vehicle cannot meet, and it lurches at lap start —
        which is the same visual defect this mode exists to remove.
        """
        log = self.get_logger()
        cx, cy, _ = self.origin
        z = self.origin[2] - self.alt
        dt = 0.05

        # Cap speed from the acceleration budget. Circular motion costs a centripetal
        # a = v^2/r CONTINUOUSLY, so a tight circle at speed is an acceleration demand the
        # vehicle cannot meet, and the visible result is a wobble rather than a clean arc.
        v_cap = math.sqrt(max(self.max_accel, 0.01) * self.radius)
        speed = min(self.speed, v_cap)
        if speed < self.speed:
            log.warn(f"speed {self.speed:.1f} m/s needs "
                     f"{self.speed ** 2 / self.radius:.2f} m/s^2 at r={self.radius:.0f} m; "
                     f"capped to {speed:.2f} m/s for max_accel={self.max_accel:.1f}")
        omega = speed / self.radius
        total = 2.0 * math.pi * self.laps

        # Enter the circle at its start point before rotating, or the first setpoint is a jump
        # of `radius` metres and the vehicle sprints to catch it.
        sx, sy = cx + self.radius, cy
        def hold_entry():
            self.stream(sx, sy, z, math.pi if self.yaw_mode == "inward" else math.pi / 2)
        if not self.spin_until(
                lambda: self.pos() and math.dist((self.pos()[0], self.pos()[1]), (sx, sy)) < 2.0
                        and self.speed_xy() < 1.0, 90.0, hold_entry):
            return {"ok": False, "error": "could not reach the circle entry point"}
        log.info(f"at entry ({sx:.1f}, {sy:.1f}); orbiting r={self.radius} m "
                 f"at {self.speed} m/s, {self.laps} lap(s)")

        def smoothstep(u: float) -> float:
            """3u^2-2u^3: zero SLOPE at both ends, so angular acceleration starts and finishes at
            zero. A linear ramp is continuous in rate but steps in acceleration, and that step is
            exactly the lurch at lap start this replaces."""
            u = min(max(u, 0.0), 1.0)
            return u * u * (3.0 - 2.0 * u)

        # Start theta at the vehicle's CURRENT bearing from the centre rather than at 0. The
        # entry hold puts it near (cx+r, cy) but not exactly, and beginning at a nominal angle
        # asks it to jump to a setpoint it is not at -- which showed up as every radius outlier
        # living in the first 4.5 s while two full laps afterwards stayed inside 3 m.
        p0 = self.pos()
        theta0 = math.atan2(p0[1] - cy, p0[0] - cx) if p0 else 0.0
        theta, elapsed, samples = 0.0, 0.0, []
        rejected, last_pos = 0, None
        ramp_ang = omega * self.ramp_s * 0.5      # angle consumed by one smoothstep ramp
        # A floor on the rate, and a wall-clock cap. The ramp-out scales w by the angle
        # REMAINING, so w -> 0 as theta -> total and the loop converges without ever arriving:
        # a first version sat 6e-5 rad short with w at 6e-9 rad/s, hovering indefinitely at the
        # end of its orbit. Any rate profile that vanishes at the target needs an explicit
        # termination guarantee -- the smooth shape is worth keeping, the Zeno tail is not.
        W_FLOOR = 0.03
        deadline = time.monotonic() + (total / omega) * 3.0 + 4.0 * self.ramp_s + 30.0
        timed_out = False
        t_next = time.monotonic()
        while theta < total:
            if time.monotonic() > deadline:
                timed_out = True
                break
            # Ramp in on elapsed time, out on angle remaining, so both ends are smooth.
            frac = min(smoothstep(elapsed / self.ramp_s) if self.ramp_s > 0 else 1.0,
                       smoothstep((total - theta) / ramp_ang) if ramp_ang > 0 else 1.0)
            w = omega * max(frac, W_FLOOR)
            theta += w * dt
            elapsed += dt

            ang = theta0 + theta
            x = cx + self.radius * math.cos(ang)
            y = cy + self.radius * math.sin(ang)
            vx = -self.radius * w * math.sin(ang)
            vy = self.radius * w * math.cos(ang)
            # Centripetal acceleration, pointing at the centre. Supplying it as feed-forward is
            # what lets the controller hold the arc instead of continuously discovering it.
            ax = -self.radius * w * w * math.cos(ang)
            ay = -self.radius * w * w * math.sin(ang)
            yaw = (ang + math.pi) if self.yaw_mode == "inward" else (ang + math.pi / 2)
            self.stream(x, y, z, math.atan2(math.sin(yaw), math.cos(yaw)),
                        vel=(vx, vy, 0.0), acc=(ax, ay, 0.0))
            rclpy.spin_once(self, timeout_sec=0.0)

            # Fixed-cadence scheduling. A naive sleep(dt) adds the loop's own work to every
            # period, so setpoints arrive unevenly and the tracker sees jitter that is ours,
            # not the vehicle's.
            t_next += dt
            slack = t_next - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            else:
                t_next = time.monotonic()

            p = self.pos()
            # xy_valid gates the sample. PX4 publishes VehicleLocalPosition continuously, and a
            # transient (or an EKF reset, which SHIFTS the local frame) yields a position that is
            # numerically fine and physically impossible. One such sample reported the vehicle
            # 106 m from a 35 m circle it was tracking to within 1 m, and turned a good flight
            # into a FAIL. Trusting a field without its validity flag is the same class of error
            # as the stale EKF origin that once cost this project a day.
            if p and self.local is not None and not self.local.xy_valid:
                rejected += 1
                p = None
            if p and last_pos is not None:
                # Second guard: reject a jump no vehicle could make between ticks.
                if math.dist((p[0], p[1]), last_pos) > max(self.speed, 1.0) * 10.0 * dt + 3.0:
                    rejected += 1
                    last_pos = (p[0], p[1])   # advance anyway: a stale reference makes the next
                    p = None                  # comparison wider still, and the guard cascades
            if p:
                last_pos = (p[0], p[1])
                r = math.dist((p[0], p[1]), (cx, cy))
                samples.append({"t": round(elapsed, 2), "theta": round(theta, 3),
                                "radius_m": round(r, 3),
                                "radius_error_m": round(r - self.radius, 3),
                                "alt_error_m": round(p[2] - z, 3),
                                "speed_ms": round(self.speed_xy(), 3)})

        # The verdict is about the ORBIT, so the entry transient is reported but not judged:
        # it is the vehicle joining the circle, which the ramp exists to smooth, not tracking.
        settle_t = self.ramp_s
        orbit = [s for s in samples if s["t"] >= settle_t] or samples
        entry = [s for s in samples if s["t"] < settle_t]
        errs = [abs(s["radius_error_m"]) for s in orbit]
        alts = [abs(s["alt_error_m"]) for s in orbit]
        spds = [s["speed_ms"] for s in orbit]
        reset_ct = getattr(self.local, "xy_reset_counter", None) if self.local else None
        return {"ok": (bool(errs) and max(errs) < max(3.0, 0.15 * self.radius)
                       and not timed_out),
                "mode": "circle", "samples": len(samples),
                "rejected_samples": rejected,
                "entry_transient_s": settle_t,
                "entry_error_max_m": round(max((abs(s["radius_error_m"]) for s in entry),
                                               default=0.0), 3),
                "timed_out": timed_out,
                "laps_flown": round(theta / (2 * math.pi), 3),
                "xy_reset_counter": int(reset_ct) if reset_ct is not None else None,
                "radius_error_max_m": round(max(errs), 3) if errs else None,
                "radius_error_mean_m": round(sum(errs) / len(errs), 3) if errs else None,
                "alt_error_max_m": round(max(alts), 3) if alts else None,
                "speed_mean_ms": round(sum(spds) / len(spds), 3) if spds else None,
                "params": {"radius": self.radius, "altitude": self.alt,
                           "speed_requested": self.speed, "speed_flown": round(speed, 3),
                           "max_accel": self.max_accel, "centripetal_accel":
                               round(speed ** 2 / self.radius, 3),
                           "ramp_s": self.ramp_s, "laps": self.laps, "yaw_mode": self.yaw_mode},
                "origin": [round(v, 2) for v in self.origin],
                "track": samples[::10]}

    def validate(self) -> str | None:
        """Reject parameters that cannot describe a flight. Returns an error, or None.

        Checked BEFORE anything arms, because the alternative is an exception partway through
        a mission with the vehicle already in the air. `--speed 0` and `--radius 0` both reach
        `speed / radius` and raise ZeroDivisionError; a negative radius reaches math.sqrt and
        raises a domain error. None of those is a useful message to debug from.
        """
        if self.radius <= 0:
            return f"radius must be > 0, got {self.radius:g}"
        if self.alt <= 0:
            return f"altitude must be > 0, got {self.alt:g} (it is a height, not a NED z)"
        if self.mode == "circle":
            if self.speed <= 0:
                return f"speed must be > 0 for a circle, got {self.speed:g}"
            if self.laps <= 0:
                return f"laps must be > 0, got {self.laps:g}"
            if self.max_accel <= 0:
                return f"max_accel must be > 0, got {self.max_accel:g}"
        else:
            if self.legs < 3:
                return f"legs must be >= 3 to close a circuit, got {self.legs}"
            if self.tol <= 0:
                return f"tolerance must be > 0, got {self.tol:g}"
        if self.mode not in ("circle", "waypoints"):
            return f"mode must be 'circle' or 'waypoints', got {self.mode!r}"
        return None

    def safe_land(self) -> bool:
        """Land and disarm. Called on the failure path too -- see main().

        An armed vehicle must never be abandoned because the mission code raised. PX4's
        offboard-loss failsafe would eventually act in SITL, but this node is the reference for
        driving the aircraft from ROS 2 and the sim/real boundary here is only a transport swap.
        """
        self.command(VehicleCommand.VEHICLE_CMD_NAV_LAND)
        landed = self.spin_until(lambda: not self.armed(), 90.0)
        self.command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, p1=0)
        self.spin_until(lambda: not self.armed(), 5.0)
        return landed

    def run(self) -> dict:
        log = self.get_logger()
        bad = self.validate()
        if bad:
            return {"ok": False, "error": f"invalid parameters: {bad}"}
        if not self.spin_until(lambda: self.local is not None and self.status is not None, 30.0):
            return {"ok": False, "error": "no /fmu/out telemetry — check QoS (trap 1)"}

        self.origin = self.pos()
        log.info(f"origin ({self.origin[0]:.1f}, {self.origin[1]:.1f}, {self.origin[2]:.1f})")

        # --- arm + take off. Stream BEFORE requesting OFFBOARD (trap 3).
        target_z = self.origin[2] - self.alt
        for _ in range(30):
            self.stream(self.origin[0], self.origin[1], target_z, 0.0)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.05)
        self.command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, p1=1, p2=6)   # 6 = OFFBOARD
        self.command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, p1=1)

        def hold_takeoff():
            self.stream(self.origin[0], self.origin[1], target_z, 0.0)

        if not self.spin_until(lambda: self.pos() and abs(self.pos()[2] - target_z) < 1.5,
                               90.0, hold_takeoff):
            return {"ok": False, "error": f"takeoff to {target_z:.1f} m failed",
                    "armed": self.armed()}
        log.info(f"airborne at z={self.pos()[2]:.2f}")

        if self.mode == "circle":
            result = self.run_circle()
            result["landed"] = self.safe_land()
            result["ok"] = bool(result.get("ok")) and result["landed"]
            return result

        # --- the circuit
        legs = []
        for i, (tx, ty, tz, yaw) in enumerate(self.waypoints(), 1):
            t0 = time.time()

            def hold():
                self.stream(tx, ty, tz, yaw)

            def near():
                """Arrived means INSIDE the tolerance AND slow enough to be stopping there.

                Distance alone is not arrival: at ~6 m/s a 2.5 m sphere is crossed in under half
                a second, so a pure distance check fires on the way THROUGH and the vehicle sails
                on. That produced a run where every leg reported an arrival error of ~2.45 m
                against a 2.5 m tolerance -- suspiciously uniform, because it was measuring the
                tolerance, not the flight -- while one leg settled 15.68 m past its waypoint.
                A demo that passes like that teaches the wrong pattern.
                """
                p = self.pos()
                return (math.dist((p[0], p[1]), (tx, ty)) < self.tol
                        and self.speed_xy() < self.arrive_speed)

            reached = self.spin_until(near, self.leg_timeout, hold)
            # Measure the arrival error IMMEDIATELY: this is the one the tolerance decided on,
            # and it is what `ok` means. Measuring after the settle instead once produced legs
            # reported ok=True with an error larger than the tolerance -- a verdict and a number
            # that disagreed, which is worse than either being wrong alone.
            pa = self.pos()
            err_arrival = math.dist((pa[0], pa[1]), (tx, ty))

            # Then settle, so the recorded imagery is steady at each corner rather than smeared.
            settle_end = time.time() + self.settle_s
            while time.time() < settle_end:
                hold()
                rclpy.spin_once(self, timeout_sec=0.02)

            p = self.pos()
            err_settled = math.dist((p[0], p[1]), (tx, ty))
            legs.append({"leg": i, "target": [round(tx, 2), round(ty, 2), round(tz, 2)],
                         "reached": [round(p[0], 2), round(p[1], 2), round(p[2], 2)],
                         "error_m": round(err_arrival, 3),
                         "error_settled_m": round(err_settled, 3),
                         "seconds": round(time.time() - t0, 1),
                         "ok": bool(reached)})
            log.info(f"leg {i}/{len(self.waypoints())} "
                     f"{'OK ' if reached else 'MISS'} arrival {err_arrival:.2f} m, "
                     f"settled {err_settled:.2f} m")

        # --- land
        landed = self.safe_land()

        worst = max((l["error_m"] for l in legs), default=float("nan"))
        ok = all(l["ok"] for l in legs) and landed
        return {"ok": ok, "landed": landed, "legs": legs,
                "worst_error_m": round(worst, 3),
                "mean_error_m": round(sum(l["error_m"] for l in legs) / len(legs), 3) if legs else None,
                "params": {"legs": self.legs, "radius": self.radius, "altitude": self.alt,
                           "tolerance": self.tol, "arrive_speed": self.arrive_speed},
                "origin": [round(v, 2) for v in self.origin]}


def main(argv=None):
    rclpy.init(args=argv)
    node = ParkTour()
    try:
        result = node.run()
    except (Exception, KeyboardInterrupt) as e:                # a crash must still leave a record
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        # An armed vehicle must not be abandoned because the mission code raised. Ctrl-C is
        # included deliberately: interrupting a flight is the most likely way this path is hit.
        try:
            if node.armed():
                result["emergency_land"] = node.safe_land()
        except Exception as land_err:
            result["emergency_land_error"] = f"{type(land_err).__name__}: {land_err}"
    finally:
        path = node.summary_path
        node.destroy_node()
        rclpy.shutdown()

    print(json.dumps(result, indent=2))
    if path:
        with open(path, "w") as f:
            json.dump(result, f, indent=2)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
