#!/usr/bin/env bash
# Measure sensor rates across image-quality configurations.               (SIM-11)
#
# SITL only. Nothing real is armed or flown.
#
# WHY: the image-quality fix found on 2026-08-03 (LumenGIEnable, LumenReflectionEnable,
# ForceUpdate) was validated on ONE camera at 1080p in a standalone simulator. The graph runs two
# cameras plus a GPU-LiDAR at 640x480, and ForceUpdate renders every ENABLED camera every frame
# rather than once per request -- so its cost scales with the graph, not with the setting. The
# C has a published claim of 31 Hz RGB. This measures what each configuration actually costs on
# the real ROS 2 graph, in the same world, so the trade is a number rather than an argument.
#
# Each configuration needs a full stack cycle: settings.json is parsed at simulator startup, and
# `sim_up.sh` deletes the ROS 2 container, which is where the AirSim wrapper is built.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS="$REPO/sim/ue5/settings.json"
OUT="${OUT:-$REPO/out/rate-matrix}"
SAMPLE_SETTLE="${SAMPLE_SETTLE:-25}"

log() { printf '\033[36m[rates]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[rates] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$OUT"
BACKUP="$(mktemp)"; cp "$SETTINGS" "$BACKUP"
restore() { cp "$BACKUP" "$SETTINGS"; rm -f "$BACKUP"; }
trap restore EXIT

# Rewrite the Scene capture block (ImageType 0) in place, preserving the file's comments by
# editing only the keys under test.
apply_config() {  # apply_config <lumen:true|false> <forceupdate:true|false>
  python3 - "$SETTINGS" "$1" "$2" <<'PY'
import json, re, sys
path, lumen, force = sys.argv[1], sys.argv[2] == "true", sys.argv[3] == "true"
raw = open(path).read()
doc = json.loads(re.sub(r'^\s*//.*$', '', raw, flags=re.M))
cs = doc["Vehicles"]["PX4"]["Cameras"]["front_center"]["CaptureSettings"]
scene = next(c for c in cs if c.get("ImageType") == 0)
for k in ("LumenGIEnable", "LumenReflectionEnable", "ForceUpdate"):
    scene.pop(k, None)
if lumen:
    scene["LumenGIEnable"] = True
    scene["LumenReflectionEnable"] = True
if force:
    scene["ForceUpdate"] = True
open(path, "w").write(json.dumps(doc, indent=2) + "\n")
PY
}

measure() {  # measure <label> <lumen> <forceupdate>
  local label=$1 lumen=$2 force=$3
  log "=== $label (Lumen=$lumen ForceUpdate=$force)"
  apply_config "$lumen" "$force"

  bash "$REPO/scripts/sim_up.sh" >"$OUT/$label.up.log" 2>&1 \
    || { log "  bring-up failed, see $OUT/$label.up.log"; return 1; }
  bash "$REPO/scripts/build_airsim_wrapper.sh" >"$OUT/$label.build.log" 2>&1 \
    || { log "  wrapper build failed"; return 1; }

  docker exec -d sim-ros2 bash -lc '
    source /opt/ros/jazzy/setup.bash
    source /airsim_root/ros2/install/setup.bash
    source /ros2_ws/install/setup.bash 2>/dev/null
    ros2 launch bringup perception.launch.py > /tmp/perception.log 2>&1'
  sleep "$SAMPLE_SETTLE"

  docker cp "$REPO/scripts/verify_sensors.py" sim-ros2:/tmp/verify.py >/dev/null
  docker exec sim-ros2 bash -lc '
    source /opt/ros/jazzy/setup.bash
    source /airsim_root/ros2/install/setup.bash
    source /ros2_ws/install/setup.bash 2>/dev/null
    python3 /tmp/verify.py' >"$OUT/$label.sensors.log" 2>&1 || true

  # Rates are read back out of the verifier's own output rather than re-derived, so this
  # reports exactly what the project's acceptance check reports.
  local rgb depth lidar imu
  rgb=$(grep -A1 'RGB image'   "$OUT/$label.sensors.log" | grep -oE '[0-9.]+ Hz' | head -1)
  depth=$(grep -A1 'Depth image' "$OUT/$label.sensors.log" | grep -oE '[0-9.]+ Hz' | head -1)
  lidar=$(grep -A1 'LiDAR point cloud' "$OUT/$label.sensors.log" | grep -oE '[0-9.]+ Hz' | head -1)
  imu=$(grep -A1 'PASS.*IMU$' "$OUT/$label.sensors.log" | grep -oE '[0-9.]+ Hz' | head -1)
  printf '%s\t%s\t%s\t%s\t%s\n' "$label" "${rgb:-?}" "${depth:-?}" "${lidar:-?}" "${imu:-?}" \
    >> "$OUT/rates.tsv"
  log "  RGB=${rgb:-?} depth=${depth:-?} lidar=${lidar:-?} imu=${imu:-?}"
}

: > "$OUT/rates.tsv"
measure "stock"        false false || true
measure "lumen"        true  false || true
measure "lumen_force"  true  true  || true

log "--- summary (config / RGB / depth / LiDAR / IMU)"
column -t "$OUT/rates.tsv" | sed 's/^/  /'
log "settings.json restored to its committed state"
