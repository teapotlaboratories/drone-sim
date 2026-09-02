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

from drone_interfaces.msg import MissionCommand, MissionResult, MissionStatus

from control.frames import enu_to_ned, flu_to_enu, yaw_enu_to_ned, yaw_ned_to_enu
from control import manual_policy
from control.sitl_interlock import simulator_present

# PX4 mode ids for VEHICLE_CMD_DO_SET_MODE. param1 = MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
# param2 = PX4 custom main mode. These are PX4's, not MAVLink standard, and are not
# exposed as constants in px4_msgs.
# What the flight reports when the runner asked it to stop but the reason could not be read.
# Deliberately not "" -- see _abort_requested. An abort with no legible reason is still an
# abort, and saying so beats a 240 s timeout that blames the flight controller.
UNPARSEABLE_ABORT = "the runner aborted this flight (its reason could not be read)"

def _wrap_pi(angle: float) -> float:
    """Wrap to [-pi, pi) -- the range PX4 documents for TrajectorySetpoint.yaw. Same helper as
    control.frames._wrap_pi; not imported because that one is private to that module."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


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
    # HAND-FLYING ONLY (SIM-45, `manual:=true`). A mission run enters neither.
    IDLE = "idle"      # on the ground, disarmed, streaming nothing, waiting for a human
    HOVER = "hover"    # holding the takeoff setpoint, waiting for a human


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
    State.IDLE: MissionStatus.STATE_IDLE,
    State.HOVER: MissionStatus.STATE_HOVER,
}

# What a hand-flown command is allowed to do, by the state it arrives in.
#
# THE POLICY ITSELF IS IN `control/manual_policy.py`, which imports nothing, so the
# off-target tests can assert the table this node actually runs rather than a copy of it
# (see that file's docstring). Here it is only mapped onto the State enum.
#
# The mapping is strict: an unknown state name raises at IMPORT, so renaming a State value
# without updating the policy kills the node at start-up instead of silently narrowing what
# the operator is allowed to do -- a failure that would otherwise look like an unresponsive
# button.
_BY_VALUE = {st.value: st for st in State}


def _states(names) -> frozenset:
    missing = sorted(set(names) - set(_BY_VALUE))
    if missing:
        raise RuntimeError(
            f"manual_policy names states that offboard_control.State does not define: "
            f"{missing}. The policy and the state machine have drifted.")
    return frozenset(_BY_VALUE[n] for n in names)


UNTIMED_STATES = _states(manual_policy.UNTIMED)
MANUAL_TRANSITIONS = {cmd: _states(froms)
                      for cmd, froms in manual_policy.ALLOWED_FROM.items()}

# The .msg constants and the policy strings are two spellings of the same tokens. Asserted
# here so a mismatch is a start-up crash rather than a command the node silently calls
# unknown; `tests/test_manual_flight.py` makes the same join off-target by parsing the .msg.
for _cmd, _const in ((manual_policy.COMMAND_TAKEOFF, MissionCommand.COMMAND_TAKEOFF),
                     (manual_policy.COMMAND_LAND, MissionCommand.COMMAND_LAND),
                     (manual_policy.COMMAND_HOLD, MissionCommand.COMMAND_HOLD)):
    if _cmd != _const:
        raise RuntimeError(
            f"manual_policy says {_cmd!r} where MissionCommand.msg says {_const!r}")


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
        # THE RUNNER'S STOP BUTTON.                                                 (SIM-27)
        #
        # A path this node polls; when the file appears, the flight ends with the reason
        # written inside it. It exists because the harness can see a fault this node cannot:
        # AirSim's physics integrator and the Unreal actor hold two independent positions for
        # the vehicle, and when they split, every sensor PX4 receives keeps describing a
        # descent that the world is not performing. Nothing in the ROS 2 graph can contradict
        # that -- the graph is downstream of the same integrator -- so the observation arrives
        # from outside it, through probe_landing.py and run_scenario.py.
        #
        # A FILE RATHER THAN A TOPIC, deliberately. `docs/conventions.md` freezes the ROS 2
        # graph, and this is harness plumbing, not part of the interface a real aircraft flies.
        # It is also a one-shot latch, which is the one thing a BEST_EFFORT topic is worst at.
        #
        # AND IT ENDS THE FLIGHT THROUGH _fail(), not by signal. The runner could `pkill -INT`
        # instead, but rclpy raises KeyboardInterrupt past _write_result (see main()), so the
        # run would lose its waypoints_reached, its per-waypoint errors and its terminal
        # MissionStatus -- on a run whose mission typically flew perfectly, which is exactly
        # the evidence that proves the aircraft was not at fault.
        self.declare_parameter("abort_file", "")
        # HAND FLYING.                                                            (SIM-45)
        #
        # Default FALSE, so every existing caller -- run_scenario.py, run_gate.py,
        # control.launch.py -- gets byte-identical behaviour and the gate path is not
        # touched. Manual mode adds two states and one subscription; it replaces nothing.
        self.declare_parameter("manual", False)
        # THE LEASH for hand-flown movement (SIM-47). Defaults live in
        # control/manual_policy.DEFAULT_LIMITS so the tests exercise the same numbers the
        # aircraft runs. Exposed as parameters so a scenario that genuinely needs a wider
        # envelope sets it at launch, where it is recorded -- rather than a browser widening
        # it at run time, which is the thing that must never be possible.
        _lim = manual_policy.DEFAULT_LIMITS
        self.declare_parameter("move_step_max_m", _lim["step_max_m"], num)
        self.declare_parameter("move_radius_max_m", _lim["radius_max_m"], num)
        self.declare_parameter("move_alt_min_m", _lim["alt_min_m"], num)
        self.declare_parameter("move_alt_max_m", _lim["alt_max_m"], num)
        self.declare_parameter("move_yaw_step_max_rad", _lim["yaw_step_max_rad"], num)
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
        self.abort_file = self.get_parameter("abort_file").value
        self.manual = bool(self.get_parameter("manual").value)
        self.move_limits = {
            "step_max_m": float(self.get_parameter("move_step_max_m").value),
            "radius_max_m": float(self.get_parameter("move_radius_max_m").value),
            "alt_min_m": float(self.get_parameter("move_alt_min_m").value),
            "alt_max_m": float(self.get_parameter("move_alt_max_m").value),
            "yaw_step_max_rad": float(self.get_parameter("move_yaw_step_max_rad").value),
        }
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

        # THE SUBSCRIPTION ONLY EXISTS IN MANUAL MODE.                             (SIM-45)
        #
        # Not "exists and is ignored" -- it is never created. A gate run therefore cannot be
        # perturbed by a stray `ros2 topic pub /mission/command`, and `ros2 topic info` shows
        # zero subscribers, which is a checkable statement rather than a promise in a comment.
        #
        # RELIABLE (the ROS default, conventions §5), unlike the /fmu/* subscriptions above:
        # a button press is a one-shot event, and BEST_EFFORT is worst at exactly those.
        self.sub_command = None
        if self.manual:
            self.sub_command = self.create_subscription(
                MissionCommand, "/mission/command", self._on_command, 10)

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
        # One command deep, deliberately. Buttons are pressed faster than an aircraft can
        # respond, and a queue would replay a stale intent into a state the operator can no
        # longer see. The newest instruction wins and the rest are dropped, loudly.
        self.pending_command: MissionCommand | None = None
        self.manual_ready = False
        # The commanded ENU yaw. 0.0 is what every setpoint has carried since this node was
        # written (`_publish_setpoint`'s default), so a mission run is bit-for-bit unchanged;
        # only MOVE ever alters it.                                                (SIM-47)
        self.target_yaw_enu = 0.0

        self.rate_hz = rate
        self.timer = self.create_timer(1.0 / rate, self._tick)
        shown_ns = ns if ns else "(none)"
        self.get_logger().info(
            f"offboard_control up: px4_ns='{shown_ns}' alt={self.alt} m "
            f"side={self.side} m rate={rate} Hz manual={self.manual}")

        # THE INTERLOCK, CHECKED AT START-UP AND AGAIN AT EVERY TAKEOFF.           (SIM-45)
        #
        # Start-up so the operator learns it is refused before they are looking at a page
        # with a button on it; per-takeoff because the answer can change under a
        # long-running node and the start-up result would then be a stale permission.
        #
        # Mission mode is NOT gated. It is launched by a human running run_scenario.py, which
        # is the per-run approval hard stop 1 asks for; this gate exists because a browser
        # button is not.
        if self.manual:
            ok, why = simulator_present()
            self.manual_ready = ok
            if ok:
                self.get_logger().info(f"manual mode: SITL interlock satisfied -- {why}")
            else:
                self.get_logger().error(
                    "manual mode: SITL INTERLOCK NOT SATISFIED -- every command will be "
                    f"REFUSED. {why}")

    # -- subscriptions ---------------------------------------------------------------

    def _on_position(self, msg: VehicleLocalPosition) -> None:
        self.position = msg

    def _on_status(self, msg: VehicleStatus) -> None:
        self.status = msg

    def _on_command(self, msg: MissionCommand) -> None:
        """A hand-flying command arrives.                                        (SIM-45)

        VALIDATED HERE, ACTED ON IN _tick. The callback runs on the executor's thread at
        whatever moment a button is pressed; the state machine runs on the timer. Mutating
        `self.state` from here would race every handler in this file -- and the failure
        would be an aircraft that changed state halfway through a tick, which is the least
        debuggable thing this node could possibly do. So this stores an intent and returns.

        Rejections are logged and DROPPED rather than latched. An operator who presses TAKE
        OFF while already hovering has been answered by the aircraft not moving; queueing it
        would take off again at some later moment they are no longer expecting.
        """
        cmd = str(msg.command or "").strip().lower()
        if cmd not in MANUAL_TRANSITIONS:
            self.get_logger().warning(
                f"ignoring unknown command {cmd!r}; known: {sorted(MANUAL_TRANSITIONS)}")
            return
        if self.pending_command is not None:
            self.get_logger().warning(
                f"dropping queued {self.pending_command.command!r}, superseded by {cmd!r}")
        msg.command = cmd
        self.pending_command = msg
        self.get_logger().info(f"command received: {cmd}")

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
            # HAND FLYING IS A SESSION, NOT ONE FLIGHT.                           (SIM-45)
            #
            # A mission run is over at DONE or FAILED and the node exits, which is what the
            # gate needs. An operator surveying a site takes off and lands repeatedly, and a
            # node that exited after the first landing would take the web interface's control
            # path with it -- leaving a page whose buttons silently do nothing.
            #
            # The terminal MissionStatus and the MissionResult are published FIRST, above, so
            # each hand-flown sortie still leaves the same evidence a mission run does. Only
            # then does the machine recycle.
            if self.manual:
                self.get_logger().info(
                    f"manual: sortie ended ({self.state.value}"
                    f"{': ' + self.failure_reason if self.failure_reason else ''}) -- idle")
                self.failure_reason = ""
                self.errors = []
                self.wp_index = 0
                self._enter(State.IDLE)
                return
            rclpy.shutdown()
            return

        # THE RUNNER'S VERDICT COMES FIRST, ahead of the state timeout.               (SIM-27)
        #
        # Both end the flight, and the order decides which sentence the report carries. The
        # known case is a landing that never terminates: the actor stops on the surface, the
        # integrator keeps descending, PX4 is told it is still falling and so never disarms,
        # and LAND runs out its budget. Checked first, that reads as "landing surface rejected
        # -- actor frozen, integrator descending"; checked second, it reads as `timeout in
        # state land` -- a CONTROL failure, on a run that flew 4/4 waypoints.
        abort = self._abort_requested()
        if abort:
            self._fail(abort)
            return

        # Every state gets a timeout EXCEPT the two that wait on a human (SIM-45). Without
        # this, a node waiting on an arming_state that never arrives hangs for the whole CI
        # budget and reports nothing; with it applied to IDLE or HOVER, an aircraft doing
        # exactly what the operator asked would be failed for doing it for too long.
        if (self.state not in UNTIMED_STATES
                and self.ticks_in_state > self.state_timeout_s * self.rate_hz):
            self._fail(f"timeout in state {self.state.value}")
            return

        # A pending command is applied BEFORE the setpoint is published, so LAND stops the
        # stream on the same tick it is accepted rather than one tick later. That one tick is
        # not important to the aircraft; the ordering is, because the alternative reads as
        # "the setpoint stream continued after the operator commanded a landing".
        if self.pending_command is not None:
            self._apply_command()

        # Setpoints stream through every FLYING state: PX4 drops out of offboard the
        # moment the stream lapses (COM_OF_LOSS_T, 1.0 s on v1.16.0).
        #
        # LAND is excluded deliberately. VEHICLE_CMD_NAV_LAND hands control to AUTO.LAND,
        # and continuing to publish an offboard setpoint at cruise altitude fights it —
        # observed as a descent that never completes and a `timeout in state land`, with
        # the vehicle happily holding 10 m. Commanding a landing means stopping telling
        # PX4 where to be.
        # IDLE joins the two exclusions for a third reason: the vehicle is on the ground and
        # disarmed, and streaming offboard setpoints at it would leave PX4 permanently ready
        # to accept an offboard mode switch from anything at all. An idle aircraft should be
        # boring.
        if self.state not in (State.WAIT_FOR_FCU, State.LAND, State.IDLE):
            self._publish_offboard_mode()
            self._publish_setpoint(self.target_enu, self.target_yaw_enu)

        self._publish_status()

        handler = getattr(self, f"_do_{self.state.value}", None)
        if handler is not None:
            handler()

    def _abort_requested(self) -> str:
        """The reason the runner wants this flight ended, or "" to keep flying.    (SIM-27)

        Absent is the overwhelmingly common answer, so this is a stat per tick and nothing
        more. The runner writes the file atomically (os.replace), so a partially written
        document should not be observable -- but it is still tolerated rather than raised on,
        because a malformed byte on the harness side must not take the aircraft down with a
        traceback out of a timer callback. A bad read simply means "not yet"; the next tick is
        100 ms away and the file is not going anywhere.
        """
        if not self.abort_file:
            return ""
        try:
            with open(self.abort_file) as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return ""
        except OSError as exc:
            # A READ that failed, not a file that is wrong. Transient (EIO, a mount blip), so
            # the answer is "not yet" and the next tick is 100 ms away.
            self.get_logger().warning(f"abort file {self.abort_file} unreadable: {exc}")
            return ""
        except ValueError as exc:
            # CONTENT that is wrong, which is a different thing, and it must still STOP.
            # The runner writes this file atomically (os.replace), so a parse failure is not a
            # half-written document -- it is a file someone meant to be an abort. Returning ""
            # here would let the flight run out its 240 s `timeout in state land`, which is
            # precisely the misdiagnosis this whole mechanism exists to remove.  (review)
            self.get_logger().error(f"abort file {self.abort_file} is not JSON: {exc}")
            return UNPARSEABLE_ABORT
        # A file that exists ends the flight even if its contents are not what we expected --
        # the runner does not create this by accident, and "the harness asked to stop but we
        # could not parse why" is still a stop.
        #
        # isinstance BEFORE .get(). Valid JSON that is not an object -- `["stop"]`, `"stop"`,
        # `null` -- reaches .get() and raises AttributeError straight out of a timer callback,
        # taking the node down WITHOUT _write_result and destroying the evidence: the exact
        # outcome the paragraph above promises cannot happen. A hand-written abort file is how
        # this mechanism was first exercised, and `"stop"` is what a hand writes.     (review)
        if not isinstance(doc, dict):
            self.get_logger().error(
                f"abort file {self.abort_file} is JSON but not an object: {type(doc).__name__}")
            return UNPARSEABLE_ABORT
        return str(doc.get("reason") or "").strip() or UNPARSEABLE_ABORT

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
        # NO MISSION IN MANUAL MODE. Not "a mission that is not flown" -- an empty list, so
        # MissionStatus reports waypoint_total 0 and a bag from a hand-flown sortie cannot be
        # misread later as a 4-waypoint run that reached none of them.
        self.waypoints = [] if self.manual else self._build_square(cur[0], cur[1])
        self.get_logger().info(
            f"FCU alive; home ENU=({cur[0]:.2f}, {cur[1]:.2f}) "
            f"waypoints={[(round(a,1), round(b,1), round(c,1)) for a, b, c in self.waypoints]}")
        if self.manual:
            self.get_logger().info("manual mode: waiting for a command on /mission/command")
            self._enter(State.IDLE)
            return
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

    def _apply_command(self) -> None:
        """Act on the pending hand-flying command, or refuse it with a reason.    (SIM-45)

        Runs on the timer thread, so it is the only place `self.state` moves in response to
        a button. Every refusal is logged: a control surface that ignores a press without
        saying why is one the operator stops trusting, and then stops using.
        """
        msg, self.pending_command = self.pending_command, None
        if msg is None:                                    # pragma: no cover - guarded above
            return
        cmd = msg.command

        # THE STATE CHECK COMES FIRST, and that ordering is a fix rather than a preference.
        # It is free, while the interlock below can cost seconds -- so checking the interlock
        # first made even a command that was going to be REFUSED pay the full stall. (review)
        allowed = MANUAL_TRANSITIONS[cmd]
        if self.state not in allowed:
            self.get_logger().warning(
                f"refused {cmd} in state {self.state.value}; allowed from "
                f"{sorted(st.value for st in allowed)}")
            return

        # THE INTERLOCK -- AND IT MUST NEVER BLOCK THIS THREAD WHILE THE AIRCRAFT IS FLYING.
        #
        # `_apply_command` runs on the timer callback, the same thread as `_publish_setpoint`.
        # `simulator_present()` opens a socket with a 2 s timeout that also covers the recv,
        # so a stalled AirSim RPC -- precisely what happens during World Partition streaming --
        # stops the setpoint stream for seconds. PX4 drops out of offboard after COM_OF_LOSS_T
        # (1.0 s on v1.16.0, see _tick). A single movement nudge could therefore drop the
        # aircraft into failsafe. Found in review; it was never flown.
        #
        # THE SPLIT IS BY WHETHER SETPOINTS ARE STREAMING, not by which command it is:
        #
        #   TAKEOFF arrives in IDLE, and _tick deliberately does NOT stream setpoints in IDLE.
        #           There is nothing to starve, so the check runs live -- which is what makes
        #           it a real per-takeoff proof rather than a cached one.
        #
        #   MOVE arrives in HOVER, where the stream is the only thing holding the aircraft in
        #        offboard. It reuses the verdict TAKEOFF established, with no network call.
        #        That is not a weakening: the question is "is a simulator present", which does
        #        not change between a takeoff and a nudge thirty seconds later -- and refusing
        #        to move an aircraft because the simulator vanished would achieve nothing,
        #        since there would be no aircraft to move.
        if cmd in manual_policy.REQUIRES_INTERLOCK:
            if self.state is State.IDLE:
                ok, why = simulator_present()
                self.manual_ready = ok
            else:
                ok, why = self.manual_ready, "verdict from the last take-off (not re-dialled "
                why += "in flight: a blocking RPC here would stall the setpoint stream)"
            if not ok:
                self.get_logger().error(f"REFUSED {cmd}: SITL interlock not satisfied -- {why}")
                return

        if cmd == MissionCommand.COMMAND_HOLD:
            # A no-op by construction: HOVER already holds `target_enu` and already streams.
            # It exists so the page has a way to say "stay" that is not "do nothing", and so
            # a future translate/yaw mode has an obvious place to land.
            self.get_logger().info("hold: already holding")
            return

        if cmd == manual_policy.COMMAND_MOVE:
            self._do_move(msg)
            return

        if cmd == MissionCommand.COMMAND_LAND:
            self.get_logger().info(f"land commanded from {self.state.value}")
            self._enter(State.LAND)
            return

        # TAKEOFF. A zero or negative altitude means "the node's parameter", so a caller that
        # only wants the default does not have to know what it is (MissionCommand.msg).
        # THE POSITION CHECK BEFORE THE MUTATION. Assigning self.alt first meant a takeoff
        # REFUSED for "no position yet" still permanently changed the node's altitude, so a
        # later takeoff sent with altitude_m = 0 ("use the node's parameter") climbed to the
        # altitude of the command that was rejected. (review)
        cur = self._position_enu()
        if cur is None:
            self.get_logger().error(
                "REFUSED takeoff: no vehicle_local_position yet -- the aircraft does not "
                "know where it is")
            return
        alt = float(msg.altitude_m)
        if alt > 0.0:
            self.alt = alt
        # Home is re-read at every takeoff rather than kept from start-up. The vehicle may
        # have been landed somewhere else, or moved; a stale home would fly it back to a
        # position nobody asked for.
        self.home_enu = (cur[0], cur[1])
        self.target_enu = (cur[0], cur[1], cur[2])
        # Reset per sortie, so a second take-off does not inherit the heading the last one
        # was left yawed to. 0.0 is what every non-manual setpoint carries.        (SIM-47)
        self.target_yaw_enu = 0.0
        self.get_logger().info(
            f"takeoff commanded to {self.alt} m from ENU=({cur[0]:.2f}, {cur[1]:.2f})")
        self._enter(State.STREAM_SETPOINTS)

    def _do_move(self, msg: MissionCommand) -> None:
        """Nudge the hold point, inside the envelope.                             (SIM-47)

        The aircraft is already holding `target_enu` and `_tick` is already streaming it at
        `setpoint_rate_hz`, so moving is moving that target. There is no separate flight mode
        and no second control path -- which is why MOVE needed no new state.
        """
        cur = self._position_enu()
        if cur is None:
            self.get_logger().error("REFUSED move: no vehicle_local_position")
            return

        # BODY -> WORLD, in the one function that owns rotations (conventions §3). The
        # vehicle's heading is NED out of PX4; frames.py converts it, here and nowhere else.
        yaw_now = yaw_ned_to_enu(self.position.heading) if self.position else self.target_yaw_enu
        delta_enu = flu_to_enu(float(msg.forward_m), float(msg.left_m), float(msg.up_m), yaw_now)

        # THE HOLD POINT MOVES, NOT THE VEHICLE'S PRESENT POSITION. Nudging from where the
        # aircraft happens to be right now would let a burst of commands accumulate the
        # tracking error -- each one measured from a position that had not finished arriving
        # at the last target. Measuring from the target keeps a held key linear.
        target, notes = manual_policy.clamp_move(
            self.target_enu, self.home_enu or (0.0, 0.0), delta_enu, self.move_limits)

        dyaw, yaw_note = manual_policy.clamp_yaw_step(
            float(msg.yaw_delta_rad), self.move_limits)
        if dyaw:
            self.target_yaw_enu = _wrap_pi(self.target_yaw_enu + dyaw)
        if yaw_note:
            notes.append(yaw_note)

        self.target_enu = target
        if notes:
            # EVERY CLAMP IS SAID OUT LOUD. A fence the operator cannot feel is a fence they
            # will keep pushing against while wondering why the aircraft stopped responding.
            self.get_logger().warning("move clamped: " + "; ".join(notes))
        self.get_logger().info(
            f"move -> ENU=({target[0]:.1f}, {target[1]:.1f}, {target[2]:.1f}) "
            f"yaw={math.degrees(self.target_yaw_enu):.0f} deg")

    def _do_idle(self) -> None:
        """On the ground, disarmed, waiting. Deliberately empty.                  (SIM-45)

        No timeout (UNTIMED_STATES), no setpoints (see _tick), no polling. The only way out
        is a command, and commands are applied in _tick before the handler runs. A handler
        that did anything here would be doing it forever."""

    def _do_hover(self) -> None:
        """Holding the takeoff setpoint, waiting.                                 (SIM-45)

        Also empty, and for a reason worth stating: the hold is performed by _tick, which
        publishes `target_enu` on every tick of every flying state. PX4 drops out of offboard
        after COM_OF_LOSS_T (1.0 s on v1.16.0) without that stream, so the hover is the
        stream -- there is nothing left for a handler to do."""

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
            if self.manual:
                # HOVER holds exactly the setpoint TAKEOFF was already flying to, so there is
                # nothing to set -- and nothing that could shift the aircraft at the moment of
                # handover. WAYPOINTS is never entered in manual mode.
                self._enter(State.HOVER)
                return
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
            "square_side_m": None if (self.scenario_wps or self.manual) else self.side,
            # "manual" wins over both. A hand-flown sortie has no mission at all, and
            # reporting "built-in-square" for one would put a square that was never flown
            # into a bag that outlives everyone's memory of the session.       (SIM-45)
            "mission_source": ("manual" if self.manual
                               else "scenario" if self.scenario_wps else "built-in-square"),
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
        # the HOST-side transport: run_scenario.py drives the bring-up script and has no ROS
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
