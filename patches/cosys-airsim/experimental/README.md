# Experimental patches — NOT applied by any build script

**Three** scripts glob `patches/cosys-airsim/*.patch` and apply what they find:
`build_airsim_wrapper.sh:66`, `build_blocks.sh:65` and `convert_world.sh:154`. All three globs are
**non-recursive**, so anything in this subdirectory is deliberately excluded from the shipped
configuration. If you ever make one of them recursive, everything here starts shipping — for
`0007` that means gating physics on every converted world, which kills bring-up.

## `0004-scene-capture-ldr.patch`

Changes the `Scene` capture source from `SCS_FinalToneCurveHDR` to `SCS_FinalColorLDR`
(`PIPCamera.cpp:178`), matching what every other image type already uses.

**Status: unvalidated. It has never actually run.**

It was built and deployed on 2026-08-02 and recorded as a negative result — but on 2026-08-03
the plugin loader was found to have been resolving a *different* copy: `--force` had left
backups inside `Plugins/`, Unreal de-duplicates plugins by name+version and kept the stale one,
so the patched binary was ignored. The md5 check that "verified" it inspected the file on disk,
not the one the engine loaded. So the negative result says nothing about the patch.

**It is not needed.** The washout it was written to fix was traced to three other causes
entirely (RGB/BGR in the measurement client, the camera being inside world geometry, and Lumen
GI being explicitly disabled). With those addressed on a **stock** plugin, AirSim's capture
matches Unreal's own render of the same view to 1.15 of 255.

**If it is ever revisited**, re-test `TargetGamma` with it. Gamma came back as a clean null on
stock (1.4 → 1.0 changed nothing), which is odd for a setting that is genuinely applied at
`PIPCamera.cpp:747` — plausibly because the HDR capture source bypasses the render target's
gamma encode. If so, `TargetGamma` would only start to matter *with* this patch, and the
"gamma is refuted" finding is strictly a stock-HDR result.

See `docs/worklog/2026-08-03-c11-washout-root-cause.md`.

## `0007-gate-physics-on-streaming.patch`

Gates AirSim's physics start on World Partition streaming, so the vehicle never begins falling
before the terrain under it exists. `PhysicsWorld` is constructed with
`start_async_updator = false`, and `ASimModeWorldBase::Tick` starts the updator once
`isStreamingSettled()` returns true or a 300 s deadline expires.

**Status: parked 2026-08-16. The mechanism works; its release condition is ruled out.**

The gate itself is proven — physics holds at `z = -0.000` and the escape releases, both measured.
What does not work is the predicate. `IsStreamingCompleted(Activated, {source at the vehicle})`
returns `TRUE` **0.400 s** in, on the far-field surrogate, and **never returns `FALSE` again in
600 s** — straight through the surrogate being replaced by the real city. The vehicle falls 32 m
at that swap. A dwell requirement cannot help, because dwell only helps if the signal changes.
`SIM-30`'s pause-and-probe is the official solution instead.

**It lives here because the build scripts would otherwise ship it.** `build_blocks.sh:65` and
`convert_world.sh:154` glob `patches/cosys-airsim/*.patch` and apply anything whose diff touches
`Unreal/`. `0007` matches. Left at the top level, the next world conversion would silently gate
physics — which starves PX4's HIL stream and kills bring-up on "no finite EKF origin" with ~166
`poll timeout` errors. This directory is excluded by the non-recursive glob; that is the whole
point of it.

**Nine known defects, found by two review passes and NOT fixed — fix them before this is ever
un-parked.** Defect 1 was corrected after the second pass showed the first pass had its mechanism
backwards, which is itself a reason to re-derive rather than trust this list:

1. **`simPause(true)` during the gate window WEDGES the gate — it does not get discarded.**
   *(Corrected 2026-08-16 by the second review; the first version of this list had the mechanism
   backwards, and a maintainer would have fixed the wrong thing.)* `ASimModeWorldBase::pause()`
   calls `physics_world_->pause()` **and** `ASimModeBase::pause()`, and the latter calls
   `UGameplayStatics::SetGamePaused` (`SimModeBase.cpp:1472`). No AirSim actor sets
   `bTickEvenWhenPaused`, so while the pause is in effect `Tick()` does not run at all — the gate
   cannot release, and its escape timer cannot advance. `sim_up.sh`'s `ensure_grounded` pauses
   right after `wait_for_sim_link` and holds for up to `STREAM_PAUSE_MAX_S` (600 s), which is
   longer than the gate's own 300 s deadline. A deadlock between the gate and the workaround that
   replaced it.
2. **`continueForTime`/`continueForFrames` hang the game thread while the gate is closed.** They
   busy-wait on `isPaused()`, which only flips inside `executorLoop()` — not running before
   release. Blocking the game thread means `Tick()` never runs, so the gate can never release and
   the 300 s escape never fires. A hang, which is exactly what the design set out to avoid.
3. **The early return skips `updateRenderedState`/`updateRendering` for every vehicle** for up to
   300 s, so a `simSetVehiclePose` during the wait is accepted and never applied — capture scripts
   would silently photograph the spawn pose, and the gate's own query keeps sampling a stale
   `getUUPosition()`.
4. **The 600 s per-tick probe is unconditional**, with no cvar or `#if !UE_BUILD_SHIPPING` — ~36k
   `IsStreamingCompleted` calls after release, on the worlds where that query is most expensive.
5. **The empty-vehicle escape latches the gate permanently open**, so vehicles added later via
   `registerPhysicsBody()` get no protection.
6. **Unchecked `static_cast<PawnSimApi*>`** over a `VehicleSimApiBase*` list; the following null
   check cannot catch a wrong-type entry.
7. **`ScheduledExecutor`'s state is indeterminate for the whole gate window.**
   `start_async_updator = false` means `World::startAsyncUpdator()` never runs, and that is the
   only caller of `ScheduledExecutor::initialize()`. Its default constructor initialises nothing
   and `PhysicsWorld` is heap-allocated, so `paused_`, `started_` and `period_nanos_` hold
   indeterminate values for up to 300 s. `simIsPaused` then reads an atomic never stored to, and
   ending the level before release runs `~World → executor_.stop()`, which branches on
   uninitialised `started_` and may `join()` a default-constructed thread.
8. **The gate blinds `ensure_grounded`, which is the safety net it defers to.** With physics
   held, `vz` is *exactly* 0 — and that is the signal `SIM-30`'s probe uses to decide the vehicle
   is resting. Its first `resting()` check therefore passes unconditionally and it reports "on
   ground" for any world. The worklog records `settled at z=-0.000` / `on ground at z=-0.000` as
   evidence the gate works; it equally means the net was blind, and PX4 initialises its EKF origin
   against a vehicle that starts falling the moment the gate releases.
9. **The 300 s escape is measured in TICKED GAME TIME, not wall clock.** It accumulates `Tick`'s
   `DeltaSeconds`, so it stops advancing whenever the game is paused (defect 1) or the game thread
   stalls — both of which this patch's own measurements produce. The header's guarantee that the
   updator starts "after `kPhysicsGateTimeoutS` regardless" is not bounded in the only clock an
   operator or bring-up script actually has.

Revisit note: CitySample lives on the 7 TB spinning disk, against this project's own rule. Slow
storage does not cause the predicate bug but it sets its blast radius — copy the world to internal
NVMe and re-run the instrumented build before writing any new predicate.

See `docs/worklog/2026-08-16-sim32-gating-physics-on-streaming.md`.
