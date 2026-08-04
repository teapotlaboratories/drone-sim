"""PX4 offboard control node — arm, take off, fly waypoints, land.

The first flying code in the project, and the thing Phase 0's throwaway MAVLink script
gets replaced by: that was MAVLink, written to produce a video. This is **uXRCE-DDS**,
which is what runs onboard.

Conventions are frozen in `docs/conventions.md` and this node is the reference
implementation of them: `px4_ns` parameter rather than hard-coded topics, ENU in / NED out
via `frames.py` alone, BEST_EFFORT subscriptions, and setpoints streaming before the mode
switch.

Flight is a flat state machine with a timeout on every state. A controller that can hang
forever waiting for `arming_state` is a controller that hangs a CI job for its full budget
and reports nothing useful.
"""

import json
import math
from enum import Enum

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from px4_msgs.msg import (
    OffboardControlMode,
    TrajectorySetpoint,
    VehicleCommand,
    VehicleLocalPosition,
    VehicleStatus,
)

from drone_interfaces.msg import MissionResult, MissionStatus

from control.frames import enu_to_ned, yaw_enu_to_ned

# PX4 mode ids for VEHICLE_CMD_DO_SET_MODE. param1 = MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
# param2 = PX4 custom main mode. These are PX4's, not MAVLink standard, and are not
# exposed as constants in px4_msgs.
PX4_CUSTOM_MODE_ENABLED = 1.0
PX4_MAIN_MODE_OFFBOARD = 6.0


class State(Enum):
    WAIT_FOR_FCU = "wait_for_fcu"
    STREAM_SETPOINTS = "stream_setpoints"
    REQUEST_OFFBOARD = "request_offboard"
    ARM = "arm"
    TAKEOFF = "takeoff"
    WAYPOINTS = "waypoints"
    LAND = "land"
    DONE = "done"
    FAILED = "failed"


# State -> MissionStatus constant. Kept beside the enum so the two cannot drift silently:
# a state added here without a constant fails loudly at publish rather than reporting the
# wrong number into a bag that will be read months later.
STATE_TO_MSG = {
    State.WAIT_FOR_FCU: MissionStatus.STATE_WAIT_FOR_FCU,
    State.STREAM_SETPOINTS: MissionStatus.STATE_STREAM_SETPOINTS,
    State.REQUEST_OFFBOARD: MissionStatus.STATE_REQUEST_OFFBOARD,
    State.ARM: MissionStatus.STATE_ARM,
    State.TAKEOFF: MissionStatus.STATE_TAKEOFF,
    State.WAYPOINTS: MissionStatus.STATE_WAYPOINTS,
    State.LAND: MissionStatus.STATE_LAND,
    State.DONE: MissionStatus.STATE_DONE,
    State.FAILED: MissionStatus.STATE_FAILED,
}


class OffboardControl(Node):
    def __init__(self) -> None:
        super().__init__("offboard_control")

        # --- parameters -------------------------------------------------------------
        # px4_ns keeps multi-vehicle a configuration change rather than a refactor
        # (conventions §1). Empty is the single-vehicle default PX4 itself uses.
        # dynamic_typing on the numeric parameters is not decoration. Declared as DOUBLE,
        # `--ros-args -p takeoff_altitude:=10` is parsed as INTEGER and rclpy raises
        # InvalidParameterTypeException at construction — the node dies before it ever
        # subscribes. Writing `10` instead of `10.0` is the natural thing to type, and the
        # failure surfaces as a stack trace in whatever ran the node, which is easy to miss
        # when that is a recorder pane. Every numeric parameter is read through float().
        num = ParameterDescriptor(dynamic_typing=True)
        self.declare_parameter("px4_ns", "")
        self.declare_parameter("takeoff_altitude", 10.0, num)  # metres, ENU (up positive)
        self.declare_parameter("square_side", 10.0, num)       # metres
        self.declare_parameter("accept_radius", 1.0, num)      # metres
        self.declare_parameter("hold_seconds", 2.0, num)       # settle time at each waypoint
        self.declare_parameter("setpoint_rate_hz", 20.0, num)  # PX4 needs >2 Hz; 20 is margin
        self.declare_parameter("state_timeout_s", 60.0, num)
        self.declare_parameter("result_path", "")              # JSON summary for the runner
        # Scenario-supplied mission: a FLAT list of ENU triples, x,y,z,x,y,z,... relative
        # to the home position. Empty (the default) keeps the built-in square, so the node
        # stays runnable by hand with no scenario file. Flat rather than nested because
        # ROS 2 parameters have no nested-array type.
        self.declare_parameter("waypoints_enu", [0.0])

        ns = self.get_parameter("px4_ns").value.rstrip("/")
        self.alt = float(self.get_parameter("takeoff_altitude").value)
        self.side = float(self.get_parameter("square_side").value)
        self.accept_radius = float(self.get_parameter("accept_radius").value)
        self.hold_seconds = float(self.get_parameter("hold_seconds").value)
        rate = float(self.get_parameter("setpoint_rate_hz").value)
        # PX4 drops offboard below 2 Hz, and int(rate) is used as a modulus for command
        # re-sends — a rate under 1 would make that ZeroDivisionError mid-flight instead
        # of failing clearly here.
        if rate < 2.0:
            raise ValueError(f"setpoint_rate_hz must be >= 2.0 (PX4 offboard minimum); got {rate}")
        self.state_timeout_s = float(self.get_parameter("state_timeout_s").value)
        self.result_path = self.get_parameter("result_path").value
        raw_wps = list(self.get_parameter("waypoints_enu").value or [])
        # A single 0.0 is the "unset" sentinel — ROS 2 rejects a genuinely empty double
        # array as an ambiguous type, so it cannot be the default.
        self.scenario_wps = raw_wps if len(raw_wps) >= 3 else []
        if self.scenario_wps and len(self.scenario_wps) % 3 != 0:
            raise ValueError(
                f"waypoints_enu has {len(self.scenario_wps)} values; must be a multiple "
                "of 3 (x,y,z triples)")

        # --- QoS --------------------------------------------------------------------
        # PX4's /fmu/out publishers are BEST_EFFORT + TRANSIENT_LOCAL (verified with
        # `ros2 topic info -v`). A default RELIABLE subscription matches NOTHING and the
        # node sees zero messages against a completely healthy stack. Its /fmu/in
        # subscribers are BEST_EFFORT + VOLATILE, so publish to match.
        sub_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        pub_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        self.pub_offboard = self.create_publisher(
            OffboardControlMode, f"{ns}/fmu/in/offboard_control_mode", pub_qos)
        self.pub_setpoint = self.create_publisher(
            TrajectorySetpoint, f"{ns}/fmu/in/trajectory_setpoint", pub_qos)
        self.pub_command = self.create_publisher(
            VehicleCommand, f"{ns}/fmu/in/vehicle_command", pub_qos)

        # OUR topics use ROS defaults (RELIABLE), unlike the PX4 ones — conventions §5.
        # TRANSIENT_LOCAL on the result so a late subscriber (or a recorder started after
        # the flight ends) still receives the verdict rather than missing it by a second.
        self.pub_status = self.create_publisher(MissionStatus, "/mission/status", 10)
        self.pub_result = self.create_publisher(
            MissionResult, "/mission/result",
            QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL,
                       history=HistoryPolicy.KEEP_LAST, depth=1))

        self.create_subscription(
            VehicleLocalPosition, f"{ns}/fmu/out/vehicle_local_position",
            self._on_position, sub_qos)
        self.create_subscription(
            VehicleStatus, f"{ns}/fmu/out/vehicle_status_v1",
            self._on_status, sub_qos)

        # --- state ------------------------------------------------------------------
        self.state = State.WAIT_FOR_FCU
        self.position: VehicleLocalPosition | None = None
        self.status: VehicleStatus | None = None
        self.home_enu: tuple[float, float] | None = None
        self.waypoints: list[tuple[float, float, float]] = []
        self.wp_index = 0
        self.target_enu = (0.0, 0.0, 0.0)
        self.ticks_in_state = 0
        self.hold_ticks = 0
        self.errors: list[float] = []
        self.last_distance_m = 0.0
        self.failure_reason = ""

        self.rate_hz = rate
        self.timer = self.create_timer(1.0 / rate, self._tick)
        shown_ns = ns if ns else "(none)"
        self.get_logger().info(
            f"offboard_control up: px4_ns='{shown_ns}' alt={self.alt} m "
            f"side={self.side} m rate={rate} Hz")

    # -- subscriptions ---------------------------------------------------------------

    def _on_position(self, msg: VehicleLocalPosition) -> None:
        self.position = msg

    def _on_status(self, msg: VehicleStatus) -> None:
        self.status = msg

    # -- helpers ---------------------------------------------------------------------

    def _px4_timestamp(self) -> int:
        """PX4 `timestamp` is microseconds on PX4's own clock — NOT a ROS Time
        (conventions §4)."""
        return int(self.get_clock().now().nanoseconds / 1000)

    def _publish_offboard_mode(self) -> None:
        """These booleans select which TrajectorySetpoint fields PX4 honours. With the
        wrong one set, PX4 ignores a perfectly valid setpoint and holds position."""
        msg = OffboardControlMode()
        msg.timestamp = self._px4_timestamp()
        msg.position = True
        msg.velocity = False
        msg.acceleration = False
        msg.attitude = False
        msg.body_rate = False
        self.pub_offboard.publish(msg)

    def _publish_setpoint(self, enu: tuple[float, float, float], yaw_enu: float = 0.0) -> None:
        msg = TrajectorySetpoint()
        msg.timestamp = self._px4_timestamp()
        msg.position = [float(v) for v in enu_to_ned(*enu)]
        # NaN, not 0.0 — the message documents NaN as "do not control this state". A
        # zeroed velocity array commands zero velocity, which fights the position
        # controller instead of leaving it free.
        msg.velocity = [math.nan] * 3
        msg.acceleration = [math.nan] * 3
        msg.jerk = [math.nan] * 3
        msg.yaw = yaw_enu_to_ned(yaw_enu)
        msg.yawspeed = math.nan
        self.pub_setpoint.publish(msg)

    def _send_command(self, command: int, param1: float = 0.0, param2: float = 0.0) -> None:
        msg = VehicleCommand()
        msg.timestamp = self._px4_timestamp()
        msg.command = command
        msg.param1 = param1
        msg.param2 = param2
        msg.target_system = 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        self.pub_command.publish(msg)

    def _position_enu(self) -> tuple[float, float, float] | None:
        """Current position in ENU, or None if the EKF has not declared it valid.

        `xy_valid`/`z_valid` are checked rather than assumed: acting on a position the
        estimator itself does not trust is how a run ends up flying to a plausible-looking
        wrong place."""
        p = self.position
        if p is None or not p.xy_valid or not p.z_valid:
            return None
        # PX4 gives NED; ned_to_enu is the same swap-and-negate.
        return (p.y, p.x, -p.z)

    def _distance_to(self, target_enu: tuple[float, float, float]) -> float | None:
        cur = self._position_enu()
        if cur is None:
            return None
        return math.dist(cur, target_enu)

    def _enter(self, state: State) -> None:
        self.get_logger().info(f"state: {self.state.value} -> {state.value}")
        self.state = state
        self.ticks_in_state = 0
        self.hold_ticks = 0

    def _fail(self, reason: str) -> None:
        self.failure_reason = reason
        self.get_logger().error(f"FAILED: {reason}")
        self._enter(State.FAILED)

    # -- the state machine -----------------------------------------------------------

    def _tick(self) -> None:
        self.ticks_in_state += 1

        if self.state in (State.DONE, State.FAILED):
            # Publish the TERMINAL status before shutting down. Without this the early
            # return happens before _publish_status() below, so the last MissionStatus in
            # the bag carries the pre-terminal state and an empty failure_reason —
            # STATE_DONE and STATE_FAILED were unreachable values, and the one field that
            # says why the controller gave up never reached the bag.
            #
            # That defeats the stated purpose of the message: a bag explaining a failed
            # seed by itself. Runs are not reproducible, so the bag is the only evidence.
            self._publish_status()
            self._write_result()
            rclpy.shutdown()
            return

        # Every state gets a timeout. Without this, a node waiting on an arming_state
        # that never arrives hangs for the whole CI budget and reports nothing.
        if self.ticks_in_state > self.state_timeout_s * self.rate_hz:
            self._fail(f"timeout in state {self.state.value}")
            return

        # Setpoints stream through every FLYING state: PX4 drops out of offboard the
        # moment the stream lapses (COM_OF_LOSS_T, 1.0 s on v1.16.0).
        #
        # LAND is excluded deliberately. VEHICLE_CMD_NAV_LAND hands control to AUTO.LAND,
        # and continuing to publish an offboard setpoint at cruise altitude fights it —
        # observed as a descent that never completes and a `timeout in state land`, with
        # the vehicle happily holding 10 m. Commanding a landing means stopping telling
        # PX4 where to be.
        if self.state not in (State.WAIT_FOR_FCU, State.LAND):
            self._publish_offboard_mode()
            self._publish_setpoint(self.target_enu)

        self._publish_status()

        handler = getattr(self, f"_do_{self.state.value}", None)
        if handler is not None:
            handler()

    def _publish_status(self) -> None:
        """Publish what the controller believes, so the MCAP explains itself.

        Runs here are not reproducible, so a bag is the only evidence a failed seed leaves.
        Without this the bag shows the vehicle in the wrong place and nothing about which
        waypoint the controller was aiming at — the two failures look identical."""
        msg = MissionStatus()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"          # ENU world frame, conventions §3
        msg.state = STATE_TO_MSG[self.state]
        msg.waypoint_index = int(self.wp_index)
        msg.waypoint_total = int(len(self.waypoints))
        msg.target_enu.x = float(self.target_enu[0])
        msg.target_enu.y = float(self.target_enu[1])
        msg.target_enu.z = float(self.target_enu[2])
        d = self._distance_to(self.target_enu)
        # -1 rather than NaN for "unknown": NaN in a bag silently defeats comparisons, which
        # is the bug the gate already had once.
        msg.distance_to_target_m = float(d) if d is not None else -1.0
        msg.failure_reason = self.failure_reason
        self.pub_status.publish(msg)

    def _do_wait_for_fcu(self) -> None:
        if self.status is None:
            return
        cur = self._position_enu()
        if cur is None:
            return
        self.home_enu = (cur[0], cur[1])
        self.target_enu = (cur[0], cur[1], cur[2])
        self.waypoints = self._build_square(cur[0], cur[1])
        self.get_logger().info(
            f"FCU alive; home ENU=({cur[0]:.2f}, {cur[1]:.2f}) "
            f"waypoints={[(round(a,1), round(b,1), round(c,1)) for a, b, c in self.waypoints]}")
        self._enter(State.STREAM_SETPOINTS)

    def _build_square(self, x0: float, y0: float) -> list[tuple[float, float, float]]:
        """The mission, in ENU, offset from home.

        A scenario's `waypoints_enu` wins when supplied; otherwise the built-in square,
        so the node remains runnable by hand. Both are expressed relative to HOME rather
        than the world origin, because PX4's local frame origin is wherever the EKF
        initialised — a mission in absolute local coordinates would silently shift with
        the spawn point.
        """
        if self.scenario_wps:
            triples = [tuple(self.scenario_wps[i:i + 3])
                       for i in range(0, len(self.scenario_wps), 3)]
            return [(x0 + x, y0 + y, z) for x, y, z in triples]
        s = self.side
        return [
            (x0 + s, y0, self.alt),
            (x0 + s, y0 + s, self.alt),
            (x0, y0 + s, self.alt),
            (x0, y0, self.alt),
        ]

    def _do_stream_setpoints(self) -> None:
        """PX4 refuses the offboard mode switch unless a setpoint stream already exists.
        Stream first, switch second — one full second of margin over the 2 Hz minimum."""
        if self.ticks_in_state >= self.rate_hz:
            self._enter(State.REQUEST_OFFBOARD)

    def _do_request_offboard(self) -> None:
        if self.ticks_in_state == 1:
            self._send_command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE,
                               PX4_CUSTOM_MODE_ENABLED, PX4_MAIN_MODE_OFFBOARD)
        if self.status and self.status.nav_state == VehicleStatus.NAVIGATION_STATE_OFFBOARD:
            self._enter(State.ARM)

    def _do_arm(self) -> None:
        # Re-send periodically rather than once: the command is fire-and-forget over a
        # BEST_EFFORT transport, and a dropped single attempt would stall until timeout.
        if self.ticks_in_state % int(self.rate_hz) == 1:
            self._send_command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,
                               float(VehicleCommand.ARMING_ACTION_ARM))
        if self.status and self.status.arming_state == VehicleStatus.ARMING_STATE_ARMED:
            self.get_logger().info("armed")
            home = self.home_enu or (0.0, 0.0)
            self.target_enu = (home[0], home[1], self.alt)
            self._enter(State.TAKEOFF)

    def _do_takeoff(self) -> None:
        if self._reached(self.target_enu):
            self.get_logger().info(f"reached takeoff altitude {self.alt} m")
            self.wp_index = 0
            self.target_enu = self.waypoints[0]
            self._enter(State.WAYPOINTS)

    def _do_waypoints(self) -> None:
        if not self._reached(self.target_enu):
            return
        # Reuse the distance _reached() already measured rather than sampling again.
        # The old code re-measured and stored NaN when the second sample happened to be
        # invalid — and a NaN error silently PASSED the gate, because every comparison
        # against NaN is False. The one case where the error is unknown must not be the
        # one that looks clean.
        d = self.last_distance_m
        self.errors.append(d)
        self.get_logger().info(
            f"waypoint {self.wp_index + 1}/{len(self.waypoints)} reached "
            f"(error {d:.2f} m)")
        self.wp_index += 1
        if self.wp_index >= len(self.waypoints):
            self._enter(State.LAND)
            return
        self.target_enu = self.waypoints[self.wp_index]
        self.hold_ticks = 0

    def _reached(self, target_enu: tuple[float, float, float]) -> bool:
        """Within the accept radius AND settled there for hold_seconds.

        The settle requirement is deliberate: a fast fly-through clips the corner within
        tolerance for one tick and would score as 'reached' while the vehicle is still
        moving, which makes waypoint error meaningless."""
        d = self._distance_to(target_enu)
        if d is None or d > self.accept_radius:
            self.hold_ticks = 0
            return False
        self.hold_ticks += 1
        if self.hold_ticks >= self.hold_seconds * self.rate_hz:
            # Remembered so the caller records the distance that actually satisfied the
            # check, instead of taking a fresh sample that might be invalid.
            self.last_distance_m = d
            return True
        return False

    def _do_land(self) -> None:
        if self.ticks_in_state % int(self.rate_hz) == 1:
            self._send_command(VehicleCommand.VEHICLE_CMD_NAV_LAND)
        if self.status and self.status.arming_state == VehicleStatus.ARMING_STATE_DISARMED:
            self.get_logger().info("landed and disarmed")
            self._enter(State.DONE)

    # -- result ----------------------------------------------------------------------

    def _write_result(self) -> None:
        """Machine-readable summary for the scenario runner (`P1-04`).

        Provisional: a JSON file, not a ROS message, because the mission contracts are
        `P1-01` and are not designed yet. Recorded in the backlog so it does not quietly
        become the permanent interface."""
        result = {
            "outcome": "success" if self.state is State.DONE else "failure",
            "failure_reason": self.failure_reason,
            "waypoints_reached": self.wp_index,
            "waypoints_total": len(self.waypoints),
            "waypoint_errors_m": [round(e, 3) for e in self.errors],
            "takeoff_altitude_m": self.alt,
            # Only meaningful for the built-in square; a scenario supplies its own path,
            # and reporting a square side for an arbitrary route reads as fact later.
            "square_side_m": None if self.scenario_wps else self.side,
            "mission_source": "scenario" if self.scenario_wps else "built-in-square",
            "accept_radius_m": self.accept_radius,
        }
        # allow_nan=False on purpose. Python happily writes a bare `NaN`, which is NOT
        # valid JSON — Python reads it back, jq and most CI consumers do not. Failing here
        # turns a silent bad artifact into an visible error.
        try:
            rendered = json.dumps(result, allow_nan=False)
        except ValueError as exc:
            self.get_logger().error(f"result contains a non-finite value: {exc}")
            rendered = json.dumps({"outcome": "failure",
                                   "failure_reason": f"non-finite value in result: {exc}"})
            result = json.loads(rendered)
        # Graph-side verdict, so the bag carries its own outcome. The JSON below remains
        # the HOST-side transport: run_scenario.py drives docker compose and has no ROS
        # environment, so it cannot subscribe. Two transports, one source of truth.
        rmsg = MissionResult()
        rmsg.header.stamp = self.get_clock().now().to_msg()
        rmsg.header.frame_id = "map"
        # EVERY field uses .get() with a default, because `result` is not always the full
        # dict: the allow_nan=False fallback above replaces it with just {outcome,
        # failure_reason}. Indexing directly raised KeyError there — and since _write_result
        # is called from the timer callback, the exception escaped, rclpy.shutdown() never
        # ran, the `result:` log line was never emitted and the JSON was never written.
        #
        # The escape hatch that exists to turn a silent bad artifact into a VISIBLE error was
        # destroying the evidence instead. A non-finite result must still produce a readable
        # verdict on both transports.
        rmsg.outcome = result.get("outcome", "failure")
        rmsg.failure_reason = result.get("failure_reason", "")
        rmsg.waypoints_reached = int(result.get("waypoints_reached", 0))
        rmsg.waypoints_total = int(result.get("waypoints_total", len(self.waypoints)))
        rmsg.waypoint_errors_m = [float(e) for e in result.get("waypoint_errors_m", [])]
        rmsg.takeoff_altitude_m = float(result.get("takeoff_altitude_m", self.alt))
        rmsg.accept_radius_m = float(result.get("accept_radius_m", self.accept_radius))
        rmsg.mission_source = result.get("mission_source", "")
        self.pub_result.publish(rmsg)

        self.get_logger().info(f"result: {rendered}")
        if self.result_path:
            try:
                with open(self.result_path, "w") as fh:
                    fh.write(json.dumps(result, indent=2, allow_nan=False))
            except (OSError, ValueError) as exc:
                self.get_logger().error(f"could not write {self.result_path}: {exc}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OffboardControl()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # rclpy.shutdown() may already have been called from the state machine.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
