#!/usr/bin/env bash
# Build the Cosys-AirSim ROS 2 wrapper (airsim_node) inside the running sim-ros2 service.
#                                                                          (SIM-04)
# WHY THIS SCRIPT EXISTS
#
# The wrapper build is NOT reproducible by reading the upstream docs. Getting to a running
# airsim_node took four undocumented discoveries, and the whole thing lives in a container
# whose next `sim_up.sh` run deletes it. Everything below is one of those discoveries; none
# of it is style.
#
#   1. drone-sim/ros2 lacks geographic_msgs and mavros_msgs. CMake fails on find_package.
#   2. It MUST be built in place. airsim_ros_pkgs/CMakeLists.txt reaches
#      ../../../cmake/{rpclib_wrapper,AirLib,MavLinkCom} via add_subdirectory, so copying the
#      packages into an ordinary colcon src/ resolves those to /cmake and fails.
#   3. The build WRITES INTO ITS OWN SOURCE TREE (external/rpclib/.../include/rpc/version.h,
#      config.h via configure_file), so it cannot be built from a read-only mount. Hence the
#      split below: symlink the big read-only parts, copy only what the build mutates.
#   4. Upstream aborts on first publish from a data race -- see the patch applied below.
#
# vendor/Cosys-AirSim stays PRISTINE. The patch is applied to the container-local copy, never
# to the vendored tree, per least-destructive-vendor-edits.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVC=${SVC:-sim-ros2}
ROOT=/airsim_root
PATCHDIR=patches/cosys-airsim

log() { printf '\033[36m[airsim-build]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[airsim-build] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

docker inspect "$SVC" >/dev/null 2>&1 || die "$SVC is not running - bring the stack up first"

log "confirming the wrapper's ROS deps (baked into drone-sim/ros2; this is a no-op guard)"
docker exec "$SVC" bash -lc '
  set -e
  need=""
  for p in ros-jazzy-geographic-msgs ros-jazzy-mavros-msgs python3-msgpack; do
    dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"
  done
  if [ -n "$need" ]; then
    apt-get update -qq >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need >/dev/null
  fi' || die "dependency install failed"

log "laying out a writable build root (symlink read-only, copy what the build mutates)"
docker exec "$SVC" bash -lc "
  set -e
  rm -rf $ROOT && mkdir -p $ROOT
  # Read-only is fine for these: the build only reads them.
  for d in cmake AirLib MavLinkCom; do
    [ -e /vendor/Cosys-AirSim/\$d ] && ln -s /vendor/Cosys-AirSim/\$d $ROOT/\$d
  done
  # These two the build WRITES to, so they must be real copies.
  cp -r /vendor/Cosys-AirSim/external $ROOT/external
  cp -r /vendor/Cosys-AirSim/ros2     $ROOT/ros2
  # The vendored tree may carry build artifacts from an in-tree build; their CMakeCache.txt
  # holds ABSOLUTE host paths and poisons this build with
  # 'current CMakeCache.txt directory is different than the directory where it was created'.
  rm -rf $ROOT/ros2/build $ROOT/ros2/install $ROOT/ros2/log
" || die "layout failed"

# Apply every patch in order. Numbered so the sequence is explicit rather than
# filesystem-order-dependent. vendor/ is never touched -- these land on the container copy.
shopt -s nullglob
patches=("$REPO/$PATCHDIR"/*.patch)
[ ${#patches[@]} -gt 0 ] || die "no patches found in $PATCHDIR"
ros_side=0
for pf in "${patches[@]}"; do
  # SKIP THE UNREAL-SIDE PATCHES. patches/cosys-airsim/ holds deviations for BOTH halves of
  # Cosys-AirSim: the ROS 2 wrapper (this build) and the Unreal plugin (applied by
  # scripts/convert_world.sh to a world's injected plugin copy). This build root contains
  # cmake/, AirLib/, MavLinkCom/, external/ and ros2/ -- there is no Unreal/ tree here, so an
  # Unreal patch cannot apply and `patch` prompts on stdin before failing.
  #
  # This is not hypothetical: adding 0005-worldpartition-streaming-source.patch broke this
  # script outright, and it was caught by running the wrapper build rather than by review.
  # Match the DIFF HEADER, not the prose. These patches carry long explanatory headers, and
  # 0005 mentions this path 4 times but only twice in a `+++ b/` line -- so a future ROS-side
  # patch that merely DESCRIBES the Unreal plugin would be silently skipped by a plain content
  # grep. Silently skipping a patch is the exact failure this filter exists to prevent.
  if grep -qE '^\+\+\+ b/Unreal/' "$pf"; then
    log "skipping $(basename "$pf") -- Unreal-side patch, applied by convert_world.sh"
    continue
  fi
  log "applying $(basename "$pf")"
  ros_side=$((ros_side + 1))
  docker cp "$pf" "$SVC:/tmp/wrapper.patch" >/dev/null
  # --batch so a mismatched patch FAILS instead of blocking forever on "File to patch:".
  docker exec "$SVC" bash -lc "cd $ROOT && patch -p1 --forward --batch < /tmp/wrapper.patch" \
    || die "$(basename "$pf") did not apply - upstream may have moved, or the hunk was
hand-written with LF endings against these CRLF sources. Re-generate it from the real file."
done
# If the router ate everything, say THAT rather than letting the artifact assertions below
# fail with a confusing message about callback groups.
[ "$ros_side" -gt 0 ] || die "every patch in $PATCHDIR was routed to the Unreal side -- no ROS 2
       wrapper patch was applied. Check the +++ b/ headers; this build needs 0001-0003."

# Assert the ARTIFACTS of the patches, not that `patch` printed something friendly. A build
# script's own success banner has lied in this repo before.
W=$ROOT/ros2/src/airsim_ros_pkgs
docker exec "$SVC" bash -lc "
  grep -q 'CallbackGroupType::MutuallyExclusive' $W/src/airsim_node.cpp \
  && ! grep -q 'CallbackGroupType::Reentrant'    $W/src/airsim_node.cpp
" || die "0001 applied but the callback group is not MutuallyExclusive"
docker exec "$SVC" bash -lc "
  grep -q 'vehicle_name + \"/\" + camera_name + \"_optical\"' $W/src/airsim_ros_wrapper.cpp
" || die "0002 applied but camera_info frame_id is still unprefixed"
docker exec "$SVC" bash -lc "
  test \$(grep -c 'cb_state_\|cb_img_\|cb_lidar_\|cb_gpulidar_\|cb_echo_' \
            $W/src/airsim_ros_wrapper.cpp) -ge 10
" || die "0003 applied but the per-timer callback groups are missing"

log "building (expect ~90 s)"
docker exec "$SVC" bash -lc "
  set +u; source /opt/ros/jazzy/setup.bash
  cd $ROOT/ros2 && colcon build --symlink-install 2>&1 | tail -4"

docker exec "$SVC" test -x "$ROOT/ros2/install/airsim_ros_pkgs/lib/airsim_ros_pkgs/airsim_node" \
  || die "colcon reported success but airsim_node is missing"

log "done. run it with:"
echo "  docker exec -d $SVC bash -lc '. /opt/ros/jazzy/setup.bash && . $ROOT/ros2/install/setup.bash && ros2 run airsim_ros_pkgs airsim_node --ros-args -p host_ip:=127.0.0.1'"
