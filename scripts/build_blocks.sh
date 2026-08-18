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
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
# One implementation, in scripts/vendor_patches.sh. This block was a copy of convert_world.sh's,
# differing only in the remedy sentence.                                                (SIM-25)
. "$REPO/scripts/vendor_patches.sh"
vp_apply_unreal "$REPO" "$PLUGIN" \
  "Re-cut the patch, or delete $PLUGIN and re-run upstream's build.sh for a clean copy first."

SO="$PLUGIN/Binaries/Linux/libUnrealEditor-AirSim.so"

# "Already applied" is a statement about SOURCE, and the thing that flies is the BINARY. Skipping
# the build on source state alone was wrong: if a previous run patched and then the compile
# failed, a re-run would report "nothing to rebuild" and exit 0 with patched source and an
# UNPATCHED .so -- the silent-skip failure this script's header is about, reproduced by the
# script itself. So the skip now has to be earned by the artifact.
if [ "$VP_APPLIED" -eq 0 ] && [ -z "$FORCE" ]; then
  # ONE rule, in scripts/vendor_patches.sh: convert_world.sh had the un-earned version and
  # could ship a plugin without 0005.                                     (review, PR 61)
  if ! vp_needs_rebuild "$PLUGIN" "$SO"; then
    ok "every patch already applied and $(basename "$SO") is newer than the patched source;
         nothing to rebuild (use --force to compile anyway)"
    exit 0
  fi
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

[ -f "$SO" ] || { rm -f "$marker"; die "no plugin binary at $SO after a build that reported success"; }
[ "$SO" -nt "$marker" ] || { rm -f "$marker"; die "$(basename "$SO") is NOT newer than the pre-build marker.
       UnrealBuildTool reported success without producing an artifact, so whatever is on disk is
       the OLD binary -- and it is the old binary that would fly."; }
rm -f "$marker"

ok "Blocks rebuilt: $(basename "$SO") $(md5sum "$SO" | cut -c1-12) $(date -r "$SO" '+%Y-%m-%d %H:%M:%S')"
log "fly it with ./scripts/sim_up.sh"
