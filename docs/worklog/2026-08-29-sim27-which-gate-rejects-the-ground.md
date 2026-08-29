# SIM-27 step 1 — which gate rejects the landing surface

**Date:** 2026-08-29 · **Ticket:** `SIM-27`, step 1 of the START HERE plan · **Flight pending
approval.**

## The question, and why it is the only one that matters right now

`SIM-27`'s central claim is **read out of the source and has never been observed running**. Only
one branch in `FastPhysicsEngine.hpp` stops a descent — the ground lock at `:228`, which zeroes
velocity, snaps the body to the contact point and calls `body.setGrounded(true)`. Reaching it
needs all of:

| gate | test | where |
|---|---|---|
| **A** | the contact is not separating — `normal · next_velocity < 0` | `:141` early return |
| **B** | it looks like ground (`\|normal_body.z()\| ≈ 1`, `kAxisTolerance 0.25`) **and** like a landing (`z_vel` dominates x and y) | `:156`, `:163` |
| **C** | `EnableGroundLock`, default **true** (`SimModeWorldBase.cpp:70`) | `:228` |

So *"is this the ground?"* is answered **entirely from the collision normal**. The hypothesis is
that CitySample's `FastGeoSurrogateActor_0` — a coarse far-field proxy — supplies an off-axis
normal that fails **B**. But **A** would produce the same visible symptom, and the two imply
different fixes, so step 3 must not be written until this is measured.

## What was built today (no flight)

`patches/cosys-airsim/experimental/0008-sim27-groundlock-gate-probe.patch`. It adds **no
behaviour** — it prints what the engine already decided and leaves every decision unchanged. One
throttled line per gate:

```
SIM27 gate=A-EARLY-RETURN dot=… normal=(…) nextvel=(…)
SIM27 gate=B-NORMAL abs_nz=… tol=0.25 is_ground_normal=… z_vel=… is_landing=… lock_enabled=…
SIM27 gate=C-GROUND-LOCK ENGAGED at z=…
SIM27 gate=C-NO-LOCK ground_collision=… lock_enabled=… nextvel_z=…
```

Read with `docker logs sim-unreal 2>&1 | grep SIM27`.

Two details worth recording:

- **The logger has to be `static`.** `getNextKinematicsOnCollision` is a static member, so it
  cannot reach the existing `throttledLogOutput` — that needs `clock()` and `last_message_time`,
  both instance state. The probe throttles on wall time via `Utils::getTimeSinceEpochNanos()`
  rather than a call count, because a 1-in-N counter can skip the transition, which is the only
  moment that matters.
- **The file is CRLF.** The first attempt at cutting the patch read it in Python text mode, which
  rewrote every line ending and produced a 478-line whole-file diff instead of a 47-line one.
  Read and written as bytes now.

### Verified offline, before spending a build

- **Applies cleanly to both plugin trees** — the injected CitySample copy and a pristine Blocks
  copy (`patch --dry-run -p4`, both clean). The world's copy is byte-identical to `vendor/`
  (md5 `e9a79bcd`), so the patch is cutting against exactly what is deployed.
- **Compiles with zero new diagnostics.** A standalone `g++ -fsyntax-only` TU including the
  header, run against the patched and the pristine tree, produces *identical* output — so
  anything the plugin build reports afterwards is not this patch. (Both need `<mutex>` and
  `<thread>` pre-included: upstream's `PhysicsBody.hpp` relies on UE's PCH for them. That is an
  upstream wart, not a patch effect, and is exactly why the comparison is run both ways.)

### Blocked, and waiting on the operator

Applying the patch to the world's plugin and rebuilding it writes into
`worlds/CitySample/Plugins/AirSim/` on the 7 TB drive. That write was **denied by the sandbox**,
so the build has not run. Nothing else is outstanding — the flight is one build away.

### The build ran, and the binary carries the probe

The sandbox denial was lifted, so the patch went into the world's plugin and
`CitySampleEditor` was rebuilt in the engine container — the same UBT invocation
`convert_world.sh` uses, sharing the `sim-ddc` cache.

```
[61/61] Link libUnrealEditor-AirSim.so
Result: Succeeded          62.29 s total, 48.33 s in the local executor
```

| | |
|---|---|
| `libUnrealEditor-AirSim.so` before | md5 `a77bfe1c` |
| after | md5 `7cd1fbf3`, 5.76 MB, 2026-08-29 08:55 |
| probe format strings **in the binary** | **5 of 5** (`strings -a`) |

### The shadowing trap, checked rather than assumed

This repo has already lost a negative result to it: a backup copy left under `Plugins/` won the
plugin manager's name+version de-duplication, so the md5 that "verified" the patch was inspecting
a file the engine never loaded.

- The world holds **five** `AirSimBackups/AirSim.bak.<ts>/` trees, each with its own `.uplugin`
  and `.so`. All are at the **project root**, not under `Plugins/` — which is deliberate:
  `inject_airsim.py:219` puts them there precisely because Unreal scans `Plugins/` recursively.
- `CitySample.uproject` declares **no `AdditionalPluginDirectories`**, so discovery is `Plugins/`
  only and those backups cannot be reached.
- My own pre-build copy of the binary was initially left at
  `Plugins/AirSim/Binaries/Linux/libUnrealEditor-AirSim.so.pre-sim27`. A stray `.so` is not a
  `.uplugin` and cannot create a duplicate plugin, but "no spare copies under `Plugins/`" is the
  rule this project learned the hard way, so it was moved to
  `AirSimBackups/sim27-pre-probe/libUnrealEditor-AirSim.so` (md5 `a77bfe1c`, the pristine one).
- Only one `libUnrealEditor-AirSim.so` now exists under `Plugins/`.

**Still to prove: that the engine LOADED it.** Every check above is about files on disk, which is
exactly the evidence that misled this project before. The flight itself settles it — if the probe
lines appear in the renderer log, the loaded binary is the patched one, and if they do not, that
is a shadowing result rather than a physics result.

### Reverting

```
patch -R -p4 -d "$W/Plugins/AirSim" < patches/cosys-airsim/experimental/0008-sim27-groundlock-gate-probe.patch
cp "$W/AirSimBackups/sim27-pre-probe/libUnrealEditor-AirSim.so" "$W/Plugins/AirSim/Binaries/Linux/"
```

## FLOWN — CitySample, seed 1, 2026-08-29 (owner-approved, SITL)

```
export DRONE_SIM_WORLDS=/var/mnt/11d5.../Developments/projects/drone-sim/worlds
./scripts/sim_up.sh --display --world $DRONE_SIM_WORLDS/CitySample/CitySample.uproject
SIM_CHASE_VIDEO=1 python3 scripts/run_scenario.py scenarios/citysample-sensors.yaml --seed 1 \
    --outdir out --no-restart
docker logs sim-unreal 2>&1 | grep SIM27      # saved to out/citysample-sensors-seed1-sim27-gates.log
```

Bring-up needed the usual ground repair (the vehicle fell to `z=+813` before being placed at
`+0.756`, `SIM-28`/`SIM-30`), then the origin was sane to **0.000 m**.

**The split did NOT reproduce** — `outcome: success`, 2/2 waypoints, 103.5 s. Expected: the rate
is roughly 1 in 10 per seed. So this is a healthy-landing measurement, not the failure case.

### The probe fired, so the engine loaded the patched binary

**513 `SIM27` lines.** That settles the shadowing question the file checks could not: the plugin
that ran is the instrumented one.

### What a HEALTHY CitySample landing looks like at the gates

```
gate=B-NORMAL abs_nz=1.00000 tol=0.25 is_ground_normal=1 | z_vel=5.788 ... normal_body=(0.000,0.000,-1.000)
gate=B-NORMAL abs_nz=0.99999 tol=0.25 is_ground_normal=1 | z_vel=0.694 ... normal_body=(0.002,0.003,-1.000)
```

**`abs_nz` is 1.00000 and 0.99999, against a tolerance of 0.25.** On this landing CitySample's
surface produced a *textbook* ground normal — nowhere near the off-axis normal the leading
hypothesis needs. The second line is the touchdown itself: `z_vel = 0.694`, i.e. `MPC_LAND_SPEED`.

The A-gate normals, by contrast, are dominated by two families:

| normal | count | reading |
|---|---|---|
| `(0.000, 0.000, -1.000)` | 330 | ground, dismissed as separating — `dot = 0.00000`, `nextvel = (0,0,0)`: a **resting** vehicle |
| `(-1.000, -0.003, 0.000)` | 180 | a **wall/vertical** normal, x-axis |
| `(0.003, -1.000, 0.000)` | 1 | the other horizontal axis |

So gate A's early return is overwhelmingly the resting-contact path (`dot >= 0` is trivially true
at zero velocity), which is unremarkable. The 180 wall-normal contacts are more interesting and
are the kind of geometry the surrogate hypothesis is about — but on this flight they never
prevented the landing.

### The instrumentation had a flaw, and this run found it

**Not one `gate=C` line, on a landing that demonstrably ground-locked.** The cause is mine, not
the engine's: `sim27Log` used **one** throttle timer for all call sites, and gates B and C are
logged microseconds apart in the *same* call — so B's line always reset the timer and C's was
always suppressed. A throttle shared across call sites cannot answer a question about which call
site fires.

Fixed in the patch (per-gate timers, `kGateA/B/C`), re-cut, syntax-checked against the pristine
tree, and rebuilt into the world so the next seed needs no preparation. **The counts above remain
indicative only** — a wall-clock throttle samples the first call in each window, so they are not
frequencies.

### The detector's first live CitySample run

`max_pose_split_m = 0.891 m` — over the 0.5 m reporting threshold, so it printed the
informational line — **and correctly did not trip**: that sample is at `vz = -3.028`, i.e.
*ascending*, which the sign condition excludes. Worst *descending* gap was **0.186 m**,
consistent with the 0.255 m corpus worst.

**And the review's skew correction turns out to be a guard, not a tax.** The new `gap` field says
the two RPCs are essentially simultaneous: median **0.000 s**, max **0.002 s**. So the 0.891 m
divergence is *not* RPC-gap skew at all — it is genuine actor-versus-integrator lag during a
3 m/s climb. Worth knowing: the correction costs nothing here, and the thing actually protecting
against that sample is the sign condition, not the subtraction.

### Where step 1 stands

**Unfinished.** The gate question is answered for a *healthy* landing (normal is clean, lock
engages) and that is genuinely useful — it makes "the proxy always gives a bad normal" harder to
believe — but the failing case has not been observed with instrumentation attached. The next run
wants more seeds, and gate C now has its own throttle so the lock/no-lock decision will be
visible when it happens.

### The run destroyed the evidence it was chasing

Re-flying `citysample-sensors` seed 1 **overwrote the 2026-08-27 failing run's artifacts** — the
1189-sample trace, the result JSON, the chase video and the MCAP bag — the files `SIM-27`'s START
HERE section names as its freshest evidence.

Nothing malfunctioned. `run_scenario.py` clears those paths up-front deliberately (a stale file
reported as this run's evidence is worse than none), and the tag is `<scenario>-seed<N>` so an
artifact is traceable to its run. Two correct rules, one trap: **the best evidence for a defect
sits at exactly the path the next reproduction attempt deletes**, and the more the run matters, the
more likely that seed is re-flown. Filed as `SIM-41`, with archive-on-clear-if-failed as the
recommended fix.

**Salvaged:** `out/sim27-evidence/2026-08-27-citysample-sensors-seed1-landing.PARTIAL.jsonl`, 427
samples to `t=85.06 s` and `dz=1.71 m` — a by-product of replaying the original through
`SplitAbort` while building the step-2 detector. The ~760 samples that carried the split to
107.9 m are gone. Every number quoted from that run predates the loss and still stands.

### And a rate correction that changes the plan

This session said the split was "~1 in 10 per seed". That is the **Blocks** figure (1.7%, a world
where the fault essentially cannot occur). On **CitySample** the ticket records a 10-seed gate
splitting on **9 of 10**, and 7 of those traces trip the step-2 detector on replay — call it
**70–90% per seed**. Tonight's clean landing is the minority outcome, not the expected one.

So the next run is **three seeds, under distinct tags**, not ten: three carries ~97% odds of at
least one failure at that rate, and distinct tags are what stops it repeating the loss above.

## ANSWERED — it is gate B, the collision-normal test

Three CitySample seeds, `citysample-gate1-seed{1,2,3}`, flown as a gate (cold stack per seed, so
the seed's spawn pose actually applies). Owner-approved, SITL.

```
python3 scripts/run_gate.py scenarios/citysample-gate1.yaml --seeds 3 --outdir out/sim27-step1
```

**3 of 3 split**, which settles the rate question too — the CitySample rate is high, not 1 in 10.
All three were caught live by the step-2 detector and **VOIDed** with the named fault: its first
true positives outside replay.

| seed | split trip | dz at trip | vz | verdict |
|---|---|---|---|---|
| 1 | 84.84 s | 1.746 m | 0.696 | VOID |
| 2 | 85.04 s | 1.806 m | 0.695 | VOID |
| 3 | 166.49 s | 3.208 m | 2.112 | VOID |

Gate report: `INCONCLUSIVE — 3 run(s) VOID`, success rate `0/0 (0%) [3 VOID excluded]`. Exactly
what step 2 was built to produce: no laundered percentage, no control failure, the cause named in
the report.

### The measurement (seed 3, timestamped)

```
16:53:30.845  gate=B-NORMAL abs_nz=0.00027 tol=0.25 is_ground_normal=0 | z_vel=0.692 is_landing=1 | lock_enabled=1
16:53:30.845  gate=C-NO-LOCK ground_collision=0 lock_enabled=1 nextvel_z=0.684
```

and then that pair, again and again:

| | |
|---|---|
| consecutive B-gate failures | **295**, over **141 s** |
| `abs_nz` across them | **0.00000 – 0.00086**, against `kAxisTolerance = 0.25` |
| B-gate passes in the whole run | **1** — at 16:51:15, i.e. bring-up |
| `C-NO-LOCK` lines | 295, `ground_collision=0`, `lock_enabled=1` |
| integrator `vz` through the window | 0.684 → **2.452 m/s**, accelerating |

**`abs_nz ≈ 0` is a normal perpendicular to the vertical axis** — a wall, not a floor. So the
engine asks "is this the ground?", the collision normal says "no", `ground_collision` stays false,
and the one branch that could stop the descent is never reached. `lock_enabled` was **1**
throughout: `EnableGroundLock` is not the problem, and neither is gate A — the contact was
reported, responded to, and rejected on its normal.

**This is the ticket's leading hypothesis, now measured rather than read.** `FastGeoSurrogateActor_0`
— or whatever coarse proxy is under the landing point — supplies an off-axis normal, and
`is_ground_normal` is answered entirely from that normal.

### The healthy contrast, from the same three runs

Every clean touchdown in this session reports `abs_nz = 0.99999–1.00000` and
`gate=C-GROUND-LOCK ENGAGED`. So the two populations are not subtly different: **1.00000 versus
0.00027**. There is no threshold to tune here — `kAxisTolerance` could be 0.9 or 0.05 and the
outcome would be identical. That kills "widen the tolerance" as a fix before anyone proposes it.

### Honest limits of this result

- Seeds 1 and 2 were captured **without timestamps** (my collector attached plain `docker logs -f`),
  so their B/C lines cannot be placed in the descent window. Both show `C-GROUND-LOCK ENGAGED at
  z≈0.75`, which is the resting height and is more likely bring-up than touchdown — but I cannot
  prove that, so only seed 3 is quoted as the measurement. **Timestamps belong in the probe's own
  output**, not in the way it happens to be captured.
- The throttle samples the first call per window per gate, so counts are indicative, not
  frequencies.
- What geometry supplies that normal is still unnamed. The probe prints the normal, not the actor
  that owns it.

### What this means for step 3

The planned fix — stop inferring ground from a collision normal, trace downward instead and
ground-lock on blocking geometry a few cm below plus slow descent — is **the right shape**, and is
now backed by a measurement rather than a reading. Two constraints the data adds:

1. **Do not touch `kAxisTolerance`.** 0.0003 versus 1.0 is not a tolerance problem.
2. **`lock_enabled` and gate A are exonerated.** The fix belongs at the `is_ground_normal`
   decision, nowhere else.

