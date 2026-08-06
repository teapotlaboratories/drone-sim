#!/usr/bin/env bash
# Convert someone else's Unreal project into a world this simulator can fly.
#
#   ./scripts/convert_world.sh /path/to/Your.uproject --map /Game/Maps/YourMap
#   ./scripts/convert_world.sh /path/to/Your.uproject --map /Game/Map/Small_City_LVL --force
#   ./scripts/convert_world.sh /path/to/Your.uproject --no-build     # inject + patch only
#
# WHAT THIS DOES, AND WHY IT IS NOT JUST inject_airsim.py
#
# `inject_airsim.py` handles the CONTENT-ONLY case (A1) completely: it copies the built plugin,
# points GlobalDefaultGameMode at AirSimGameMode, and sets the default map. For a project that
# ships its own `Source/` (A2) it prints a warning and stops, because from there the project's
# C++ must be COMPILED against UE5.8 -- and that is the part that actually costs a day.
#
# This script is that missing half. It runs the injection, applies the Unreal-side vendor
# patches to the injected plugin, relaxes the two warnings UE5.8 turns into errors, and drives
# UnrealBuildTool in the engine container. Every step below exists because it was hit for real
# converting Epic's CitySample; see docs/worlds.md and docs/todo.md SIM-21.
#
# THE FOUR THINGS THAT GO WRONG, IN THE ORDER YOU HIT THEM
#
#   1. The world loads but PX4 never connects. GlobalDefaultGameMode was the project's own game
#      mode, so AirSim never instantiated and nothing ever listened on 4560. There is no error --
#      PX4 just waits. inject_airsim.py fixes this; the failure is silent, so it is worth naming.
#   2. The build fails on -Werror. UE5.8 ships a newer clang than most projects were written
#      against and promotes two unreachable-code warnings to errors. --relax-warnings (default
#      ON for A2) downgrades exactly those two rather than patching upstream source.
#   3. UBT refuses the compiler args, because the editor target shares build products with
#      UnrealEditor. The documented alternative, TargetBuildEnvironment.Unique, forces a full
#      ENGINE rebuild -- hours, for two warnings. bOverrideBuildEnvironment=true is the cheap way.
#   4. The drone falls through the world forever. World Partition streams cells around a
#      registered streaming source and AirSim's pawn was not one, so NO cell ever loaded and
#      there was no collision geometry anywhere. patches/cosys-airsim/0005 fixes it.
#
# AND THE ONE THAT IS STILL NOT FIXED
#
# Patch 0005 is necessary but NOT sufficient: cell streaming takes seconds while the vehicle
# falls immediately, so it can still lose the race. Measured 2 successes in 3 identical runs.
# If bring-up fails with a huge positive `z`, RETRY -- and judge a run by resting `z`, never by
# whether the level looked like it loaded. Details in docs/vendor/cosys-airsim.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_IMAGE=${ENGINE_IMAGE:-drone-sim/unreal:ue5.8}
UE=/home/ue4/UnrealEngine
MAP=""
FORCE=""
DO_BUILD=1
RELAX_WARNINGS=auto
UPROJECT=""

C=$'\033[36m'; Y=$'\033[33m'; R=$'\033[31m'; G=$'\033[32m'; X=$'\033[0m'
log()  { printf '%s[convert]%s %s\n' "$C" "$X" "$*"; }
warn() { printf '%s[convert] WARNING:%s %s\n' "$Y" "$X" "$*"; }
ok()   { printf '%s[convert] %s%s\n' "$G" "$*" "$X"; }
die()  { printf '%s[convert] FATAL:%s %s\n' "$R" "$X" "$*" >&2; exit 1; }

usage() { sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --map)             MAP="${2:?--map needs a value}"; shift 2 ;;
    --map=*)           MAP="${1#*=}"; shift ;;
    --force)           FORCE="--force"; shift ;;
    --no-build)        DO_BUILD=0; shift ;;
    --relax-warnings)  RELAX_WARNINGS=1; shift ;;
    --no-relax-warnings) RELAX_WARNINGS=0; shift ;;
    -h|--help)         usage ;;
    -*)                die "unknown option: $1" ;;
    *)                 UPROJECT="$1"; shift ;;
  esac
done

[ -n "$UPROJECT" ] || usage
[ -f "$UPROJECT" ] || die "no such .uproject: $UPROJECT"
case "$UPROJECT" in *.uproject) ;; *) die "not a .uproject: $UPROJECT" ;; esac

UPROJECT="$(cd "$(dirname "$UPROJECT")" && pwd)/$(basename "$UPROJECT")"
ROOT="$(dirname "$UPROJECT")"
NAME="$(basename "$UPROJECT" .uproject)"

# The simulator's live working set wants the internal NVMe -- UE5 is latency-sensitive random
# I/O. Say so rather than silently being slow; this is a warning, not a refusal, because a
# spinning disk still works and the operator may have no choice.
src_dev=$(df --output=source "$ROOT" 2>/dev/null | tail -1 || true)
if [ -n "$src_dev" ]; then
  pk=$(lsblk -no PKNAME "$src_dev" 2>/dev/null | head -1 || true)
  if [ -n "$pk" ] && [ "$(cat "/sys/block/$pk/queue/rotational" 2>/dev/null || echo 0)" = "1" ]; then
    warn "this world lives on a ROTATIONAL disk ($pk). UE5 streams badly from spinning media --
       expect long loads, and expect patch 0005's streaming race (see header) to lose more often."
  fi
fi

log "project : $NAME"
log "root    : $ROOT"

TIER=A1
[ -d "$ROOT/Source" ] && TIER=A2
log "tier    : $TIER$([ "$TIER" = A2 ] && echo "  (ships Source/ -- a compile is required)" || echo "  (content/Blueprint only -- no compile)")"
[ "$RELAX_WARNINGS" = auto ] && { [ "$TIER" = A2 ] && RELAX_WARNINGS=1 || RELAX_WARNINGS=0; }

# Validate the map BEFORE step 1. Injection rewrites config and copies a plugin; failing
# after that would leave a half-converted project behind for a typo.
if [ -z "$MAP" ]; then
  warn "no --map given. The project keeps its own default map, which is usually NOT what you
       want: if that map has no AirSim-compatible ground, the vehicle has nothing to land on."
else
  # VALIDATE THE MAP EXISTS. A wrong content path is written into DefaultEngine.ini without
  # complaint and then fails exactly like every other silent failure here: the level never
  # loads, AirSim never starts, PX4 waits forever on 4560. Caught by getting it wrong -- a
  # plausible-looking /Game/Maps/Demonstration was accepted for a project whose map is actually
  # /Game/CityPark/Maps/Showcase.
  case "$MAP" in
    /Game/*)
      umap="$ROOT/Content/${MAP#/Game/}.umap"
      if [ ! -f "$umap" ]; then
        printf '%s[convert] FATAL:%s no such map: %s\n' "$R" "$X" "$MAP" >&2
        printf '       expected it at: %s\n' "$umap" >&2
        printf '       maps this project actually has:\n' >&2
        find "$ROOT/Content" -name '*.umap' 2>/dev/null | head -10 | while read -r m; do
          rel="${m#"$ROOT/Content/"}"; printf '         /Game/%s\n' "${rel%.umap}" >&2
        done
        exit 1
      fi
      log "map     : $MAP  (verified on disk)" ;;
    *) warn "--map $MAP is not under /Game/, so it cannot be checked on disk. If the level
       never loads, an unresolvable map path is the first thing to suspect." ;;
  esac
fi

# --------------------------------------------------------------------------------------
# 1. Inject AirSim -- plugin, game mode, default map.
# --------------------------------------------------------------------------------------
log "step 1/4  injecting AirSim (plugin, GlobalDefaultGameMode, default map)"
inject_args=("$UPROJECT")
[ -n "$MAP" ]   && inject_args+=(--map "$MAP")
[ -n "$FORCE" ] && inject_args+=("$FORCE")
python3 "$REPO/scripts/inject_airsim.py" "${inject_args[@]}" \
  || die "injection failed -- nothing was built, the project is unchanged apart from any backup"

# --------------------------------------------------------------------------------------
# 2. Apply the Unreal-side vendor patches to the INJECTED copy.
#
# vendor/Cosys-AirSim stays pristine (least-destructive vendor edits). inject_airsim.py copies
# the BUILT plugin from Blocks, which does not carry these, so they are applied here to the
# project's own copy. Only patches touching Unreal/Plugins are relevant -- the others are ROS 2
# wrapper patches applied by build_airsim_wrapper.sh.
# --------------------------------------------------------------------------------------
PLUGIN="$ROOT/Plugins/AirSim"
[ -d "$PLUGIN" ] || die "expected $PLUGIN after injection, and it is not there"

log "step 2/4  applying Unreal-side vendor patches to the injected plugin"
applied=0; skipped=0
for p in "$REPO"/patches/cosys-airsim/*.patch; do
  [ -e "$p" ] || continue
  grep -q 'Unreal/Plugins/AirSim' "$p" || continue          # ROS 2 wrapper patch; not ours
  name=$(basename "$p")
  # -p4 strips a/Unreal/Plugins/AirSim -> the plugin root (which contains Source/). --forward
  # makes re-runs idempotent, and --batch stops patch prompting on stdin if the level is ever
  # wrong again -- an interactive prompt here would hang the whole conversion.
  if patch -p4 -d "$PLUGIN" --forward --batch --dry-run < "$p" >/dev/null 2>&1; then
    patch -p4 -d "$PLUGIN" --forward --batch --silent < "$p" >/dev/null
    log "  applied  $name"; applied=$((applied + 1))
  elif patch -p4 -R -d "$PLUGIN" --batch --dry-run < "$p" >/dev/null 2>&1; then
    # Reverse-applies cleanly => it is ALREADY in the tree. Benign, and the common case on a
    # re-run.
    log "  already  $name"; skipped=$((skipped + 1))
  else
    # Neither applies nor reverse-applies: the patch no longer matches this plugin. Do NOT
    # continue quietly. Treating this as "already applied" was a real defect -- 0005 is what
    # stops the vehicle falling through a World Partition world, so silently skipping it
    # produces exactly the failure the patch exists to prevent, with nothing to point at.
    die "$name does NOT apply to $PLUGIN and is not already present.
       The plugin has drifted from what the patch was cut against. Do not fly a World Partition
       world without 0005 -- the vehicle will fall through it forever. Re-cut the patch, or
       re-inject with --force to get a clean plugin copy first."
  fi
done
[ "$applied" -gt 0 ] || [ "$skipped" -gt 0 ] || warn "no Unreal-side patches found under patches/cosys-airsim/"

# A patched plugin has SOURCE that must be compiled, so it forces a build even for a project
# that would otherwise be A1. Say so plainly rather than letting the user wonder.
if [ "$applied" -gt 0 ] && [ "$DO_BUILD" -eq 0 ]; then
  warn "patches were applied but --no-build was given: the plugin source now differs from its
       compiled binary, and the change will NOT take effect until the project is built."
fi

# --------------------------------------------------------------------------------------
# 3. Relax the two warnings UE5.8 promotes to errors.
# --------------------------------------------------------------------------------------
# ONLY an *Editor.Target.cs counts. There used to be a fallback to any *.Target.cs here, which
# silently selected the GAME target -- contradicting the rule two steps below and producing a
# build that compiles, passes the artifact check, and then fails at runtime as "PX4 waits
# forever on 4560" with no error line.
TARGET_CS=""
if [ "$TIER" = A2 ]; then
  # Prefer the target NAMED for the project. A plain *Editor glob sorts alphabetically, and on
  # CitySample that silently selected CitySampleCookedEditor.Target.cs -- a content-cooking
  # target, not the editor target `UnrealEditor <project> -game` runs. It happened to build, which
  # is exactly why it needed catching by name rather than by outcome.
  TARGET_CS=""
  [ -f "$ROOT/Source/${NAME}Editor.Target.cs" ] && TARGET_CS="$ROOT/Source/${NAME}Editor.Target.cs"
  [ -n "$TARGET_CS" ] || TARGET_CS=$(ls "$ROOT/Source/"*Editor.Target.cs 2>/dev/null | head -1 || true)
  [ -n "$TARGET_CS" ] || warn "this project ships Source/ but no *Editor.Target.cs. Falling back to
       the engine's own UnrealEditor target, which compiles the project's plugins but NOT its
       game modules -- if the world needs those, add an editor target to the project."
fi

if [ "$RELAX_WARNINGS" = 1 ] && [ -n "$TARGET_CS" ]; then
  if grep -q 'drone-sim: UE5.8 warning relaxation' "$TARGET_CS"; then
    log "step 3/4  warning relaxation already present in $(basename "$TARGET_CS")"
  else
    log "step 3/4  relaxing 2 UE5.8 warnings-as-errors in $(basename "$TARGET_CS")"
    cp "$TARGET_CS" "$TARGET_CS.pre-airsim"
    python3 - "$TARGET_CS" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
block = '''
\t\t// drone-sim: UE5.8 warning relaxation.
\t\t// UE5.8 ships a newer clang than most projects were written against and promotes two
\t\t// unreachable-code warnings to errors under -Werror. Downgrading exactly these two is
\t\t// narrower than disabling warnings-as-errors wholesale, and it touches no upstream source.
\t\t// bOverrideBuildEnvironment is required with it: UBT otherwise refuses per-target compiler
\t\t// args for a target sharing build products with UnrealEditor, and its suggested
\t\t// alternative (TargetBuildEnvironment.Unique) forces a full ENGINE rebuild.
\t\tbOverrideBuildEnvironment = true;
\t\tAdditionalCompilerArguments += " -Wno-error=unreachable-code-break -Wno-error=unreachable-code-loop-increment";
'''
# Insert at the end of the constructor body: match the ctor, then its closing brace.
m = re.search(r'(public\s+\w+Target\s*\(\s*TargetInfo\s+Target\s*\)\s*:\s*base\s*\(\s*Target\s*\)\s*\{)', s)
if not m:
    print("  could not find the target constructor -- NOT modified", file=sys.stderr)
    sys.exit(3)
i, depth = m.end(), 1
while i < len(s) and depth:
    if s[i] == '{': depth += 1
    elif s[i] == '}': depth -= 1
    i += 1
open(p, 'w').write(s[:i-1] + block + s[i-1:])
print("  inserted")
PY
    rc=$?
    [ "$rc" -eq 0 ] || die "could not patch $(basename "$TARGET_CS") (rc=$rc). Add these two lines
       to its constructor by hand, then re-run with --no-relax-warnings:
         bOverrideBuildEnvironment = true;
         AdditionalCompilerArguments += \" -Wno-error=unreachable-code-break -Wno-error=unreachable-code-loop-increment\";"
  fi
else
  if [ "$TIER" = A1 ]; then
    why=" (A1 -- no project target file exists to patch)"
  elif [ -z "$TARGET_CS" ]; then
    why=" (no *Editor.Target.cs in this project -- nothing to patch)"
  else
    why=" (--no-relax-warnings)"
  fi
  log "step 3/4  warning relaxation skipped$why"
fi

# --------------------------------------------------------------------------------------
# 4. Build.
# --------------------------------------------------------------------------------------
if [ "$DO_BUILD" -eq 0 ]; then
  log "step 4/4  build skipped (--no-build)"
elif [ "$TIER" = A1 ] && [ "$applied" -eq 0 ]; then
  log "step 4/4  no build needed (A1, and no plugin patches to compile)"
else
  docker image inspect "$ENGINE_IMAGE" >/dev/null 2>&1 \
    || die "engine image $ENGINE_IMAGE is not present. It is credential-gated -- see README."

  # The editor target is what `UnrealEditor <project> -game` needs; a game target is not enough.
  #
  # A CONTENT-ONLY (A1) project has no target of its own, so "<Name>Editor" does not exist and
  # UBT fails with `Result: Failed (RulesError)`, exit 8 -- verified against CityPark. The stock
  # UnrealEditor target with -project is the correct choice there: it compiles the project's
  # plugins (which is the whole reason an A1 project needs a build at all after patching) and
  # needs no target file. Verified: exit 0, Succeeded.
  if [ -n "$TARGET_CS" ]; then
    TARGET=$(basename "$TARGET_CS" .Target.cs)
  else
    TARGET=UnrealEditor
  fi
  log "step 4/4  building $TARGET (Linux Development) -- this is the slow part"

  # Timestamp marker for the artifact check below. Counting .so files cannot work on its own:
  # injection copies a plugin that ALREADY contains one, so `so_count > 0` is true before any
  # compilation and the check can never fail -- it gave confidence it had not earned.
  marker=$(mktemp); : > "$marker"
  sleep 1                      # 1 s filesystem timestamp granularity; without it a fast build
                               # can produce artifacts that are not strictly -newer than marker
  cname="convert-build-$$"
  docker rm -f "$cname" >/dev/null 2>&1 || true
  # Same uid as the caller so the artifacts land owned by them, not root. sim-ddc is shared
  # with sim_up.sh so the derived-data cache survives between conversion and first run.
  docker run -d --name "$cname" --user "$(id -u):$(id -g)" \
    -v "$ROOT:/world" -v sim-ddc:/home/ue4/.config/Epic \
    --entrypoint bash "$ENGINE_IMAGE" -lc \
    "$UE/Engine/Build/BatchFiles/Linux/Build.sh $TARGET Linux Development \
       -project=/world/$(basename "$UPROJECT") -waitmutex 2>&1" >/dev/null \
    || die "could not start the build container"

  docker logs -f "$cname" 2>&1 | sed 's/^/    /' &
  tailpid=$!
  rc=$(docker wait "$cname" 2>/dev/null || echo 1)
  kill "$tailpid" 2>/dev/null || true; wait "$tailpid" 2>/dev/null || true

  if [ "$rc" != "0" ]; then
    echo
    docker logs "$cname" 2>&1 | grep -E 'error:' | head -12 | sed 's/^/    /' || true
    docker rm -f "$cname" >/dev/null 2>&1 || true
    if [ -n "$TARGET_CS" ]; then
      die "build FAILED (exit $rc). The errors above are the real ones; UBT's own summary is not.
       If they are -Werror warnings other than the two handled here, add them the same way in
       $(basename "$TARGET_CS")."
    fi
    die "build FAILED (exit $rc). The errors above are the real ones; UBT's own summary is not.
       This project has NO target file of its own, so there is nowhere to relax a warning: the
       build used the engine's UnrealEditor target. If these are -Werror warnings inside the
       AirSim plugin, they belong in a patch under patches/cosys-airsim/ rather than a build flag."
  fi
  docker rm -f "$cname" >/dev/null 2>&1 || true

  # Assert the ARTIFACT, not the exit code: a build can report success having compiled nothing
  # useful if the target was wrong. Only objects written by THIS build count.
  fresh=$(find "$ROOT/Binaries" "$ROOT/Plugins" -name '*.so' -newer "$marker" 2>/dev/null | wc -l)
  total=$(find "$ROOT/Binaries" "$ROOT/Plugins" -name '*.so' 2>/dev/null | wc -l)
  rm -f "$marker"
  if [ "$fresh" -eq 0 ]; then
    die "build reported success but wrote NO shared object ($total already present, none touched).
       Either the target compiled nothing for this project, or the wrong target was selected.
       Target was: $TARGET"
  fi
  log "built $fresh shared object(s) ($total present in total)"
fi

echo
ok "converted: $NAME"
cat <<EOF

  Fly it:
      ./scripts/sim_up.sh --world "$UPROJECT" --spawn 0,0,-50

  Then CHECK IT LANDED, because this is the failure that looks like success:
      docker exec sim-ros2 bash -lc 'source /opt/ros/jazzy/setup.bash; \\
        source /ros2_ws/install/setup.bash; ros2 topic echo --qos-reliability best_effort \\
        --qos-durability volatile --once --field z /fmu/out/vehicle_local_position'

  z is NED -- +z is DOWN. Near 0 means resting on the ground. A large positive z means the
  vehicle fell through: retry first (the streaming race in the header loses roughly 1 run in 3),
  and if it never lands, the map has no collision under the spawn point.
EOF
