#!/usr/bin/env bash
# Start, stop and check the hand-flying web interface.                              (SIM-45)
#
#   ./scripts/web_ui.sh start      # then open http://127.0.0.1:8080
#   ./scripts/web_ui.sh status
#   ./scripts/web_ui.sh stop
#
# SITL ONLY. This puts a page with a TAKE OFF button in a browser. CLAUDE.md hard stop 1
# requires the operator's explicit go-ahead before the real Pixhawk moves, every run, and a
# web page cannot ask for it. The enforcement is NOT in this script -- it is in
# ros2_ws/src/control/control/sitl_interlock.py, checked by the node that actually sends the
# arm command, so `ros2 topic pub` cannot walk around it either. This script only refuses to
# make the situation worse.
#
# IT SPANS TWO CONTAINERS, and the split is the point of SIM-46.
#
#   sim-webui   THE GROUND STATION. rosbridge (:9090), web_video_server (:8181), and the page
#               (:8080). On real hardware this is a laptop, not the companion computer. It has
#               no ffmpeg, no AirSim client and no path to the renderer.
#   sim-ros2    THE COMPANION COMPUTER. offboard_control with manual:=true (the flight code and
#               the SITL interlock), and chase_camera -- which reads the renderer's screen and
#               therefore cannot live on a ground station. A real aircraft has no chase camera.
#
# `./scripts/sim_up.sh --down` stops both: STACK_CONTAINERS in that script is a single list and
# both its teardown and its verifier are built from it.
#
# The chase camera needs `./scripts/sim_up.sh --display`; without it the page shows a message
# saying so rather than a black rectangle. The drone camera needs perception
# (`ros2 launch bringup perception.launch.py`), which sim_up.sh does not start -- `start`
# reports on both rather than pretending.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROS=${ROS:-sim-ros2}          # the companion computer
GCS=${GCS:-sim-webui}         # the ground station
SIM=${SIM:-sim-unreal}
PAGE_PORT=${PAGE_PORT:-8080}
BRIDGE_PORT=${BRIDGE_PORT:-9090}
VIDEO_PORT=${VIDEO_PORT:-8181}
# Must match sim_up.sh's DISPLAY_NUM (default 77). :99 is QGroundControl's -- filming it
# instead of the world is a failure this repo has already had once (sim_up.sh:75).
CHASE_DISPLAY=${CHASE_DISPLAY:-:77}
TAKEOFF_ALT=${TAKEOFF_ALT:-10.0}
LOG=/tmp/webui.log            # in sim-webui
CHASE_LOG=/tmp/webui-chase.log  # in sim-ros2
DEPTH_LOG=/tmp/webui-depth.log  # in sim-ros2
# The colour ramp for the depth pane. FIXED, not auto-scaled: a colour has to mean the
# same distance in every frame or two frames cannot be compared.            (SIM-48)
DEPTH_NEAR_M=${DEPTH_NEAR_M:-0.5}
DEPTH_FAR_M=${DEPTH_FAR_M:-40.0}
CTRL_LOG=/tmp/webui-offboard.log # in sim-ros2

log()  { printf '\033[36m[webui]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[webui] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[webui] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

need_stack() {
  local c
  for c in "$ROS" "$GCS"; do
    docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null | grep -q true \
      || die "'$c' is not running -- bring the stack up first: ./scripts/sim_up.sh --display"
  done
}

# `docker exec` WITHOUT -l bypasses /etc/profile.d and reports 0 topics on a healthy stack.
# The image ships docker/ros-profile.sh for exactly this reason, so every call here is -lc.
rexec() { docker exec "$ROS" bash -lc "$*"; }     # companion computer
gexec() { docker exec "$GCS" bash -lc "$*"; }     # ground station

# WHERE THE SERVERS MAY BIND, decided from the stack's ACTUAL network mode rather than from
# an environment variable this script hopes matches how sim_up.sh was run.
#
#   host   : the container's namespace IS the host's, so 0.0.0.0 would put an arm button on
#            every interface this machine has, the VPN included. Loopback, always.
#   shared : the namespace is private and unreachable except through the loopback -p
#            mappings sim_up.sh publishes on the renderer. The server must bind 0.0.0.0
#            INSIDE that namespace, because docker forwards a published port to the
#            container's address and not to its loopback -- binding 127.0.0.1 there makes
#            the mapping connect-refused, which looks exactly like a server that failed to
#            start.
bind_address() {
  local mode
  mode=$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$SIM" 2>/dev/null || echo unknown)
  case "$mode" in
    host)    echo "127.0.0.1" ;;
    unknown) die "cannot read '$SIM' network mode -- is the stack up?" ;;
    *)       echo "0.0.0.0" ;;
  esac
}

# IS A ROS NODE ALIVE? Asked of the GRAPH, not of the process table.
#
# `pgrep -f offboard_control` DOES NOT WORK HERE, and the way it fails is instructive. The
# sim-ros2 container's entrypoint is one enormous `bash -lc` whose text contains the literal
# string `offboard_control` -- it is in the comment about colcon artifacts and in the
# `[ -x /ros2_ws/install/control/lib/control/offboard_control ]` check. So `pgrep -f` matches
# PID 44 forever, and `web_ui.sh start` refused to start the controller on a stack where no
# controller had ever run.
#
# CLAUDE.md warns about the neighbouring trap -- `pgrep -f` matching the asking shell, and
# `pgrep -x` silently seeing nothing for names over 15 characters (`offboard_control` is 16).
# This is the same family: a liveness check that reads a COMMENT as a process. It fails OPEN
# in the "already running" direction, which is why it presented as a warning rather than a
# crash.
#
# `ros2 node list` is the authority on whether a node is up, it cannot match a comment, and
# it is what the graph itself believes.
# Asked of the graph, from EITHER side. The two containers share a network namespace, so both
# see the whole graph -- which is exactly the property that makes the split honest: the ground
# station reaches the companion's topics the same way a laptop on the same network would.
node_up() {  # node_up(/node_name)
  rexec "ros2 node list 2>/dev/null" | grep -qx "$1"
}

# For the ONE thing here that is not a ROS node -- the static page server. `http.server`
# appears nowhere in the entrypoint text, and this is checked against that text deliberately
# by tests/test_web_ui_probes.py so the trap above cannot come back by a different route.
proc_up() {  # proc_up(pattern) -- on the COMPANION
  # ZOMBIES ARE NOT RUNNING PROCESSES, and pgrep matches them anyway. Everything here is
  # started with `docker exec -d`, whose shell exits without waiting, so an exited node is
  # reparented and lingers as <defunct> until PID 1 reaps it. `stop` then saw chase_camera
  # "still running", escalated to SIGKILL, and signalled a corpse -- harmless, but it reports
  # a clean shutdown as a stubborn one, and this project has been burned twice by liveness
  # checks that describe something other than liveness.
  _proc_up_in "$ROS" "$1"
}

gproc_up() {  # proc_up(pattern) -- on the GROUND STATION
  _proc_up_in "$GCS" "$1"
}

_proc_up_in() {  # _proc_up_in(container, pattern)
  docker exec "$1" bash -c \
    'ps -eo stat=,args= | grep -v "^Z" | grep -v grep | grep -q -- "$1"' _ "$2" 2>/dev/null
}

cmd_start() {
  need_stack

  gproc_up "webui.launch.py" && die "the web interface is already running -- ./scripts/web_ui.sh stop"

  local addr; addr=$(bind_address)
  log "bind address: $addr  (stack network mode: $(docker inspect -f '{{.HostConfig.NetworkMode}}' "$SIM"))"

  # --- the servers -----------------------------------------------------------------
  # `docker exec -d` returns 0 whenever the CONTAINER exists, even for a command that cannot
  # start -- so nothing below is trusted until the readiness check further down. That lesson
  # is already written into run_scenario.py twice.
  # THE GROUND STATION, in its own container.
  docker exec -d "$GCS" bash -lc "
    ros2 launch webui webui.launch.py \
      bind_address:=$addr \
      page_port:=$PAGE_PORT rosbridge_port:=$BRIDGE_PORT video_port:=$VIDEO_PORT > $LOG 2>&1"

  # THE CHASE CAMERA, on the SIMULATOR side. It reads the renderer's X screen through an
  # abstract socket scoped to the network namespace -- and, more to the point, a real aircraft
  # has no chase camera. It is simulator scaffolding and stays with the simulator.
  if node_up "/chase_camera"; then
    warn "chase_camera is already running -- leaving it alone"
  else
    docker exec -d "$ROS" bash -lc "
      ros2 launch chase_camera chase_camera.launch.py \
        chase_display:=$CHASE_DISPLAY > $CHASE_LOG 2>&1"
  fi

  # THE DEPTH VIEW, also on the simulator side.                                  (SIM-48)
  # It colourises a 1.2 MB/frame 32FC1 topic into a JPEG beside the source rather than
  # shipping floats to the ground station. Needs perception running for its input to exist;
  # if it is not, the node says so every 10 s instead of the page showing a silent blank.
  if node_up "/depth_view"; then
    warn "depth_view is already running -- leaving it alone"
  else
    docker exec -d "$ROS" bash -lc "
      ros2 launch depth_view depth_view.launch.py \
        near_m:=$DEPTH_NEAR_M far_m:=$DEPTH_FAR_M > $DEPTH_LOG 2>&1"
  fi

  # --- the controller, in manual mode ------------------------------------------------
  # A SEPARATE PROCESS from the launch above, deliberately. The servers are observation and
  # transport; this is the thing that arms an aircraft. Keeping them separable means `stop`
  # can take the control path down while leaving the cameras up, which is what an operator
  # wants when a flight has gone wrong and they still want to look.
  if node_up "/offboard_control"; then
    warn "offboard_control is already running -- NOT starting a second one."
    warn "If it was started without manual:=true, the buttons will do nothing: stop it first."
  else
    docker exec -d "$ROS" bash -lc "
      ros2 run control offboard_control --ros-args \
        -p manual:=true -p takeoff_altitude:=$TAKEOFF_ALT > $CTRL_LOG 2>&1"
  fi

  # --- did any of it actually come up? -----------------------------------------------
  local ok=0
  for _ in $(seq 1 40); do
    if rexec "ros2 topic list 2>/dev/null" | grep -qx /mission/status; then ok=1; break; fi
    sleep 0.5
  done
  if [ "$ok" != 1 ]; then
    warn "/mission/status never appeared -- the controller did not reach WAIT_FOR_FCU."
    warn "last 20 lines of $CTRL_LOG inside $ROS:"
    rexec "tail -20 $CTRL_LOG" 2>/dev/null | sed 's/^/      /' || true
  fi

  cmd_status
  log ""
  log "open  http://127.0.0.1:$PAGE_PORT"
}

cmd_status() {
  need_stack
  printf '\n'
  # Grouped by MACHINE, because that is the thing SIM-46 made true and the thing an operator
  # needs to be able to see. Nodes are asked of the graph; the page server is not a node.
  printf '  \033[1m%s\033[0m  (ground station)\n' "$GCS"
  for pair in "/rosbridge_websocket:rosbridge" "/web_video_server:video"; do
    local node=${pair%%:*} name=${pair#*:}
    if node_up "$node"; then printf '    %-14s \033[32mup\033[0m\n' "$name"
    else                     printf '    %-14s \033[31mdown\033[0m\n' "$name"; fi
  done
  if gproc_up "http.server"; then printf '    %-14s \033[32mup\033[0m\n' "page server"
  else                            printf '    %-14s \033[31mdown\033[0m\n' "page server"; fi

  printf '  \033[1m%s\033[0m  (companion computer)\n' "$ROS"
  for pair in "/offboard_control:controller" "/chase_camera:chase camera" \
              "/depth_view:depth view"; do
    local node=${pair%%:*} name=${pair#*:}
    if node_up "$node"; then printf '    %-14s \033[32mup\033[0m\n' "$name"
    else                     printf '    %-14s \033[31mdown\033[0m\n' "$name"; fi
  done

  # THE TWO THINGS THAT LOOK BROKEN IN THE BROWSER BUT ARE NOT THIS SCRIPT'S FAULT.
  # Reported here so the operator reads them next to the page's own "no stream" message.
  printf '\n'
  if docker exec "$SIM" test -S "/tmp/.X11-unix/X${CHASE_DISPLAY#:}" 2>/dev/null; then
    printf '  chase view     available (X server on %s in %s)\n' "$CHASE_DISPLAY" "$SIM"
  else
    printf '  chase view     \033[33mUNAVAILABLE\033[0m -- bring the stack up with `./scripts/sim_up.sh --display`\n'
  fi
  if rexec "ros2 topic list 2>/dev/null" | grep -q '^/airsim_node/'; then
    printf '  drone camera   available\n'
  else
    printf '  drone camera   \033[33mUNAVAILABLE\033[0m -- start perception:\n'
    printf '                 docker exec -d %s bash -lc "ros2 launch bringup perception.launch.py"\n' "$ROS"
  fi

  # The interlock's verdict, read from the controller's own log rather than re-tested here --
  # a second implementation of the check could disagree with the one that governs.
  if rexec "grep -q 'SITL INTERLOCK NOT SATISFIED' $CTRL_LOG" 2>/dev/null; then
    printf '  interlock      \033[31mNOT SATISFIED\033[0m -- take-off will be refused. See %s\n' "$CTRL_LOG"
  elif rexec "grep -q 'SITL interlock satisfied' $CTRL_LOG" 2>/dev/null; then
    printf '  interlock      satisfied (AirSim RPC answered)\n'
  fi
}

cmd_stop() {
  need_stack
  # SIGTERM, and the CONTROLLER FIRST. If the servers went down first, a browser mid-press
  # would lose its feedback channel while the control path was still live -- the operator
  # would be looking at a frozen page with an aircraft still flying. Taking the controller
  # down first means the aircraft stops being commandable before the page stops showing it.
  #
  # NOTE this does not land a flying aircraft. Stopping the controller stops the setpoint
  # stream, and PX4 drops out of offboard after COM_OF_LOSS_T and runs its own failsafe.
  # That is PX4's decision to make and not this script's; land before stopping.
  if node_up "/offboard_control"; then
    log "stopping the controller (this does NOT land a flying aircraft -- PX4 failsafe will act)"
    docker exec "$ROS" pkill -INT -f offboard_control || true
  fi
  # SIGINT, NOT SIGTERM, and the difference is not cosmetic.
  #
  # rclpy installs a handler for SIGINT only. SIGTERM takes the default action and kills the
  # interpreter outright, so `ChaseCamera.destroy_node()` never runs -- and its whole job is
  # to terminate the ffmpeg it spawned. Measured: SIGTERM left both chase_camera and its
  # x11grab alive long enough to need a SIGKILL escalation, and an orphaned grabber holds the
  # renderer's X connection so the NEXT start finds the display already taken.
  for pat in webui.launch.py rosbridge_websocket web_video_server "http.server"; do
    docker exec "$GCS" pkill -INT -f "$pat" >/dev/null 2>&1 || true
  done
  # NOT the bare string `chase_camera`. SIM-46 added it to sim_up.sh's sim-ros2 entrypoint
  # (the `for p in interfaces control bringup chase_camera` copy loop), so `pkill -f
  # chase_camera` matches PID 44 -- the shell SUPERVISING THE uXRCE-DDS AGENT. Signalling that
  # would take the agent, the workspace and every /fmu/* topic down, which is far worse than
  # the SIM-45 version of this bug that merely misreported.
  #
  # These two patterns appear on the node's own command line and nowhere in sim_up.sh, which
  # tests/test_web_ui_probes.py asserts.
  for pat in "chase_camera.launch.py" "lib/chase_camera/chase_camera"; do
    docker exec "$ROS" pkill -INT -f "$pat" >/dev/null 2>&1 || true
  done
  # WAIT FOR THE GRAPH TO AGREE, rather than printing a verdict discovery has not caught up
  # with. `ros2 node list` lagged the actual exits by several seconds, so the first version of
  # this reported "chase camera up" about a process that was already a zombie -- a teardown
  # that says it failed when it succeeded is only marginally better than the reverse.
  local settled=0
  for _ in $(seq 1 20); do
    if ! node_up "/chase_camera" && ! node_up "/web_video_server" \
       && ! node_up "/rosbridge_websocket" && ! node_up "/offboard_control"; then
      settled=1; break
    fi
    sleep 1
  done
  [ "$settled" = 1 ] || warn "nodes still visible in the graph after 20 s"
  # ffmpeg is a CHILD of chase_camera. destroy_node() terminates it on a clean SIGINT, but if
  # the node was killed some other way the grabber outlives it and keeps holding the screen.
  docker exec "$ROS" pkill -TERM -f "x11grab" >/dev/null 2>&1 || true

  # PATTERNS THAT CANNOT MATCH THE ENTRYPOINT TEXT. `offboard_control` is deliberately NOT
  # in this list -- see node_up above; it is checked through the graph instead, and pkill for
  # it was already sent by name.
  local survivors=""
  for pat in webui.launch.py rosbridge_websocket web_video_server; do
    gproc_up "$pat" && survivors="$survivors $pat"
  done
  for pat in "lib/chase_camera/chase_camera" "lib/depth_view/depth_view" x11grab; do
    proc_up "$pat" && survivors="$survivors $pat"
  done
  node_up "/offboard_control" && survivors="$survivors offboard_control"
  if [ -n "$survivors" ]; then
    warn "still running after SIGINT:$survivors -- escalating to SIGKILL"
    for pat in $survivors; do
      docker exec "$GCS" pkill -KILL -f "$pat" >/dev/null 2>&1 || true
      docker exec "$ROS" pkill -KILL -f "$pat" >/dev/null 2>&1 || true
    done
  fi
  cmd_status
}

case "${1:-}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  -h|--help|"") sed -n '2,28p' "$0"; exit 0 ;;
  *) die "unknown subcommand '$1' -- start | stop | status" ;;
esac
