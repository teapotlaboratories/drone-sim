#!/usr/bin/env bash
# The ONE implementation of the Unreal-vs-ROS2 patch routing rule.                     (SIM-25)
#
# Sourced, not executed: `. "$REPO/scripts/vendor_patches.sh"`.
#
# WHY THIS FILE EXISTS
# --------------------
# `patches/cosys-airsim/` holds deviations for BOTH halves of Cosys-AirSim -- the ROS 2 wrapper
# and the Unreal plugin -- and every consumer has to decide which half a given patch belongs to.
# That decision lived in THREE scripts:
#
#   build_airsim_wrapper.sh   skips Unreal-side patches (its tree has no Unreal/)
#   convert_world.sh          applies them to a world's injected plugin
#   build_blocks.sh           applies them to the Blocks plugin, copy-pasted from the above
#
# Misrouting is not a cosmetic failure. Miss `0005` and the vehicle falls through a World
# Partition world forever; miss `0006` and the renderer dies mid-flight. All three copies already
# carried a `die` for the drift case, which is the tell: the risk was understood and the cause was
# not addressed.
#
# THIS REPO HAS PAID FOR THE SAME MISTAKE TWICE.
#   * `collision_witness.py` exists because "an absent witness is UNKNOWN" was written twice, and
#     the two copies disagreed WITHIN ONE COMMIT -- one scoring a run PASS that the other failed.
#   * 2026-08-17: `patches/cosys-airsim/0007` was described as "applied to nothing" three times in
#     one session. One script had been checked; all three globbed the directory and 0007 matched
#     every one, so the next world conversion would have silently shipped a physics gate that
#     kills bring-up. Caught by review, not by the claim being checkable.
#
# THE GLOB IS NON-RECURSIVE, AND THAT IS LOAD-BEARING. `patches/cosys-airsim/experimental/` is how
# a patch is parked -- 0004 and 0007 live there precisely because this glob does not reach them.
# Making it recursive would ship both.

# The routing predicate. ONE definition, because two would eventually disagree.
#
# Match the DIFF HEADER, never the prose. These patches carry long explanatory headers: 0005
# mentions the Unreal plugin path four times but only twice in a `+++ b/` line, so a content grep
# would route a ROS-side patch to the plugin merely for describing it -- and silently skipping a
# patch is the exact failure this filter exists to prevent.
vp_is_unreal_side() {
  grep -qE '^\+\+\+ b/Unreal/' "$1"
}

# Every patch this routing rule considers, in a stable order. Callers must not re-glob: a second
# glob is a second copy of "which patches exist", which is the bug this file closes.
vp_list() {
  local repo="$1" p
  for p in "$repo"/patches/cosys-airsim/*.patch; do
    [ -e "$p" ] || continue
    printf '%s\n' "$p"
  done
}

# Apply every Unreal-side patch to a plugin root. Idempotent, and FATAL on drift.
#
#   vp_apply_unreal <repo> <plugin_root> <remedy-sentence>
#
# The remedy differs by caller -- re-inject with --force for a converted world, delete the plugin
# and re-run upstream's build.sh for Blocks -- so it is a parameter rather than a third copy of
# the loop with one sentence changed.
# Callers need the counts afterwards -- build_blocks.sh skips the compile when nothing was newly
# applied. They are published as VP_APPLIED / VP_ALREADY rather than returned, because a bash
# function's return value is an exit status and squeezing two numbers through it is how the next
# person gets it wrong.
VP_APPLIED=0
VP_ALREADY=0

vp_apply_unreal() {
  local repo="$1" plugin="$2" remedy="$3"
  local applied=0 already=0 p name

  [ -d "$plugin" ] || { vp_die "plugin root does not exist: $plugin"; return 1; }

  while IFS= read -r p; do
    vp_is_unreal_side "$p" || continue                     # ROS 2 wrapper patch; not ours
    name=$(basename "$p")
    # -p4 strips a/Unreal/Plugins/AirSim -> the plugin root (which contains Source/). --forward
    # makes re-runs idempotent; --batch stops patch prompting on stdin if the strip level is ever
    # wrong, because an interactive prompt here would hang the caller with no output.
    if patch -p4 -d "$plugin" --forward --batch --dry-run < "$p" >/dev/null 2>&1; then
      patch -p4 -d "$plugin" --forward --batch --silent < "$p" >/dev/null
      vp_log "  applied  $name"; applied=$((applied + 1))
    elif patch -p4 -R -d "$plugin" --batch --dry-run < "$p" >/dev/null 2>&1; then
      # Reverse-applies cleanly => already in the tree. Benign, and the common case on a re-run.
      vp_log "  already  $name"; already=$((already + 1))
    else
      # NEVER "probably fine". Neither applying nor reverse-applying means the plugin has drifted
      # from what the patch was cut against, and skipping quietly reinstates the exact defect the
      # patch exists to prevent -- with nothing to point at afterwards.
      vp_die "$name does NOT apply to $plugin and is not already present.
         The plugin has drifted from what the patch was cut against. $remedy"
      return 1
    fi
  done < <(vp_list "$repo")

  VP_APPLIED=$applied
  VP_ALREADY=$already
  [ "$applied" -gt 0 ] || [ "$already" -gt 0 ] \
    || vp_warn "no Unreal-side patches found under patches/cosys-airsim/ -- nothing to apply"
  return 0
}

# Each caller has its own log/die/warn with its own prefix; use them when present so output keeps
# looking like the script the operator invoked, and fall back when sourced from anywhere else.
vp_log()  { if declare -F log  >/dev/null 2>&1; then log  "$@"; else printf '%s\n' "$*"; fi; }
vp_warn() { if declare -F warn >/dev/null 2>&1; then warn "$@"; else printf 'WARN: %s\n' "$*" >&2; fi; }
vp_die()  { if declare -F die  >/dev/null 2>&1; then die  "$@"; else printf 'FATAL: %s\n' "$*" >&2; exit 1; fi; }
