# SIM-32 — Gating physics on streaming: the query had to be scoped, and 0.4 s is too fast

Worklog for `SIM-32`, the fix that stops the vehicle falling during bring-up rather than
detecting and repairing the fall afterwards. Appended as the work happened.

## Why a gate at all

On a World Partition world the terrain streams in *around* the vehicle, and the vehicle starts
falling on the first physics tick. Everything built before this either raced that (`0005`,
registering the pawn as a streaming source — necessary, not sufficient) or reacted to it
(`SIM-30`'s pause-and-probe, which works and is a workaround made of timing constants).

The gate is the fix: `PhysicsWorld` is constructed with `start_async_updator = false`, and
`ASimModeWorldBase::Tick` starts the updator once the world says streaming has settled, or once
a 300 s deadline expires. A gate with no escape is a hang, which is worse than the race.

## First cut: `IsStreamingCompleted(nullptr)` — negative result

`nullptr` means "every streaming level in the world". On CitySample it never went true, so every
bring-up would pay the full 300 s timeout rather than releasing when the ground was ready. Slow
but functional, not a hang.

I also recorded a wrong conclusion here and corrected it: I claimed the timeout never fired. It
does. Held at `z = -8.000, vz = 0.000` for ~38 s, then released, fell, settled at `z = 0.698`.
I had not waited long enough before calling the escape hatch broken.

## Second cut: scope the query to the vehicle

`isStreamingSettled()` now builds one `FWorldPartitionStreamingQuerySource` per vehicle at
`PawnSimApi::getUUPosition()`, with `bUseGridLoadingRange = true` so the radius comes from the
world's own streaming grid rather than a number invented here, and asks:

```cpp
wp->IsStreamingCompleted(EWorldPartitionRuntimeCellState::Activated, sources, /*bExactState=*/false)
```

`Activated` is the state a cell must reach before its collision exists, which is the whole point
of waiting. Both the enum's header (`WorldPartition/WorldPartitionRuntimeCell.h`) and the
`static_cast<PawnSimApi*>` idiom were checked against the engine and plugin trees, not recalled.
Empty vehicle list returns `true`; `setupVehiclesAndCamera()` runs in `BeginPlay()`, before any
`Tick`, so that branch is a safety valve rather than a hole the gate falls through.

## A trap walked into and closed

Backing the plugin up as `Plugins/AirSim.pristine.bak` put a **second `AirSim.uplugin` inside
UE's recursive scan root**. Confirmed by listing before and after: two AirSim uplugins existed.
The backup now lives at `worlds/.plugin-backups/`, outside the scan root. This is the same
shadowing failure that has cost this project time before, reintroduced by the act of being
careful.

## Measured on CitySample, cold, 2026-08-16

Built from a pristine plugin with only the regenerated `0007` applied; the `.so` installed into
CitySample is md5-identical to the one built (`8a16919a8954`), 3 gate symbols in the binary.

| time (UTC) | event |
|---|---|
| 02:45:56 | bring-up start |
| 02:47:19 | `WorldPartition initialize took 27.7 sec` |
| — | **`SIM-32: streaming settled; physics started after seconds: 0.400000`** |
| 02:45–02:51 | `sim_up.sh`: `settled at z=-0.000`, `on ground at z=-0.000` |
| 02:51:17 | **FATAL** — no finite EKF origin; 166 `poll timeout` errors in PX4 |
| 02:52:25 | screen still **black**; `Preparing Textures (393)`, `Preparing Static Meshes (135)` |
| ~02:52:5x | vehicle at `z = +31.96, vz = +8.57` — **falling** |
| 02:53:32 | resting at `z = +0.751, vz = 0.000`; world now renders |

**The scoping works.** 0.4 s instead of the 300 s ceiling — the query does answer, and answers
fast, once it is asked about the cells under the vehicle instead of the whole world.

**0.4 s is the wrong answer.** The world was still black five minutes later. The on-screen log
names the mechanism:

```
Collision#2 with FastGeoSurrogateActor_0 - ObjID -1
```

The vehicle came to rest on the **far-field surrogate**, not on the city. Those cells are
`Activated`, so the query is satisfied. When the real cells stream in and replace the surrogate,
the support underneath the vehicle disappears and it falls — 32 m, then lands on the actual
ground at `z = 0.751`.

So a one-shot "is streaming complete" is answerable at a moment when the world is legitimately
complete *for the detail level currently requested*, and that is not the same question as "is
the ground under me the ground it will still be in a minute".

**Stated as a limit on the above:** the collision line and the timeline are what I have. I have
not directly instrumented the surrogate being swapped out, so the surrogate-swap account is the
leading explanation, not a proven one. The experiment that would settle it is in the next step.

## The other half, now demonstrated rather than predicted

The run died at 02:51:17 on **no finite EKF origin**, with 166 `poll timeout` errors and PX4
reporting `GPS Vertical Pos Drift too high` / `height estimate not stable`. Holding physics
starves PX4's HIL sensor stream, and PX4 was started as soon as TCP 4560 accepted a connection —
long before the gate had any verdict. `SIM-10` is not a separate task from this one; PX4 must
start *after* the gate releases. That coupling was written down as a prediction and is now a
measurement.

## Confound worth naming

CitySample lives on the 7 TB **spinning disk**, which the project's own rule says is the wrong
place for the simulator's live working set. Every duration above is inflated by that, and the
five-minute black screen may be mostly disk. It does not change the mechanism — the surrogate is
`Activated` regardless of how fast the real cells arrive — but it does mean these numbers are not
the numbers this fix would post on NVMe.

## Next

1. **Instrument instead of latching.** Log the query's value every tick for the whole bring-up
   rather than stopping at the first `true`. That answers directly whether it goes true at 0.4 s,
   goes false again as higher-detail cells are requested, and when it finally stays true.
2. **Then decide the predicate.** A dwell requirement (settled continuously for N seconds) is the
   obvious candidate, but only step 1 says whether a dwell that is short enough to be usable is
   also long enough to outlast the surrogate.
3. **Fold in `SIM-10`** — start PX4 after the gate releases.
4. Only then measure over N cold bring-ups, and on NVMe.

State on leaving: stack torn down and verified (0 containers, no `UnrealEditor`/`px4`/`Xvfb`/
`ffmpeg` by exact name, only the host's `X0` socket). CitySample's plugin restored from the
pristine backup — 0 `SIM-32` strings, exactly 1 `AirSim.uplugin` under `Plugins/`.

---

## Step 1 done — instrumented, and the answer kills the dwell idea

Rebuilt with two changes: the verdict now also goes out via `UE_LOG` (it had only been going to
the Slate overlay, so a headless run was blind and I had read it off a screenshot), and the query
is evaluated **every tick for 600 s** — logging on *change*, not latching on the first `true`.

Second cold CitySample bring-up, 03:00:39 UTC. Complete transition trace across the whole window:

```
[03.08.12] SIM-32 probe: streaming settled -> TRUE at 0.400 s
[03.08.12] SIM-32: streaming settled; physics started after 0.400 s
```

**One transition. `TRUE` at 0.400 s, and never `FALSE` again in 600 s.**

Two things follow.

**0.400 s is deterministic**, not a timing fluke — the identical value on a second cold bring-up.

**A dwell requirement cannot fix this.** That was the leading candidate going in: wait for the
query to hold true for N seconds. But dwell only helps if the signal *changes*, and this one does
not. The 600 s window covers the period in which the previous run's vehicle fell 32 m — the query
read `TRUE` continuously straight through the surrogate being replaced by the real city. Waiting
longer on a constant would add latency and nothing else.

The vehicle ended this run resting on the real pavement at **`z = +0.752`**, against `+0.751` last
run — the same fall-then-land sequence, reproduced to the millimetre. Bring-up FATAL on the EKF
origin again, as expected while PX4 start ordering is unfixed.

### What this actually rules out

`IsStreamingCompleted(Activated, {source at the vehicle}, bExactState=false)` **cannot express the
question the gate needs to ask.** It answers "are the currently-requested cells activated", which
is true of the far-field surrogate and stays true as the world swaps detail levels underneath. It
has no notion of "the ground under me is the ground that will still be here in a minute".

This is a negative result about the predicate, not about the gate. The gate mechanism itself is
sound and proven: physics holds, the escape releases, and both were measured. What has no working
implementation is the *condition*.

### Where that leaves the next step

Not "tune the dwell". The predicate has to come from somewhere other than the streaming
subsystem's completion flag. Candidates, none yet tested:

- **Ask the ground directly** — a downward trace from the vehicle, gated on the hit actor not
  being a `FastGeoSurrogateActor` / HLOD proxy. This asks the question literally rather than by
  proxy, and the surrogate is nameable, which is why it showed up in the log at all.
- **Wait on the cell that contains the spawn point** specifically, rather than a radius query, if
  World Partition exposes per-cell state at that granularity.
- **Accept the workaround.** `SIM-30`'s pause-and-probe already ships and already works. The gate
  is the better design, but "better design, no working predicate" does not beat "working".

The honest summary after two cold runs: **`SIM-32` does not work yet, and the fall it was built to
remove is still there.** Nothing has regressed — the patch is applied to nothing on `main` — but
the fix is not closer to shipping than it was this morning; the understanding is.

---

## Closed out — pause-and-probe is the official solution

Owner's call, 2026-08-16: *"let's just log all of this effort and for now let's use the simPause
method as the official solution. We can check back on this in the future."*

So `SIM-30`'s pause-and-probe is **the** answer to the falling vehicle. It ships, it works, and
City Sample flies on it. `SIM-32` stays on its branch as a recorded negative result —
`patches/cosys-airsim/0007` applied to nothing.

### The note to read first when this is picked up again

**This is most likely a slow-storage problem.** CitySample lives on the 7 TB **spinning disk**,
against this project's own rule that the simulator's live working set belongs on the internal
NVMe. Both cold runs took roughly five minutes to render a first frame, and the whole failure is
what happens in the gap between the gate releasing at 0.400 s and the real city arriving.

The distinction that decides whether a revisit is worth anything:

> **Slow storage does not cause the predicate bug. It sets its blast radius.**

`IsStreamingCompleted` would still answer `TRUE` on the far-field surrogate on the fastest disk
ever made — that is semantics, not timing. What storage changes is how long the vehicle is left
standing on an answer that is about to stop being true. On NVMe that window should shrink roughly
with the load time, plausibly to the point where the surrogate is replaced before the vehicle has
fallen anywhere that matters — which would make the existing patch good enough as written.

**Untested, and cheap to test.** Copy CitySample to the internal NVMe and re-run the same
instrumented build. The probe already logs every transition, so the result reads off `docker logs`
in one line. That is the first thing to do next time, before writing any new predicate.

### What is banked

- The gate mechanism is **proven**: physics holds at `z = -0.000`, the 300 s escape releases, both
  measured.
- The scoped query returns a verdict in **0.400 s**, deterministic across two cold bring-ups —
  against `nullptr`, which never resolved at all.
- `IsStreamingCompleted` is **ruled out** as the release predicate: one transition to `TRUE`, never
  `FALSE` in 600 s, straight through the surrogate swap. A dwell requirement cannot fix a constant.
- `SIM-10` is **coupled and demonstrated**: holding physics starves PX4's HIL stream — 166
  `poll timeout` errors, no finite EKF origin. Any future gate must start PX4 after it releases.
- Two defects found and fixed along the way: the verdict reached only the Slate overlay and not
  stdout (`UE_LOG` added), and a plugin *backup* placed a second `AirSim.uplugin` inside UE's
  recursive scan root.

Untested idea left on the table, if NVMe does not settle it: a downward trace from the vehicle that
rejects the hit when it is a `FastGeoSurrogateActor` — asking whether there is real ground below,
instead of asking the streaming subsystem a question it cannot answer.

---

## Correction — "applied to nothing" was false

Review of PR 57 caught it, and it is the most important thing on this page.

I stated repeatedly, here and in the PR, that `patches/cosys-airsim/0007` was **applied to
nothing** and that nothing in the shipped bring-up path changed. That was wrong.

`scripts/build_blocks.sh:65` and `scripts/convert_world.sh:154` both glob
`patches/cosys-airsim/*.patch` and apply every patch whose diff touches `Unreal/`. `0007` matches
that filter. Verified after the fact:

```
APPLIES: 0005-worldpartition-streaming-source.patch
APPLIES: 0006-gpulidar-empty-readback.patch
APPLIES: 0007-gate-physics-on-streaming.patch
```

So the next `convert_world.sh` on any World Partition world, or the next `build_blocks.sh`, would
have silently shipped the gate — which by the measurements on this very page starves PX4's HIL
stream and kills bring-up on "no finite EKF origin". The safety property that justified merging a
parked negative result at all did not hold.

**How I missed it, since that is the reusable part.** My own build output said
`applied  0007-gate-physics-on-streaming.patch`, in this session, more than once. I read it as
"the build applied it for my test" when it actually meant "the build applies it, always". The
evidence was in front of me and I fitted it to what I already believed.

**Fixed** by moving it to `patches/cosys-airsim/experimental/`, which the non-recursive glob does
not reach — the mechanism that already parks `0004`, and which existed the whole time. Confirmed
after the move: the glob now matches `0005` and `0006` only.

The review also found five further defects in the patch, none of them fixed, all recorded in
`patches/cosys-airsim/experimental/README.md` as blockers for un-parking. Two are severe enough to
change the design's own claim about itself: `simPause(true)` issued during the gate window is
**silently discarded** when `startAsyncUpdator()` runs `initializePauseState()` — which collides
directly with `SIM-30`'s pause-and-probe, the solution this work just deferred to — and
`continueForTime` busy-waits on a flag only the unstarted executor can flip, blocking the game
thread so `Tick()` never runs, the gate never releases, and the 300 s escape never fires. That is
a hang, which the patch header explicitly claims to have designed against.
