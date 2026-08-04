#!/usr/bin/env bash
# Fly a recorded circuit of a world over ROS 2, and keep the evidence.            (C-16)
#
# SITL ONLY. Nothing real is armed or flown. Renders on GPU 0; GPU 1 is left alone.
#
#   ./scripts/run_park_tour.sh                                   # Blocks — the known-good control
#   ./scripts/run_park_tour.sh --world /path/CityPark.uproject --spawn 50,-30,-10
#
# ARTIFACT LAYOUT — deliberately the same shape Lane A's gate writes (run_gate.py), so the two
# lanes stay comparable rather than each inventing their own:
#
#   out/lane-c/park-tour-<UTC>/
#     park-tour_0.mcap   every /fmu/*, /airsim_node/*, /tf and /clock for the whole run
#     metadata.yaml      ros2 bag's own
#     summary.json       waypoints, per-leg error, verdict
#     mission.log        the node's stdout
#     stack.log          bring-up, for when a run dies before it flies
#
# The bag is started BEFORE the mission node and stopped after, so the recording brackets the
# flight rather than clipping its start -- a bag that begins mid-takeoff cannot answer the one
# question anybody asks of it later ("what did it do at the beginning?").
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WORLD=""; SPAWN=""; SETTINGS=""; LEGS=4; RADIUS=25.0; ALTITUDE=8.0; TOLERANCE=2.0; ARRIVE_SPEED=0.7; KEEP_UP=""
MODE="waypoints"; SPEED=4.0; LAPS=1.0; YAW_MODE="inward"; MAX_ACCEL=2.0; RAMP_S=6.0
RECORD_REGEX='/fmu/out/.*|/airsim_node/.*|/tf.*|/clock'
while [ $# -gt 0 ]; do
  case "$1" in
    --world)     WORLD="${2:-}";     shift 2 ;;
    --settings)  SETTINGS="${2:-}";  shift 2 ;;
    --spawn)     SPAWN="${2:-}";     shift 2 ;;
    --legs)      LEGS="${2:-}";      shift 2 ;;
    --radius)    RADIUS="${2:-}";    shift 2 ;;
    --altitude)  ALTITUDE="${2:-}";  shift 2 ;;
    --tolerance) TOLERANCE="${2:-}"; shift 2 ;;
    --arrive-speed) ARRIVE_SPEED="${2:-}"; shift 2 ;;
    --mode)      MODE="${2:-}";      shift 2 ;;
    --speed)     SPEED="${2:-}";     shift 2 ;;
    --laps)      LAPS="${2:-}";      shift 2 ;;
    --yaw-mode)  YAW_MODE="${2:-}";  shift 2 ;;
    --max-accel) MAX_ACCEL="${2:-}"; shift 2 ;;
    --ramp)      RAMP_S="${2:-}";    shift 2 ;;
    --record)    RECORD_REGEX="${2:-}"; shift 2 ;;
    --keep-up)   KEEP_UP=1;          shift ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# rclpy types parameters from the literal: `radius:=20` is an INTEGER and is REJECTED against a
# DOUBLE default ("expecting type 'DOUBLE'"). Force a decimal point so 20 and 20.0 behave the same.
asfloat(){ python3 -c "print(float('$1'))"; }
RADIUS=$(asfloat "$RADIUS"); ALTITUDE=$(asfloat "$ALTITUDE"); TOLERANCE=$(asfloat "$TOLERANCE")
ARRIVE_SPEED=$(asfloat "$ARRIVE_SPEED"); SPEED=$(asfloat "$SPEED"); LAPS=$(asfloat "$LAPS")
MAX_ACCEL=$(asfloat "$MAX_ACCEL"); RAMP_S=$(asfloat "$RAMP_S")
LEGS=$(python3 -c "print(int(float('$LEGS')))")

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$REPO/out/lane-c/park-tour-$STAMP"
mkdir -p "$RUN"
log(){ printf '\033[36m[park-tour]\033[0m %s\n' "$*"; }
die(){ printf '\033[31m[park-tour] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

log "run directory: $RUN"

# --- bring up the stack -----------------------------------------------------------------
up_args=()
[ -n "$WORLD" ] && up_args+=(--world "$WORLD")
[ -n "$SPAWN" ] && up_args+=(--spawn "$SPAWN")
[ -n "$SETTINGS" ] && up_args+=(--settings "$SETTINGS")
log "bringing up Lane C ${up_args[*]:-(Blocks, default spawn)}"
bash "$REPO/scripts/lane_c_up.sh" "${up_args[@]}" > "$RUN/stack.log" 2>&1 \
  || { tail -5 "$RUN/stack.log"; die "bring-up failed — see $RUN/stack.log"; }

# The wrapper lives in the ROS 2 container, which lane_c_up.sh deletes on every run.
log "building the AirSim wrapper (~2 min)"
bash "$REPO/scripts/build_airsim_wrapper.sh" >> "$RUN/stack.log" 2>&1 \
  || die "wrapper build failed — see $RUN/stack.log"

log "building the control package (park_tour)"
docker exec lane-c-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  cd /ros2_ws && colcon build --packages-select control --symlink-install' \
  >> "$RUN/stack.log" 2>&1 || die "colcon build of control failed — see $RUN/stack.log"

log "starting the perception graph"
docker exec -d lane-c-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 launch bringup lane_c_perception.launch.py > /tmp/perception.log 2>&1'
sleep 25

# --- record, fly, stop ------------------------------------------------------------------
# Recorded by regex rather than -a: -a also picks up rosout and parameter-event chatter, which
# on a 17 Hz imagery stream buries the flight in noise and inflates the bag for no benefit.
log "starting the bag"
docker exec -d lane-c-ros2 bash -lc "
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  cd /out/lane-c/park-tour-$STAMP && \
  ros2 bag record --storage mcap -o park-tour \
    --regex '$RECORD_REGEX' > /tmp/bag.log 2>&1"
sleep 6

if [ "$MODE" = "circle" ]; then
  log "flying: CIRCLE r=${RADIUS} m at ${SPEED} m/s, ${LAPS} lap(s), alt ${ALTITUDE} m, yaw ${YAW_MODE}"
else
  log "flying: $LEGS legs, radius ${RADIUS} m, altitude ${ALTITUDE} m"
fi
docker exec lane-c-ros2 bash -lc "
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 run control park_tour --ros-args \
    -p legs:=$LEGS -p radius:=$RADIUS -p altitude:=$ALTITUDE -p tolerance:=$TOLERANCE \
    -p arrive_speed:=$ARRIVE_SPEED -p mode:=$MODE -p speed:=$SPEED \
    -p laps:=$LAPS -p yaw_mode:=$YAW_MODE -p max_accel:=$MAX_ACCEL -p ramp_s:=$RAMP_S \
    -p summary:=/out/lane-c/park-tour-$STAMP/summary.json" 2>&1 | tee "$RUN/mission.log"
MISSION_RC=${PIPESTATUS[0]}

log "stopping the bag"
docker exec lane-c-ros2 bash -lc 'pkill -INT -f "ros2 bag record" || true'
sleep 5

# ros2 bag writes into a subdirectory OWNED BY ROOT (the container writes as root), so flattening
# from the host fails with EACCES on the directory -- silently, which once produced a false
# "no .mcap was written" against a 1.8 GB bag that existed. Do it inside the container, then hand
# ownership back so the artifacts are readable and removable by the operator.
docker exec lane-c-ros2 bash -lc "
  cd /out/lane-c/park-tour-$STAMP 2>/dev/null || exit 0
  [ -d park-tour ] && { mv park-tour/* . 2>/dev/null; rmdir park-tour 2>/dev/null; }
  chown -R $(id -u):$(id -g) /out/lane-c/park-tour-$STAMP" 2>/dev/null || true

# --- report -----------------------------------------------------------------------------
BAG=$(ls -S "$RUN"/*.mcap 2>/dev/null | head -1)
if [ -n "$BAG" ]; then
  SZ=$(du -h "$BAG" | cut -f1)
  log "bag: $(basename "$BAG") ($SZ)"
else
  log "WARNING: no .mcap was written — the run has no replayable evidence"
fi

if [ -f "$RUN/summary.json" ]; then
  python3 - "$RUN/summary.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
if "error" in d:
    print(f"  verdict: FAILED — {d['error']}")
elif d.get("mode") == "circle":
    print(f"  verdict: {'PASS' if d.get('ok') else 'FAIL'}   "
          f"radius error max {d.get('radius_error_max_m')} m / mean {d.get('radius_error_mean_m')} m   "
          f"alt error max {d.get('alt_error_max_m')} m")
    print(f"    mean speed {d.get('speed_mean_ms')} m/s over {d.get('samples')} samples, "
          f"landed={d.get('landed')}")
else:
    print(f"  verdict: {'PASS' if d.get('ok') else 'FAIL'}   "
          f"worst {d.get('worst_error_m')} m   mean {d.get('mean_error_m')} m   "
          f"landed={d.get('landed')}")
    for l in d.get("legs", []):
        print(f"    leg {l['leg']}: {'ok  ' if l['ok'] else 'MISS'} "
              f"error {l['error_m']:>6.2f} m  {l['seconds']:>5.1f}s")
PY
else
  log "WARNING: no summary.json — the mission node did not complete"
fi

[ -n "$KEEP_UP" ] || docker rm -f lane-c-ros2 lane-c-qgc lane-c-px4 lane-c-xrce lane-c-sim >/dev/null 2>&1
log "artifacts in $RUN"
exit "$MISSION_RC"
