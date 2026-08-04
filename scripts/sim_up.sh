#!/usr/bin/env bash
# Bring up the simulator stack in an order that actually works.  (SIM-10, from SIM-09)
#
# WHY ORDERING IS THE WHOLE POINT
#
# PX4 sets its EKF local origin ONCE. If it initialises before the simulated vehicle has
# settled at its final altitude, ref_alt freezes at the wrong height and every altitude PX4
# reports is offset for the rest of the session -- silently. In SIM-09 that offset was 35.167 m:
# the vehicle "was" 35 m up while sitting on the ground, the controller (which targets an
# absolute altitude) commanded a descent into the ground, nothing moved, PX4 auto-disarmed,
# and every symptom pointed at the flight code. The flight code was fine.
#
# So this script does NOT just start five containers. It waits for the vehicle to be settled
# before PX4 connects, and then VERIFIES the origin rather than assuming the wait was enough.
#
# The configuration below was recovered from a stack that was verified flying 4/4 waypoints,
# not transcribed from the reference docs -- per the project rule that a recipe written from
# docs rather than evidence reproduces a broken stack.
#
# This is the ONLY supported bring-up. There is no compose file: every service shares the
# renderer's network and IPC namespaces, which compose could express but which also has to
# be sequenced around the settle-then-verify step below, and a second half-correct path is
# worse than none.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM=sim-unreal
SETTLE_SAMPLES=${SETTLE_SAMPLES:-5}     # consecutive stable ground-truth reads required
SETTLE_EPS=${SETTLE_EPS:-0.05}          # metres of movement tolerated between reads
ORIGIN_RETRIES=${ORIGIN_RETRIES:-2}     # PX4 restarts allowed before giving up

# SIM-13: where to put the vehicle. AirSim spawns at a level's PlayerStart and falls back to world
# ORIGIN when there isn't one -- and an arbitrary world has no obligation to put usable ground
# there, so in a user's own map the drone can spawn INSIDE the terrain. Blocks has ground at the
# origin, so the default is empty and nothing changes for it.
#   ./scripts/sim_up.sh --spawn 50,-30,-10       (metres, NED: Z NEGATIVE is UP)
#   SPAWN=50,-30,-10,315 ./scripts/sim_up.sh     (fourth component is yaw, degrees)
SPAWN=${SPAWN:-}
SPAWN_VEHICLE=${SPAWN_VEHICLE:-}
SPAWN_ALLOW_BELOW=${SPAWN_ALLOW_BELOW:-}
WORLD=${WORLD:-}                        # .uproject to load; default is the vendored Blocks

log() { printf '\033[36m[sim]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[sim] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
usage: sim_up.sh [--world PATH.uproject] [--settings PATH.json]
                 [--spawn X,Y,Z[,YAW]] [--vehicle NAME] [--allow-below-origin]

  --settings PATH       your own settings.json — selects which sensors are active and
                        their tuning (camera resolution/FOV, LiDAR channels, rates).
                        Copied to a run-time file; the committed artifact is untouched.

  --spawn X,Y,Z[,YAW]   vehicle spawn in metres, NED. Z NEGATIVE is UP: for 10 m of
                        altitude pass Z=-10. Written into a RUN-TIME copy of
                        sim/ue5/settings.json; the committed file is never modified.
  --vehicle NAME        required only when settings.json defines several vehicles.
  --world PATH          .uproject to load (default: the vendored Blocks environment).
  --allow-below-origin  permit a positive Z (i.e. genuinely below the origin).

Environment equivalents: SPAWN, SPAWN_VEHICLE, WORLD, SETTINGS_FILE, SPAWN_ALLOW_BELOW.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --settings)           SETTINGS_FILE="${2:-}"; shift 2 ;;
    --settings=*)         SETTINGS_FILE="${1#*=}"; shift ;;
    --spawn)              SPAWN="${2:-}";         shift 2 ;;
    --spawn=*)            SPAWN="${1#*=}";        shift ;;
    --vehicle)            SPAWN_VEHICLE="${2:-}"; shift 2 ;;
    --vehicle=*)          SPAWN_VEHICLE="${1#*=}"; shift ;;
    --world)              WORLD="${2:-}";         shift 2 ;;
    --world=*)            WORLD="${1#*=}";        shift ;;
    --allow-below-origin) SPAWN_ALLOW_BELOW=1;    shift ;;
    -h|--help)            usage; exit 0 ;;
    *)                    usage >&2; die "unknown argument: $1" ;;
  esac
done

# The settings file the simulator is actually given. Rewritten only when a spawn is requested,
# so a plain run still mounts the committed artifact unchanged.
BASE_SETTINGS="${SETTINGS_FILE:-$REPO/sim/ue5/settings.json}"
[ -f "$BASE_SETTINGS" ] || die "settings file not found: $BASE_SETTINGS"

# A user-supplied settings file is NORMALISED into the run-time copy rather than mounted from
# wherever it lives. Two reasons: a file under /tmp cannot be bind-mounted read-only (it is on
# the container's fuse-overlayfs — "remount-ro: operation not permitted"), and copying keeps the
# committed artifact untouched whichever source is used.
SETTINGS="$BASE_SETTINGS"
if [ -n "$SPAWN" ] || [ "$BASE_SETTINGS" != "$REPO/sim/ue5/settings.json" ]; then
  RUN_SETTINGS="$REPO/sim/ue5/.settings.run.json"
  cp "$BASE_SETTINGS" "$RUN_SETTINGS"
  chmod 644 "$RUN_SETTINGS"
  SETTINGS="$RUN_SETTINGS"
  [ "$BASE_SETTINGS" != "$REPO/sim/ue5/settings.json" ] && log "settings: $BASE_SETTINGS"
fi
if [ -n "$SPAWN" ]; then
  # Deliberately NOT mktemp/$TMPDIR. /tmp is on the container's fuse-overlayfs, and a read-only
  # bind mount from there is refused by the daemon:
  #   remount-ro /var/lib/docker/fuse-overlayfs/.../settings.json: operation not permitted
  # The repo is a host bind mount, so a file beside the source mounts exactly like the source.
  # Gitignored as sim/ue5/.settings.run.json.
  args=(--settings "$SETTINGS" --out "$SETTINGS" --spawn "$SPAWN")
  [ -n "$SPAWN_VEHICLE" ]     && args+=(--vehicle "$SPAWN_VEHICLE")
  [ -n "$SPAWN_ALLOW_BELOW" ] && args+=(--allow-below-origin)
  # A bad spawn must ABORT here. Falling through to the committed settings would start the
  # stack at the origin while the operator believes their coordinate took effect -- which is
  # the original bug wearing a fix's clothing.
  python3 "$REPO/scripts/apply_spawn.py" "${args[@]}" || die "spawn rejected; not starting"
fi

# --------------------------------------------------------------------------------------
teardown() {
  log "removing any previous stack"
  docker rm -f sim-ros2 sim-qgc sim-px4 sim-xrce "$SIM" >/dev/null 2>&1 || true
}

start_sim() {
  log "starting simulator (ipc shareable: it is the netns + /dev/shm donor)"
  # --ipc shareable is not optional: Fast-DDS discovers over UDP but DELIVERS over shared
  # memory, and a joiner with its own /dev/shm sees silence on a healthy stack (D-02).
  #
  # --shm-size=2g goes with it. Docker defaults /dev/shm to 64 MB, and because this container
  # DONATES its namespace, that 64 MB is the whole stack's shared-memory budget -- PX4, the
  # XRCE agent and every ROS 2 node included. Fast-DDS starves at that size; it was measured
  # on the retired Gazebo stack, which set it in compose and is where the number comes from.
  # The simulator ran without it, which proves 64 MB is survivable at these rates, not that
  # it is correct: the failure mode is silent starvation under load, not a clean error.
  local mounts=(-v "$REPO/vendor/Cosys-AirSim:/src")
  local uproject=/src/Unreal/Environments/Blocks/Blocks.uproject
  if [ -n "$WORLD" ]; then
    [ -f "$WORLD" ] || die "world not found: $WORLD"
    # The user's project is mounted read-write: Unreal writes Saved/ and shader caches into it.
    mounts+=(-v "$(cd "$(dirname "$WORLD")" && pwd):/world")
    uproject="/world/$(basename "$WORLD")"
    log "world: $uproject"
  fi
  docker run -d --name "$SIM" \
    --ipc shareable \
    --shm-size=2g \
    --gpus '"device=nvidia.com/gpu=0"' \
    "${mounts[@]}" \
    -v "$SETTINGS:/settings.json:ro" \
    -v sim-ddc:/home/ue4/.config/Epic \
    drone-sim/unreal:ue5.8 \
    bash -lc "/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor \
      $uproject -game -RenderOffScreen -nosound \
      -unattended -stdout -settings=/settings.json" >/dev/null
}

join() {  # join(name, image, cmd...) -- share the sim's network AND ipc namespaces
  local name=$1 image=$2; shift 2
  docker run -d --name "$name" \
    --network "container:$SIM" --ipc "container:$SIM" \
    "$image" "$@" >/dev/null
}

# --------------------------------------------------------------------------------------
# Wait until AirSim answers RPC *and* the vehicle has stopped moving. Answering RPC is not
# enough: the sim serves RPC while the level is still settling the vehicle onto geometry,
# which is exactly the window in which PX4 must NOT initialise.
wait_for_settled_vehicle() {
  log "waiting for the vehicle to settle (${SETTLE_SAMPLES} reads within ${SETTLE_EPS} m)"
  docker cp "$REPO/scripts/airsim_rpc_client.py" "$SIM:/tmp/airsim_rpc_client.py" >/dev/null
  docker exec "$SIM" python3 - "$SETTLE_SAMPLES" "$SETTLE_EPS" <<'PY' || die "vehicle never settled"
import sys, time
sys.path.insert(0, "/tmp")
need, eps = int(sys.argv[1]), float(sys.argv[2])
from airsim_rpc_client import Rpc

deadline, rpc = time.time() + 300, None
while time.time() < deadline and rpc is None:
    try:
        rpc = Rpc(); rpc.call("getServerVersion")
    except Exception:
        rpc = None; time.sleep(2)
if rpc is None:
    print("RPC never came up"); sys.exit(1)

stable, last = 0, None
while time.time() < deadline:
    z = rpc.call("simGetGroundTruthKinematics", "PX4")["position"]["z_val"]
    if last is not None and abs(z - last) <= eps:
        stable += 1
        if stable >= need:
            print(f"settled at z={z:+.3f} m"); sys.exit(0)
    else:
        stable = 0
    last = z
    time.sleep(1)
print("still moving at deadline"); sys.exit(1)
PY
}

# Wait for PX4 to actually reach the renderer before waiting on telemetry.
#
# WHY THIS IS A SEPARATE STEP, added 2026-08-04 after it bit a cold start.
#
# Answering AirSim RPC (the settle-wait above) happens EARLY -- well before Unreal has
# finished initialising the engine. On a machine with a warm derived-data cache the gap is
# ~30 s and nothing notices. On a COLD cache the engine compiles shaders first, and the
# measured gap here was 199 s: PX4 sat in "Waiting for simulator to accept connection on TCP
# port 4560" the whole time while wait_for_fmu burned its 120 s budget and the script
# declared the stack dead. The stack was fine; it came up 80 s after we gave up.
#
# That is exactly the fresh-machine case the reproducibility goal is about -- a first run on
# a new box ALWAYS has a cold cache -- so the budget is now generous and, more importantly,
# waits for the RIGHT EVENT rather than for a fixed number of seconds.
SIM_LINK_TIMEOUT=${SIM_LINK_TIMEOUT:-900}

wait_for_sim_link() {
  log "waiting for PX4 to connect to the renderer on TCP 4560 (up to ${SIM_LINK_TIMEOUT}s)"
  log "  a cold shader cache makes the first run of a world slow; this is progress, not a hang"
  local waited=0 px4log running
  while [ "$waited" -lt "$SIM_LINK_TIMEOUT" ]; do
    # CAPTURE THEN MATCH -- deliberately NOT `docker logs ... | grep -q`.
    #
    # `grep -q` exits on its first match and closes the pipe; `docker logs` then takes
    # SIGPIPE and the pipeline returns 141. Under `set -o pipefail` (line 24) the `if`
    # therefore reads FALSE at exactly the moment the match succeeds, and only when PX4 has
    # printed enough after the match to still be writing -- so the link is never detected,
    # bring-up burns the full 900 s, and it fails with "PX4 never connected" on a stack that
    # connected fine. It is timing-dependent, which is worse than always broken.
    px4log=$(docker logs sim-px4 2>&1 || true)
    case "$px4log" in
      *"Simulator connected on TCP port 4560"*)
        log "renderer link up after ${waited}s"
        return 0 ;;
    esac
    # Neither the renderer nor PX4 dying should be waited out for fifteen minutes. Same
    # capture-then-match rule: a pipeline here would reintroduce the defect above.
    running=$(docker ps --format '{{.Names}}')
    case "$running" in
      *sim-unreal*) : ;;
      *) die "sim-unreal exited while PX4 was waiting for it -- check: docker logs sim-unreal" ;;
    esac
    case "$running" in
      *sim-px4*) : ;;
      *) die "sim-px4 exited before it could reach the renderer -- check: docker logs sim-px4" ;;
    esac
    sleep 5
    waited=$((waited + 5))
    [ $((waited % 60)) -eq 0 ] && log "  still waiting (${waited}s) -- shader compilation on a cold cache"
  done
  die "PX4 never connected to the renderer within ${SIM_LINK_TIMEOUT}s. Raise SIM_LINK_TIMEOUT, \
or check: docker logs sim-unreal"
}

# Wait for the ROS 2 workspace to BUILD, and assert its artifacts.
#
# WHY THIS IS A BARRIER OF ITS OWN, added 2026-08-04.
#
# Nothing downstream can catch a failed workspace build. wait_for_fmu and verify_origin both
# source /ros2_ws/install/setup.bash, which the image already ships populated with px4_msgs and
# px4_ros_com -- so both pass happily on the base image alone, and this script would print
# "safe to fly" over a container with no `control` package in it. The gate would then exec
# `ros2 run control offboard_control`, get "Package 'control' not found", write no result, and
# score the seed as a FLIGHT FAILURE. A build error would be reported as a control defect, for
# every seed, with the compiler output already discarded.
#
# The retired compose stack got this right with a `.build-ok` marker written only after the
# artifacts were proven, gated by a healthcheck. That guarantee is restored here rather than
# lost with the file.
WORKSPACE_TIMEOUT=${WORKSPACE_TIMEOUT:-300}

wait_for_workspace() {
  log "waiting for the ROS 2 workspace to build (up to ${WORKSPACE_TIMEOUT}s)"
  local waited=0
  while [ "$waited" -lt "$WORKSPACE_TIMEOUT" ]; do
    if docker exec sim-ros2 test -f /ros2_ws/.build-ok 2>/dev/null; then
      log "workspace built and artifacts verified after ${waited}s"
      return 0
    fi
    docker exec sim-ros2 true 2>/dev/null \
      || die "sim-ros2 is not running -- check: docker logs sim-ros2"
    sleep 5
    waited=$((waited + 5))
  done
  # Surface the reason rather than the symptom: the container logs carry the FATAL line and
  # the build tail, and both are far more useful than a timeout message.
  log "workspace never built. Last output from sim-ros2:"
  docker logs --tail 40 sim-ros2 2>&1 | sed 's/^/    /' || true
  die "the ROS 2 workspace did not build within ${WORKSPACE_TIMEOUT}s. Do NOT score runs on \
this stack -- a missing 'control' package presents as a flight failure, not a build failure."
}

wait_for_fmu() {
  log "waiting for /fmu/out telemetry and a FINITE EKF origin"
  docker exec sim-ros2 bash -lc '
    set +u; source /opt/ros/jazzy/setup.bash
    source /ros2_ws/install/setup.bash 2>/dev/null; source /ros2_ws_src/install/setup.bash 2>/dev/null
    for i in $(seq 1 60); do
      # BEST_EFFORT is mandatory: /fmu/out publishers are BEST_EFFORT and a default RELIABLE
      # subscription matches nothing, reading as silence on a HEALTHY stack (P1-02).
      # A published ref_alt is NOT enough: PX4 publishes it as NaN until the EKF has
      # actually established an origin, and an earlier version of this function accepted
      # that and handed back a stack with no origin at all. Require a FINITE value.
      v=$(timeout 8 ros2 topic echo --qos-reliability best_effort --qos-durability volatile \
            --once --field ref_alt /fmu/out/vehicle_local_position 2>/dev/null \
          | grep -vE '^-{3}$' | head -1)
      if [ -n "$v" ] && python3 -c "import math,sys; sys.exit(0 if math.isfinite(float(sys.argv[1])) else 1)" "$v" 2>/dev/null; then
        exit 0
      fi
      sleep 2
    done
    exit 1' || die "no finite EKF origin appeared -- /fmu/out silent (check ipc/netns sharing, D-02) or the EKF never initialised"
}

verify_origin() {
  docker cp "$REPO/scripts/check_ekf_origin.py" sim-ros2:/tmp/check_ekf_origin.py >/dev/null
  docker exec sim-ros2 bash -lc '
    set +u; source /opt/ros/jazzy/setup.bash
    source /ros2_ws/install/setup.bash 2>/dev/null; source /ros2_ws_src/install/setup.bash 2>/dev/null
    python3 /tmp/check_ekf_origin.py'
}

# --------------------------------------------------------------------------------------
teardown
start_sim
wait_for_settled_vehicle

log "starting XRCE agent, PX4, QGC and the ROS 2 workspace"
join sim-xrce drone-sim/px4:v1.16.0 bash -lc 'MicroXRCEAgent udp4 -p 8888'
join sim-px4  drone-sim/px4:v1.16.0 bash -lc \
  'stty min 1 time 0 2>/dev/null; cd /opt/px4/build/px4_sitl_default && \
   PX4_SYS_AUTOSTART=10016 PX4_SIM_HOSTNAME=127.0.0.1 ./bin/px4 -s etc/init.d-posix/rcS -d 2>&1'
# QGC supplies the GCS datalink. NAV_DLL_ACT is left ENFORCED on purpose, because a real
# Pixhawk refuses to arm without a datalink and the whole point is that sim and real differ
# only by transport -- so this container is load-bearing, not a convenience. Stop it and
# arming is denied, verified both directions.
join sim-qgc  drone-sim/qgc:v1.16.0
# vendor/ is mounted read-only for SIM-04: the Cosys-AirSim ROS 2 wrapper (airsim_node) is
# built from source here, and colcon compiles AirLib itself via add_subdirectory, so it needs
# the whole tree rather than just ros2/src.
docker run -d --name sim-ros2 --network "container:$SIM" --ipc "container:$SIM" \
  -v "$REPO/ros2_ws:/ros2_ws_src" -v "$REPO/out:/out" \
  -v "$REPO/vendor/Cosys-AirSim:/vendor/Cosys-AirSim:ro" drone-sim/ros2:v1.16.0 bash -lc '
    rm -f /ros2_ws/.build-ok
    mkdir -p /ros2_ws/src
    # A failed copy used to be indistinguishable from a healthy start. Fail loudly.
    for p in interfaces control bringup; do
      rm -rf "/ros2_ws/src/$p"
      cp -r "/ros2_ws_src/src/$p" "/ros2_ws/src/$p" \
        || { echo "ros2: FATAL - could not copy $p from /ros2_ws_src"; exit 1; }
    done
    cd /ros2_ws
    # ASSERT ON ARTIFACTS, NOT ON THE EXIT STATUS. `colcon build` exits 0 when it finds no
    # packages at all, and an ament_python build never checks imports -- which is exactly how
    # drone_interfaces once shipped missing while colcon reported success and the node died at
    # import. The marker file is written ONLY after both artifacts are proven to exist.
    if colcon build --symlink-install --packages-skip px4_msgs px4_ros_com > /ros2_ws/build.log 2>&1; then
      if [ -x /ros2_ws/install/control/lib/control/offboard_control ] \
         && bash -lc ". /ros2_ws/install/setup.bash && python3 -c \"import drone_interfaces.msg\"" >/dev/null 2>&1; then
        touch /ros2_ws/.build-ok; echo "ros2: workspace built, artifacts verified"
      else
        echo "ros2: FATAL - colcon reported success but the artifacts are not there"
      fi
    else
      echo "ros2: FATAL - workspace build failed; see /ros2_ws/build.log"
      tail -30 /ros2_ws/build.log
    fi
    exec sleep infinity' >/dev/null

# Three distinct waits, in order, because they fail for different reasons and a single
# combined timeout cannot tell them apart:
#   1. the ROS 2 workspace BUILDS         -- ~90 s, and a failure here is not a flight failure
#   2. PX4 <-> renderer MAVLink link      -- slow on a cold shader cache, minutes
#   3. EKF establishes a finite origin    -- seconds once sensors flow
wait_for_workspace
wait_for_sim_link
wait_for_fmu

# The wait above is a best effort, not a proof. Verify, and if the origin is still stale,
# restart PX4 -- which is the one action known to re-initialise it -- rather than handing
# back a stack that will silently mis-score every run on it.
for attempt in $(seq 0 "$ORIGIN_RETRIES"); do
  if verify_origin; then
    log "stack up and origin verified -- safe to fly"
    exit 0
  fi
  if [ "$attempt" -lt "$ORIGIN_RETRIES" ]; then
    log "origin stale; restarting PX4 (attempt $((attempt + 1))/$ORIGIN_RETRIES)"
    docker restart sim-px4 >/dev/null
    sleep 20
    wait_for_fmu
  fi
done

die "EKF origin still stale after $ORIGIN_RETRIES restarts. Do NOT score runs on this stack -- \
they would be VOID, and would look like control failures. See docs/todo.md SIM-10."
