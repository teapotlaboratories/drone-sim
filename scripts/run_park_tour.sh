#!/usr/bin/env bash
# Fly a recorded circuit of a world over ROS 2, and keep the evidence.            (SIM-16)
#
# SITL ONLY. Nothing real is armed or flown. Renders on GPU 0; GPU 1 is left alone.
#
#   ./scripts/run_park_tour.sh                                   # Blocks — the known-good control
#   ./scripts/run_park_tour.sh --world /path/CityPark.uproject --spawn 50,-30,-10
#
# ARTIFACT LAYOUT — deliberately the same shape the flight gate writes (run_gate.py), so a demo
# run and a gate run stay comparable rather than each inventing their own:
#
#   out/park-tour-<UTC>/
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

WORLD=""; SPAWN=""; SETTINGS=""; LEGS=4; RADIUS=25.0; ALTITUDE=20.0; TOLERANCE=2.0; ARRIVE_SPEED=0.7; KEEP_UP=""
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
RUN="$REPO/out/park-tour-$STAMP"
mkdir -p "$RUN"
log(){ printf '\033[36m[park-tour]\033[0m %s\n' "$*"; }
die(){ printf '\033[31m[park-tour] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }
warn(){ printf '\033[33m[park-tour] WARNING:\033[0m %s\n' "$*" >&2; }

log "run directory: $RUN"

# --- bring up the stack -----------------------------------------------------------------
up_args=()
[ -n "$WORLD" ] && up_args+=(--world "$WORLD")
[ -n "$SPAWN" ] && up_args+=(--spawn "$SPAWN")
[ -n "$SETTINGS" ] && up_args+=(--settings "$SETTINGS")
log "bringing up the simulator ${up_args[*]:-(Blocks, default spawn)}"
bash "$REPO/scripts/sim_up.sh" "${up_args[@]}" > "$RUN/stack.log" 2>&1 \
  || { tail -5 "$RUN/stack.log"; die "bring-up failed — see $RUN/stack.log"; }

# The wrapper lives in the ROS 2 container, which sim_up.sh deletes on every run.
log "building the AirSim wrapper (~2 min)"
bash "$REPO/scripts/build_airsim_wrapper.sh" >> "$RUN/stack.log" 2>&1 \
  || die "wrapper build failed — see $RUN/stack.log"

log "building the control package (park_tour)"
docker exec sim-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  cd /ros2_ws && colcon build --packages-select control --symlink-install' \
  >> "$RUN/stack.log" 2>&1 || die "colcon build of control failed — see $RUN/stack.log"

log "starting the perception graph"
docker exec -d sim-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 launch bringup perception.launch.py > /tmp/perception.log 2>&1'
sleep 25

# --- record, fly, stop ------------------------------------------------------------------
# Recorded by regex rather than -a: -a also picks up rosout and parameter-event chatter, which
# on a 17 Hz imagery stream buries the flight in noise and inflates the bag for no benefit.
# Start the collision witness BEFORE the bag, so it brackets the flight the same way. It is a
# separate process on purpose: the mission node reporting on its own crash is not a witness.
# Until this existed, nothing in the harness could tell a collision from bad tracking -- a 48 m
# miss looks identical either way in the leg scoring.
log "starting the collision witness"
# One implementation, shared with run_gate.py. This used to be three bash lines that did the
# same docker cp / exec -d / rm dance, and the two copies drifted: this one scored a missing
# witness as CLEAN while the gate correctly failed it.
# REMEMBER whether it started; do not merely warn. run_gate.py records -1 when start() fails,
# and this used to just warn and carry on -- two callers, two behaviours for one condition,
# which is the drift this whole change removes.
#
# It is not only a consistency point. "No file means unknown" holds because start() DELETES the
# previous run's file first; if start fails at that very step the stale file survives, and a
# clean previous run would then be read back as this run's verdict. The only caller that can
# see that happened is this one, here.
WITNESS_OK=1
python3 "$REPO/scripts/collision_witness.py" start >/dev/null || WITNESS_OK=0
[ "$WITNESS_OK" -eq 1 ] || \
  warn "collision witness did not start -- this run cannot be scored clean"

log "starting the bag"
docker exec -d sim-ros2 bash -lc "
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  cd /out/park-tour-$STAMP && \
  ros2 bag record --storage mcap -o park-tour \
    --regex '$RECORD_REGEX' > /tmp/bag.log 2>&1"
sleep 6

if [ "$MODE" = "circle" ]; then
  log "flying: CIRCLE r=${RADIUS} m at ${SPEED} m/s, ${LAPS} lap(s), alt ${ALTITUDE} m, yaw ${YAW_MODE}"
else
  log "flying: $LEGS legs, radius ${RADIUS} m, altitude ${ALTITUDE} m"
fi
docker exec sim-ros2 bash -lc "
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 run control park_tour --ros-args \
    -p legs:=$LEGS -p radius:=$RADIUS -p altitude:=$ALTITUDE -p tolerance:=$TOLERANCE \
    -p arrive_speed:=$ARRIVE_SPEED -p mode:=$MODE -p speed:=$SPEED \
    -p laps:=$LAPS -p yaw_mode:=$YAW_MODE -p max_accel:=$MAX_ACCEL -p ramp_s:=$RAMP_S \
    -p summary:=/out/park-tour-$STAMP/summary.json" 2>&1 | tee "$RUN/mission.log"
MISSION_RC=${PIPESTATUS[0]}

log "stopping the collision witness"
# Exit code carries the verdict: 0 clean, 1 collided, 2 unknown. Scoring lives in the module,
# so "unknown is not clean" is stated once rather than re-derived in shell.
# BRANCH ON THE EXIT CODE, not on parsed output. The exit code cannot be garbled; stdout can.
#   0 clean · 1 collided · 2 unknown
#
# An earlier version of this captured stdout with 2>&1 and took `cut -f1`, so any stderr line
# arriving first -- a docker warning, a traceback -- became the "count". With the numeric guard
# also gone, `[ "$COLLIDED" -lt 0 ]` then errored, both branches fell through, and the run
# scored CLEAN. That is the exact defect this whole change exists to remove, reintroduced while
# removing it. stderr now goes to the terminal where it belongs, and the count is only ever
# used for the message.
COLL_OUT=$(python3 "$REPO/scripts/collision_witness.py" stop --save "$RUN/collisions.json")
COLL_RC=$?
COLLIDED=$(printf '%s' "$COLL_OUT" | tail -1 | cut -f1)
# Belt and braces: the count is display-only below, but a non-numeric value must never reach a
# numeric test and silently take the else branch. Strip ONE optional leading minus and then
# demand digits only -- `*[!0-9-]*` alone also accepted "1-2" and a bare "-", because it allows
# a minus at any position.
case "${COLLIDED#-}" in ''|*[!0-9]*) COLLIDED=-1 ;; esac
# One source of truth for the verdict: the exit code. COLLIDED carries only the NUMBER, for the
# message, and is forced to agree so the printed tag can never contradict the branch taken.
# A witness that never started cannot score this run, and must not be rescued by a file that
# merely happens to be readable -- see WITNESS_OK above.
[ "$WITNESS_OK" -eq 1 ] || COLL_RC=2
case "$COLL_RC" in
  0) COLLIDED=0 ;;
  2) COLLIDED=-1 ;;
  *) [ "$COLLIDED" -gt 0 ] 2>/dev/null || COLLIDED=1 ;;
esac

log "stopping the bag"
docker exec sim-ros2 bash -lc 'pkill -INT -f "ros2 bag record" || true'
sleep 5

# ros2 bag writes into a subdirectory OWNED BY ROOT (the container writes as root), so flattening
# from the host fails with EACCES on the directory -- silently, which once produced a false
# "no .mcap was written" against a 1.8 GB bag that existed. Do it inside the container, then hand
# ownership back so the artifacts are readable and removable by the operator.
docker exec sim-ros2 bash -lc "
  cd /out/park-tour-$STAMP 2>/dev/null || exit 0
  [ -d park-tour ] && { mv park-tour/* . 2>/dev/null; rmdir park-tour 2>/dev/null; }
  chown -R $(id -u):$(id -g) /out/park-tour-$STAMP" 2>/dev/null || true

# --- report -----------------------------------------------------------------------------
BAG=$(ls -S "$RUN"/*.mcap 2>/dev/null | head -1)
if [ -n "$BAG" ]; then
  SZ=$(du -h "$BAG" | cut -f1)
  log "bag: $(basename "$BAG") ($SZ)"
else
  log "WARNING: no .mcap was written — the run has no replayable evidence"
fi

# A COLLISION FAILS THE RUN, regardless of what the leg scoring says.
#
# Leg scoring measures distance-to-waypoint and arrival speed. Neither notices an impact: a run
# that flew into a building at 8 m altitude scored PASS on all five legs while the witness
# recorded a sustained scrape against TemplateCube_Rounded_7. A harness that records a crash
# beside a PASS does not merely miss the failure, it launders it -- so the verdict is overridden
# here and MISSION_RC is forced non-zero.
# COLL_RC and COLLIDED were set above by scripts/collision_witness.py, which owns the scoring.
# This used to re-parse collisions.json here in bash -- a second implementation of the same
# rule, which is exactly how the two copies came to disagree in the first place. The human-
# readable detail is not carried in a variable: the block below reads collisions.json directly,
# so a second copy of the wording would be one more thing that can drift.

if [ -f "$RUN/summary.json" ]; then
  python3 - "$RUN/summary.json" "${COLLIDED:-0}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
# A collision overrides the leg scoring outright. Printing "PASS" on a line above "COLLISION"
# is how a harness launders a crash -- the verdict has to carry it.
# -1 is UNKNOWN, not "collided". Both fail the run, but calling an unobserved run a collision
# is a different lie from calling it clean -- report which one actually happened.
collided = int(sys.argv[2]) if len(sys.argv) > 2 else 0
tag = " (COLLISION)" if collided > 0 else (" (COLLISION STATE UNKNOWN)" if collided < 0 else "")
if collided:
    d["ok"] = False
if "error" in d:
    print(f"  verdict: FAILED — {d['error']}")
elif d.get("mode") == "circle":
    print(f"  verdict: {'PASS' if d.get('ok') else 'FAIL'}"
          f"{tag}   "
          f"radius error max {d.get('radius_error_max_m')} m / mean {d.get('radius_error_mean_m')} m   "
          f"alt error max {d.get('alt_error_max_m')} m")
    print(f"    mean speed {d.get('speed_mean_ms')} m/s over {d.get('samples')} samples, "
          f"landed={d.get('landed')}")
else:
    print(f"  verdict: {'PASS' if d.get('ok') else 'FAIL'}"
          f"{tag}   "
          f"worst {d.get('worst_error_m')} m   mean {d.get('mean_error_m')} m   "
          f"landed={d.get('landed')}")
    for l in d.get("legs", []):
        print(f"    leg {l['leg']}: {'ok  ' if l['ok'] else 'MISS'} "
              f"error {l['error_m']:>6.2f} m  {l['seconds']:>5.1f}s")
PY
else
  log "WARNING: no summary.json — the mission node did not complete"
fi

if [ "$COLL_RC" -eq 2 ]; then
  log "COLLISION STATE UNKNOWN -- no readable collisions.json, so this run cannot be scored
       clean. Treating it as FAIL; check /tmp/collision_witness.log inside sim-ros2."
  MISSION_RC=1
elif [ "$COLL_RC" -ne 0 ]; then
  python3 -c "
import json
d = json.load(open('$RUN/collisions.json'))
print(f\"  COLLISION: {d['collision_count']} contact(s) -- run is a FAIL whatever the legs say\")
for e in d['collisions'][:5]:
    print(f\"    t+{e['t']}s  {e['object_name']}  {e.get('duration_s',0)}s in contact  at {e['impact_point']}\")
" 2>/dev/null || log "COLLISION detected (collisions.json unreadable)"
  MISSION_RC=1
else
  log "no collisions ($(python3 -c "
import json
try: print(json.load(open('$RUN/collisions.json'))['ground_contacts'])
except Exception: print(0)" 2>/dev/null) ground contacts, expected at takeoff and landing)"
fi

# sim-xrce is listed although nothing creates it any more -- the agent lives in sim-ros2.
# A stale one from an older checkout would hold udp/8888 and break the next bring-up.
[ -n "$KEEP_UP" ] || docker rm -f sim-ros2 sim-qgc sim-px4 sim-xrce sim-unreal >/dev/null 2>&1
log "artifacts in $RUN"
exit "$MISSION_RC"
