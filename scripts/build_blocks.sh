#!/usr/bin/env bash
# Patch and build the bundled Blocks environment — the default world and the flight gate. (SIM-23)
#
#   ./scripts/build_blocks.sh            # apply Unreal-side patches, rebuild if anything changed
#   ./scripts/build_blocks.sh --force    # rebuild even when the patches were already in place
#
# WHY THIS EXISTS
# ---------------
# Quickstart 0.2 builds the plugin with upstream's own `build.sh`, from PRISTINE vendor source.
# Nothing then applies `patches/cosys-airsim/*.patch` to it. `convert_world.sh` does that job for
# a USER's world and deliberately refuses to touch anything inside this repo:
#
#   [inject] FATAL: refusing to inject into a path inside this repo: .../Environments/Blocks
#
# which is correct — but it left the world we actually fly, and the one the gate scores, as the
# only world that never received an Unreal-side patch. So a fresh machine following the
# quickstart would rebuild the very crash 0006 fixes, and there would be nothing in the repo to
# point at. That gap is the whole reason for this script.
#
# It is the last step of first-time setup, after `build.sh`, and again whenever a patch under
# patches/cosys-airsim/ touching Unreal/ is added or changed.
#
# WHAT IT REFUSES TO DO
# ---------------------
# Report success without evidence. UnrealBuildTool exits 0 for a no-op, and the plugin directory
# ALWAYS contains a .so — it shipped with one — so "a .so exists" can never fail and would be
# confidence this has not earned. The artifact is therefore checked against a timestamp marker
# taken before the build, exactly as convert_world.sh does.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_IMAGE=${ENGINE_IMAGE:-drone-sim/unreal:ue5.8}
UE=/home/ue4/UnrealEngine
ROOT="$REPO/vendor/Cosys-AirSim/Unreal/Environments/Blocks"
PLUGIN="$ROOT/Plugins/AirSim"
TARGET=BlocksEditor
FORCE=""

C=$'\033[36m'; Y=$'\033[33m'; R=$'\033[31m'; G=$'\033[32m'; X=$'\033[0m'
log()  { printf '%s[blocks]%s %s\n' "$C" "$X" "$*"; }
warn() { printf '%s[blocks] WARNING:%s %s\n' "$Y" "$X" "$*"; }
ok()   { printf '%s[blocks] %s%s\n' "$G" "$*" "$X"; }
die()  { printf '%s[blocks] FATAL:%s %s\n' "$R" "$X" "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -f "$ROOT/Blocks.uproject" ] || die "no Blocks.uproject at $ROOT
       Run quickstart step 0.2 first: vcs import vendor < .repos, then upstream's build.sh."
[ -d "$PLUGIN/Source" ] || die "no plugin source at $PLUGIN/Source
       Upstream's build.sh has not run, so there is nothing here to patch or compile."

# --------------------------------------------------------------------------------------
# 1. Apply the Unreal-side patches. Same routing rule as convert_world.sh: match the DIFF
#    HEADER, never the prose, so a ROS 2 wrapper patch is not dragged in for merely naming
#    the plugin in its description.
# --------------------------------------------------------------------------------------
log "step 1/2  applying Unreal-side vendor patches to $PLUGIN"
applied=0; already=0
for p in "$REPO"/patches/cosys-airsim/*.patch; do
  [ -e "$p" ] || continue
  grep -qE '^\+\+\+ b/Unreal/' "$p" || continue          # ROS 2 wrapper patch; not ours
  name=$(basename "$p")
  if patch -p4 -d "$PLUGIN" --forward --batch --dry-run < "$p" >/dev/null 2>&1; then
    patch -p4 -d "$PLUGIN" --forward --batch --silent < "$p" >/dev/null
    log "  applied  $name"; applied=$((applied + 1))
  elif patch -p4 -R -d "$PLUGIN" --batch --dry-run < "$p" >/dev/null 2>&1; then
    log "  already  $name"; already=$((already + 1))
  else
    # Never "probably fine". A patch that neither applies nor reverse-applies means the plugin
    # has drifted from what it was cut against, and skipping it quietly reinstates the exact
    # defect it exists to prevent -- for 0006 that is a renderer that dies mid-flight.
    die "$name does NOT apply to $PLUGIN and is not already present.
       The plugin has drifted from what the patch was cut against. Re-cut the patch, or delete
       $PLUGIN and re-run upstream's build.sh for a clean copy first."
  fi
done
[ "$applied" -gt 0 ] || [ "$already" -gt 0 ] \
  || warn "no Unreal-side patches found under patches/cosys-airsim/ -- nothing to apply"

if [ "$applied" -eq 0 ] && [ -z "$FORCE" ]; then
  ok "every patch was already in place; nothing to rebuild (use --force to compile anyway)"
  exit 0
fi

# --------------------------------------------------------------------------------------
# 2. Compile. Same invocation convert_world.sh uses for an A2 project.
# --------------------------------------------------------------------------------------
log "step 2/2  compiling $TARGET (this takes ~1-2 min)"
marker=$(mktemp); : > "$marker"
sleep 1                    # 1 s filesystem timestamp granularity -- without it a fast build can
                           # produce artifacts that are not strictly -newer than the marker
cname="blocks-build-$$"
docker rm -f "$cname" >/dev/null 2>&1 || true
# Same uid as the caller so artifacts land owned by them, not root. sim-ddc is shared with
# sim_up.sh so the derived-data cache survives between build and first run.
docker run -d --name "$cname" --user "$(id -u):$(id -g)" \
  -v "$ROOT:/world" -v sim-ddc:/home/ue4/.config/Epic \
  --entrypoint bash "$ENGINE_IMAGE" -lc \
  "$UE/Engine/Build/BatchFiles/Linux/Build.sh $TARGET Linux Development \
     -project=/world/Blocks.uproject -waitmutex 2>&1" >/dev/null \
  || die "could not start the build container"

docker logs -f "$cname" 2>&1 | sed 's/^/    /' &
tailpid=$!
rc=$(docker wait "$cname" 2>/dev/null || echo 1)
kill "$tailpid" 2>/dev/null || true; wait "$tailpid" 2>/dev/null || true

if [ "$rc" != "0" ]; then
  echo
  docker logs "$cname" 2>&1 | grep -E 'error:' | head -12 | sed 's/^/    /' || true
  docker rm -f "$cname" >/dev/null 2>&1 || true
  rm -f "$marker"
  die "build FAILED (exit $rc). The errors above are the real ones; UBT's own summary is not."
fi
docker rm -f "$cname" >/dev/null 2>&1 || true

SO="$PLUGIN/Binaries/Linux/libUnrealEditor-AirSim.so"
[ -f "$SO" ] || { rm -f "$marker"; die "no plugin binary at $SO after a build that reported success"; }
[ "$SO" -nt "$marker" ] || { rm -f "$marker"; die "$(basename "$SO") is NOT newer than the pre-build marker.
       UnrealBuildTool reported success without producing an artifact, so whatever is on disk is
       the OLD binary -- and it is the old binary that would fly."; }
rm -f "$marker"

ok "Blocks rebuilt: $(basename "$SO") $(md5sum "$SO" | cut -c1-12) $(date -r "$SO" '+%Y-%m-%d %H:%M:%S')"
log "fly it with ./scripts/sim_up.sh"
