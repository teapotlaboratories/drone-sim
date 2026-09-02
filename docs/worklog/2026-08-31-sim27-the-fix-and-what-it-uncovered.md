# SIM-27 — the fix, and the thing it uncovered

**Date:** 2026-08-31 (later the same day) · Follows
`2026-08-31-sim27-the-witness-was-the-bug.md`, which established the cause.

> **Rewritten 2026-09-02, and that needs saying out loud.** Worklogs in this project are frozen
> once written — the rule exists so a record cannot be tidied after the fact. This file is the
> documented exception, at the owner's instruction in `docs/handoff-2026-09-01.md`: *"is WRONG.
> It describes the vendor-patch fix, which was abandoned twice over after it was written.
> Rewrite it around the harness-side fix before committing anything."*
>
> It had never been committed, so nothing in history is being changed. What follows keeps the
> cause and the two hard-won lessons from the abandoned attempt, and replaces the fix and its
> measurements — the old numbers measured a patch that was deleted, and leaving them in would
> have been a false record of what shipped.

## The cause — unchanged, and still the whole story

`has_collided` is one-shot, and upstream clears it inside `getCollisionInfoAndReset()`, reached
only from the `simGetCollisionInfo` RPC binding. So an external **observer** was clearing a flag
the **physics engine** depends on: `watch_collisions.py` polled that RPC at 20 Hz, consumed the
touchdown contact, and `FastPhysicsEngine` never engaged the ground lock. The integrator kept
descending through the world while the Unreal actor rested on the surface, every PX4 sensor
described a fall that was not happening, and `LAND` ran out its budget.

## The fix that was NOT shipped, and why it was abandoned

The direct fix is to move the ownership in Cosys-AirSim: stop `getCollisionInfoAndReset()`
resetting, add a `clearCollisionFlag()` called from `MultirotorPawnSimApi::updateRenderedState`
immediately after the contact is copied into the physics body, making that handoff the sole
consumer. It works, and it was measured working.

**It was still the wrong change.** Patching an upstream so that *our* observation tool stops
breaking it inverts this project's primary rule — reuse and integrate upstream, don't reinvent.
The tool is ours and the defect is ours; the vendor tree was doing what its own API documents.
The patch (`0012`) was deleted, the plugin was reverted to byte-identical upstream, and
`tests/test_collision_witness.py::test_no_vendor_patch_is_needed_for_the_collision_fix` now
fails if a collision-ownership patch reappears in the auto-applied directory.

### Two things the abandoned attempt taught, which are still true

**A change under `AirLib/src` does nothing.** The obvious first edit was the RPC binding itself,
`AirLib/src/api/RpcLibServerBase.cpp`. It compiles, the build reports success, and it changes
nothing: AirLib ships as a **prebuilt static library** (`AirSim.Build.cs:53`, `AddLibDependency`
→ `Source/AirLib/lib/libAirLib.a`), so UnrealBuildTool never compiles `AirLib/src/*.cpp`. It
cost a full verification gate to discover — 3 seeds, ~35 minutes, behaving exactly as before.

**Headers are compiled** into the plugin's own translation units, which is why every probe in
`FastPhysicsEngine.hpp` worked and this did not. The rule: a change under `AirLib/src` requires
rebuilding the static library; a change in plugin sources or AirLib headers does not.

## The fix that shipped: the witness brackets the flight instead of watching it

If the RPC cannot be read without consuming the flag — and it cannot; the alternative channel is
closed too, because upstream's `UE_LOG` calls in `UAirBlueprintLib::LogMessage` are commented
out — then an observer must simply stop reading it during a flight.

So `watch_collisions.py` **brackets** the airborne phase with **exactly two reads**: it samples
`collision_count` as the vehicle crosses an altitude gate going up, and again as it crosses back
down, and reports the difference. `collision_count` is monotonic and was never part of the
reset. `simGetVehiclePose` is a plain getter that consumes nothing, so the altitude gate itself
can be polled as fast as we like.

The witness is **blind between those two samples**, and says so in its own output rather than
implying coverage it does not have. A run that never crosses the gate reports `measured: false`
and scores UNKNOWN — never clean.

## Verification

`citysample-gate1`, 3 seeds, witness ON — the configuration that split 10 of 12:

| | before | after |
|---|---|---|
| worst pose split | **24.75 m** | **1.009 m** |

The landing defect is fixed: the flag survives to the physics tick, the ground lock engages, and
landings terminate.

## What it uncovered, and how that premise changed

Making collisions honest surfaced a seed held several metres above the ground, climbing, in
continuous contact with the same actor this ticket had circled from the start. It was filed as
`SIM-44` on the theory that a far-field visual impostor was carrying collision geometry it
should not.

**That premise was corrected on 2026-09-01 and the theory withdrawn.** Zooming the chase footage
and pulling the aircraft's own front camera at the same instant settles it: a branch passes
through the drone and the onboard view is filled by trunk and canopy at AirSim truth
`z = -4.98 m`. **It is a tree.** The aircraft took off under one and climbed into it, and the
collision is entirely legitimate — stripping the proxy's collision would have let the aircraft
fly through trees.

So the finding is not about physics at all. It is about **where the vehicle was put**, which is
what `SIM-44` became, and what `SIM-45` was eventually built to make visible.

## Status

Landing fixed and verified. The witness rewrite, its tests and the corrected spawn are part of
the change set committed on 2026-09-02 alongside `SIM-45`–`SIM-49`; the review pass that day
also fixed three consumers of this witness that the rewrite had left behind — see
`2026-09-01-sim45-a-web-interface-over-ros-2.md`.
