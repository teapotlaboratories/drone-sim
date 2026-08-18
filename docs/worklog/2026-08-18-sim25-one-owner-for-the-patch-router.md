# SIM-25 — one owner for the patch routing rule, after paying for it three times

`patches/cosys-airsim/` holds deviations for **both** halves of Cosys-AirSim — the ROS 2 wrapper
and the Unreal plugin — so every consumer has to decide which half a patch belongs to. That
decision lived in three scripts:

| script | did |
|---|---|
| `build_airsim_wrapper.sh` | skipped Unreal-side patches (its tree has no `Unreal/`) |
| `convert_world.sh` | applied them to a world's injected plugin |
| `build_blocks.sh` | same, copy-pasted, differing only in one remedy sentence |

Misrouting is not cosmetic: miss `0005` and the vehicle falls through a World Partition world
forever; miss `0006` and the renderer dies mid-flight. All three copies already carried a `die`
for the drift case — which is the tell. The risk was understood; the cause was not addressed.

## This repo has paid for it twice before, and I paid again this week

- **`collision_witness.py` exists** because "an absent witness is UNKNOWN" was written twice, and
  the two copies disagreed **within one commit** — one scoring a run PASS that the other failed.
- **2026-08-17:** I told the owner `patches/cosys-airsim/0007` was "applied to nothing" **three
  times**. I had checked one script. All three globbed that directory and `0007` matched every
  one, so the next world conversion would have silently shipped a physics gate that kills
  bring-up. Caught by review, not by the claim being checkable.

## What shipped

`scripts/vendor_patches.sh`, sourced by all three:

- **`vp_is_unreal_side`** — the routing predicate, one definition. It matches the **diff header**
  (`^\+\+\+ b/Unreal/`), never the prose: `0005` mentions the plugin path four times but only
  twice in a `+++ b/` line, so a content grep would misroute a ROS-side patch for merely
  *describing* the plugin.
- **`vp_apply_unreal`** — the three-way apply / already / die loop, with the remedy sentence as a
  parameter, since that was the only real difference between the two copies.
- **`vp_list`** — so no caller re-globs. A second glob is a second copy of "which patches exist",
  which is the same bug one level down.

**The non-recursive glob is load-bearing and now has a test.** `experimental/` is how a patch is
parked; `0004` and `0007` live there *because* the glob does not reach them. Making it recursive
would ship both.

## Verified by building, and the artifact settles it

Both paths exercised: patches already present report `already`; reversed out and rebuilt, they
report `applied` and the compile succeeds. The resulting binary is
**`libUnrealEditor-AirSim.so` md5 `20f5430c1a61`** — byte-identical to the pre-refactor build from
two days earlier. Same inputs, same artifact.

## Two bugs the build caught that review would have had to reason about

**`$applied` became unbound.** Extracting the loop removed the variable, but callers still used
the count — `build_blocks.sh` to decide whether the compile can be skipped, and `convert_world.sh`
in *two* further places I had not noticed. Under `set -u` the build died after patching. The
counts are now published as `VP_APPLIED` / `VP_ALREADY`, deliberately not squeezed through a
return status, which is an exit code and would be the next person's mistake.

**And two of my new tests were wrong in the same way as one earlier today.** One matched the glob
inside a *comment*; the other rejected `-r` and hit `read -r`. Substring checks passing for the
wrong reason — the identical mistake as the `UnrealEditor-C` assertion in `SIM-33`, made twice more
within a few hours. Both now check code rather than text.

183 tests, up from 177.
