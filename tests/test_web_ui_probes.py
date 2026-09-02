"""Regression guards for the seven defects the first live bring-up of `SIM-45` exposed.

Off-target: no simulator, no ROS 2 runtime. Not every one of those seven can be checked here
-- "PX4 advertises /fmu/out/vehicle_gps_position" is a claim about the target and only the
target can answer it. What CAN be pinned is pinned, and each test names the failure it is
standing in for, because none of these look like bugs when you read the code that has them.

Worklog: docs/worklog/2026-09-01-sim45-a-web-interface-over-ros-2.md
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB_UI = REPO / "scripts/web_ui.sh"
SIM_UP = REPO / "scripts/sim_up.sh"
CHASE = REPO / "ros2_ws/src/chase_camera/chase_camera/chase_camera.py"
APP_JS = REPO / "ros2_ws/src/webui/webui/static/app.js"
DOCKERFILE = REPO / "docker/ros2.Dockerfile"
GCS_DOCKERFILE = REPO / "docker/webui.Dockerfile"


# --- 1. the ldd assertion needs ROS on LD_LIBRARY_PATH --------------------------------

def test_ldd_assertions_source_ros_first():
    """A Dockerfile RUN gets `bash -o pipefail -c`, NOT a login shell, so /etc/profile.d
    never runs. Without sourcing, `ldd` reports three ROS libraries "not found" for a
    perfectly healthy binary and the build fails for a reason unrelated to the package.

    Checked as an ordering property: every `ldd` must have a `source .../setup.bash` before
    it somewhere in the file."""
    for path in (DOCKERFILE, GCS_DOCKERFILE):
        src = path.read_text()
        # COMMAND lines only. The first version matched the word "ldd" inside the comment
        # explaining this very trap, which is a test that fails on its own documentation.
        for m in re.finditer(r"^(?!\s*#).*\bldd\s", src, re.M):
            before = src[:m.start()]
            assert "source /opt/ros/${ROS_DISTRO}/setup.bash" in before, (
                f"{path.name}: an ldd assertion appears before any `source setup.bash`; it "
                "will report ROS libraries as 'not found' on a healthy binary")


# --- 2. ament_python console scripts need setup.cfg -----------------------------------

def test_every_ament_python_package_has_a_script_redirect():
    """`colcon build` reports SUCCESS and installs console scripts to `bin/` instead of
    `lib/<pkg>/`, and the failure only appears later as

        package 'webui' found at '...', but libexec directory '.../lib/webui' does not exist

    which is a launch-time error, not a build-time one."""
    for setup_py in (REPO / "ros2_ws/src").rglob("setup.py"):
        pkg_dir = setup_py.parent
        if "console_scripts" not in setup_py.read_text():
            continue
        cfg = pkg_dir / "setup.cfg"
        assert cfg.exists(), f"{pkg_dir.name} declares console_scripts but has no setup.cfg"
        text = cfg.read_text()
        assert f"install_scripts=$base/lib/{pkg_dir.name}" in text, (
            f"{pkg_dir.name}/setup.cfg does not redirect install_scripts to lib/{pkg_dir.name}")


# --- 3. pgrep patterns must not match the sim-ros2 entrypoint -------------------------

def _entrypoint_text():
    """The exact `bash -lc '...'` sim_up.sh hands to sim-ros2 -- the command line `pgrep -f`
    actually sees on PID 44.

    PRECISE, NOT A SUPERSET. The first version of this test took the whole of sim_up.sh, and
    it failed on the string `webui.launch.py` appearing in a comment inside `start_sim` --
    the RENDERER's launch, which never reaches this command line. A safety test that cries
    wolf about an unrelated comment gets weakened or deleted, so it is worth the extra ten
    lines to slice the real thing."""
    src = SIM_UP.read_text()
    start = src.index('docker run -d --name sim-ros2')
    end = src.index("exec sleep infinity'", start)
    return src[start:end]


def test_pgrep_patterns_cannot_match_the_entrypoint():
    """THE BUG: `pgrep -f offboard_control` matched PID 44 -- the sim-ros2 entrypoint, whose
    text contains `offboard_control` in a comment AND in its `[ -x .../offboard_control ]`
    build assertion. `web_ui.sh start` then refused to start a controller on a stack where
    none had ever run.

    CLAUDE.md already warns about two neighbours of this (pgrep -f matching the asking shell;
    pgrep -x seeing nothing for names over 15 chars). This is the third: a liveness check
    that reads a comment as a process."""
    src = WEB_UI.read_text()
    entry = _entrypoint_text()
    patterns = set(re.findall(r'proc_up "([^"]+)"', src))
    patterns |= set(re.findall(r'pkill -\w+ -f "([^"]+)"', src))
    patterns |= set(re.findall(r'for pat in ([^;]+); do', src))
    flat = set()
    for p in patterns:
        flat.update(w.strip('"') for w in p.split())
    assert flat, "no pgrep/pkill patterns found -- has the script been restructured?"
    for pat in sorted(flat):
        if pat.startswith("$"):
            continue
        assert pat not in entry, (
            f"pgrep/pkill pattern {pat!r} appears in scripts/sim_up.sh, so it can match the "
            f"sim-ros2 entrypoint's own command line and report a comment as a process")


def test_node_liveness_is_asked_of_the_graph():
    """`offboard_control` in particular must never go back to pgrep: it is 16 characters, so
    `pgrep -x` cannot see it either, and it appears in the entrypoint text."""
    src = WEB_UI.read_text()
    assert "ros2 node list" in src, "node liveness must be asked of the graph"
    assert 'proc_up "offboard_control"' not in src
    assert 'node_up "/offboard_control"' in src


def test_liveness_check_ignores_zombies():
    """Everything is started with `docker exec -d`, whose shell exits without waiting, so an
    exited node lingers as <defunct> and pgrep matches it. `stop` reported a clean shutdown as
    a stubborn one and signalled a corpse."""
    src = WEB_UI.read_text()
    assert "ps -eo stat=" in src and 'grep -v "^Z"' in src, (
        "proc_up must filter zombie processes")


# --- 4. the chase camera's QoS is decided by its consumer -----------------------------

def test_chase_camera_publishes_reliable():
    """MEASURED: with a BEST_EFFORT publisher the topic ran at 9.99 Hz and web_video_server's
    HTTP stream returned 22 bytes -- the multipart boundary and not one frame. image_transport
    subscribes RELIABLE, and a RELIABLE subscriber matches NOTHING from a BEST_EFFORT
    publisher.

    This is the mirror of the /fmu/out/* trap in docs/quickstart.md, and neither direction
    produces a warning anywhere: it presents as an empty video pane."""
    src = CHASE.read_text()
    pub = src[src.index("self.pub = self.create_publisher"):]
    pub = pub[:pub.index(")\n\n") if ")\n\n" in pub else 400]
    assert "ReliabilityPolicy.RELIABLE" in pub, (
        "the chase camera must publish RELIABLE or web_video_server receives nothing")
    assert "BEST_EFFORT" not in pub


# --- 5. the GPS topic name ------------------------------------------------------------

def test_page_uses_the_topic_name_not_the_message_type_for_gps():
    """`SensorGps` is the TYPE; PX4 v1.16 advertises it on /fmu/out/vehicle_gps_position.
    Using the type name as the topic name left fix and satellites permanently blank.

    Only the target can prove the right name; this pins the wrong one out."""
    src = APP_JS.read_text()
    assert "/fmu/out/sensor_gps" not in src, (
        "/fmu/out/sensor_gps does not exist on PX4 v1.16 -- the topic is "
        "/fmu/out/vehicle_gps_position, of type px4_msgs/msg/SensorGps")
    assert "/fmu/out/vehicle_gps_position" in src


# --- 6. rclpy only handles SIGINT ------------------------------------------------------

def test_nodes_are_stopped_with_sigint_not_sigterm():
    """rclpy installs a handler for SIGINT only. SIGTERM kills the interpreter outright, so
    ChaseCamera.destroy_node() -- whose job is terminating the ffmpeg it spawned -- never
    runs, and the orphaned grabber holds the renderer's X connection."""
    src = WEB_UI.read_text()
    body = src[src.index("cmd_stop()"):]
    for pat in ("chase_camera", "offboard_control", "rosbridge_websocket"):
        # Two spellings reach the same place: a direct `pkill -INT -f offboard_control`, and
        # a `for pat in ... ; do pkill -INT -f "$pat"` loop that names it in the list.
        direct = re.findall(rf"pkill -(\w+) -f [\"']?[^\"'\n]*{pat}", body)
        via_loop = []
        for lst in re.findall(r"for pat in ([^;]+); do", body):
            if pat in lst:
                m = re.search(r"pkill -(\w+) -f \"\$pat\"", body)
                if m:
                    via_loop.append(m.group(1))
        sigs = direct + via_loop
        assert sigs, f"cmd_stop does not signal {pat}"
        assert sigs[0] == "INT", (
            f"{pat} is first signalled with SIG{sigs[0]}; rclpy only handles SIGINT, so "
            "destroy_node() will not run and its ffmpeg child is orphaned")


def test_stop_waits_for_the_graph_before_reporting():
    """The first version printed "chase camera up" about a process that was already a zombie,
    because `ros2 node list` lagged the exits by several seconds."""
    src = WEB_UI.read_text()
    body = src[src.index("cmd_stop()"):]
    assert "settled" in body, "cmd_stop must wait for the graph to agree before reporting"


# --- 8. the ground station must have no path to the simulator ------------------------- (SIM-46)

def test_webui_package_has_no_python_nodes():
    """The separation is meant to be physical, not a convention. If a node ever lands in the
    ground-station package it will be built into an image with no ffmpeg, no msgpack and no
    AirSim -- so it would fail at run time, in a container, rather than here."""
    pkg = REPO / "ros2_ws/src/webui/webui"
    modules = {p.name for p in pkg.glob("*.py")} - {"__init__.py", "allowlist.py"}
    assert not modules, f"the ground-station package gained Python nodes: {sorted(modules)}"
    # Asserted on the DECLARATION, not on the words: setup.py's comment explains why there
    # are no console scripts, and naming them there must not trip this.
    setup = (REPO / "ros2_ws/src/webui/setup.py").read_text()
    assert "entry_points={}" in setup, (
        "the ground-station package must declare no console_scripts at all")


def test_ground_station_image_asserts_it_cannot_reach_the_simulator():
    """A real aircraft's ground station has no simulator behind it. The image asserts that at
    BUILD time so the claim cannot rot into a comment that used to be true."""
    src = GCS_DOCKERFILE.read_text()
    assert "! python3 -c \"import msgpack\"" in src, "no AirSim RPC client"
    assert "test ! -d /airsim_root" in src, "no vendored Cosys-AirSim tree"
    assert "test ! -e /opt/ros/${ROS_DISTRO}/lib/chase_camera" in src, "no chase camera"
    # NOT ffmpeg: it is a hard Depends of web_video_server and cannot be removed without
    # removing the server. Asserting its absence made the build fail, correctly. The boundary
    # rests on there being no node here that opens a display.
    assert "command -v ffmpeg" not in src, (
        "the ground station cannot assert ffmpeg's absence -- web_video_server Depends on it")


def test_companion_image_no_longer_ships_the_bridge():
    """rosbridge is a GENERAL ROS-to-websocket bridge. Hosting one on the companion computer is
    what hard stop 1 is about, and SIM-46 moved it to the ground station."""
    src = DOCKERFILE.read_text()
    installs = [l for l in src.splitlines()
                if "rosbridge-suite" in l or "web-video-server" in l]
    installs = [l for l in installs if not l.lstrip().startswith("#")]
    assert not installs, f"the companion image still installs the bridge: {installs}"


def test_companion_image_keeps_ffmpeg_for_the_chase_camera():
    """The other half of the same split: the chase camera stays with the simulator, so its
    runtime dependency must stay too."""
    src = DOCKERFILE.read_text()
    assert any("ffmpeg \\" in l or l.strip() == "ffmpeg \\" for l in src.splitlines()) \
        or "        ffmpeg" in src, "the companion image lost ffmpeg; chase_camera needs it"
