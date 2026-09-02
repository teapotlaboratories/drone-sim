"""What the browser may write, and what it may read.                            (SIM-45)

THE SAFETY BOUNDARY OF THE WHOLE FEATURE, AND IT IMPORTS NOTHING. `webui.launch.py` cannot
be imported without a ROS 2 environment (`ament_index_python`, `launch_ros`), so a test that
read the lists from there could only ever pass on this machine -- and CI runs off-target.
The rule this project already learned once: a test that needs the target can only ever pass
on the target, so the thing worth asserting must not live behind that import.

So the lists live here, the launch file imports them, and `tests/test_manual_flight.py`
imports the same module. The thing asserted is the thing that runs.

WHY ANY OF THIS IS NEEDED. `rosbridge_suite` exists to expose a whole ROS graph to a
browser. That is the right tool by this project's reuse rule and exactly the wrong default
for a page with a TAKE OFF button on it -- see hard stop 1 in CLAUDE.md. Three properties of
rosbridge 2.7.0, all read out of its source rather than its documentation, decide how these
lists have to be written:

  1. The glob parameters are STRINGS CONTAINING A LIST LITERAL, not string arrays --
     `rosbridge_websocket.py` declares them `str` and parses with `parse_glob_string`. Hence
     `render()` below rather than passing a list.
  2. AN UNSET GLOB MEANS NO CHECKING AT ALL: `parse_glob_string("")` returns None, and every
     capability treats None as "do not check". Security is opt-in, so a missing glob opens
     the bridge rather than closing it.
  3. `topics_pub_glob` and `topics_sub_glob` are INDEPENDENT. The read set has to be wide
     (all of `/fmu/out`) while the write set must be exactly one topic, so the legacy
     single `topics_glob` cannot express this.

Enforcement point: `rosbridge_library/capabilities/publish.py` checks `topics_pub_glob` and
returns BEFORE creating the topic registration, so a publish to a non-matching topic never
reaches the graph at all.
"""

# --- THE WRITE ALLOWLIST. One topic. -------------------------------------------------
#
# `/mission/command` is subscribed ONLY by offboard_control with manual:=true, so even this
# single permission is inert during a gate run. Everything else a browser might want to
# write -- `/fmu/in/vehicle_command` above all, which is a bare arm/disarm/reboot channel --
# is unreachable.
#
# NO PATTERNS HERE, EVER. A `*` would re-open everything this list exists to close while
# still reading as an allowlist at a glance.
TOPICS_PUB = ["/mission/command"]

# --- THE READ ALLOWLIST. -------------------------------------------------------------
#
# Wide on purpose: reading cannot move the aircraft, and showing what the graph already
# carries is the entire point of the page. Still ENUMERATED rather than left as `*`, so that
# a topic added later which is NOT safe to expose -- a command channel someone introduces in
# six months -- is opt-in rather than automatically published to a browser.
TOPICS_SUB = [
    "/fmu/out/*",          # PX4's own telemetry, byte-identical to a real Pixhawk's
    "/mission/status",     # what the controller believes it is doing
    "/mission/result",     # the outcome of the last sortie
    "/chase/*",            # the chase camera, published by chase_camera.py
    "/depth_view/*",       # the depth camera, colourised by depth_view (SIM-48)
    "/airsim_node/*",      # vehicle cameras and sensors, when perception is up
    "/clock",              # so a page can tell sim time from wall time
    "/rosout",             # node logs, so a refusal is visible in the browser
]

# Services and actions are closed OUTRIGHT. Without this the page could call any node's
# `set_parameters` and retune `takeoff_altitude` mid-flight. "[]" is an EMPTY ALLOWLIST,
# which is not the same as "" (no checking) -- see property 2 above. rosbridge appends
# '/rosapi/*' to a non-None services glob by itself, so roslibjs can still resolve topic
# types.
SERVICES_GLOB = "[]"
ACTIONS_GLOB = "[]"


def render(entries) -> str:
    """Format a list for rosbridge's glob parameters. See property 1.

    Never returns "" for a non-empty list, because "" is how rosbridge spells "no checking"
    -- the one output that would silently disable the boundary this module exists to draw.
    """
    return "[" + ", ".join(f"'{e}'" for e in entries) + "]"
