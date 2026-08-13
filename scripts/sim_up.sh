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
# So this script does NOT just start four containers. It waits for the vehicle to be settled
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

# SIM-29: give the engine a screen so the chase camera can be recorded. AirSim's `ViewMode`
# defaults to `FlyWithMe`, so the chase view renders on EVERY run -- `-RenderOffScreen` is the
# only reason nobody can read it. With DISPLAY_MODE=1 the renderer starts an Xvfb server and
# launches windowed instead, and `scripts/record_chase.sh` can then grab it.
#
# OFF BY DEFAULT, deliberately: headless stays the gate's normal path. The measured cost of the
# capture is 1.3 fps of 32.0 (4.2%), but two passing flights are NOT evidence that a windowed
# renderer leaves flight timing alone, and the gate is the thing that must not move.
DISPLAY_MODE=${DISPLAY_MODE:-}
# Accept BOTH `99` and `:99`. docker/qgc-entrypoint.sh:9 reads this same variable name WITH the
# colon (`${DISPLAY_NUM:-:99}`), so an operator following the repo's existing script would
# otherwise get `Xvfb ::99` and a bring-up that fails 20 s later with a confusing message.
# :77, NOT :99 -- and this is not cosmetic. Every container in this stack shares ONE network
# namespace (that is the point of the `--ipc shareable` / `--network container:` design), and an
# X server binds an ABSTRACT unix socket, which Linux scopes to the NETWORK namespace rather
# than the filesystem. So a display number is stack-global here, not container-local.
#
# docker/qgc-entrypoint.sh:9 has owned :99 since the Gazebo era. Using it here meant
# `DISPLAY=:99` inside the RENDERER resolved to QGROUNDCONTROL'S Xvfb -- and on a headless
# stack, where the renderer has no X server at all, the chase recorder cheerfully captured
# QGC's map view and wrote it out as `<tag>-chase.mp4`. A plausible-looking artifact of
# entirely the wrong thing, which is worse than a black rectangle: a black frame reads as
# broken, a map reads as evidence.                                                   (SIM-29)
DISPLAY_NUM=${DISPLAY_NUM:-77}
DISPLAY_NUM=${DISPLAY_NUM#:}
DISPLAY_GEOM=${DISPLAY_GEOM:-1920x1080}

# NETWORK MODE. Default `shared`: the renderer donates a private network + IPC namespace and
# every other container joins it. Nothing is published, nothing is reachable off this machine.
#
# `host` puts every container on the HOST's network and IPC namespaces instead. That is what
# makes the ROS 2 graph reachable from another machine: DDS then binds the host's real
# interfaces and advertises a ROUTABLE address, instead of the docker-bridge address
# 172.17.0.2 that only this machine can reach. Loopback still means the same thing to every
# container, so PX4 still dials 127.0.0.1 for both the agent and the renderer.
#
#   NET_MODE=host ./scripts/sim_up.sh
#
# EXPOSURE, stated plainly: in host mode PX4's MAVLink ports land on every interface this
# machine has, including the VPN. MAVLink is unauthenticated -- anyone routable can arm and
# command the vehicle. docs/docker/todo.md records that port 14540 was previously reachable
# over the netbird overlay for exactly this reason. Use host mode on a network you trust.
NET_MODE=${NET_MODE:-shared}
case "$NET_MODE" in shared|host) ;; *) echo "NET_MODE must be 'shared' or 'host'" >&2; exit 2 ;; esac

# DISCOVERY_SERVER=<ip>:<port> -- reach the graph across a link that carries no multicast
# (a VPN, a routed subnet). Every DDS participant in sim-ros2 becomes a discovery CLIENT of
# that server and announces itself over plain unicast UDP; a remote subscriber points at the
# same address and gets the whole graph, /fmu/* included. Unset means multicast, the default.
DISCOVERY_SERVER=${DISCOVERY_SERVER:-}
DS_ENV=()
if [ -n "$DISCOVERY_SERVER" ]; then
  # Require a non-empty host and an all-digits port. `*:*` alone accepted ":" and "a:b", which
  # reach Fast-DDS as a silently-ignored address -- and a discovery setting that is ignored
  # rather than rejected presents as an empty graph, which is the hardest failure here to read.
  case "$DISCOVERY_SERVER" in
    *:*[!0-9]*|:*|*:) echo "DISCOVERY_SERVER must be <host>:<port>, e.g. 10.0.0.5:11811 (got '$DISCOVERY_SERVER')" >&2; exit 2 ;;
    *:*) ;;
    *) echo "DISCOVERY_SERVER must be <host>:<port>, e.g. 10.0.0.5:11811 (got '$DISCOVERY_SERVER')" >&2; exit 2 ;;
  esac
  # ROS_SUPER_CLIENT is NOT optional here, and leaving it out is what made this look
  # impossible the first time. A plain discovery CLIENT is only told about participants it
  # has already matched on a topic it subscribes to. Graph introspection needs the whole
  # picture -- and `ros2 topic echo` performs introspection BEFORE it can subscribe, because
  # it has to resolve the message TYPE from the graph. As a plain client it fails with
  # "Could not determine the type for the passed topic" even though the publisher is right
  # there and healthy. wait_for_fmu below runs exactly that command, so without this the
  # bring-up fails its own health check and reports "no finite EKF origin".
  DS_ENV=(-e "ROS_DISCOVERY_SERVER=$DISCOVERY_SERVER" -e "ROS_SUPER_CLIENT=true")
fi


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
  --display             run the renderer on an Xvfb screen instead of -RenderOffScreen, so
                        AirSim's chase camera can be recorded with scripts/record_chase.sh.
                        Off by default: headless is the gate's path. (SIM-29)

Environment equivalents: SPAWN, SPAWN_VEHICLE, WORLD, SETTINGS_FILE, SPAWN_ALLOW_BELOW,
DISPLAY_MODE, DISPLAY_NUM, DISPLAY_GEOM.
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
    --display)            DISPLAY_MODE=1;         shift ;;
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
  # --spawn=VALUE, not --spawn VALUE. A jittered spawn is frequently NEGATIVE in X, and
  # argparse treats a following "-3.656,..." as an option rather than a value:
  #   apply_spawn.py: error: argument --spawn: expected one argument
  # This is why the flight gate had never completed a single seed -- the --reuse path always
  # spawns at 0,0,0, so every test took the one route where the bug cannot fire (SIM-07).
  args=(--settings "$SETTINGS" --out "$SETTINGS" "--spawn=$SPAWN")
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
  # sim-xrce is listed although this script no longer creates it: the agent moved into
  # sim-ros2, and a stale sim-xrce left by an older checkout would still hold udp/8888,
  # so the new agent would fail to bind -- and it exits 0 when it does.
  docker rm -f sim-ros2 sim-qgc sim-px4 sim-xrce "$SIM" >/dev/null 2>&1 || true
}

start_sim() {
  log "starting simulator (ipc shareable: it is the netns + /dev/shm donor)"
  # --ipc shareable is not optional: Fast-DDS discovers over UDP but DELIVERS over shared
  # memory, and a joiner with its own /dev/shm sees silence on a healthy stack (D-02).
  #
  # --shm-size=2g goes with it. Docker defaults /dev/shm to 64 MB, and because this container
  # DONATES its namespace, that 64 MB is the whole stack's shared-memory budget -- PX4, the
  # uXRCE-DDS agent and every ROS 2 node included. Fast-DDS starves at that size; it was measured
  # on the retired Gazebo stack, which set it in compose and is where the number comes from.
  # The simulator ran without it, which proves 64 MB is survivable at these rates, not that
  # it is correct: the failure mode is silent starvation under load, not a clean error.
  local mounts=(-v "$REPO/vendor/Cosys-AirSim:/src")
  # In display mode the renderer becomes a WRITER of artifacts, so it needs the same /out bind
  # mount every other artifact reaches the host through (sim-ros2 gets it below). Without it a
  # chase recording would have to be `docker cp`-ed out of the container's writable overlay --
  # which works, but stages the whole file on the overlay first, and a long capture is GBs.
  # Writing straight into /out also keeps ONE delivery route for artifacts, which is what
  # SIM-26 (root-owned files nobody could read) came out of.                          (SIM-29)
  if [ -n "$DISPLAY_MODE" ]; then
    mkdir -p "$REPO/out"
    mounts+=(-v "$REPO/out:/out")
  fi
  local uproject=/src/Unreal/Environments/Blocks/Blocks.uproject
  if [ -n "$WORLD" ]; then
    [ -f "$WORLD" ] || die "world not found: $WORLD"
    # The user's project is mounted read-write: Unreal writes Saved/ and shader caches into it.
    mounts+=(-v "$(cd "$(dirname "$WORLD")" && pwd):/world")
    uproject="/world/$(basename "$WORLD")"
    log "world: $uproject"
  fi
  local netargs=(--ipc shareable --shm-size=2g)
  if [ "$NET_MODE" = host ]; then
    # NETWORK namespace only. `--ipc host` is NOT used: this daemon refuses it
    # ("error mounting mqueue ... operation not permitted" under rootless/nested Docker), and
    # it buys nothing for the case host mode exists for -- a peer on another machine reaches
    # us over UDP, never over shared memory. The renderer still donates the IPC namespace, so
    # the containers keep their fast local path to each other.
    netargs=(--network host --ipc shareable --shm-size=2g)
    log "NET_MODE=host -- the graph will be reachable off this machine (see the header)"
  fi
  # The renderer's command line differs in exactly one respect between the two modes: whether
  # the engine is given a surface. Everything else -- the image, the plugin, the settings, the
  # mounts -- is identical, which is why display mode needs no change to Cosys-AirSim.
  local launch
  if [ -n "$DISPLAY_MODE" ]; then
    # `sleep` would be a race. Xvfb forks, binds a socket and only then accepts clients; if
    # Unreal dials DISPLAY before that, it dies at startup with no usable diagnostic. xdpyinfo
    # is the readiness check the engine image now carries for exactly this reason.
    # -ac and the extensions are NOT decoration. docker/qgc-entrypoint.sh:37 carries the same
    # set with a comment recording that without them a Qt/GL client dies MID-SESSION with
    # "XIO: fatal IO error 2 on X server" -- and that it cost an afternoon to find. Three
    # ~106 s flights are thin evidence against a failure mode that shows up late, so this
    # carries the flags rather than betting the renderer is different. `-nolisten tcp` also
    # matters under NET_MODE=host, where this X server would otherwise sit on the HOST's
    # network namespace. (review, PR 49)
    launch="Xvfb :$DISPLAY_NUM -screen 0 ${DISPLAY_GEOM}x24 \
        -ac +extension GLX +extension RANDR +render -noreset -nolisten tcp >/tmp/xvfb.log 2>&1 &
      for i in \$(seq 1 80); do DISPLAY=:$DISPLAY_NUM xdpyinfo >/dev/null 2>&1 && break; sleep 0.25; done
      DISPLAY=:$DISPLAY_NUM xdpyinfo >/dev/null 2>&1 || { echo 'FATAL: Xvfb :$DISPLAY_NUM never came up' >&2; cat /tmp/xvfb.log >&2; exit 1; }
      export DISPLAY=:$DISPLAY_NUM
      /home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor \
        $uproject -game -nosound -windowed -ResX=${DISPLAY_GEOM%x*} -ResY=${DISPLAY_GEOM#*x} \
        -unattended -stdout -settings=/settings.json"
    log "display mode: Xvfb :$DISPLAY_NUM at $DISPLAY_GEOM -- record with scripts/record_chase.sh"
  else
    launch="/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor \
      $uproject -game -RenderOffScreen -nosound \
      -unattended -stdout -settings=/settings.json"
  fi

  docker run -d --name "$SIM" \
    "${netargs[@]}" \
    --gpus '"device=nvidia.com/gpu=0"' \
    "${mounts[@]}" \
    -v "$SETTINGS:/settings.json:ro" \
    -v sim-ddc:/home/ue4/.config/Epic \
    drone-sim/unreal:ue5.8 \
    bash -lc "$launch" >/dev/null
}

# In `shared` mode this joins the renderer's namespaces; in `host` mode every container goes
# on the host's instead. Either way they all share ONE namespace, which is what makes 127.0.0.1
# mean the same thing to PX4, the agent and the renderer.
netns_args() {
  if [ "$NET_MODE" = host ]; then printf '%s\n' --network host --ipc "container:$SIM"
  else printf '%s\n' --network "container:$SIM" --ipc "container:$SIM"; fi
}

join() {  # join(name, image, cmd...)
  local name=$1 image=$2; shift 2
  local n; mapfile -t n < <(netns_args)
  docker run -d --name "$name" \
    "${n[@]}" \
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
    # Report what is ACTUALLY happening rather than asserting a cause. The old message said
    # "shader compilation on a cold cache" unconditionally, and when a user world failed to load
    # for an entirely different reason it sent the reader to raise a timeout that was never the
    # problem. An error that names an unverified cause is worse than one that names none.
    if [ $((waited % 60)) -eq 0 ]; then
      if [ "$(docker logs "$SIM" 2>&1 | grep -aic airsim || true)" -gt 0 ]; then
        log "  still waiting (${waited}s) -- AirSim is up; the level is still loading"
      else
        log "  still waiting (${waited}s) -- AirSim has logged nothing yet"
      fi
    fi
  done
  # DIAGNOSE before dying. The three causes look identical from PX4's side -- it just sits in
  # "Waiting for simulator to accept connection on TCP port 4560" -- but they need opposite
  # fixes, and only one of them is a timeout.
  local airsim_lines workers
  airsim_lines=$(docker logs "$SIM" 2>&1 | grep -aic airsim || true)
  # `|| true`, NOT `|| echo 0`. `grep -c` PRINTS its count and EXITS 1 when the count is zero,
  # docker exec propagates that 1, so `|| echo 0` fires IN ADDITION to grep's own output and
  # workers becomes "0\n0" -- which then makes `[ "$workers" -gt 0 ]` emit
  # "integer expression expected". That is the common case, and it turned a diagnostic into a
  # shell error. `true` prints nothing, so the count survives alone.
  workers=$(docker exec "$SIM" bash -lc 'ps -eo comm 2>/dev/null | grep -c ShaderCompileWorker' 2>/dev/null || true)
  workers=${workers:-0}
  log "diagnosis: AirSim log lines=${airsim_lines}  shader workers=${workers}"
  if [ "$airsim_lines" -eq 0 ]; then
    die "PX4 never reached the renderer in ${SIM_LINK_TIMEOUT}s, and THE AIRSIM PLUGIN LOGGED \
NOTHING -- so it never instantiated and there was never going to be a listener on 4560. This is \
almost always the world's config, not a timeout. Check that GlobalDefaultGameMode is \
/Script/AirSim.AirSimGameMode:
    grep -i GlobalDefaultGameMode <your-project>/Config/DefaultEngine.ini
  Fix it by injecting properly:  scripts/inject_airsim.py <your.uproject> --map <YourMap> --force
  Raising SIM_LINK_TIMEOUT will NOT help."
  fi
  if [ "$workers" -gt 0 ]; then
    die "PX4 never reached the renderer in ${SIM_LINK_TIMEOUT}s, but ${workers} ShaderCompileWorker \
process(es) are still running -- it is genuinely still compiling, not stuck. Re-run with a larger \
budget; the derived-data cache persists in the sim-ddc volume, so the next run is much faster:
    SIM_LINK_TIMEOUT=3600 ./scripts/sim_up.sh ..."
  fi
  die "PX4 never reached the renderer in ${SIM_LINK_TIMEOUT}s. AirSim HAS logged (${airsim_lines} \
lines) but no shader work is in progress, so it is neither misconfigured nor still compiling -- \
read the renderer log directly:  docker logs $SIM"
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
  # Filter the agent's own chatter FIRST, then tail. The uXRCE-DDS agent shares this
  # container's log and narrates every DDS entity PX4 creates -- measured at 159 lines inside a
  # minute -- so a plain `--tail 40` shows forty lines of [xrce] and hides the build error this
  # message exists to surface.
  log "workspace never built. Last output from sim-ros2 (agent chatter filtered):"
  docker logs --tail 400 sim-ros2 2>&1 | grep -v '^\[xrce\] ' | tail -40 | sed 's/^/    /' || true
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
    exit 1' || {
      # NAME THE DISCOVERY SERVER BEFORE THE EKF. An unreachable or mistyped server produces
      # EXACTLY this symptom -- /fmu/out silent -- because the participants never get
      # introduced, and the graph looks empty rather than broken. That is not hypothetical:
      # it is the failure that made an earlier pass conclude the Discovery Server "takes /fmu
      # offline" and revert a working feature. The EKF was never involved. So when the flag is
      # set, say so first and give the one-line check, rather than sending the reader to the
      # EKF for a discovery problem.
      if [ -n "$DISCOVERY_SERVER" ]; then
        die "no finite EKF origin appeared, and DISCOVERY_SERVER=$DISCOVERY_SERVER is set.
  CHECK DISCOVERY FIRST -- an unreachable server looks EXACTLY like this, and the EKF is
  probably fine. The server must be running and reachable BEFORE the stack starts:
      fastdds discovery -i 0 -l 0.0.0.0 -p ${DISCOVERY_SERVER##*:}
  Confirm the stack can see its own topics through it:
      docker exec sim-ros2 bash -lc 'ros2 topic list | grep -c \"^/fmu\"'     # expect 51
  A 0 there is discovery, not flight. If it reports 51, then this IS the EKF."
      fi
      die "no finite EKF origin appeared -- /fmu/out silent (check ipc/netns sharing, D-02) or the EKF never initialised"
    }
}

verify_origin() {
  docker cp "$REPO/scripts/check_ekf_origin.py" sim-ros2:/tmp/check_ekf_origin.py >/dev/null
  docker exec sim-ros2 bash -lc '
    set +u; source /opt/ros/jazzy/setup.bash
    source /ros2_ws/install/setup.bash 2>/dev/null; source /ros2_ws_src/install/setup.bash 2>/dev/null
    python3 /tmp/check_ekf_origin.py'
}

# --------------------------------------------------------------------------------------
# Record the discovery mode in the run log. Which mechanism introduced the participants is not
# recoverable from the stack afterwards, and every result on this stack is judged by the run
# that produced it -- so a bag with no record of its discovery mode cannot be fully read back.
if [ -n "$DISCOVERY_SERVER" ]; then
  log "discovery: SERVER $DISCOVERY_SERVER (multicast not used; it must already be running)"
else
  log "discovery: multicast (default)"
fi

teardown
start_sim
wait_for_settled_vehicle

log "starting the companion (agent + ROS 2), PX4 and QGC"

# THE COMPANION CONTAINER GOES UP FIRST, and that ordering is load-bearing.
#
# It hosts the uXRCE-DDS agent, which PX4 dials at 127.0.0.1:8888 the moment its
# uxrce_dds_client starts. The agent used to live in its own container created BEFORE PX4;
# folding it in here without moving this `docker run` up would have started PX4 first and
# left the client retrying against nothing.
#
# vendor/ is mounted read-only for SIM-04: the Cosys-AirSim ROS 2 wrapper (airsim_node) is
# built from source here, and colcon compiles AirLib itself via add_subdirectory, so it needs
# the whole tree rather than just ros2/src.
mapfile -t ROS2_NS < <(netns_args)
docker run -d --name sim-ros2 "${ROS2_NS[@]}" ${DS_ENV[@]+"${DS_ENV[@]}"} \
  -v "$REPO/ros2_ws:/ros2_ws_src" -v "$REPO/out:/out" \
  -v "$REPO/vendor/Cosys-AirSim:/vendor/Cosys-AirSim:ro" drone-sim/ros2:v1.16.0 bash -lc '
    # --- the uXRCE-DDS agent, supervised ------------------------------------------------
    #
    # It runs HERE because this is the companion computer. On the aircraft the agent is a
    # process on the Jetson beside every other ROS 2 node, not a machine of its own -- it is
    # plumbing that makes /fmu/* appear, and nothing connects to it directly. Giving it a
    # container of its own modelled a boundary that does not exist.
    #
    # A SUPERVISING SUBSHELL, not `agent &` and not `exec agent`. Both of those were measured
    # and both are silently wrong:
    #   * `MicroXRCEAgent & ... exec sleep infinity` -- exec REPLACES the parent, so nothing
    #     ever reaps the child. A crashed agent becomes a ZOMBIE that `pgrep -f MicroXRCEAgent`
    #     still matches, so a liveness check reports healthy over a dead bridge. `--init` does
    #     not fix it.
    #   * `exec MicroXRCEAgent` as PID 1 -- a crash then takes the whole container down,
    #     including the ROS 2 workspace and any in-flight `docker exec`, which destroys the
    #     MCAP that is the only evidence a failing run leaves.
    # The loop below stays the parent, so it reaps, and it restarts the bridge rather than
    # leaving the graph silently dead. That is strictly more than the separate container had:
    # this stack has never carried a restart policy on anything.
    #
    # `MicroXRCEAgent` EXITS 0 ON A BIND FAILURE, so "it started" proves nothing. What proves
    # it is wait_for_fmu below: real /fmu/out topics with a finite EKF origin.
    ( while true; do
        MicroXRCEAgent udp4 -p 8888 2>&1 | sed -u "s/^/[xrce] /"
        # PIPESTATUS[0], not $? -- the agent is the HEAD of a pipe, so $? is sed'"'"'s status and
        # reports 0 no matter how the agent died. Measured: a SIGKILLed agent logged "rc=0".
        rc=${PIPESTATUS[0]}
        echo "[xrce] agent exited (rc=$rc) -- restarting in 2s"
        sleep 2
      done ) &

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

join sim-px4  drone-sim/px4:v1.16.0 bash -lc \
  'stty min 1 time 0 2>/dev/null; cd /opt/px4/build/px4_sitl_default && \
   PX4_SYS_AUTOSTART=10016 PX4_SIM_HOSTNAME=127.0.0.1 ./bin/px4 -s etc/init.d-posix/rcS -d 2>&1'
# QGC supplies the GCS datalink. NAV_DLL_ACT is left ENFORCED on purpose, because a real
# Pixhawk refuses to arm without a datalink and the whole point is that sim and real differ
# only by transport -- so this container is load-bearing, not a convenience. Stop it and
# arming is denied, verified both directions.
join sim-qgc  drone-sim/qgc:v1.16.0

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
