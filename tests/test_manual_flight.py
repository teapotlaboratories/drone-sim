"""The hand-flying policy, the SITL interlock, and the rosbridge allowlist.       (SIM-45)

Off-target: no simulator, no ROS 2 runtime. This file exists because `SIM-45` puts a button
that ARMS AN AIRCRAFT in a browser, and the rules that keep that safe are the kind that fail
open -- a glob left unset, a state name that drifted, a refusal that returns "" instead of
stopping. None of those show up as a crash.

Unlike `tests/test_park_tour.py`, this does NOT re-express the logic it checks. The policy
lives in `control/manual_policy.py`, which imports nothing, and `control/sitl_interlock.py`
imports only the standard library plus msgpack -- both are imported here directly, so the
thing asserted is the thing that runs.
"""
import importlib.util
import math
import socket
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONTROL = REPO / "ros2_ws" / "src" / "control" / "control"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


policy = _load("manual_policy", CONTROL / "manual_policy.py")
interlock = _load("sitl_interlock", CONTROL / "sitl_interlock.py")


# --- the policy ------------------------------------------------------------------------

def test_takeoff_is_reachable_from_exactly_one_state():
    """Arming from anywhere else means arming an aircraft that is already flying, or one
    whose state the operator cannot see. IDLE is the only state in which the vehicle is
    known to be on the ground."""
    assert policy.ALLOWED_FROM[policy.COMMAND_TAKEOFF] == frozenset({policy.IDLE})


def test_land_is_reachable_from_the_whole_climb():
    """SIM-44 is the case: a drone rising into a tree. An operator watching it must not have
    to wait for it to finish arriving before being allowed to stop it."""
    land_from = policy.ALLOWED_FROM[policy.COMMAND_LAND]
    for state in (policy.HOVER, policy.TAKEOFF, policy.STREAM_SETPOINTS,
                  policy.REQUEST_OFFBOARD, policy.ARM):
        assert state in land_from, f"LAND must be allowed from {state}"


def test_land_is_never_harder_to_reach_than_takeoff():
    """A standing property, not an example: any state that can arm must also be able to
    stop. Written as a comparison so a future state added to TAKEOFF's set cannot quietly
    become one the operator has no way out of."""
    assert (policy.ALLOWED_FROM[policy.COMMAND_TAKEOFF]
            <= policy.ALLOWED_FROM[policy.COMMAND_LAND] | {policy.IDLE})


def test_energy_adding_commands_require_the_interlock():
    """TAKEOFF and MOVE are gated; LAND and HOLD are NOT, and the asymmetry is the whole rule:
    refusing a command must never be the dangerous option.

    Refusing to MOVE leaves the aircraft holding station, which is safe. Refusing to LAND
    leaves it airborne, which is not -- a safety check that causes the accident it exists to
    prevent."""
    assert policy.REQUIRES_INTERLOCK == frozenset({policy.COMMAND_TAKEOFF, policy.COMMAND_MOVE})
    assert policy.COMMAND_LAND not in policy.REQUIRES_INTERLOCK
    assert policy.COMMAND_HOLD not in policy.REQUIRES_INTERLOCK


def test_untimed_states_are_only_the_two_that_wait_on_a_human():
    """Every other state keeps the timeout that stops a controller hanging a CI job for its
    whole budget -- the property `offboard_control`'s docstring opens with."""
    assert policy.UNTIMED == frozenset({policy.IDLE, policy.HOVER})


# --- the policy agrees with the message definition ---------------------------------------

def _msg_constants(path):
    """Parse `string NAME = value` out of a .msg. ROS strips trailing whitespace and does
    NOT quote string constants, which is why the value is taken verbatim after '='."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("string ") and "=" in line:
            decl, value = line.split("=", 1)
            out[decl.split()[1]] = value.strip()
    return out


def test_policy_strings_match_missioncommand_msg():
    """The one join `manual_policy` cannot make itself without importing a generated message.

    `offboard_control` asserts the same equality at import, so a mismatch is a start-up crash
    rather than a command the node silently calls unknown -- but that assertion only runs
    with a ROS 2 environment, and this one runs in CI."""
    consts = _msg_constants(REPO / "ros2_ws/src/interfaces/msg/MissionCommand.msg")
    assert consts["COMMAND_TAKEOFF"] == policy.COMMAND_TAKEOFF
    assert consts["COMMAND_LAND"] == policy.COMMAND_LAND
    assert consts["COMMAND_HOLD"] == policy.COMMAND_HOLD
    assert consts["COMMAND_MOVE"] == policy.COMMAND_MOVE
    assert set(policy.ALLOWED_FROM) == {consts["COMMAND_TAKEOFF"], consts["COMMAND_LAND"],
                                        consts["COMMAND_HOLD"], consts["COMMAND_MOVE"]}, \
        "a command exists in one file and not the other"


def test_missionstatus_carries_the_two_new_states():
    """Appended as 9 and 10, never inserted: the numbers ARE the contract, and renumbering
    the original eight would silently rewrite the meaning of every recorded run."""
    text = (REPO / "ros2_ws/src/interfaces/msg/MissionStatus.msg").read_text()
    assert "uint8 STATE_IDLE             = 9" in text
    assert "uint8 STATE_HOVER            = 10" in text
    assert "uint8 STATE_FAILED           = 8" in text, "the original numbering moved"


def test_offboard_control_maps_every_state_it_defines():
    """`STATE_TO_MSG` fails loudly on a state without a constant, per its own comment. Read
    as text because importing the node needs rclpy and px4_msgs."""
    src = (CONTROL / "offboard_control.py").read_text()
    states = set()
    in_enum = False
    for line in src.splitlines():
        if line.startswith("class State("):
            in_enum = True
            continue
        if in_enum:
            if line and not line.startswith((" ", "\t")):
                break
            if "=" in line and '"' in line:
                states.add(line.split("=")[0].strip())
    assert {"IDLE", "HOVER"} <= states
    for name in states:
        assert f"State.{name}:" in src, f"State.{name} has no MissionStatus constant"


# --- the interlock -----------------------------------------------------------------------

def test_interlock_refuses_when_nothing_is_listening():
    """A closed port is the real-hardware case, and it must fail CLOSED."""
    # Port 1 is reserved and never bound; any refusal is the answer we want.
    ok, why = interlock.simulator_present(host="127.0.0.1", port=1, timeout=0.5)
    assert ok is False
    assert why, "a refusal must carry a reason the operator can read"


def test_interlock_refuses_a_server_that_answers_but_is_not_airsim():
    """FAILING OPEN IS THE WHOLE RISK. Something is listening on the port -- an SSH forward,
    another service, a half-started process -- and answers bytes that are not a msgpack-RPC
    reply. That must be a refusal, not a permission."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\nnot msgpack at all")
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ok, why = interlock.simulator_present(host="127.0.0.1", port=port, timeout=1.0)
    srv.close()
    assert ok is False, "a non-AirSim server must not satisfy the interlock"
    assert why


def test_interlock_accepts_a_wellformed_airsim_reply():
    """The positive case, so the test above is proving discrimination rather than that the
    function always says no."""
    msgpack = pytest.importorskip("msgpack",
                                  reason="python3-msgpack is in the ros2 image, not always here")
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        # [1, msgid, error, result] -- msgid 1, no error, a version number.
        conn.sendall(msgpack.packb([1, 1, None, 1], use_bin_type=True))
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    ok, why = interlock.simulator_present(host="127.0.0.1", port=port, timeout=2.0)
    srv.close()
    assert ok is True, why
    assert "version" in why


def test_interlock_never_raises():
    """It is called from a timer callback. An exception escaping there takes the node down
    without publishing a result -- the failure mode offboard_control documents twice."""
    for host, port in (("no.such.host.invalid", 41451), ("127.0.0.1", 1), ("", 0)):
        ok, why = interlock.simulator_present(host=host, port=port, timeout=0.5)
        assert ok is False and isinstance(why, str)


# --- the rosbridge allowlist -------------------------------------------------------------

LAUNCH = REPO / "ros2_ws/src/webui/launch/webui.launch.py"
# IMPORTED, NOT RE-EXPRESSED. `webui.launch.py` cannot be imported without a ROS 2
# environment, so the lists live in a module that imports nothing and both sides read the
# same object -- see allowlist.py's docstring.
allow = _load("allowlist", REPO / "ros2_ws/src/webui/webui/allowlist.py")


def test_only_mission_command_is_publishable():
    """THE SAFETY BOUNDARY. rosbridge is general by design; this list is what makes it
    narrow. If `/fmu/in/*` ever appears here, a browser can arm and disarm directly."""
    assert allow.TOPICS_PUB == ["/mission/command"]
    assert not any(t.startswith("/fmu/in") for t in allow.TOPICS_PUB)


def test_no_publish_entry_is_a_wildcard():
    """A `*` in the publish list would re-open everything the list exists to close, and would
    still read as an allowlist at a glance."""
    for topic in allow.TOPICS_PUB:
        assert "*" not in topic and "?" not in topic, f"{topic} is a pattern, not a topic"


def test_glob_format_is_the_string_rosbridge_actually_parses():
    """rosbridge declares these parameters as `str` and parses them with `parse_glob_string`,
    which expects a list LITERAL inside a string. A real list here is a type error at best
    and a silently unfiltered bridge at worst."""
    rendered = allow.render(["/a/*", "/b"])
    assert rendered == "['/a/*', '/b']"
    assert rendered.startswith("[") and rendered.endswith("]")
    # And it must never render as the empty string, which rosbridge reads as "no checking".
    assert allow.render(["/mission/command"]) != ""


def test_read_allowlist_is_enumerated_not_a_bare_wildcard():
    """Reading cannot move the aircraft, so this list is wide -- but a bare `*` would mean a
    command channel added later is exposed to a browser automatically."""
    assert "*" not in allow.TOPICS_SUB
    assert "/fmu/out/*" in allow.TOPICS_SUB


def test_services_and_actions_are_closed():
    """An empty allowlist, not an absent one. Without this the page could call any node's
    set_parameters and retune takeoff_altitude mid-flight."""
    assert allow.SERVICES_GLOB == "[]"
    assert allow.ACTIONS_GLOB == "[]"
    # And that the launch file actually passes them, rather than defining them unused.
    src = LAUNCH.read_text()
    assert "allowlist.SERVICES_GLOB" in src and "allowlist.ACTIONS_GLOB" in src


def test_bind_address_defaults_to_loopback():
    """Under NET_MODE=host the container's namespace IS the host's, so this default is the
    only thing keeping an arm button off every interface the machine has."""
    src = LAUNCH.read_text()
    assert '"bind_address", default_value="127.0.0.1"' in src


def test_launch_file_uses_the_shared_allowlist():
    """Guards the whole arrangement: if the launch file ever inlines its own lists again,
    every assertion above would keep passing while describing a module nothing reads."""
    src = LAUNCH.read_text()
    assert "from webui import allowlist" in src
    assert "allowlist.render(allowlist.TOPICS_PUB)" in src
    assert "allowlist.render(allowlist.TOPICS_SUB)" in src
    assert "TOPICS_PUB = [" not in src, "the launch file has its own copy of the allowlist"


# --- MOVE, and the leash ---------------------------------------------------------- (SIM-47)

def test_move_is_reachable_only_from_hover():
    """So it is reachable only through a TAKEOFF that already satisfied the interlock."""
    assert policy.ALLOWED_FROM[policy.COMMAND_MOVE] == frozenset({policy.HOVER})


def test_an_unclamped_move_is_left_alone():
    """The envelope must be invisible until it bites; a fence that nudges every command would
    make the aircraft feel broken."""
    target, notes = policy.clamp_move((0, 0, 10), (0, 0), (3.0, 0.0, 0.0))
    assert target == pytest.approx((3.0, 0.0, 10.0))
    assert notes == []


def test_one_command_cannot_teleport_the_hold_point():
    """THE ORDER MATTERS: the step cap runs BEFORE the radius check, so a 10 km delta is cut to
    the step first rather than being 'clamped' to a legal-but-wildly-unintended fence point."""
    target, notes = policy.clamp_move((0, 0, 10), (0, 0), (10000.0, 0.0, 0.0))
    assert target[0] == pytest.approx(policy.DEFAULT_LIMITS["step_max_m"])
    assert any("step" in n for n in notes)


def test_the_step_cap_preserves_direction():
    """A diagonal nudge must stay diagonal. Clamping x and y independently would rotate the
    commanded direction, so the aircraft would drift somewhere the operator did not point."""
    target, _ = policy.clamp_move((0, 0, 10), (0, 0), (100.0, 100.0, 0.0))
    assert target[0] == pytest.approx(target[1])           # still 45 degrees
    assert math.hypot(*target[:2]) == pytest.approx(policy.DEFAULT_LIMITS["step_max_m"])


def test_the_radius_fence_holds_at_the_boundary():
    r = policy.DEFAULT_LIMITS["radius_max_m"]
    target, notes = policy.clamp_move((r - 1.0, 0, 10), (0, 0), (5.0, 0.0, 0.0))
    assert math.hypot(target[0], target[1]) == pytest.approx(r)
    assert any("radius" in n for n in notes)


def test_the_radius_is_measured_from_home_not_from_the_origin():
    """Home is where the aircraft took off, which is not the world origin in any real scenario
    -- citysample-gate1 spawns at (-8, -8)."""
    home = (-8.0, -8.0)
    r = policy.DEFAULT_LIMITS["radius_max_m"]
    target, _ = policy.clamp_move((home[0] + r - 1.0, home[1], 10), home, (5.0, 0.0, 0.0))
    assert math.hypot(target[0] - home[0], target[1] - home[1]) == pytest.approx(r)


def test_the_altitude_floor_stops_a_descent_into_the_ground():
    target, notes = policy.clamp_move((0, 0, 3.0), (0, 0), (0.0, 0.0, -5.0))
    assert target[2] == pytest.approx(policy.DEFAULT_LIMITS["alt_min_m"])
    assert any("floor" in n for n in notes)


def test_the_altitude_ceiling_stops_a_climb_out_of_sight():
    hi = policy.DEFAULT_LIMITS["alt_max_m"]
    target, notes = policy.clamp_move((0, 0, hi - 1.0), (0, 0), (0.0, 0.0, 5.0))
    assert target[2] == pytest.approx(hi)
    assert any("ceiling" in n for n in notes)


def test_the_fence_is_never_a_silent_clamp():
    """Every bound that bites produces a note. A fence the operator cannot feel is one they
    keep pushing against while wondering why the aircraft stopped responding."""
    for delta, current in (((999.0, 0, 0), (0, 0, 10)),
                           ((0, 0, -999.0), (0, 0, 10)),
                           ((0, 0, 999.0), (0, 0, 10))):
        _, notes = policy.clamp_move(current, (0, 0), delta)
        assert notes, f"{delta} was clamped silently"


def test_limits_are_overridable_but_default_when_absent():
    """offboard_control passes ROS parameters through; a None must not blow away a default."""
    target, _ = policy.clamp_move((0, 0, 10), (0, 0), (100.0, 0, 0), {"step_max_m": 20.0})
    assert target[0] == pytest.approx(20.0)
    target, _ = policy.clamp_move((0, 0, 10), (0, 0), (100.0, 0, 0), {"step_max_m": None})
    assert target[0] == pytest.approx(policy.DEFAULT_LIMITS["step_max_m"])


def test_yaw_step_is_capped_both_ways():
    cap = policy.DEFAULT_LIMITS["yaw_step_max_rad"]
    d, note = policy.clamp_yaw_step(math.pi)
    assert d == pytest.approx(cap) and note
    d, note = policy.clamp_yaw_step(-math.pi)
    assert d == pytest.approx(-cap) and note
    d, note = policy.clamp_yaw_step(0.1)
    assert d == pytest.approx(0.1) and note is None


# --- the body -> world rotation ----------------------------------------------------------

frames = _load("frames", CONTROL / "frames.py")


def test_forward_is_east_at_zero_yaw():
    """ENU yaw is counter-clockwise FROM EAST, so yaw 0 faces east. Getting this backwards
    gives an aircraft that flies 90 degrees off every command and looks plausible doing it."""
    assert frames.flu_to_enu(1, 0, 0, 0.0) == pytest.approx((1.0, 0.0, 0.0))


def test_left_is_north_at_zero_yaw():
    assert frames.flu_to_enu(0, 1, 0, 0.0) == pytest.approx((0.0, 1.0, 0.0))


def test_facing_north_forward_is_north():
    assert frames.flu_to_enu(1, 0, 0, math.pi / 2) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)


def test_facing_north_left_is_west():
    """THE SIGN THAT MATTERS. An FRD convention would put 'left' to the east here, and the
    aircraft would strafe the wrong way on every press."""
    assert frames.flu_to_enu(0, 1, 0, math.pi / 2) == pytest.approx((-1.0, 0.0, 0.0), abs=1e-9)


def test_up_is_untouched_by_yaw():
    for yaw in (0.0, 1.0, math.pi, -2.5):
        assert frames.flu_to_enu(0, 0, 3.0, yaw)[2] == pytest.approx(3.0)


def test_rotation_preserves_length():
    for yaw in (0.0, 0.7, math.pi / 3, -1.9):
        e, n, _ = frames.flu_to_enu(2.0, -3.0, 0.0, yaw)
        assert math.hypot(e, n) == pytest.approx(math.hypot(2.0, -3.0))


# --- fixes from the /review pass -------------------------------------------------------

def test_a_pure_yaw_nudge_does_not_command_a_descent():
    """THE FENCE MUST BE A FENCE, NOT A MAGNET.

    The first version clamped the ABSOLUTE target on every MOVE, so hovering at 100 m (the page
    allows a take-off to 120) a yaw-only nudge — delta (0,0,0) — hit the 60 m ceiling and
    commanded a 40 m descent from one keypress. A fence built to keep the aircraft safe was
    itself a way to command a dive."""
    target, notes = policy.clamp_move((0, 0, 100.0), (0, 0), (0.0, 0.0, 0.0))
    assert target[2] == pytest.approx(100.0), "a yaw-only nudge moved the aircraft vertically"
    assert notes == []


def test_the_band_still_stops_you_going_further_out():
    """Widening the band to include where the aircraft already is must not disable it."""
    up, notes = policy.clamp_move((0, 0, 100.0), (0, 0), (0.0, 0.0, 5.0))
    assert up[2] == pytest.approx(100.0) and notes, "climbing above an over-ceiling hover"
    down, _ = policy.clamp_move((0, 0, 100.0), (0, 0), (0.0, 0.0, -5.0))
    assert down[2] == pytest.approx(95.0), "descending back toward the band must work"


def test_the_real_ceiling_applies_once_inside_the_band():
    target, notes = policy.clamp_move((0, 0, 58.0), (0, 0), (0.0, 0.0, 5.0))
    assert target[2] == pytest.approx(policy.DEFAULT_LIMITS["alt_max_m"])
    assert any("ceiling" in n for n in notes)


def test_the_interlock_never_blocks_while_setpoints_are_streaming():
    """`_apply_command` runs on the timer thread — the same one as `_publish_setpoint`.
    `simulator_present()` blocks for up to its socket timeout, and PX4 drops out of offboard
    after COM_OF_LOSS_T = 1.0 s. So the live check is allowed only from IDLE, where _tick
    deliberately publishes no setpoints; from HOVER the verdict is reused."""
    src = (CONTROL / "offboard_control.py").read_text()
    body = src[src.index("    def _apply_command(self)"):]
    body = body[:body.index("\n    def ")]
    # The CALL SITE, not the word: the comment above it explains the hazard and names the
    # function, so a bare index() finds the prose and passes for the wrong reason.
    live = body.index("ok, why = simulator_present()")
    guard = body.index("if self.state is State.IDLE:")
    assert guard < live, "the live interlock call is not gated on IDLE"
    # And the flying branch must not dial anything at all.
    flying = body[body.index("else:", guard):body.index("if not ok:", guard)]
    assert "simulator_present" not in flying, "the HOVER path still makes a blocking call"


def test_the_state_check_runs_before_the_interlock():
    """The state check is free and the interlock can cost seconds; checking the interlock
    first made even a command that would be refused pay the full stall."""
    src = (CONTROL / "offboard_control.py").read_text()
    body = src[src.index("    def _apply_command(self)"):]
    body = body[:body.index("\n    def ")]
    assert body.index("if self.state not in allowed") < body.index("REQUIRES_INTERLOCK")


def test_a_refused_takeoff_does_not_change_the_altitude():
    """`self.alt = alt` ran before the position check, so a takeoff refused for "no position
    yet" permanently changed the node's altitude — and a later `altitude_m = 0` ("use the
    parameter") then climbed to the rejected command's height."""
    src = (CONTROL / "offboard_control.py").read_text()
    body = src[src.index("        # TAKEOFF."):]
    body = body[:body.index("self._enter(State.STREAM_SETPOINTS)")]
    assert body.index("if cur is None:") < body.index("self.alt = alt")
