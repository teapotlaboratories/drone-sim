#!/usr/bin/env bash
# Attach YOUR code to a running simulator, over ROS 2.
#
#   ./scripts/attach.sh                                  # interactive shell, ROS 2 sourced
#   ./scripts/attach.sh ros2 topic list
#   ./scripts/attach.sh --image my/autonomy:latest ros2 run my_pkg my_node
#   ./scripts/attach.sh --volume "$PWD/my_ws:/my_ws" bash -lc 'cd /my_ws && colcon build'
#
# WHAT THIS IS FOR
#
# The simulator does NOT host your application. `sim-ros2` runs the uXRCE-DDS bridge and this
# repo's reference nodes (interfaces, control, bringup) -- the things that prove the graph
# works. Your autonomy code is yours: it lives in your image, in your workspace, on your
# branch, and it attaches to the graph from outside. This script is the supported way to do
# that, and it exists because getting it right by hand is easy to get *almost* right.
#
# THE TRAP THIS SCRIPT EXISTS TO CLOSE -- MEASURED, NOT THEORETICAL
#
# Sharing the network namespace WITHOUT sharing IPC is the one combination that does not
# work, and it is worse than sharing nothing at all. Measured against a live stack, same
# subscriber, three ways:
#
#   no namespaces shared (fully separate)   3 messages   OK
#   --network container:sim-unreal          0 messages   BROKEN
#   --network ... --ipc container:sim-unreal 3 messages  OK
#
# Why the middle one fails: sharing the netns makes Fast-DDS see the peer as same-host, so it
# picks the SHARED MEMORY transport -- but /dev/shm is still your own, so nothing is
# delivered. `ros2 topic list` shows all 51 topics throughout. Share both, or share neither.
#
# YOU MAY NOT NEED THIS SCRIPT AT ALL. A process in its own namespaces already receives the
# whole graph over UDP, with no flags and no DDS configuration -- Fast-DDS falls back on its
# own. Use attach.sh when you want the shared-memory path (large image topics) or a shell
# with the stack's environment already set up.
#
# WHAT YOUR IMAGE NEEDS
#
# To speak /fmu/* you need `px4_msgs` built from the SAME branch as the firmware
# (release/1.16). The simplest route is to base your image on drone-sim/ros2:v1.16.0, which
# already has it; the default below does exactly that. If you bring your own base, match the
# px4_msgs branch or your subscriptions will silently match nothing.
#
# AND THE QoS RULE THAT BITES EVERYONE ONCE
#
# /fmu/out/* publishers are BEST_EFFORT + TRANSIENT_LOCAL. A default RELIABLE subscription
# matches NOTHING and reads as silence on a perfectly healthy stack. See docs/conventions.md.
set -euo pipefail

SIM=sim-unreal
IMAGE=${IMAGE:-drone-sim/ros2:v1.16.0}
VOLUMES=()

log() { printf '\033[36m[attach]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[attach] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image)   IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --image=*) IMAGE="${1#*=}"; shift ;;
    --volume|-v)   VOLUMES+=(-v "${2:?--volume needs a value}"); shift 2 ;;
    --volume=*)    VOLUMES+=(-v "${1#*=}"); shift ;;
    -h|--help) usage ;;
    --)        shift; break ;;
    *)         break ;;
  esac
done

# The renderer owns the namespaces the whole stack joins, so its absence means there is
# nothing to attach TO. Say that, rather than letting `docker run` fail with a bare
# "No such container".
docker inspect "$SIM" >/dev/null 2>&1 \
  || die "$SIM is not running -- bring the simulator up first:  ./scripts/sim_up.sh"

# A stack that is up but has no bridge would hand you an empty graph and no explanation.
# Checking for the ROS 2 container is cheap and names the problem.
docker inspect sim-ros2 >/dev/null 2>&1 \
  || die "sim-ros2 is not running -- the uXRCE-DDS bridge lives there, so there is no
       /fmu/* graph to attach to. Re-run ./scripts/sim_up.sh"

if [ "$#" -eq 0 ]; then
  set -- bash -l
  TTY=(-it)
  log "no command given -- opening an interactive shell (ROS 2 sourced by /etc/profile.d)"
else
  TTY=()
  [ -t 0 ] && TTY=(-it)
fi

log "image  : $IMAGE"
log "attach : --network container:$SIM --ipc container:$SIM   (BOTH are required)"

# --ipc is not optional. See the header: without it you get discovery without delivery.
exec docker run --rm "${TTY[@]}" \
  --network "container:$SIM" \
  --ipc "container:$SIM" \
  "${VOLUMES[@]}" \
  "$IMAGE" "$@"
