# drone-sim — the backlog

> **Running the simulator?** See [`quickstart.md`](quickstart.md) ([HTML](quickstart.html)) —
> how to launch it, choose a world, select and tune sensors, the full list of sensor topics
> with types and measured rates, and how to command the drone. **The control interface is
> ROS 2 only.** Status as a single page: [`roadmap.html`](roadmap.html).

**This is the project's backlog, and there is one.** Every feature or non-trivial change
exists here as a documented TODO *before* it is built, and is marked done when it lands
(`.ai/AGENTS.md:154`). One cross-cutting area keeps its own file — Docker reproducibility, in
[`docker/todo.md`](docker/todo.md). The retired Gazebo and Isaac Sim backlogs, and the
research plan that framed them, are preserved under [`history/`](history/) rather than
deleted — the measurements that retired them are the reason not to repeat them.

**Status legend:** `todo` · `in progress` · `done` · `blocked`

---

## What this repo is

**A photoreal drone simulator: Unreal Engine 5.8 + Cosys-AirSim + PX4 SITL + ROS 2 Jazzy.**
Bring your own Unreal world, place the vehicle in it, choose your sensors, and fly it over
ROS 2 — the same graph you would fly on real hardware.

Goals, in order:

1. **A photoreal simulator that flies, on your world.** Not a curated demo scene: the
   deliverable is the *mechanism* that takes a `.uproject` this project has never seen,
   injects the AirSim plugin into it with no compile, no editor and no GUI, places the
   vehicle deliberately, and flies it. `SIM-11`, `SIM-13`, `SIM-14`.
2. **The whole setup reproducible as Docker from this repo alone.** *(added 2026-07-29)* A
   fresh machine must reach a working stack from the repo — no undocumented manual steps, no
   "it works on carbonite". Pin the versions actually built and smoke-tested, and record
   deviations from the reference docs; a Dockerfile written from the docs rather than from
   evidence reproduces a *broken* stack. Backlog: [`docker/todo.md`](docker/todo.md).

   **One documented exception, forced by upstream licensing** *(amended 2026-07-31)*. The
   engine base image `ghcr.io/epicgames/unreal-engine` is **credential-gated**: anonymous
   pulls are HTTP 403 (`DENIED: invalid token`), and building needs EpicGames GitHub org
   membership plus a PAT with `read:packages`. A clone plus a Dockerfile is therefore **not
   sufficient**, and no amount of pinning changes that. The goal is restated rather than
   quietly failed: **from the repo alone, plus one documented credential step** — documented
   in `docker/README.md`, failing with a readable message rather than a registry 403, and
   named up front rather than discovered halfway through a 24 GB pull. See
   [`docker/todo.md`](docker/todo.md) `D-04`.
3. **Reuse and integrate upstream, don't reinvent.** PX4, Cosys-AirSim, Isaac ROS and
   EGO-Planner are pinned and wrapped. The glue — the ROS 2 graph, the bring-up, the
   scenario and eval harness — is the original work.
4. **Sim-to-real parity: one ROS 2 graph, swap only the transport.** The controller that
   flies here is byte-identical to the one that would fly the real Pixhawk 6C; the only
   difference is what sits on the far end of uXRCE-DDS. That is demonstrated rather than
   argued — see `SIM-09`.

**What people build *on* the simulator** — vision-based navigation, VLM agents, planners,
perception research, benchmark reproduction — are applications. They are named here only as
examples of what the simulator is *for*. They are not the repo's purpose, and the repo does
not carry their code.

---

## Status — 2026-08-04

**The simulator flies, its sensors are verified by value, and its imagery is
photorealistic.** Those are three separate measurements, so they are stated separately:

| | |
|---|---|
| **It flies** | The unmodified ROS 2 `offboard_control` node reaches **4/4 waypoints** — errors 0.78 / 0.79 / 0.78 / 0.78 m — then lands and disarms. Reproduced three times, once from a cold start. **The controller was never patched**; only the transport was swapped (`SIM-09`). |
| **The sensors are real** | RGB, depth, GPU-LiDAR, IMU, GPS, magnetometer and odometry all publish and pass **value-based** checks: IMU reads 9.807 m/s² at rest, depth carries bounded returns, LiDAR points are not all at the origin, RGB is not a blank frame (`SIM-04`, `scripts/verify_sensors.py`). |
| **The imagery is photoreal** | `simGetImages` matches Unreal's own render of the **same camera actor at the same transform** to **1.15 of 255**, across six scenes from close-up to 70 m — on a **stock** plugin binary, via three `settings.json` keys (`SIM-11`). |
| **It runs on someone else's world** | A real Fab project declaring `EngineAssociation: "4.24"` — a UE4 project from 2019 — loaded in UE5.8 headless with no conversion: 856 scene objects, AirSim serving, the vehicle placed by coordinate (`SIM-11`, `SIM-13`). |

**The current work is dynamic actors in the user's world — the remaining half of `SIM-11`.**
The world is photorealistic and the drone can be placed in it deliberately; what it lacks is
people, vehicles and moving obstacles — anything to navigate *around*.

The path is settled and needs **no project C++ and no plugin change**: the RPC surface
(`simListAssets`, `simSpawnObject`, `simSetObjectPose`, `simSetObjectScale`,
`simDestroyObject`, plus `simAddDetectionFilterMeshName` for ground-truth labels) works in any
project, which keeps the drop-in property that makes bring-your-own-world work at all.
`simGetObjectPose` was exercised against City Park on 2026-08-03, so that surface is known
live in this build.

**One design decision to make deliberately:** the vendored `-startSeed`/`-spawnAI`
determinism lives on the **C++ side**, which means adding a `Source/` module to the user's
project — breaking bring-your-own-world for exactly the feature the eval gate needs. **Seed
it in our own scenario harness instead** (seeded RNG → `simSpawnObject` poses → record the
seed): reproducible in *any* world, no vendor change, and squarely the "the glue is the
original work" split this project is built on.

**First step, deliberately small:** spawn a few actors into City Park over RPC, confirm they
appear in **both** RGB and GPU-LiDAR, and re-measure sensor rates with actors present — RGB
already sits at 17.1 Hz against a 20 Hz cap.

### `SIM-07` — the flight gate, RUN and MET: 10/10 over independent seeded cold starts

**Status:** `done` — **2026-08-07.** First full gate run in the project's history.

```
success rate : 10/10  (100%)   zero VOID
wall clock   : 1944s
waypoint error: 0.775 - 0.805 m against a 1.0 m accept radius
per-seed time : 193 - 195 s
PASS -- flight gate criterion met
```

**The simulator has an acceptance criterion of its own for the first time.** Ten independent
cold starts, spawn pose applied per seed, each leaving a bag, a video and a collision record.

### It took two defects to get here, and neither was visible before

**1. The gate could never complete a single seed.** Jittered spawns are frequently negative in
X, and `sim_up.sh` relayed the value to `apply_spawn.py` as two arguments:

```
apply_spawn.py: error: argument --spawn: expected one argument
[sim] FATAL: spawn rejected; not starting
```

argparse read `-3.656,3.474,0,94.956` as a flag. Fixed by relaying `--spawn=VALUE`.

**This is why the entry sat "wired but never run" for so long.** `--reuse` always spawns at
`0,0,0` -- no minus sign -- so every test anyone ever ran took the one path where the bug
cannot fire. The bug lived exactly in the code path nobody exercised.

**2. The scenario's own premise was false.** `square-10m` says "an empty world, no obstacles by
design", and at 10 m altitude in Blocks that was not true. The mission is a square RELATIVE TO
THE SPAWN, so jitter walks it across the map. Measured, first real run:

```
10 m   9/10   seed 2 FAIL -- 2 collisions (TemplateCube_Rounded_101, _102), worst 0.820 m
20 m  10/10   seed 2 PASS -- 0 collisions,                                  worst 0.799 m
```

**Seed 2 is the argument for `SIM-22` in one line.** At 10 m it scored **0.820 m** -- inside
tolerance, and indistinguishable from every passing run on waypoint error alone. It failed only
because the collision witness saw the impacts. Without that witness this gate reports 100%
either way, and one of those two 100%s is a lie.

Altitude raised to 20 m, which is what the park tour flies a wider circuit at with zero
collisions.

### What a gate run now produces, per seed

```
out/<scenario>-gate.json                  the report: pass/reason/collisions/errors + SR
out/<scenario>-seed<N>/                   MCAP bag
out/<scenario>-seed<N>.json               raw controller result
out/<scenario>-seed<N>.mp4                video, ~37 MB
out/<scenario>-seed<N>-collisions.json    impact points, objects, durations
```

Video is on by default (`SIM_NO_VIDEO=1` opts out) and records over the AirSim **RPC**, not ROS 2
topics -- `airsim_node` is not running during a gate run, so subscribing to camera topics would
have recorded nothing at all, silently.

Collision detail is persisted per seed because the count alone is not actionable: the first
question after "it hit something" is "how high was the something", and only the impact points
answer it.

### Unchanged limitations, stated so the number is not over-read

**A seed still controls the SPAWN POSE ONLY.** No wind, no mass variation -- that needs
`simSetWind`, still unwired (`SIM-08`). **Do not describe this as covering varied conditions.**
It is the same mission from ten different starting points.

**It proves the controller and the harness, not perception.** `airsim_node` does not run; there
are no camera or LiDAR topics in the loop, and the controller flies fixed waypoints blind. Give
it a waypoint inside a building and it will fly at the building -- which is exactly what seed 2
did at 10 m.

### Three facts worth carrying into any session on this stack

- **Lockstep is confirmed dead code** in Cosys-AirSim -- `initialize()` sets the flag and
  `openAllConnections()` clears it twice. `"LockStep": true` is silently ineffective and
  **every timing number here is free-running**. Never quote an RTF from this simulator as
  deterministic.
- **A stale PX4 EKF origin makes the vehicle report tens of metres of altitude while
  grounded**, and it looks exactly like a control bug. `sim_up.sh` verifies and repairs it;
  `run_gate.py` **VOIDs** such runs rather than failing them. Zero VOID in this run.
- **Frames are NWU, not ENU**, despite upstream's docs saying otherwise. The conversion lives
  in `control/frames.py` and, unlike `enu_to_ned`, the NWU pair is **not** an involution.

### Follow-up

The intermittent leg timeout seen in `SIM-22`'s park-tour runs did **not** appear in the 10-seed
gate -- uniform 193-195 s, no timeouts.

**Updated 2026-08-07: it is NOT park-tour-specific.** A later single-seed gate run failed with
`timeout in state waypoints` at 89 s, worst error 0.323 m, with **zero collisions**; the
immediate re-run passed at 109 s. So the same failure reaches the square mission too, and the
"specific to the longer circuit" half of the earlier guess is dead.

What is known: it is not a collision, not a VOID, and not spawn-dependent (it has appeared on
`--reuse`, which always spawns at 0,0,0). Frequency is somewhere under 1-in-10 -- ten seeds
passed uniformly, then a single-seed run failed. Still open, and it is the one thing standing
between this gate and a success rate anyone should quote without a caveat.

`scripts/run_gate.py` is the simulator's gate: `scripts/run_scenario.py` drives
`scripts/sim_up.sh`, and the gate keeps its scoring semantics unchanged — **VOID is distinct
from FAIL**, a void run is excluded from the success rate *and* separately blocks the
criterion (excluding without blocking would let a gate where 9 of 10 runs were void report
100%). What does not exist yet is a full seeded run against this stack. **Until it does, the
simulator has no acceptance criterion of its own.**

**One honest limitation of the gate as it stands: a seed controls the SPAWN POSE ONLY.** The
retired Gazebo harness seeded wind and vehicle mass through a generated world overlay, and
there is no equivalent here — it would need Cosys-AirSim's wind API (`simSetWind`), which is
not wired in; the RPC surface is recorded under `SIM-08`. **Do not describe a gate run as
covering varied conditions.**

### Three facts worth carrying into any session on this stack

- **Lockstep is confirmed dead code** in Cosys-AirSim — `initialize()` sets the flag and
  `openAllConnections()` clears it twice. So `"LockStep": true` is silently ineffective and
  **every timing number here is free-running**. Never quote an RTF from this simulator as
  deterministic.
- **A stale PX4 EKF origin makes the vehicle report tens of metres of altitude while
  grounded**, and it looks exactly like a control bug. Bring the stack up with
  `scripts/sim_up.sh`, which verifies and repairs it; `run_gate.py` **VOIDs** such runs rather
  than failing them.
- **Frames are NWU, not ENU**, despite upstream's docs saying otherwise. The conversion lives
  in `control/frames.py` — the single conversion point [`conventions.md`](conventions.md) §3
  mandates — and, unlike `enu_to_ned`, the NWU pair is **not** an involution.

---

## Settled decisions, and the evidence behind them

### UE5.8 and tag `5.8-v3.4.1`, not UE5.5

The design doc [`history/reference/04_ue5_stack_architecture.md`](history/reference/04_ue5_stack_architecture.md)
targets UE5.5, because UE5.5 was upstream's target when it was written. Research on
2026-07-31 turned up a conflict it could not have known about.

**UE5.5 and ROS 2 Jazzy cannot be had from one upstream tag.** The last UE5.5 release is
`5.5-v3.3` (2025-04-16, SHA `e029c244…`). It predates the Jazzy fix, and upstream's v3.4
CHANGELOG says the 5.5 branch "will no longer be receive updates or be actively maintained".
The Jazzy fix — commit `83d1b81c`, rewriting `cv_bridge`/`tf2_geometry_msgs` includes from
`.h` to `.hpp` — landed in v3.4, which targets **UE5.8**.

**Measured on this machine rather than inferred:**

```
$ ls /opt/ros/jazzy/include/cv_bridge/cv_bridge/
cv_bridge.hpp  cv_bridge_export.h  cv_mat_sensor_msgs_image_type_adapter.hpp
rgb_colors.hpp  visibility_control.h
```

`ros-jazzy-cv-bridge 4.1.0-1noble.20260615.144656` ships **no `cv_bridge.h` shim at all**,
and `tf2_geometry_msgs` likewise ships only `.hpp`. So `5.5-v3.3` was not merely
unsupported-on-paper — **it was measurably unbuildable here.**

Two further marks against UE5.5, both open upstream issues:
`Cosys-Lab/Cosys-AirSim#135` reports segmentation and annotation rendering **all black** on
exactly UE5.5 + Ubuntu 24.04 + clang-18 — and segmentation ground truth is load-bearing for
label generation. `Cosys-Lab/Cosys-AirSim#129` reports PX4 SITL lockstep desync against
`5.5-v3.3` with PX4 v1.16.0, the exact PX4 line this project runs.

**And the fallback inverted the same day.** Cesium for Unreal **v2.28.0** added UE5.8
(`CHANGES.md`: *"Added support for Unreal Engine 5.8."*, PR `CesiumGS/cesium-unreal#1856`),
while **v2.29.0 removes UE5.5** (*"Unreal Engine 5.6 or later is now required"*). Falling
back to UE5.5 would now mean an end-of-line Cosys-AirSim branch **plus** a header patch owned
forever **plus** a Cesium frozen permanently at v2.28.0. **UE5.5 is no longer a safe fallback
and must not be described as one.**

### ROS 2 Jazzy, not Humble

`04` recommends Humble. **That recommendation is overridden, and the evidence inverted it
rather than merely outvoting it.** Upstream now documents Jazzy on Ubuntu 24.04
(`docs/ros2.md`: *"The following was tested with ROS2 Jazzy"*, changed 2026-07-10;
`install_linux.md`: *"The current recommended and tested environment is **Ubuntu 24.04
LTS**"*). Humble is now the **riskier** choice: current upstream includes
`<cv_bridge/cv_bridge.hpp>`, and Humble's `vision_opencv` ships only `cv_bridge.h`, so `04`'s
Humble recommendation would fail to compile immediately. The decision was originally made on
the reasoning that `04`'s Humble was inherited from example docs rather than measured; the
evidence then showed it is simply out of date.

Jazzy is also where the GPU perception stack lives — cuVSLAM and nvblox are Jazzy packages
(`SIM-05`) — so a move to Humble would put perception on the wrong side of the split.

### What `04` says that no longer applies

`04` is kept in [`history/reference/`](history/reference/) as the survey that chose
Cosys-AirSim, and its simulator comparison still stands: Microsoft AirSim was archived
2023-12-15 and Colosseum 2026-07-11, which leaves Cosys-AirSim as the only actively
maintained option meeting every hard requirement. **What is superseded:** UE5.5, ROS 2
Humble, its weeks-based phase numbering, and everything it says about running a second,
Gazebo-based stack alongside this one. Those stacks are retired — see
[`history/`](history/).

**The 2026-07-31 framing also said, correctly at the time, that none of this had ever been
built**, and the plan rated it **High likelihood / Med impact** for build fragility
(`02_development_plan.md:252`). That is now historical: the stack builds, flies, and is
pinned. The fragility that remains is in the vendored C++, and every deviation from upstream
is recorded in [`vendor/cosys-airsim.md`](vendor/cosys-airsim.md).

---

## Task ID convention

Tasks are `SIM-NN` — `SIM-01`, `SIM-11`. **Never write them as `#N`.** A bare `#N` in a PR
body, issue, or commit message auto-links to an unrelated same-repo issue
(`.ai/AGENTS.md:243`). `SIM-11` cannot mis-link, which is why the scheme exists.

Cross-repo references are always fully qualified: `PX4/PX4-Autopilot#25089`, never
`PX4-Autopilot#25089` (which does not link at all) and never a bare `#25089`.

**The old `C-NN` IDs are the same numbers** — `C-11` is `SIM-11`; nothing was renumbered.
`D-*` IDs are the Docker reproducibility backlog in [`docker/todo.md`](docker/todo.md).
`P0-*` and `P1-*` IDs belong to the retired Gazebo and Isaac Sim work and live in
[`history/`](history/); an active doc citing one is citing an archive.

---

## Cross-cutting rules that shape every task

These are not tasks; they are constraints every task inherits.

- **Verify by running it, end to end.** A clean `colcon build` proves nothing about flight.
  Exercise the full ROS 2 graph against the simulator and record the evidence — MCAP bag,
  metric table, measured latency. If you cannot verify, say so and name the blocker
  (`.ai/AGENTS.md:305`).
- **A success rate over N seeded runs, never a single pass.** A flaky green is a fail until
  the real-time-factor floor holds.
- **Reuse upstream; don't reinvent.** PX4, Cosys-AirSim, Isaac ROS and EGO-Planner are pinned
  and wrapped. The original work is the glue and the experiment harness.
- **Version coupling is the architecture.** `px4_msgs` branch-matched to firmware, one ROS 2
  distro (Jazzy), and the engine tag pinned together with the Cosys-AirSim SHA. See
  [`../versions.lock`](../versions.lock). **The two-PX4-tree question is settled:** the
  simulator drives **v1.16.0** — the same line the real Pixhawk 6C is flashed from — so the
  project needs one tree, not two (`SIM-03`). The v1.14.3 pin belonged to Pegasus, which is
  retired with Isaac Sim.
- **Least-destructive vendor edits.** `vendor/` stays byte-identical to upstream; every
  deviation is a numbered patch under `patches/`, applied to a container-local copy by
  `scripts/build_airsim_wrapper.sh`, and written down in
  [`vendor/cosys-airsim.md`](vendor/cosys-airsim.md).
- **Never command the real aircraft without explicit per-run approval.** SITL is exempt and
  safe; say which you are doing. Approval never carries over (`.ai/AGENTS.md:120`).

---

## Open blockers

Tracked here because they cross task boundaries. Detail lives with the task.

| ID | Blocker | Blocks | Detail |
|---|---|---|---|
| `SIM-17b` | **NVENC cannot open an encode session** on driver 610.43.03 — `OpenEncodeSessionEx: unsupported device (2)`. CUDA works; the encoder specifically refuses. UE 5.8's only NVIDIA backend is NVENC, so Pixel Streaming would fall back to *software* VP8 — which reintroduces the readback it exists to avoid | `SIM-17` (1080p60 video). **Nothing that flies** | [`nvenc-driver-blocker.md`](nvenc-driver-blocker.md) |

**Everything that flies is unaffected**: perception imagery (640×480 → ROS 2 at ~17–20 Hz),
flight, control, sensors and MCAP recording all work. What is lost is presentation-quality
video above ~14 Hz.

**The calculus for fixing it changed with the pivot, and the change is worth stating rather
than quietly dropping.** The same driver also SIGSEGVs Isaac Sim 5.1 (validated driver:
580.65.06), so a host rebase to R580 would once have bought back **two** capabilities. Isaac
Sim is retired, so the rebase now buys exactly one: hardware video encoding. The measurement
is unchanged and still recorded in
[`history/isaac/driver-decision.md`](history/isaac/driver-decision.md); what changed is what
it is worth.

**Cruel detail worth knowing before deciding:** the driver *does* expose
`VK_KHR_video_encode_h264` — the hardware encoder is present and functional through Vulkan.
Neither UE 5.8 (no Vulkan-video-encode backend) nor our ffmpeg 6.1 (no `h264_vulkan`) can
reach it. The capability exists; the software cannot use it.

---

## Related documents

| Doc | What it is |
|---|---|
| [`quickstart.md`](quickstart.md) | How to run it — launch, world selection, sensors, topics, commanding |
| [`../versions.lock`](../versions.lock) | The pinned toolchain and the couplings CI must assert |
| [`roadmap.html`](roadmap.html) | Capabilities and status, as a single page |
| [`bench.md`](bench.md) | The machine and container being worked in |
| [`conventions.md`](conventions.md) | Frames, units and message contracts — ENU/FLU outside, NED inside, converted in one tested place |
| [`vendor/cosys-airsim.md`](vendor/cosys-airsim.md) | Every deviation from upstream Cosys-AirSim, and the evidence for each |
| [`docker/todo.md`](docker/todo.md) | The reproducible-as-Docker backlog |
| [`nvenc-driver-blocker.md`](nvenc-driver-blocker.md) | Why hardware video encoding is blocked on this host |
| [`history/reference/04_ue5_stack_architecture.md`](history/reference/04_ue5_stack_architecture.md) | The simulator survey that chose Cosys-AirSim — read with the overrides above |
| [`history/`](history/) | The retired Gazebo and Isaac Sim backlogs, and the research plan that framed them |
| [`worklog/`](worklog/) | Running record of each non-trivial investigation, written as it happens |

---

## Execution order — deliberately not ID order

IDs are stable references (`versions.lock` and the roadmap cite `SIM-01`, `SIM-02`,
`SIM-03`), so new tasks take new numbers rather than renumbering the old ones. Execution
order is a separate question, and it has changed twice.

| Order | Task | State |
|---|---|---|
| 1 | `SIM-06` ROS 2 wrapper on Jazzy | ✅ **done 2026-08-01** — builds in 1m21s, artifacts asserted |
| 2 | `SIM-01` harden the Cosys-AirSim / UE pin | ✅ **done 2026-07-31** — both gates cleared, tag and SHA in `versions.lock` |
| 3 | `SIM-02` UE5.8 base image and source build | ✅ **done 2026-08-01** — image pulled, digest matched the registry query, plugin compiles and links |
| 4 | `SIM-03` PX4 ↔ Cosys-AirSim and `/fmu/*` parity | ✅ **done 2026-08-01** — 51 `/fmu/` topics, identical to the Gazebo baseline's |
| 5 | `SIM-09` make it actually fly | ✅ **done 2026-08-01** — 4/4 waypoints; a stale PX4 EKF origin, not lockstep |
| 6 | `SIM-10` deterministic EKF-origin bring-up | 🟡 **built, verified once** — the "N cold starts in a row" run is outstanding |
| 7 | `SIM-04` sensors into the ROS 2 graph | ✅ **done 2026-08-02** — RGB, depth, LiDAR, IMU, GPS, mag, odom, verified by value |
| 8 | `SIM-13` operator-supplied spawn | ✅ **done 2026-08-03** — pose holds unheld, drift 0.000 m over 6 s |
| 9 | `SIM-15` the navigation command interface | ✅ **done 2026-08-03** — all five capabilities confirmed by movement |
| 10 | `SIM-16` an example mission, recorded | ✅ **done 2026-08-03** — waypoint and smooth-orbit modes, MCAP + video + ground track |
| 11 | **`SIM-11` the user's own world + dynamic actors** | 🟡 **in progress — the current work.** Photorealism ✅; **actors not started** |
| 12 | `SIM-07` the flight gate | ✅ **run and MET 2026-08-07 — 10/10, zero VOID.** Wants a real world and actors to be worth gating |
| 13 | `SIM-14` automatic spawn derivation | 📋 backlog — unblocked; `SIM-13` handed it a working ground probe |
| 14 | `SIM-12` capture aliasing | ⏸️ deferred — `ForceUpdate` fixed the Lumen part; the residual metric is untrustworthy |
| 15 | `SIM-05` Isaac ROS perception on this imagery | ⏸️ deprioritised 2026-08-02 — not abandoned; the imagery is ready whenever it resumes |
| 16 | `SIM-17` 1080p60 video via Pixel Streaming | 🚫 blocked on NVENC — see open blockers above |
| 17 | `SIM-08` Cesium georeferenced terrain | 📋 backlog — georeferencing is benchmark reproduction, not photorealism |

**The ordering lesson worth keeping.** `SIM-06` ran first because it was the cheapest test
that could invalidate the ROS 2 distro decision, and it needed no Unreal Engine, no GPU and
no simulator: the wrapper is an ordinary colcon package. Discovering a distro incompatibility
*after* a multi-hour engine build would have been the most expensive possible ordering of the
same two facts. It cost 1m21s instead of a 24 GB pull.

**Scene work moved forward on 2026-08-02.** The reason it had been last — *"scene work on an
unproven simulator"* — stopped holding once the simulator flew and its sensors were verified
by value.

---

## SIM-06 — Build the Cosys-AirSim ROS 2 wrapper against Jazzy

**Status:** ✅ **`done` (2026-08-01)** — **it builds.** Evidence:
[`worklog/2026-08-01-c06-wrapper-on-jazzy.md`](worklog/2026-08-01-c06-wrapper-on-jazzy.md)

```
colcon build --symlink-install   # ROS 2 Jazzy / Ubuntu 24.04 / g++ 13.3.0
Summary: 2 packages finished [1min 21s]     exit 0, 0 errors, warnings only
```

**The stay-on-Jazzy decision is now evidence rather than reasoning** — which was the entire
point of running this first, for 1m21s instead of after a 24 GB engine pull.

**Artifacts asserted, not assumed** (the `D-01` rule): `airsim_node` is 12 MB, `ldd` reports
**0 unresolved libraries**, it links `/opt/ros/jazzy`, and 24 `airsim_interfaces` types
register. No clang needed — `colcon` builds AirLib itself via `add_subdirectory`.

**Two findings that outlived the task:**

- **A CARLA UE4 instance on this host is bound to `0.0.0.0:41451`** — the exact AirSim RPC
  port. The node connected to *it*, negotiated a version handshake and failed. The log line
  before the failure said `Connected!`. **A port conflict is waiting for `SIM-03`**, and the
  failure mode looks like success. Recorded as coupling `airsim-rpc-port-conflict`.
- **The wrapper crashes rather than degrades** when an API call fails — the ROS context is
  torn down and a timer handle throws (`exit 250`). Worth knowing before `SIM-04` relies on it
  surviving a simulator hiccup.

**Still unproven:** it has never run against an actual Cosys-AirSim server. That needs
`SIM-02`.

**Original definition:** ~~`todo` · RUN THIS FIRST · Blocks: the distro decision, and therefore `SIM-02`~~

**What.** Check out Cosys-AirSim at **`5.8-v3.4.1`** (SHA `a552dd6cd517b8d5d26629ad88004356c3007326`)
and attempt `colcon build` of its ROS 2 wrapper package alone, on the existing Jazzy /
Ubuntu 24.04 container. No Unreal Engine, no GPU, no simulator — just the bridge package and
its dependencies. **~30 minutes, and it needs no 12 GB engine image.**

**Why this is a task and not an assumption.** Upstream's own CI never builds this. The only
workflow (`build-linux.yml`) runs `setup.sh`, `build.sh` and a `MavLinkTest --help` smoke
check — **`colcon` is never invoked and `ros2/` is never built, on any distro or tag.**
"Tested with ROS2 Jazzy" is a maintainer assertion with no automated signal behind it, and a
GitHub issue search for "jazzy" in the repo returns **zero** results. That is ambiguity, not
health: we are plausibly an early adopter of this path.

**Acceptance.** One of two recorded outcomes, both useful:

- **Builds** → record the dependency list and any patches needed, and set the wrapper's
  `builds_against: jazzy` in `versions.lock` with the evidence.
- **Does not build** → record the *specific* failure (missing package, API change, message
  incompatibility), not "it failed". The escape hatch is a small header patch carried as a
  patch file in this repo — **not** a distro flip, and **not** a Humble sidecar (the
  `ros2_distro_fallback` entry in `versions.lock` explains why that idea was withdrawn the
  same day it was written).

**Traps.**
- **Do not judge this by a green `colcon build` alone.** An `ament_cmake` build succeeding
  says nothing about whether the node runs — this project already learned that an
  `ament_python` build reports success while the package fails at import (`P1-01`).
- **Do not copy upstream's ROS 2 container tooling.** `docker/build_ros2.sh` hardcodes
  `ROS_DISTRO=humble` and `tools/Dockerfile-ROS2` is `FROM ros:foxy-ros-base` — both stale
  and both contradict upstream's own docs. Write our own Jazzy/24.04 image, which the
  reproducible-as-Docker goal wants anyway.
- **`docs/ros2.md` still says `source /opt/ros/iron/setup.bash`.** The Jazzy update touched
  one line; the rest of that page was not re-validated. Expect other stale build steps.
- **Do not patch the vendored tree to make it build** without recording it. Least-destructive
  vendor edits: push integration into the build layer and write the deviation down.
- **`tf2/LinearMath/*.h` shims are still used upstream** — deprecation warnings on Jazzy.
  `CMAKE_CXX_FLAGS` sets `-Wall -Wextra` but **not** `-Werror`, so they will not fail the
  build. Do not "fix" them by adding `-Werror`.

---

## SIM-01 — Harden the Cosys-AirSim / UE pin

**Status:** ✅ **`done`** — release chosen and both gates cleared 2026-07-31; the pin then
proved itself when `SIM-02` pulled the image and the digest matched byte for byte.
**Blocked:** every build in the stack, until it was answered.

**Chosen 2026-07-31:** tag **`5.8-v3.4.1`**, SHA **`a552dd6cd517b8d5d26629ad88004356c3007326`**,
targeting **UE5.8**. Reasoning and the measured evidence are above; the full record is in
`versions.lock`, under the Cosys-AirSim entry.

### Both gates cleared — 2026-07-31

**Gate 1 — Cesium for Unreal on UE5.8: PASSED.** Cesium for Unreal **v2.28.0** (2026-07-01,
current `releases/latest`) adds UE5.8. `CHANGES.md`: *"Added support for Unreal Engine 5.8."*
PR `CesiumGS/cesium-unreal#1856` merged 2026-06-26. The UE5.8 asset is a real 1.23 GB
download returning HTTP 200 — an artifact, not a roadmap promise.

**Gate 2 — the Epic image tag: EXISTS, and this one was re-confirmed by hand.**
`dev-slim-5.8.0`, digest `sha256:daac0262…4a0c46`, published 2026-06-17 — the day UE5.8
shipped. Found by an authenticated tag listing (85 tags, four carrying 5.8), then
**independently re-queried from this container**: the manifest resolves, 30 layers,
**24.0 GB compressed**, and the config blob reads back
`org.opencontainers.image.version 22.04`.

**Inspection costs ~17 KB and needs no `docker login`.** The `gh` CLI token exchanges for a
read-only ghcr bearer, so the pin can be checked without touching the 24 GB — worth wiring
into `SIM-02` as a preflight rather than discovering a 403 partway through a build.

**Sanity check that could have sunk the whole pin: UE5.8 is a released engine**, shipped
2026-06-17 as current stable, with hotfix 5.8.1 on 2026-07-28. The `5.8-v3.4.1` upstream tag
targets a production engine ~6 weeks old, not a preview.

> **The fallback has inverted — this reverses what this document said hours ago.** Cesium
> **v2.29.0 removes UE5.5 support** (*"Unreal Engine 5.6 or later is now required"*), and
> v2.28.0 is the terminal Cesium release for 5.5. Falling back to UE5.5 would now mean an
> end-of-line Cosys-AirSim branch **plus** a header patch we own forever **plus** a Cesium
> frozen permanently at v2.28.0. **UE5.5 is no longer a safe fallback and must not be
> described as one.** UE5.8 is the only forward-supported path.

**Held at `TODO-verify` deliberately, and then settled by `SIM-02`.** The tag was *observed*,
not *pulled* — and existing is not working. The re-confirmation was therefore left to the
pull itself:

```bash
docker login ghcr.io      # Epic-org account, PAT with read:packages
docker manifest inspect ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0
```

**It reproduced.** `SIM-02` pulled the image on 2026-08-01 and the digest came back
`sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46` — byte-identical to
the one recorded from the registry query. The pin is now `LOCKED` in `versions.lock`.

**Pin the three-component tag, never `dev-slim-5.8`.** Both resolve to the same digest
today, but the two-component form is a moving alias — the registry shows `dev-slim-5.5` and
`dev-slim-5.5.4` sharing one digest, i.e. `-5.5` tracked four patch releases. It is the
pin-a-SHA-not-a-branch coupling in `versions.lock`, applied to an image. And do not write a
`5.8.1` tag on the
assumption it will appear: `dev-slim-5.8.1` is a 404, and Epic does not image every hotfix
(there is no `dev-5.7.1` at all).

**Pin a SHA, not a branch — and here that is not stylistic.** There is **no `5.5` branch in
the repo at all** (only a stale `5.5dev` last touched 2026-01-14), and `main` has already
migrated 5.5 → 5.6dev → 5.7pdev → 5.8. The exact branch-evaporation failure this rule exists
for has already happened upstream. `main` currently points at `a552dd6c` — **do not pin
`main`**, it will move to 5.9. Recorded as a `versions.lock` coupling, which this project
earned twice: eProsima deleted the Fast-DDS branch the XRCE agent's `v2.4.2` tag depended on,
and QGroundControl's `latest` channel had to be repinned to an exact release.

**Also verify before pinning: the Epic base image tag — and note the ground is softer than it
looked.** *(corrected 2026-07-31)* **Neither tag has ever been confirmed to exist.** What was
verified on 2026-07-28 is EpicGames **org membership** — the access hurdle — not the tag. The
string `dev-slim-5.5.4` traces to `02_development_plan.md:145`: it came from a reference doc,
never from a registry query or a pull, and no UE image has ever been pulled here.

That makes it a documentation artifact, not a precedent — so it cannot be used to infer the
5.8 naming pattern. **List the registry's tags.**

Checked and blocked 2026-07-31: `ghcr.io` denies anonymous reads for this repository
(HTTP 403, `DENIED: invalid token`), and no ghcr credentials are configured in the container.
Confirming the tag needs an authenticated `docker manifest inspect` as the Epic-org account.

**Acceptance.** `versions.lock` carries the SHA, the confirmed engine image tag, and the
Cesium answer — with the evidence each was chosen for.

**Fallback, with a caveat that has changed.** Colosseum (UE5.6) — but Colosseum was
**archived read-only on 2026-07-11** (`04`). It is a *frozen* fallback, not a maintained
one, and building on it is technical debt from day one. Prefer an older Cosys-AirSim release
over a dead fork.

---

## SIM-02 — UE5.8 base image and source build

**Status:** ✅ **`done` (2026-08-01)** — image pulled in 9m05s with the digest matching
`SIM-01`'s registry query exactly, and the Cosys-AirSim plugin **compiles and links** against
it (`./build.sh --ue-root` inside `drone-sim/unreal:ue5.8`, exit 0). Evidence:
[`worklog/2026-08-01-c02-ue58-engine-image.md`](worklog/2026-08-01-c02-ue58-engine-image.md).
**Measured on the way through:** 24.0 GB compressed / 30 layers expands to **57.4 GB on
disk**, 54 GB of which is the engine at `/home/ue4/UnrealEngine` — a 2.4× expansion the
compressed figure did not predict, and the `D-04` disk decision was made on the compressed
figure.

**What.** Build from `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0`, digest
`sha256:daac0262…4a0c46` — the tag `SIM-01` found in the registry. Pin the three-component
form, never `dev-slim-5.8`.

> ### The engine image is Ubuntu 22.04, not 24.04
>
> **Confirmed by reading the image config blob directly (2026-07-31) — measured, not
> inferred:**
>
> ```
> org.opencontainers.image.version   22.04
> org.opencontainers.image.ref.name  ubuntu
> maintainer                         NVIDIA CORPORATION <cudatools@nvidia.com>
> ```
>
> **ROS 2 Jazzy has no jammy packages**, so **nothing Jazzy can be installed inside the
> engine image.**
>
> This makes `04`'s separate `sim` and `ros2` containers **mandatory rather than stylistic**.
> The AirSim ↔ ROS 2 boundary must stay the RPC / MAVLink socket; it cannot become a shared
> filesystem or a single container.
>
> **The plan already survives this** — `SIM-06` builds the wrapper in the existing 24.04/Jazzy
> container, which is now the only place it *can* be built. But anyone who assumed one image
> would hold both halves needs to stop assuming it. Verify the label at first pull.

**Why.** Cosys-AirSim must be built from source against the engine. The precompiled Linux
plugin is **not** an option: the UE5.5 one was built on Ubuntu 22.04 / CMake 3.22.1 /
clang-14 with upstream's explicit warning to "use the same toolchains … otherwise it is
better to build the plugin from source", and we are on Ubuntu 24.04.

**Access** — EpicGames org membership (verified 2026-07-28) **plus a PAT with
`read:packages`**. Org membership alone is not enough, which is the distinction that caused
the earlier "the tag was verified" error.

**Reproducibility hazard, flag it now:** CI will need its own credential with the same org
membership. That is a real gap against the *"fresh machine from the repo alone"* goal — a
clone plus a Dockerfile is not sufficient to build the engine image. It is the one documented
exception in the project goals above. Document the credential step in `docker/README.md`
before `D-05`.

**Check the CUDA runtime against the host driver at first pull**, not after a failed build.
The image sits on an NVIDIA CUDA base and this bench runs driver 610.43.03 — the same class
of mismatch that killed Isaac Sim on this host.

**Acceptance.** Engine image pulls and a trivial project packages.

**Traps.**
- **Do not run a UE5 shader compile concurrently with other GPU work** — the hardware
  assessment is explicit that 64 GB will not comfortably hold UE5 compilation alongside a
  heavy sim (`03_hardware_assessment.md:86`). Recorded as a `versions.lock` rule.
- **Budget disk before starting.** The internal NVMe is the constrained volume — currently
  ~262 GB free. Isaac Sim's images were already deleted to reclaim ~36 GB when that stack was
  dropped. UE5 projects and assets belong on the **external drive**, under
  `<drive-root>/Developments/projects/drone-sim/`, never in `~`.
- **Headless rendering needs the explicit flag.** Vulkan requires `-RenderOffScreen`;
  without it UE falls back to OpenGL silently. Mount the Vulkan/EGL ICD JSONs.
  **`NVIDIA_DRIVER_CAPABILITIES` is already set correctly by the base image**
  (`graphics,compat32,utility,compute,display,video`, read from the config blob) — do not
  override it with a narrower list, which is the more likely mistake here.
- **Budget the download before starting it: 24.0 GB compressed, 30 layers.** Docker's
  data-root is on the internal NVMe. Settle the storage question in `docker/todo.md` `D-04`
  *first* — this is the actual blocker for `SIM-02`, not the credential.
- **GPU selection under `-RenderOffScreen` has historically ignored `SDL_HINT_CUDA_DEVICE`
  and defaulted to GPU 0.** The project's GPU work split (render on the 3080, infer on the
  5060 Ti) is a hard rule — enforce it at the **container boundary** with
  `--device nvidia.com/gpu=0`, the way the Isaac probe did, rather than trusting an
  application-level flag.
- **Toolchain ABI mismatch is a real link failure, not a theoretical one.** Building AirLib
  with the plain system `clang` can produce a library incompatible with UE's linker.
  `5.8-v3.4.1`'s `build.sh` supports `--ue-root`/`UE_ROOT` to use the engine's bundled
  toolchain — **use it.** (The 5.5 line has no such flag at all, which was one more reason
  to move off it.)
- **This is a `docker/todo.md` `D-04` dependency** — build it containerized from the start,
  per the reproducibility goal.

---

## SIM-03 — PX4 ↔ Cosys-AirSim, and `/fmu/*` parity

**Status:** ✅ **`done` (2026-08-01)** — parity proved. **The vehicle arms but does not
climb; that is [`SIM-09`](#sim-09--make-it-actually-fly-lockstep-first), not a reopening of
this task.** Evidence:
[`worklog/2026-08-01-c03-px4-airsim-link.md`](worklog/2026-08-01-c03-px4-airsim-link.md)

```
Gazebo baseline : 51 /fmu/ topics (24 /fmu/out)
this simulator  : 51 /fmu/ topics (24 /fmu/out)
diff            : IDENTICAL
```

**The acceptance criterion as written is met** — identical topic names, transport swapped
only, verified by diffing rather than inspection. `Simulator connected on TCP port 4560`,
`lockstep_scheduler` initialised, `uxrce_dds_client` publishing.

**What the criterion did NOT ask for, and is therefore still open:** the vehicle has never
armed, taken off or moved. Identical *names* is necessary, not sufficient — the real test is
flying the same controller, unchanged, against this simulator. Filed as the next step rather
than folded into this task, because the stated bar was met and moving the bar retroactively
hides what was actually proved.

**Also unresolved here:** `/fmu/out/vehicle_status` was silent in the sample; lockstep
initialises but was not characterised (do not quote an RTF from this run); sensor values are
unchecked for physical sanity.

> **Port collision, found the hard way — and now historical.** The Gazebo stack published
> 4560, 8888, 14540, 14550 and 18570 — exactly what this simulator needs — so the two could
> not run at once and the parity diff had to be captured sequentially. That constraint
> disappeared with the Gazebo stack; it is recorded because the *sequential* comparison it
> forced is how the diff above was actually taken.

**Original definition:** ~~`todo` · Blocked by: `SIM-02`~~

**What.** Connect Cosys-AirSim to PX4 SITL via the Simulator MAVLink API (TCP 4560),
external-autopilot mode, **with PX4 also running `uxrce_dds_client`**.

**Why.** PX4↔AirSim over the Simulator MAVLink API is a **documented upstream capability of
AirSim**, not a bridge this project would have to write — which is the whole reuse-upstream
argument in one link.

**The parity requirement is the whole point, and it is easy to get subtly wrong.** MAVLink
lockstep drives the *sim physics handshake*; XRCE-DDS drives the *autonomy code*. The ROS 2
graph must see the same `/fmu/out/*` topics the real Pixhawk 6C produces, so a controller
written against the real aircraft ports across **unchanged**. If autonomy ends up subscribing
to `/airsim/*` poses instead, the code that flies in sim is not the code that flies on the
aircraft, and that divergence will not surface until the first real flight. Recorded as a
topic-parity coupling in `versions.lock`.

**Which PX4 tree?** The open question this task exists to answer. **v1.16.0** is already
built and working, and it is the tree the real Pixhawk 6C is flashed from. Cosys-AirSim talks
MAVLink rather than uXRCE-DDS for the physics handshake, so the v1.14.3 tree that Pegasus
needed may not be required at all. **If this simulator can use v1.16.0, the project drops
from two PX4 trees to one** — collapsing the development plan's dominant architectural risk.

**Do not assume it.** AirSim-lineage sims have broken across PX4 releases before (the
documented 1.10-vs-1.11 breakage), and Project AirSim pins v1.12.3 verbatim.

> **Answered: v1.16.0.** The parity diff below was taken against PX4 v1.16.0 and the flight
> in `SIM-09` flew on it. The v1.14.3 pin belonged to Pegasus and retired with it, so the
> project now carries **one** PX4 tree — the same one the aircraft runs.

**Acceptance.** Vehicle spawns in a UE5 scene, PX4 arms, and
`ros2 topic list | grep '^/fmu/'` **diffs clean against a Gazebo-baseline run** — identical
topic names, transport swapped only. Verified by diffing, not by inspection.

**Three defects to expect, found by research before the first build.** None is a blocker;
each costs a day if hit blind:

1. **`PX4Scripts/run_airsim_sitl.sh` is broken for any PX4 ≥ v1.14.** It exports
   `PX4_SIM_MODEL=iris`, which no longer matches an airframe filename after the rename to
   `10016_none_iris` — `rcS` exits 1 with "Unknown model". Upstream's multi-vehicle docs
   also reference `Tools/sitl_multiple_run.sh`, which 404s at v1.16.0. **This stack needs its
   own launch scripts**, which is fine — we already have a `bringup` package.
2. **`LockStep` may be dead code.** `initialize()` sets `lock_step_enabled_`
   (`MavLinkMultirotorApi.hpp:66`) and then `openAllConnections()` → `resetState()` clears
   it back to false. If that holds, **every documented "LockStep: true" setup is actually
   running free-running** — precisely the mode that degrades under LiDAR + multi-camera +
   heavy inference load, i.e. our exact target workload. **Verify empirically before trusting
   any timing result.** The fix is a small patch; the danger is not knowing.
3. **The "disabling lockstep" docs point at `boards/px4/sitl/default.cmake`**, which 404s in
   v1.16.0 — it is `sitl.cmake` now.

**Known upstream defect that lands squarely on obstacle avoidance.**
`Cosys-Lab/Cosys-AirSim#129` reports lockstep desync with ~1.2 s stale timestamps causing
PX4 to **reject `/fmu/in/obstacle_distance` as too old**. Obstacle avoidance writes exactly
that topic. Any graph mixing `/airsim_node/*` and `/fmu/*` needs an explicit time-alignment
design — do not assume both are on the same clock.

**Traps.**
- **PX4 lockstep is fragile against slow UE frames** — a slow render can trip SITL timeouts.
  Set `LockStep:true`, `UseTcp:true`, `SteppableClock`, and the `PressureFactorSigma`
  barometer tweak for fast GPS lock — then confirm lockstep is *actually engaged*, per
  defect 2 above.
- **Assert an AGGREGATE real-time metric, never an instantaneous one.** This project already
  paid for that lesson twice on the Gazebo baseline: the instantaneous `real_time_factor`
  field is a short-window estimate that swings 0.14–1.01 while the true ratio is 0.977. See
  `couplings.rtf-floor`.
- **Arming needs a GCS datalink** (`NAV_DLL_ACT=2`), supplied by the `sim-qgc` container, and
  the check is deliberately left enforced. The failure mode is "refuses to arm with no useful
  error".
- **Verify hover thrust and motor ordering empirically.** Actuator-output semantics changed
  at PX4 v1.13 (control allocator): pre-1.13 PX4 normalised PWM internally, v1.14+ forwards
  `actuator_outputs_sim` directly. Both are nominally 0..1 for multirotors and AirSim applies
  `0.8*x + 0.20`, so it *should* hold — but nothing upstream tests thrust fidelity against
  v1.16.
- **PX4 classifies AirSim as community-supported** and explicitly disclaims that it "may or
  may not work with current versions of PX4". No upstream regression test protects this
  integration. That is the argument for the exact tag pin we already have, and for `SIM-07`:
  with no upstream signal, our own flight gate is the only thing that would catch a
  regression.

---

## SIM-09 — Make it actually fly (lockstep first)

**Status:** ✅ **`done` (2026-08-01) — THE SIMULATOR FLIES.** The unmodified
`offboard_control` node reached 4/4 waypoints (errors 0.78 / 0.79 / 0.78 / 0.78 m), landed
and disarmed. Filed from `SIM-03`'s evidence:
[`worklog/2026-08-01-c03-px4-airsim-link.md`](worklog/2026-08-01-c03-px4-airsim-link.md)

**Symptom, reproduced twice.** The *unmodified* `offboard_control` node — the one written
against the Gazebo baseline, byte for byte — arms and then never climbs:

```
wait_for_fcu -> stream_setpoints -> request_offboard -> armed     ✓
FAILED: timeout in state takeoff                                  ✗   0/4 waypoints
PX4:  Preflight: GPS Vertical Pos Drift too high
PX4:  Ready for takeoff!  ->  Disarmed by auto preflight disarming
```

**What is already ruled out.** The first version of this failure was a `settings.json` bug —
a `Sensors` block *replaces* the defaults rather than extending them, so listing only the
barometer left the vehicle with no IMU, GPS or magnetometer. Fixed; the complaint moved from
`ekf2 missing data` to `GPS Vertical Pos Drift too high`, which is the difference between
having no GPS and having one that will not settle. **The controller is not at fault** — it is
byte-identical to the one that scored 10/10 on the Gazebo baseline.

### Diagnosed 2026-08-01 — root cause found. Evidence: [`worklog/2026-08-01-c09-lockstep-dead-and-the-35m-offset.md`](worklog/2026-08-01-c09-lockstep-dead-and-the-35m-offset.md)

**Two real defects; only one causes the failure.**

**(a) Lockstep is dead code — CONFIRMED, and NOT the cause.** `initialize()` sets
`lock_step_enabled_` (`:66`) and `openAllConnections()` (`:68`) clears it *twice* before
returning — `close()`→`disconnect()`→`resetState()` (`:957`) and directly (`:992`). Nothing
sets it again, so the `:1613` guard can never pass. Runtime confirms: **zero** `"Enabling
lockstep mode"` across a full session while another message from the *same* `addStatusMessage`
path does appear; measured RTF 0.9193 tracks wall time. **`"LockStep": true` is silently
ineffective, so every timing number from this simulator is free-running** — recorded in `versions.lock` and
`sim/ue5/settings.json`. It does not explain the takeoff failure; free-running SITL flies fine.

**(b) The vehicle thinks it is already at 35 m — THIS is the cause.**

```
z (NED, negative = up):  min=-35.168  max=-35.166  samples=9415   <- 2 mm spread
ref_alt          88.113 m   (EKF local origin)
altitude_msl_m  123.280 m   (GPS)          difference = 35.167 m  <- exactly the stuck z
dist_bottom       0.0999 m  z_valid true  fix_type 3  sats 15     <- it is ON THE GROUND
```

The EKF's local origin sits 35.17 m below where GPS says the vehicle is. Nothing is drifting;
`GPS Vertical Pos Drift too high` is that 35 m disagreement under a misleading name.

**Why it stops the flight:** `offboard_control.py:344` captures home as **x,y only (z dropped)**
and `:395` targets **absolute** ENU z = 10 m. On the Gazebo baseline the vehicle rests at
z ≈ 0 so that is 10 m AGL; here it reports +35.17 m, so **the controller commands a 25 m
descent into the ground**. `_reached()` measures 25.17 m against a 1 m radius, never passes, times out — and PX4,
having armed without taking off, auto-disarms via `COM_DISARM_PRFLT`. Every symptom accounted for.

**The controller is not at fault and must not be patched.** It is byte-identical to the one
scoring 10/10 on the Gazebo baseline, and correct for any sim whose origin is at ground level.
The fix belongs in the sim — a controller needing per-simulator altitude fudging is not the
same controller, which is the whole parity claim.

**Open question, deliberately not silently fixed:** capturing `cur[2]` at `:344` would make the
controller origin-agnostic, but it changes what `takeoff_altitude` means (AGL vs local-frame
absolute). Decide it; do not patch it mid-diagnosis.

**(c) CORRECTION — the sensor diagnosis above was inferred, and measuring refuted it.**
Querying directly: `getBarometerData` → 122.883 m, `getGpsData` → 123.280 m. **They agree.**
There is no sensor disagreement; the 35 m lives entirely in PX4's `ref_alt`, an origin set at
88.113 m and never revised while both sensors read ~123 m throughout. So it is a **stale EKF
origin**, i.e. a **startup-ordering** problem, not a sensor one — and `OriginGeopoint` would
have fixed nothing.

**Confirmed by restarting `sim-px4` alone, sim untouched:**

```
before:   ref_alt  88.113 m    z = -35.167 m
after:    ref_alt 123.280 m    z =  -0.0002 m     <- matches GPS exactly; no config change
```

**This also explains the intermittent bring-up** that `SIM-03` recorded and could not pin down —
an order-dependent origin works some runs and not others. Same root, two symptoms.

**(d) RESULT — it flies.**

```
reached takeoff altitude 10.0 m
waypoint 1/4 (0.78 m)  2/4 (0.79 m)  3/4 (0.78 m)  4/4 (0.78 m)
landed and disarmed          outcome: success   4/4
```

Peak −10.233 m NED against a 10 m target; AirSim truth and PX4 EKF tracked within 0.8 m. The
run was recorded to video under the untracked `out/` directory. **The controller was never
patched**, which is what makes the parity claim mean anything.

**Remaining work:**

1. **Make the ordering deterministic in the bring-up** so this cannot regress — PX4 must
   initialise its EKF origin only after the vehicle has settled. The restart is the *diagnosis*,
   not the fix; the launch layer should enforce it, and a gate should assert `ref_alt` matches
   GPS before a run counts. **Filed as `SIM-10`.**
2. **Patch lockstep separately** — restore `lock_step_enabled_` from `connection_info_` rather
   than forcing false in `resetState()`, which also survives reconnects. Vendored C++: needs a
   recorded patch plus a plugin rebuild, and **two** copies of the header exist
   (`AirLib/…` and `Unreal/Plugins/AirSim/Source/AirLib/…`) — both must be patched.
3. **Decide the AGL-vs-absolute question** above.

<details><summary>Original hypotheses as filed (kept — 1 and 2 both proved real)</summary>

1. **Settle whether lockstep is actually engaged.** The highest-value open question here.
   `initialize()` sets `lock_step_enabled_`; `openAllConnections()` → `resetState()` appears
   to clear it. If AirSim free-runs while PX4's `lockstep_scheduler` is active, sensor cadence
   and sim time diverge — and GPS vertical drift is exactly what an EKF would then report.
   **The same defect would explain the intermittent bring-up deadlock already observed**, so
   two open symptoms may have one cause. Measure it; do not read it off the config.
2. **Set `OriginGeopoint`.** Currently unset, so AirSim's GPS origin and PX4's `LPE_LAT`/
   `LPE_LON` may disagree — `04` flags this as where AirSim+Cesium coordinate mismatches bite.
3. **Confirm AirSim physics steps at all**, by commanding the vehicle over AirSim's own RPC
   with PX4 out of the loop. If it does not move there either, the problem is upstream of PX4
   entirely.

</details>

**Done when:** the controller, unchanged from the one that flew the Gazebo baseline, reaches
all four waypoints here — and the lockstep question is answered with a measurement, not an
inference.

**Blocks:** `SIM-07` (a flight gate needs a flight), and therefore `SIM-05`.

---

## SIM-10 — Make the EKF-origin ordering deterministic

**Status:** 🟡 **built and verified once (2026-08-01); "N times in a row" not yet run.**
Evidence: [`worklog/2026-08-01-c10-deterministic-bringup.md`](worklog/2026-08-01-c10-deterministic-bringup.md)

`scripts/sim_up.sh` cold-starts the stack in 83 s unattended and `scripts/check_ekf_origin.py`
asserts the origin before anything flies. On its first honest cold start the check **caught a real
stale origin at 9.069 m** — not a replay of `SIM-09`'s 35.167 m but a fresh race at a different
magnitude — restarted PX4, re-verified at 0.000 m apart, and the stack then flew **4/4**
(errors 0.772 / 0.786 / 0.773 / 0.773).

**Two things recorded against the obvious write-up:**

- **The settle-wait alone was NOT sufficient** — the origin still came up 9 m stale. What saves
  the run is the verify-then-restart loop. The honest description is *"verify and repair"*, not
  *"order it correctly"*; deleting the retry loop as redundant would break it.
- **The check reported `OK` on a `NaN`.** `abs(nan - x)` is `nan` and `nan > tol` is False, so it
  green-lit the one state it existed to catch — PX4 publishes `ref_alt` as NaN until the EKF has
  any origin. Guarded, regression-tested, and `UNKNOWN` now differs from `STALE` in the exit code.
  This repo already had `test_nan_error_must_not_pass` for the same class; the lesson was not
  applied to new code.

**Gate integration landed 2026-08-01.** `run_gate.py` now asserts the EKF origin before each
run. A void run is **excluded from the success rate** (it never measured the flight code) and
**separately blocks the criterion** — excluding without blocking would let a gate where 9 of 10
runs were void report 100%. Scoring moved into a pure `score()` so the semantics are unit-tested:
all-void is not a pass, an empty run list is not a pass, and a real failure is still a failure.
The pre-existing `--reuse` caveat survives the rework. `--no-origin-check` exists as an escape
hatch for stacks without `/fmu/out/vehicle_gps_position`.

**One bug caught before it shipped:** the first version ran the checker with `sys.executable` on
the gate host — where `ros2` does not exist — which would have made **every** run void and left
the gate permanently INCONCLUSIVE. A check that fails closed on its own plumbing disables a gate
as surely as one that fails open. It now execs into the `ros2` service the same way
`run_scenario.py` does, piped over stdin rather than assuming a mount path, and an AST test pins
the call site.

**Still open:**

- ~~The live gate run on the Gazebo baseline is UNVERIFIED.~~ **Verified 2026-08-01.** I had
  written the port collision up as if it blocked this; it did not. The two stacks only conflicted
  when run *simultaneously*, and running them one at a time is the same sequencing already used to
  capture the `SIM-03` parity diff. Tore this stack down, freed all five ports, ran the baseline
  gate: seed 1 **PASS, worst 0.375 m, `void: false`** — the origin check exec'd into the `ros2`
  service and correctly did **not** void a healthy run, which is the exact regression that
  mattered. Report carries `valid_total`, `voids: 0`, and the updated criterion string.
  **Full 10-seed gate then re-run under the new code: 10/10, SR 100%, `met: true`,
  `voids: 0`, worst error 0.555 m across all seeds, 1350 s wall.** So the origin check added a
  per-run assertion without disturbing the baseline result.

  **Lesson: "blocked by a resource conflict" deserves one second of thought about whether the
  conflict is concurrent or absolute.** It was concurrent, and the work was twenty minutes away.
- Run the cold start N times; the 5-reads-at-0.05 m settle heuristic was chosen, not derived.

`SIM-09` proved the vehicle only flies when PX4 initialises its EKF origin **after** the sim
vehicle has settled. Before this task that was achieved by restarting `sim-px4` by hand once the
sim was up — a diagnosis, not a fix. Left as is, the simulator flies or does not depending on
container start order, which is exactly the intermittency `SIM-03` could not pin down.

**Do:**

1. **Enforce the ordering in the bring-up** rather than relying on luck or a manual restart —
   PX4 waits until AirSim reports the vehicle settled before connecting.
2. **Assert it in the gate.** A run must not count unless `ref_alt` agrees with
   `vehicle_gps_position.altitude_msl_m` to within a metre at start. The failure mode is silent
   and looks like a control bug, so it needs a check that names it. It is the same
   void-versus-real-run distinction the Gazebo gate had to make, applied to a different failure.

**Done when:** cold-starting the whole stack from nothing produces a flyable vehicle N times in
a row with no manual intervention, and a deliberately mis-ordered start is *failed by the gate*
rather than scored.

**Blocks:** `SIM-07` — a flight gate that can silently score a mis-ordered stack is worse than none.

### Review fixes verified live — 2026-08-02

`/review` on both stacked PRs found three defects; all fixed, and the Gazebo-baseline gate
re-run to prove the gate change did not regress:

```
10/10  SR 100%  voids 0  met true  worst 0.565 m   1220 s
```

**Faster than before the fix** (1220 s vs 1350 s; 124 s/seed vs 135 s) — the barrier returns
immediately when the origin is already sane, so it costs nothing on a healthy stack and only
spends time when it is actually preventing a bad run. Worth measuring rather than assuming: a
per-seed wait is exactly the kind of change that quietly triples a gate.

- **The gate had no barrier before the origin check.** `restart_stack()` returns on *container
  health*, not on the EKF establishing an origin, so the check raced the estimator. Any void
  blocks the criterion, so one slow start would have turned the whole gate INCONCLUSIVE. It
  passed 10/10 before the fix **by timing coincidence, not by construction.** Now waits up to
  90 s on `VOID_UNKNOWN` and voids immediately on `VOID_STALE` — the first actual use of a
  distinction those exit codes always carried.
- **The depth assertion could not fail.** `max(pos) > 0.5` passes on a frame where every pixel
  is the 16312 m no-return sentinel. Replaced with `depth_is_usable()`, requiring bounded
  returns.
- **`versions.lock` contradicted itself** — `status: LOCKED` alongside `why_not_LOCKED_yet`.
  Fixed on both branches, and `check_versions_conflicts.py` now fails CI on the whole class.

56 tests, each new one verified by breaking the code it guards.

---

## SIM-04 — Camera/depth/LiDAR into the existing ROS 2 graph

**Status:** ✅ **`done` (2026-08-02)** — **sensor data is properly published to ROS 2, verified
by value.** Closed on the owner's criterion: the acceptance is that the modalities reach the
ROS 2 graph usably, which they do.

```
RGB     31.2 Hz  640x480 rgb8     Depth   29.6 Hz  32FC1, metric, real geometry
LiDAR   17.4 Hz  8192 points      IMU    366 Hz published / 311 Hz distinct
GPS / magnetometer / odometry  365 Hz    camera_info resolves in TF    /clock advancing
```

Verified with `scripts/verify_sensors.py`, which asserts **values** rather than topic
presence — IMU reads 9.807 m/s² at rest, depth contains bounded returns, LiDAR points are not
all at the origin, RGB is not a blank frame.

**Carried forward rather than blocking** (all recorded in
[`vendor/cosys-airsim.md`](vendor/cosys-airsim.md)): the IMU is a polled snapshot with
~15% duplicate timestamps; frames are NWU with a tested conversion in `control/frames.py` that
nothing calls yet; and the unexplained 7.342° yaw residual. None of these stop the data being
published and usable — they are `SIM-05`'s problem, and `SIM-05` is deprioritised.

### Where it actually stands

`airsim_node` builds (both packages, 1 min 22 s — matching `SIM-06`), connects to AirSim, logs
`Connected!` and `AirsimROSWrapper Initialized!` — **then dies before publishing anything:**

```
terminate called after throwing an instance of 'eprosima::fastcdr::exception::BadParamException'
  what():  The string contains null characters
[ros2run]: Aborted          ->  0 /airsim_node/* topics
```

A Fast-CDR serialisation fault: a string field carrying embedded NULs.

**Localised by backtrace (gdb, in-container), not by guessing:**

```
#9  geometry_msgs::msg::TransformStamped cdr_serialize
#10 tf2_msgs::msg::TFMessage cdr_serialize
#16 tf2_ros::TransformBroadcaster::sendTransform
#18 AirsimROSWrapper::publish_odom_tf(nav_msgs::msg::Odometry const&)   <- HERE
#19 AirsimROSWrapper::publish_vehicle_state()
#20 AirsimROSWrapper::drone_state_timer_cb()
```

So it is the **odometry TF** — `header.frame_id` or `child_frame_id` in `publish_odom_tf`
(`airsim_ros_wrapper.cpp:1232`) — not an image, sensor or segmentation topic.

**Three candidate sources ruled out empirically:**

| Hypothesis | Checked | Result |
|---|---|---|
| Segmentation object names carry NULs | `simListInstanceSegmentationObjects` | **clean** — 255 names, 0 NULs, 0 empties |
| The RPC settings string carries NULs | `getSettingsString` | **clean** — 4827 chars, 0 NULs |
| The frame-id constants are malformed | header `AIRSIM_ODOM_FRAME_ID` / `AIRSIM_FRAME_ID` | **clean** — plain `"odom_local"` / `"world"` |

`listVehicles` returns `['PX4']`, so `vehicle_name_` — which feeds `child_frame_id` at `:1452`
— looks clean from the outside too.

### ROOT CAUSE — a data race, not a bad string. FIXED and verified.

Instrumenting `publish_odom_tf` to hex-dump both frame ids killed the fourth hypothesis too:

```
ODOMTF frame_id=[PX4/odom_local] size=14 hex=50 58 34 2f 6f 64 6f 6d 5f 6c 6f 63 61 6c
ODOMTF child=[PX4]               size=3  hex=50 58 34
862 prints, ZERO containing a 00 byte  ->  and it still aborted
```

**Clean strings that still serialise as containing NULs means the value changes between the
copy and the write** — a race. The log timestamps proved it directly: **30% of consecutive
`publish_odom_tf` prints were out of order**, i.e. the callback was executing concurrently on
several threads for a single vehicle.

```
airsim_node.cpp:22   create_callback_group(rclcpp::CallbackGroupType::Reentrant)
airsim_node.cpp:25   rclcpp::executors::MultiThreadedExecutor
```

**`Reentrant` + `MultiThreadedExecutor` lets `drone_state_timer_cb` re-enter concurrently**,
so threads race on the shared per-vehicle `curr_odom_`. Copying a `std::string` while another
thread reassigns it is a torn read, and Fast-CDR sees the result as embedded NULs. That explains
every observation at once: the strings log clean, 862 publishes succeed, then one aborts, and it
is non-deterministic.

**Fix — one word:** `Reentrant` → `MutuallyExclusive` at `airsim_node.cpp:22`.

```
node alive: yes    crashes: 0    /airsim_node topics: 14    odom_local flowing
/airsim_node/PX4/{imu/imu, gps/gps, magnetometer/magnetometer, altimeter/barometer,
                  odom_local, environment, global_gps}  + segmentation, object_transforms
```

**Method note worth keeping:** three guesses at "which string has a NUL" all died, and the
answer was that no string ever did. The backtrace narrowed the site; the hex dump refuted the
whole *class* of hypothesis and forced the race explanation. **Measuring the thing I was sure
about is what broke the deadlock** — the same lesson as `ref_alt` in `SIM-09`.

### The fix is NOT yet in the tree — this is vendored C++

Applied to a container-local copy only; `vendor/Cosys-AirSim` is untouched and pristine. Per
least-destructive-vendor-edits it must land as a **recorded patch plus vendoring notes**, not an
in-place edit:

1. Write the one-line change as a patch file and apply it in the build, not by editing `vendor/`.
2. Start `docs/vendor/cosys-airsim.md` (still missing) and record this as deviation #1 — it is
   an upstream defect, worth reporting to Cosys-Lab.
3. **Clean the 163 MB of build artifacts out of `vendor/Cosys-AirSim/ros2/`** and keep builds
   out-of-tree, so the tree stays diffable against upstream.
4. ~~Re-check the trap list against a running node~~ — **done 2026-08-02**, three of four
   confirmed. Evidence:
   [`worklog/2026-08-02-c04-trap-list-measured.md`](worklog/2026-08-02-c04-trap-list-measured.md)

   | # | Trap | Verdict |
   |---|---|---|
   | 1 | Frames NWU not ENU | **CONFIRMED** — `convert_tf_msg_to_enu()` has 0 call sites; measured yaw missed ENU by 97.3° and NWU by 7.3° |
   | 2 | `/clock` wrong topic | **CONFIRMED, double defect** — `publish_clock` is never *declared*, and it publishes to `~/clock`. Fix: `-r /airsim_node/clock:=/clock`, in the launch |
   | 3 | Polled IMU | **CONFIRMED, quantified** — 1501 Hz published, 6630 distinct, **77.9% duplicates**, real rate ~333 Hz, gaps to 3× base |
   | 4 | `camera_info` frame_id | **CONFIRMED and FIXED** — `camera_info` said `front_center_optical` while TF and the image said `PX4/front_center_optical`. Patch `0002`; verified |

   **Cameras and GPU-LiDAR are now in the graph** — `sim/ue5/settings.json` gained a `Cameras`
   block (RGB + `DepthPlanar`, 640×480) and a GPU-LiDAR (`SensorType: 8`). **19 topics, up from
   14**, carrying real data: 640×480 rgb8 and an 8192-point cloud. Parsing semantics were checked
   *before* editing — `loadCameraSettings` clears but defaults to an empty map (additive), while
   per-vehicle `Sensors` is iterated by key (so the LiDAR was added alongside the existing four,
   with an assertion that all five survive).

   **The build is also no longer ephemeral:** `patches/cosys-airsim/*.patch` +
   `scripts/build_airsim_wrapper.sh` reproduce it in ~2 min with `vendor/` pristine. The script
   applies every patch in numbered order and asserts each one's artifact.

   **Trap 2 is now fixed properly** — `ros2_ws/src/bringup/launch/perception.launch.py`
   sets `publish_clock:=true` and remaps `/airsim_node/clock` → `/clock` unconditionally.
   `ros2 launch bringup perception.launch.py` with **no flags** gives a ticking `/clock`.

   **Trap 1 now has a conversion, in the frozen place.** `nwu_to_enu` / `enu_to_nwu` /
   `yaw_nwu_to_enu` / `yaw_enu_to_nwu` were added to `control/frames.py` — the single conversion
   point `conventions.md` §3 mandates — not to a simulator-specific node. **Unlike `enu_to_ned`, this pair is
   NOT an involution** (90° rotation, so twice = 180°), which breaks the intuition the rest of
   that module earns; pinned by a dedicated test. 7 new tests, 15 in the file, verified by
   breaking the implementation. *Nothing consumes it yet* — that is `SIM-05`'s job.

   **Navigation-readiness verified 2026-08-02** — `scripts/verify_sensors.py` checks
   sensor **values**, not topic presence, and all required checks pass:

   | | before | after |
   |---|---|---|
   | RGB | 1.1 Hz | **31.2 Hz** |
   | Depth | 1.1 Hz | **29.6 Hz** |
   | GPU-LiDAR | 1.6 Hz | **17.4 Hz** |
   | IMU | 1328 Hz, 77.3% dup | **366 Hz, 311 Hz distinct, 14.6% dup** |
   | GPS / mag / odom | 1330 Hz (dup) | **365 Hz** |

   The gap was **five uninitialized `double` timer periods** in the wrapper — `get_parameter`
   returns false for an undeclared name and leaves the value untouched, so every sensor rate was
   stack garbage. Fixed in the launch (with `value_type=float` forced, or it silently stays
   uninitialized). **That also re-explains trap 3:** the IMU duplicates were never inherent to
   the polled design, just a garbage poll period. Patch `0003` then removed the serialisation
   that patch `0001` had introduced.

   **Artifacts delivered (filed retroactively — these were built before being written down,
   which the plan-first rule says should not happen):** `scripts/verify_sensors.py`,
   `scripts/build_airsim_wrapper.sh`, `patches/cosys-airsim/000{1,2,3}`,
   `ros2_ws/src/bringup/launch/perception.launch.py`, and
   [`vendor/cosys-airsim.md`](vendor/cosys-airsim.md).

   **Still open on `SIM-04`:**
   - the unexplained 7.342° yaw residual;
   - **the simulator segfaulted once after ~57 minutes** — `Array index out of bounds: 18823
     into an array of size 0`, preceded by a MAVLink `hil` EPIPE. **Soaked 2026-08-03 and NOT
     reproduced**: 90 minutes of the full stack plus a concurrent RPC load, 74,253 captures,
     zero anomalies. Both the count hypothesis (upstream's "every 2000 or so calls") and the
     time hypothesis (~57 min) are refuted. Still `n = 1` with no stack trace, so this is
     *unreproduced*, not fixed.

**Blocked by:** nothing — the crash is fixed; the work is now the trap list.

**What.** Bring Cosys-AirSim's sensors up on the ROS 2 C++ wrapper: RGB, depth,
GPU-LiDAR, and the annotation/segmentation cameras.

**Why.** This is the whole reason for a photoreal renderer. Cosys-AirSim's sensor set is the
richest of the living AirSim forks — GPU-LiDAR with tunable noise and ground-truth labels,
echo/radar sensors, event cameras, camera distortion (`01_sim_stack_report.md:31`).

**The topics are a new surface, not a renamed one.** The wrapper publishes under
`/airsim_node/<vehicle>/…` with `airsim_interfaces/*` custom message types. The Gazebo
baseline had no equivalent — "only the transport is swapped" was always a claim about the
**controller** (`/fmu/*`, `SIM-03`), never about perception. Budget for genuinely new
integration here.

**Traps — four of these are documented wrong upstream, so read carefully.**

- **FRAMES ARE NWU, NOT ENU — AND THE DOCS SAY OTHERWISE.** `docs/ros2.md` claims *"the
  right-handed coordinate frame of the ROS standard and not in NED"*. The code negates only
  y and z, which is **NED→NWU** (and FRD→FLU). `convert_tf_msg_to_enu()` exists at
  `airsim_ros_wrapper.cpp:1600` and is **never called** — every path calls
  `convert_tf_msg_to_ros()` instead. **Anything written against REP-103 or `px4_ros_com`'s
  ENU assumption will be yaw-rotated 90°.** Verify empirically against a known heading
  before trusting a single pose. This is exactly the silent-frame-error class
  [`conventions.md`](conventions.md) exists to prevent, and the frozen
  convention (ENU/FLU outside, NED inside, converted in one tested place) still governs —
  the simulator does not get to invent a second convention, but it does have to *reach* the
  first one from NWU.
- **`/clock` IS PUBLISHED ON THE WRONG TOPIC.** `publish_clock` publishes to `~/clock`,
  which resolves to `/airsim_node/clock` on a node named `airsim_node` — **not** `/clock` —
  and it defaults to `False` in the launch file. **This project already paid for that exact
  failure shape once on the Gazebo baseline:** `use_sim_time: true` with nothing publishing
  `/clock` freezes every node's timers at zero and looks precisely like a deadlocked
  controller. Remap it.
- **IMU IS A POLLED SNAPSHOT, NOT A STREAM.** `publish_vehicle_state()` calls `getImuData()`
  over RPC once per `drone_state_timer` tick (default 0.01 s) and publishes only the latest
  sample — intermediate samples are dropped and spacing is set by RPC/executor jitter, not
  the sensor. cuVSLAM and any preintegrating VIO expect a dense, evenly-spaced stream.
  **Measure the actual arrival distribution.** IMU messages also ship with zero covariances
  (`// todo covariances` in the source).
- **`camera_info.header.frame_id` does not match the TF tree** — it is `<camera>_optical`
  while the image and the static TF use `<vehicle>/<camera>_optical`, so `image_proc`,
  `depth_image_proc` and any TF-aware perception node cannot resolve it. Trivial patch, but
  it bites on first integration.
- **Sensor cadence versus lockstep.** `04`'s own decision threshold: if UE5 lockstep cannot
  sustain sensor cadence without PX4 timeouts, split the work across GPUs or hosts. (`04` also
  offered "move physics to Isaac/Pegasus" as the other escape hatch; that option is gone with
  Isaac Sim, so splitting is the one that remains.) Note this interacts with `SIM-03`'s
  defect 2 — confirm lockstep is genuinely engaged before concluding anything about cadence.
- **`odom_local` is sim ground truth, not an estimate.** It comes from
  `getMultirotorState()`/`kinematics_estimated` — the physics engine's truth, not PX4's
  EKF2. On the real Pixhawk 6C the equivalent `/fmu/out/vehicle_odometry` is a *noisy
  estimate*. **Tuning or training against noiseless odometry is a sim-to-real gap that will
  not surface until the real flight.** Prefer `/fmu/out/vehicle_odometry` wherever both
  exist.

**Acceptance.** Depth and LiDAR topics at stable rates, with **measured** timestamp jitter
and IMU inter-arrival distribution recorded — not asserted — and a heading check proving
which frame convention the poses actually arrive in.

---

## SIM-07 — The flight gate

**Status:** ✅ **done 2026-08-07 — 10/10, 100%, zero VOID.** See the `SIM-07` entry above for
the run, the two defects that had to be fixed to get there, and what the number does and does
not cover. This section is the original PLAN and is kept for its design rationale; it is no
longer the status.

> The line below said "wired, never run to a success rate" until 2026-08-07. It was true for
> longer than it should have been, and this file carried **two** `SIM-07` headings, so updating
> one left the other lying. Worth remembering when adding an entry: grep the ID first.

**What.** The same 4-waypoint square the Gazebo baseline used, run across N seeded runs,
scored the same way, with an MCAP per run.

**Why it is necessary.** Without its own gate the simulator has no acceptance criterion, and
every later result rests on an unmeasured foundation. **The Gazebo baseline's SR 10/10 does
not transfer** — it was evidence about Gazebo, and that stack is retired.

**Reuse, do not rewrite — and this is already done.** `run_gate.py` re-derives pass/fail from
the numbers rather than trusting the controller's `outcome` field, and rejects non-finite
errors — a check missing from its first version that caught a real NaN-laundering bug. Its
scoring semantics are unchanged and unit-tested: **VOID is distinct from FAIL**, a void run is
excluded from the success rate *and* separately blocks the criterion, all-void is not a pass,
and an empty run list is not a pass. What changed is the stack it brings up.

> **A seed currently controls the SPAWN POSE ONLY.** The retired Gazebo harness seeded wind
> and vehicle mass through a generated world overlay; there is no equivalent here. Getting one
> means driving Cosys-AirSim's wind API (`simSetWind`, recorded under `SIM-08`) from the
> harness. **Until that exists, do not describe a gate run as covering varied conditions** —
> it covers varied starting positions, which is a much weaker claim.

**Acceptance.** SR reported over N seeded runs, with the per-seed table and MCAP paths. Each
run's EKF origin is asserted before it counts, and voids are reported separately from
failures rather than folded in.

**Traps.**
- **Budget.** The Gazebo gate took ~19 min and already missed the 10-minute CI budget; a UE5
  stack starts slower, not faster. Decide the seed count with evidence, and **do not fit a
  budget by quietly weakening the gate.**
- **The gate needs a barrier, not just a check.** `restart_stack()` returns on *container
  health*, not on the EKF establishing an origin, so a check placed straight after it races
  the estimator — and since any void blocks the criterion, one slow start turns the whole gate
  INCONCLUSIVE. `SIM-10` fixed this by waiting up to 90 s on `VOID_UNKNOWN` and voiding
  immediately on `VOID_STALE`; do not remove the wait as redundant.

---

## SIM-05 — Isaac ROS perception on the simulator's imagery

**Status:** ⏸️ **deprioritised 2026-08-02 by the owner** in favour of `SIM-11` (photorealistic
scene + dynamic actors). **Unblocked, not abandoned** — `SIM-04` is done and the imagery is ready
whenever this resumes.

**What it will inherit when it does**, so the next person does not rediscover it: cuVSLAM
preintegrates IMU between frames and wants a dense, evenly-spaced stream, and the wrapper's IMU
carries ~15% duplicate timestamps by upstream design. Starting **visual-only** and adding
inertial afterwards is the lower-risk order. Frames are also NWU, and `control/frames.py` has a
tested conversion that nothing calls yet.

**Originally:** `todo` · **Blocked by:** `SIM-04`

**What.** Run `isaac_ros_visual_slam` (cuVSLAM) and `isaac_ros_nvblox` against the simulator's
camera/depth topics.

**Why — worth stating clearly:** **Isaac ROS is not Isaac Sim.** cuVSLAM and nvblox are
ROS 2 **Jazzy** packages that consume image and depth topics from *any* source. Dropping Isaac
Sim cost the project a renderer, **not** the GPU perception stack — which is why this task
survived the pivot unchanged.

**This is also a second, independent argument for the Jazzy decision** (`SIM-06`): these
packages are Jazzy, and moving the project to Humble to satisfy a documentation preference
would put the perception stack on the wrong side of the split.

**Acceptance.** cuVSLAM produces odometry from the simulator's imagery; nvblox produces a
costmap.

---

## SIM-11 — Load the user's own world (bring-your-own `.uproject`)

**Status:** 🟡 **half done — actors are the remaining work and the current focus.**
Photorealism landed 2026-08-03 (PR 33): `simGetImages` matches Unreal's own render of the
same camera actor to **1.15 of 255** across six scenes, via three `settings.json` keys on a
**stock** plugin. `SIM-13` gives deliberate vehicle placement, `SIM-15` confirms the command
interface, `SIM-16` provides a recorded mission to demonstrate against. **What the world still
lacks is people, vehicles and moving obstacles** — nothing to navigate *around*.
Filed 2026-08-02; **rescoped 2026-08-03** after the
owner corrected the goal.

**The goal is a MECHANISM, not a scene.** The user builds or buys a photorealistic world
wherever they like — Fab on a Windows box, their own Unreal work, a colleague — and tells this
simulator to load it. Picking one world for them is not the deliverable; **the pipeline that
accepts theirs is.**

**Corrections that produced this rescope** — the first survey answered a narrower question and
got several constraints wrong:

- **Authenticating on a non-Linux machine is acceptable.** The "Fab needs the Epic Games
  Launcher, which has no Linux build" finding is therefore *not* disqualifying. Users can fetch
  on Windows/macOS and copy the project across; upstream's own docs say exactly this.
- **The reproducible-as-Docker rule was applied too strictly.** The project already carries a
  documented amendment — *"from the repo alone, plus one documented credential step"* (see the
  project goals above). A Cesium ion token or an Epic account fits that precedent. Cesium's
  real cost is that tiles **stream at runtime** (a run needs network), not a licence
  conflict.
- **The NoAI/`isAiForbidden` clause was over-read.** It targets training generative models on
  the assets. Running SLAM, optical flow or 3D mapping over rendered frames is not that. Worth
  a second look only before *fine-tuning* a model on rendered frames.
- **The simulator is not a VLM project.** It is drone simulation with a photorealistic world,
  and what people do with the imagery — 3D mapping, optical flow, visual SLAM, language-driven
  navigation — is theirs to choose. The earlier emphasis on per-object segmentation reflected a
  narrower reading of who the world is for.
- **"Fused mesh hurts perception" was overstated.** VSLAM consumes images and cannot see mesh
  topology; optical flow likewise; for 3D mapping a fused mesh is a natural ground-truth
  reference. Fused geometry also still has *collision* — you simply cannot move or delete an
  individual building. It costs per-object semantic labels, and nothing else on that list.

### There is no upstream world to adopt — verified

Cosys-AirSim ships **exactly one environment: `Blocks`** (1.0 GB of untextured grey boxes).
Every upstream release from 5.2 to 5.8 publishes `Blocks_packaged_*` / `Blocks_editor_project_*`
and **nothing else, ever**. `DynamicObjects` (19 MB) is a *library*, not a world. The classic
Neighborhood / Landscape Mountains / Africa environments belong to Microsoft AirSim, are
**UE4.27 cooked binaries with no `.uproject` or `Content/`**, and cannot take a UE5.8 plugin.

### The mechanism: inject AirSim into the user's project

Upstream documents this manually; **all of it is text**, and one step that the docs present as
a GUI action is avoidable:

1. copy the prebuilt `Plugins/AirSim` folder into their project
2. add `AirSim` + `ChaosVehiclesPlugin` to the `"Plugins"` array of their `.uproject`
3. `Config/DefaultEngine.ini` → `GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode` and
   `GameDefaultMap=<their map>`
4. `Config/DefaultGame.ini` → the `+DirectoriesToAlwaysCook` entries for AirSim content

**Step 3 is the finding that makes this scriptable at all.** Upstream's step 9 says to set
`GameMode Override` in `Window/World Settings` — a GUI operation. But `AAirSimGameMode` is a
**plugin** class (`AIRSIM_API`, `Plugins/AirSim/Source/AirSimGameMode.h`), so it can be named
directly in config and applied globally. `Blocks` proves the pattern works, using its own
project class: `GlobalDefaultGameMode=/Script/Blocks.BlocksGameMode`.

### Two tiers, and the sequencing matters

| | what it is | cost |
|---|---|---|
| **A1** | user's project is **content/Blueprint-only** | pure text edits + a folder copy. No compile, no GUI. Most Marketplace environment projects. |
| **A2** | user's project has its own **`Source/` C++** | UnrealBuildTool must compile *their* module against UE5.8, inheriting their engine-version assumptions. |

**Do A1 first.** It is the common case, fully scriptable, and testable today. A2 and
engine-version conversion carry the unknowns, and they are better discovered against a working
A1 pipeline than treated as a prerequisite.

### ✅ A1 IS BUILT AND PROVEN END TO END — 2026-08-03

`scripts/inject_airsim.py` takes a user's `.uproject` and injects AirSim with **no compile, no
editor, no GUI, no display**. Verified against a project that was never ours:

```
server version: 4              vehicles: ['PX4']
position: x=0.00 y=0.00 z=2.29 scene objects: 32
from OUR level: ['PlayerStart_0']
sim log: "Waiting for mavlink vehicle..."
         WeatherActor_C /Game/Maps/TheirMap.TheirMap:PersistentLevel.WeatherActor_C_0
```

The second log line is the load-bearing one: **the AirSim plugin spawned its own actors into
the user's map**, and the PX4 vehicle from *our* `settings.json` appeared in *their* project.

**What the script does** — four text edits and a folder copy:
1. copies the **built** plugin (`Blocks/Plugins/AirSim`, 506 MB, has `Binaries/Linux/*.so`)
2. enables `AirSim` + `ChaosVehiclesPlugin` in their `.uproject`
3. `DefaultEngine.ini` → `GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode` (+ optional map)
4. `DefaultGame.ini` → 9 cook directives

**Design decisions worth keeping:**

- **It refuses the source-only plugin copy.** `Unreal/Plugins/AirSim` (330 MB) has no
  `Binaries/`, so injecting it would silently force a UnrealBuildTool compile — turning A1 into
  A2, which is the entire distinction this path exists to remove. The script hard-fails with
  that explanation rather than producing a project that mysteriously wants to build.
- **It refuses to inject into anything inside this repo**, so `vendor/` cannot be dirtied.
- **Idempotent**, verified by running twice: one `GlobalDefaultGameMode`, one `AirSim` entry,
  8 cook directives, and the user's own settings (`RendererSettings`, their existing plugins)
  **preserved**.
- **Hand-rolled ini editing rather than `configparser`**, which mangles Unreal's `+Key=` repeat
  syntax and `[/Script/Foo.Bar]` section names on write. Losing a user's settings while "adding"
  ours would be a bad trade.
- **Asserts its artifacts** — plugin descriptor, `.so` present, plugins enabled, and exactly
  **one** `GlobalDefaultGameMode` (two would mean a later one wins silently).
- Warns, but continues, when `EngineAssociation` is not 5.8 or when `Source/` exists (A2).

### The engine-version question, answered — 2026-08-03

**A project declaring an older engine opens in UE5.8 headless and unattended. No conversion
step is required to launch it.** Measured: a project with `EngineAssociation: "5.2"`, injected
and launched with `-game -RenderOffScreen -unattended`, loaded its map and initialised AirSim:

```
Waiting for mavlink vehicle...
WeatherActor_C /Game/Maps/TheirMap.TheirMap:PersistentLevel.WeatherActor_C_0
```

No conversion dialog, no refusal, no prompt. This was flagged as the likely dealbreaker for
bring-your-own-world — the concern being that UE's "this project was made with an older
version" dialog would block automation. It does not, under `-unattended`.

**What this does NOT prove, stated precisely:** the test project's assets were authored *by
5.8*; only the version *declaration* was changed. So the `EngineAssociation` mismatch is proven
harmless, but genuinely 5.2-era `.uasset` files loading in 5.8 is **not** proven. Those are
different claims and the second still needs a real old project. UE reads older package versions
by design (backward compatibility is the supported direction; *forward* is what breaks), so it
is likely — but "likely" is not "measured".

**Practical consequence:** a user downloads whatever version Fab offers and copies it across.
An editor pass on Windows/macOS is **optional**, and worth doing only to absorb first-run shader
compilation (the City Park seller quotes ~2.5 h) rather than because conversion is required.

**The one case that still needs Windows:** a project shipping its own `Source/` C++ is A2, and
that C++ must compile against UE5.8. `inject_airsim.py` detects this and warns rather than
pretending it is A1.

### Tested against a REAL Fab project — 2026-08-03. Both open threads closed.

**City Park Environment Collection** (SilverTm, free on Fab, 3.5 GB zip / 4.1 GB extracted),
downloaded on a non-Linux machine and copied across. It is **A1** — 2,364 zip entries and
**zero C++ source files** — so the no-compile path applies.

**1. The engine-version question is answered, and far more strongly than the synthetic test
managed. City Park declares `EngineAssociation: "4.24"` — a UE4 project from 2019.** It loaded
in UE5.8 headless with no conversion, no dialog, and no loader errors:

```
server version : 4        vehicles: ['PX4']
scene objects  : 856      park geometry: Landscape_0, TennisBenchHISMA, Bench03HISMA, ...
```

856 objects against 32 in the synthetic test, and camera frames show real trees, foliage and a
stone stairway. **A UE4→UE5 major-version jump loaded fine, so no Windows editor pass is needed
for conversion.** (The earlier synthetic test only changed a version *declaration*; this loaded
genuinely 7-year-old assets.)

**2. A gap in the mechanism, and it will affect every user world: THE DRONE SPAWNED
UNDERGROUND.** Reported `z = +8.25` in NED — positive is *down*, so 8.25 m below origin. The
first camera frame was the underside of the terrain with light bleeding through.

Cause: **the Showcase level contains no `PlayerStart` and no `TargetPoint`** — verified via
`simListSceneObjects`. AirSim therefore spawns at world origin, and City Park's ground sits
*above* origin. Lifting the vehicle with `simSetVehiclePose` confirmed it — the park rendered
correctly for the moment it was above ground, then gravity pulled it back under.

**This is not a City Park quirk; it is intrinsic to bring-your-own-world.** An arbitrary user
world has no obligation to put its ground at the origin or to ship a `PlayerStart`. Spawn
placement therefore belongs in the mechanism, not left to the world.

**Next on this thread:** give `inject_airsim.py` a spawn-position option that writes the
per-vehicle `X`/`Y`/`Z` into `settings.json` (AirSim supports it), and work out how to *find* a
sane spawn automatically — the landscape bounds are available over RPC. Requiring users to edit
their level to add a `PlayerStart` would defeat the point of the path.

### 3. "No conversion needed" needs qualifying — it LOADS correctly, it does not RENDER correctly

Frames captured from altitude show the park is really there — plaza, steps, mature trees, a
road, a pond — but **everything is hazy and blown out** (mean pixel 156–191 where ~110–140 is
normal exposure), with a cyan cast.

**A wrong hypothesis, recorded because the measurement is the useful part.** Upstream's
`unreal_custenv.md` step 7 warns of a "camera scene rendering bug" on UE5.3+, fixed by copying
`DefaultScalability.ini` (`r.DetailMode=2` at every quality level). `Blocks` ships one; City
Park did not; **`inject_airsim.py` was not copying it.** That was a genuine missing step and is
now fixed — but it did **not** fix the washout: re-measured means came back
156.4 / 182.9 / 176.1 / 191.1 against 156.5 / 182.8 / 175.9 / 191.0. Identical to three
significant figures. The step is still correct to apply; it just is not this bug.

**The likely actual cause, from the level's own actor list:**

```
Fog        : AtmosphericFog_1, ExponentialHeightFog_1
Atmosphere : 0   <- no SkyAtmosphere
Light      : 18  PostProcess: 4
```

**`AtmosphericFog` is the UE4 fog actor, deprecated in UE5 and superseded by `SkyAtmosphere`.**
This map carries the deprecated one with no replacement, and its four `PostProcessVolume`s hold
UE4-era settings. That is a very plausible source of a hazy, over-bright sky.

**So the earlier conclusion is right but incomplete, and the distinction matters:**

| claim | verdict |
|---|---|
| A UE4.24 project **loads and runs** in UE5.8 headless, no conversion | **true, proven** — 856 objects, AirSim up, world navigable |
| It **renders correctly** without conversion | **false for this project** — deprecated fog, UE4 post-process |

For a project whose whole point is *photorealism*, "loads" is not the bar. **An editor pass on
Windows/macOS is worth doing after all** — not to make it load, but to let UE replace deprecated
actors and rebuild lighting. That is a much smaller claim than "conversion is required to run",
and it is the one supported by evidence.

**Also worth saying plainly: City Park is a park, not a cityscape.** Paths, trees, playgrounds,
benches, a pond. There are no street canyons or building interiors. Fine for low-altitude
obstacle avoidance and visual SLAM over natural clutter; wrong if the goal is urban navigation.

### Why it does not look photorealistic — two real config gaps found, cause still open

Chasing the washed-out render turned up **two genuine gaps in `inject_airsim.py`**, both worth
having regardless of whether they explain the look:

1. **`DefaultScalability.ini` was not being copied** — upstream's step 7 fix for the UE5.3+
   "scene camera bug" (`r.DetailMode=2` at every quality level). Blocks ships one; a user
   project will not.
2. **`+TargetedRHIs=SF_VULKAN_SM6` was not being set, and this one is significant.** UE5's
   photorealism *is* Lumen and Nanite, and **both require Shader Model 6**. On Linux/Vulkan the
   engine falls back to SM5 unless the project asks for SM6. Measured: Blocks sets it and runs
   `rhifeaturelevel="SM6"`; City Park had no such line and came up `VULKAN_SM5` with *"Vulkan
   RayTracing disabled because SM6 shader platform is required."* Blocks also **removes** SM5
   (`-TargetedRHIs=SF_VULKAN_SM5`) — adding SM6 alone is not enough. Both lines now injected.

   **Consequence for earlier results:** Blocks has been SM6 all along, so `SIM-04`'s sensor-rate
   numbers were measured on the full renderer and remain valid.

**Neither fixed the appearance, and the measurements say so.** Frame means across four vantage
points, before → after scalability → after SM6:

```
156.5 / 182.8 / 175.9 / 191.0     (original)
156.4 / 182.9 / 176.1 / 191.1     (+ DefaultScalability.ini)
156.5 / 182.7 / 176.2 / 191.3     (+ SM6)
```

Identical to three significant figures. Both fixes are correct to keep; neither is this bug.

**Ruled out by measurement, not assumption:** the scalability fix; SM5-vs-SM6; a missing
`ExtendDefaultLuminanceRange` (neither project sets it); and missing baked lighting —
`Showcase_BuiltData.uasset` (8.4 MB) *is* shipped with the map.

**Still open.** Geometry and textures render correctly — trees, plaza steps, road and pond are
all clearly visible — so this is not a loading or asset problem. It is a **lighting/tonemapping**
problem. The remaining suspects, none confirmed: the map's **four `PostProcessVolume`s carrying
UE4-era settings**, the deprecated **`AtmosphericFog`** actor (UE5 replaced it with
`SkyAtmosphere`, and the level has no `SkyAtmosphere`), or baked lighting that is present but
version-stale for UE5.

### PROVED BY A SIDE-BY-SIDE: the world renders beautifully, AirSim's capture does not

The decisive test was to bypass AirSim entirely and take **Unreal's own** screenshot of the same
scene, from the same viewpoint, in the same process — triggered over RPC with
`simRunConsoleCommand("HighResShot 1920x1080")`, which goes through UE's normal render path
including tonemapping.

```
UE native capture : mean 172.9   std 41.4   min 28   max 255
AirSim capture    : mean ~176-191, washed out, cyan cast, flat
```

**The native screenshot is photorealistic**: green grass with cast shadows, a turquoise lake
with visible depth, a terracotta paved plaza and steps, trees with correct shadowing, the drone
hovering over the water. Saved as `out/citypark_UE_native.png`.

Same scene, same frame, same GPU, same process. **The only difference is which capture path
produced the pixels.** The environment was never the problem, and neither was the UE4 origin of
the assets — a conclusion that took three wrong hypotheses to reach and one direct comparison to
prove.

### ROOT CAUSE — it is the AirSim capture source, not the world

`Unreal/Plugins/AirSim/Source/PIPCamera.cpp:178`:

```cpp
if (image_type == Scene || image_type == Lighting)
    captures_[image_type]->CaptureSource = SCS_FinalToneCurveHDR;   // <-- HDR
else
    captures_[image_type]->CaptureSource = SCS_FinalColorLDR;
```

**The Scene camera captures `FinalToneCurveHDR`** — values after the tone curve but *before*
the final LDR/sRGB encode — and AirSim packs that into 8-bit RGB. That is a gamma-encoding
mismatch, not an exposure fault.

**It explains every observation, including the ones that refuted my earlier hypotheses:**

- **Why nothing in the world mattered.** Six interventions — `DefaultScalability.ini`, SM5→SM6,
  the UE4→UE5 package conversion, and replacing `AtmosphericFog` with `SkyAtmosphere` — all
  landed *upstream* of an encoding fault that happens afterward. Frame means stayed within
  ±0.7 across all six.
- **Why Blocks looks fine.** Grey boxes are low-dynamic-range, so the mis-encode barely shows.
  A bright outdoor HDR scene makes it glaring.
- **The histogram:** `min=13 max=251`, **0% saturated**. Nothing is clipped — the range is
  simply distributed wrongly, which is a gamma signature, not an overexposure one.

### The plan to fix it, and an honest correction to the confidence above

**Written as `patches/cosys-airsim/0004-scene-capture-ldr.patch` but NOT yet validated.**

| # | step | status |
|---|---|---|
| 1 | Characterise the fault quantitatively | **abandoned — kept getting confounded** |
| 2 | Write the one-line patch (`SCS_FinalToneCurveHDR` → `SCS_FinalColorLDR`) | done |
| 3 | Rebuild the plugin inside `Blocks`, re-inject, capture, compare visually | **plugin BUILT and running; comparison BLOCKED** |
| 4 | Decide the HDR-vs-LDR policy and record it | after 3 |

**Step 1 was abandoned deliberately, and that is worth recording.** Four attempts to build a
controlled A/B each acquired a new confound: comparing different viewpoints (onboard camera vs
chase camera); the vehicle falling between the two captures because `simSetVehiclePose` does
not hold it; `ViewMode: Fpv` not matching `front_center`; and the native screenshot being
letterboxed, which skews every percentile. Even with `simPause` freezing the frame to 0.000 m
drift, correlation between the two paths was 0.35 — i.e. still not the same view.

Two post-hoc corrections also failed: applying an sRGB **encode** made contrast worse
(std 39.9 → 11.4), and a **decode** overshot (std → 57.0 against a 36.8 reference). Neither is
evidence about the true cause, because the images being compared were not the same frame.

**So the earlier claim that this is "a gamma mis-encode with a one-line fix" is not established.**
What *is* established: AirSim's Scene capture of this world looks visibly worse than Unreal's own
render of the same world, and six world-side interventions changed the AirSim output not at all —
which places the fault downstream of the world. The capture-source asymmetry at `:178` is the
obvious candidate, and building it is a cheaper way to find out than more measurement gymnastics.

**It is also a decision, not purely a fix.** HDR capture is arguably right for perception work
that wants dynamic range. What is not defensible is packing an HDR buffer into 8 bits, which
both discards range and mis-encodes it — if HDR is wanted, the right form is float output
(`pixels_as_float`), not `uint8`.

**The one-line change** — use `SCS_FinalColorLDR` for `Scene` as every other image type already
does.

### Step 3 result: the patch builds and runs; evaluating it is blocked on spawn placement

`Blocks` was copied to a writable location (**`vendor/` verified pristine, 0 modifications**),
patched, and rebuilt with UnrealBuildTool:

```
[79/81] Link libUnrealEditor-AirSim.so
Result: Succeeded          70.28 s
```

The rebuilt plugin was injected into City Park via `inject_airsim.py --plugin ... --force`
(which correctly moved the previous one aside to `AirSim.bak.<ts>`), and the sim came up with
AirSim serving. **So the patch is real and deployable.**

**What could not be done: a clean before/after image.** Repeated attempts produced frames of
the underside of the terrain, because of the spawn problem already recorded above — City Park's
ground sits above world origin, its height varies across the map, and `simSetVehiclePose` does
not hold the vehicle against gravity. Freezing with `simPause` fixes the falling but not the
placement: z = −9 m was still below the surface at that location.

### The LDR patch is DEPLOYED and does NOT fix the washout — hypothesis refuted

The rebuilt plugin is confirmed in use: the injected `.so` md5 matches the LDR rebuild
(`1599713e…`) and differs from the unpatched vendor build (`2122e037…`).

With it running, a clean capture of the treeline — no `simPause`, vehicle held above terrain by
re-asserting its pose — still reads **mean 188.8**, indistinguishable from the pre-patch
captures. **`SCS_FinalToneCurveHDR` → `SCS_FinalColorLDR` is not the cause.**

That is a clean negative result, and it retires the hypothesis this whole thread was built on.
The capture-source asymmetry at `PIPCamera.cpp:178` is real but is not what makes AirSim's
imagery look worse than Unreal's own render.

**What survives:** the AirSim capture path still differs visibly from UE's native render of the
same world, which the side-by-side established. The mechanism is now **unknown**, and the
candidates that remain are the ones a capture source does not cover — the `SceneCaptureComponent2D`
having its own `PostProcessSettings` and `ShowFlags` independent of the world's post-process
volumes, its own exposure state, and a render target whose format/sRGB flag may not match what
AirSim assumes when packing bytes.

**Do not chase these without fixing spawn placement first.** Three separate investigations here
were confounded by the camera being buried in terrain, and a fourth by `simPause` returning
stale frames.

### The "pavement border" in the captures — explained, and a methodology bug found

Several captures showed a concrete-block border framing a blurred centre, as if the image were
matted. Two separate causes, and neither is a rendering defect:

**1. The camera was inside geometry.** The blocks are the material of whatever surface the
camera is embedded in, seen at point-blank range with depth-of-field blur. Confirmed by
elimination: at 250 m in open air the border is **completely absent**; every frame that showed
it was taken at an altitude below City Park's terrain surface. It is the spawn problem again,
wearing a different disguise.

**2. `simPause` makes `simGetImages` return a STALE FRAME.** Proven accidentally: three captures
at 300 m, 120 m and 9 m altitude returned border/centre means of 137.4/206.7, 102.6/206.7 and
102.6/206.7 — **byte-identical for the last two at completely different positions**. Pausing
stops the scene capture re-rendering, so the RPC hands back the previous frame.

**This invalidates part of the earlier investigation.** The `simPause`-based A/B that produced a
0.35 correlation was comparing a fresh native screenshot against a stale AirSim frame, so that
number means nothing. `simPause` was introduced to stop the vehicle falling between captures;
it solves that and silently breaks the capture instead.

**Rule for any future capture work here: never `simPause` before `simGetImages`.** Hold the
vehicle another way — or better, fix the spawn so it does not need holding.

**So the honest dependency is the other way round from how this was sequenced.** The capture
fix cannot be evaluated until the drone can be *reliably placed somewhere with a view* — which
is the spawn-position work already filed above as the next A1 task. Measurements taken before
that are comparing whatever the camera happened to be buried in.

**Numbers gathered, and why they are not yet conclusive:** the LDR capture read mean 76.5 /
std 43.6 / max 229 against the HDR path's 176.0 / 39.9 / 255 — consistent with less clipping and
a darker, more contrasty image, which is the expected direction. But the two frames show
different content, so this is a hint, not a result. It is a vendored C++ change, so it needs a recorded patch plus a plugin rebuild, and it is
**a decision rather than an obvious win**: HDR capture is arguably the *right* choice for some
perception work (tone-mapped LDR discards dynamic range that HDR-aware pipelines may want).
What is not defensible is the current state, where the HDR buffer is silently packed into 8 bits.

**Everything below remains true and worth keeping** — the conversion, SM6 and scalability fixes
are all correct — they simply were not this bug.

**Practical position:** all three of those are exactly what an editor conversion pass fixes —
UE offers to replace deprecated actors and rebuild lighting on open. So the earlier
qualification stands and is now better evidenced: **a UE4 project loads and runs headless without conversion.** Converting it is
still worth doing for correctness — and **it can be done entirely on Linux**, headless, with the
engine already in the `drone-sim/unreal:ue5.8` image:
`UnrealEditor-Cmd -run=ResavePackages -IGNORECHANGELIST`
upgraded all 11 packages from UE version 518 (UE4.24) to 1018 (UE5.8) in ~2 minutes, and a
Python commandlet swapped the deprecated fog actor. **No Windows machine is required for any of
it.** The `-IGNORECHANGELIST` flag is essential: `dev-slim` reports `BuiltFromCL: 0`, so the
commandlet's default filter considers every package newer than the editor and skips them all.

**Housekeeping:** the 3.5 GB zip landed in `assets/`, which was **not gitignored** — a stray
`git add -A` would have tried to commit it. `/assets/` is ignored now, and the extracted world
lives on the 7 TB drive under the mirrored project path per the repo's storage rule.

**Why not the alternative** — having the user drop a *level* into our `Blocks` project — even
though it looks simpler: a `.umap` carries path-encoded references to its materials, meshes and
blueprints, so moving one between projects means UE's editor **Migrate** dependency walk. That
is a GUI operation on the user's machine, and getting it wrong yields a map that loads with
everything silently missing. It does not remove the hard part; it relocates it onto the user
with worse tools. Note that this option is a *subset* of A — building A gets it nearly free.

### Actors work in the user's project. Three paths, one caveat

| path | needs project C++? | provides |
|---|---|---|
| **Plugin RPC API** | **no** — works in any project | `simSpawnObject`, `simDestroyObject`, `simSetObjectPose`, `simSetObjectScale`, `simGetObjectPose`, `simListSceneObjects` |
| **`DynamicObjects` Blueprints** | mostly no | `GroupedAI` (human_ai, controller, spawner, target points, animations), spline animations, conveyor belts |
| **`DynamicObjects` C++** (4 files) | **yes** | `-startSeed` / `-spawnAI` / `-isStatic` / `-startPoint`, random prop spawning |

**Caveat, measured rather than assumed:** `strings` across the `DynamicObjects` uassets finds
`RandomPropSpawner` **0** times but `LaunchParameterHelper` **2** times (control:
`GroupedAIController` 14, `Character` 114 — the method works). So at least one Blueprint *does*
depend on the C++. An A1 project either copies in those four small files — which means adding a
`Source/` module and becoming A2 — or accepts one broken asset reference. **The seeded
determinism sits on the C++ side of that line**, which matters because the gate wants it.

### Acceptance

1. A user-supplied `.uproject` (A1) is loaded by `sim_up.sh` **without editing the repo** —
   pointed at by path/parameter — and the drone flies in it.
2. `scripts/verify_sensors.py` passes against that world, with **re-measured** rates.
3. Actors are present and moving, and the cameras see them.
4. A bundled **example world** exists so the simulator is useful out of the box (see below).
5. The steps a user must perform on a non-Linux machine are **documented**, not folklore.

### The bundled example world

The CC0 route from the first survey stays — **demoted from "the answer" to "the default"**, so
the thing works with no downloads or accounts. Poly Haven (**521 CC0 models**) + ambientCG
(**2,876 CC0 assets**), both plain HTTPS with no auth, and headless glTF import was verified in
the pinned image: `Success - 0 error(s)`, 5 uassets on disk from a 4.79 MB source. It yields a
natural/cluttered-outdoor scene, not a city — acceptable for a default.

### Still unknown, and each wants an experiment rather than a document

- ~~**Can a UE 5.3-era project be converted to 5.8 headlessly?**~~ ✅ **Answered above** —
  `UnrealEditor-Cmd -run=ResavePackages -IGNORECHANGELIST` upgraded all 11 of City Park's
  packages from UE version 518 (UE4.24) to 1018 (UE5.8) in ~2 minutes, on Linux. No editor pass
  on the user's machine is required.
- ~~**Does the prebuilt plugin drop cleanly into a foreign project**, or does UBT insist on
  rebuilding?~~ ✅ **Answered above** — it drops in, for an A1 project. `inject_airsim.py`
  refuses the source-only copy precisely so that a silent UBT rebuild cannot happen.
- **Rendering cost is entirely unmeasured.** Re-measure per world; a heavy user world may not
  hold it.
  **Partly answered 2026-08-03** — the image-quality settings were priced on the real ROS 2
  graph (two cameras + GPU-LiDAR, Blocks, `scripts/measure_sensor_rates.sh`):

  | config | RGB | depth | LiDAR | IMU |
  |---|---|---|---|---|
  | stock settings | 18.7 Hz | 18.1 Hz | 10.0 Hz | 333 Hz |
  | + Lumen GI/reflections | 17.3 Hz | 16.8 Hz | 9.9 Hz | 333 Hz |
  | + `ForceUpdate` | 17.1 Hz | 16.6 Hz | 9.0 Hz | 333 Hz |

  Lumen costs ~7% of RGB; `ForceUpdate` costs ~1% more on RGB but ~9% on LiDAR. Total ~8.6%
  RGB and ~10% LiDAR for the image quality — an acceptable trade, and **the frame rate is
  capped by the launch file, not by these settings**: `perception.launch.py` pins
  `update_airsim_img_response_every_n_sec = 0.05` (20 Hz) and `update_gpulidar_every_n_sec
  = 0.1` (10 Hz), so 18.7 Hz is 94% of its ceiling and the LiDAR sits exactly on target.

- **CORRECTION — the "31 Hz RGB / 29.6 Hz depth / 17.4 Hz LiDAR" figure is retired.** It does
  not reproduce: stock settings on the same grey-box world now measure 18.7 / 18.1 / 10.0 Hz.
  It predates `perception.launch.py`, i.e. it was the free-running rate before the five
  timer periods were pinned — and that same launch file records the measured Scene+Depth RPC
  ceiling as **~21.7 Hz**, so 31 Hz was never a sustainable Scene+Depth rate. The LiDAR figure
  is the clearest tell: 17.4 Hz from a sensor configured `RotationsPerSecond: 10` was reporting
  poll rate, not data rate. Nothing regressed; the old number was measuring the wrong thing.
- **The ~57-minute segfault** is uncharacterised. **Corrected 2026-08-03:** an earlier version
  of this line claimed it "gets more likely with more actors". That claim has no measurement
  behind it and is withdrawn — it was observed **once**, before any actor work existed.

**Blocks:** `SIM-07` — a flight gate is worth more against a real world than against grey boxes.

### `SIM-12` — the capture is noisier than Unreal's own render (deferred)

**Status:** `todo` · **Deferred 2026-08-03**, deliberately: the tone problem it was entangled
with is solved and verified, and this residue does not block building the simulator.

AirSim's `simGetImages` carries visibly more high-frequency speckle and colour fringing on
foliage than Unreal's `HighResShot` of the identical view. `"ForceUpdate": true` (now shipped in
`sim/ue5/settings.json`) removes the Lumen-attributable part — measured −13.9% at 1080p, and a
Lumen-off control confirms it is specifically denoising Lumen's stochastic GI sampling.

**What is left is not trustworthy as a number.** A residual of ~17.6 vs native ~7.0 survives
every stock lever, and — the important detail — **is not meaningfully reduced by 2× supersampling**
(−3.5% for 2.3× the frame-rate cost). Genuine geometric edge aliasing would have collapsed under
a box filter. That points at a large part of the gap being **native's TSR softening the
reference** rather than AirSim adding noise: the metric used, `|image − median3|`, cannot
distinguish speckle from sharpness, and the native frame is measurably blurrier.

**So the first task here is a better metric, not a better fix**: match blur before comparing, or
score chroma against a luma-preserving baseline. Only then is "how much noisier" a real question.

Rejected already, with numbers (`out/noise-exp/`, `scripts/noise_experiment.py`):
- `r.AntiAliasingMethod 1` (FXAA) — AirSim's own noise went *up* 2%. Its better-looking *ratio*
  was an artifact of degrading the native reference too (hf 7.04 → 10.36). **Ratios are only
  meaningful when the denominator holds still.**
- 2× supersample + downsample — 2.3× the frame-rate cost for 3.5%.

Untried, and cheap: TSR/TAA cvars (`r.TSR.History.*`, `r.TemporalAACurrentFrameWeight`) over
`simRunConsoleCommand`; a longer settle before capture so accumulation converges further; and
whether the residual even matters to the consumers — feature trackers and VLMs may be entirely
indifferent to it, which would close this as won't-fix rather than as a fix.

**Blocks:** nothing.

### Filed late — the capture measurement harness (`SIM-11`/`SIM-12` tooling)

**This should have been a TODO before it was built, and was not.** Recording it now rather than
leaving it undocumented. Eight artifacts, all reusable, all in the repo:

| artifact | what it is for |
|---|---|
| `docker/airsim-client.Dockerfile` | pinned AirSim RPC client, replacing throwaway `pip install`s |
| `scripts/_capture_client.py` | single capture; encodes *never `simPause`* and *hold pose by re-assertion* |
| `scripts/capture_experiment.py` | settings variants as a factorial, one simulator run per cell |
| `scripts/capture_pose_sweep.py` | many poses in ONE run (pose is free over RPC; settings are not) |
| `scripts/capture_vs_native.py` + `_capture_paired.py` | AirSim vs Unreal `HighResShot`, same actor, same frame |
| `scripts/compare_vs_native.py` | scores the pairs; crops `HighResShot`'s letterbox first |
| `scripts/noise_experiment.py` + `_capture_noise.py` + `noise_compare.py` | prices anti-aliasing levers, measures capture rate |
| `scripts/measure_sensor_rates.sh` | sensor rates across image-quality configs on the real graph |
| `scripts/_check_channel_order.py` | asserts the raw buffer is RGB against AirSim's own PNG encoder |

`docker/airsim-client.Dockerfile` pins `msgpack-rpc-python==0.4.1`, `tornado<5`,
`numpy==1.26.4`, `opencv-python-headless==4.10.0.84` — the first two because msgpack-rpc-python
is unmaintained and tornado ≥ 5 breaks its IOLoop usage.

---

## `SIM-13` — operator-supplied spawn coordinates

**Status:** ✅ **done 2026-08-03**, merged in PR 33 · **Scoped
down deliberately:** automatic derivation is deferred to `SIM-14`. Ship the manual coordinate
first — it unblocks everything `SIM-14` would, at a fraction of the cost, and the operator
already knows where their own world is usable.

**The change.** Let the operator pass a spawn position **when starting the simulator**:

```
scripts/sim_up.sh --spawn X,Y,Z[,YAW]        # or: SPAWN=X,Y,Z ./scripts/sim_up.sh
```

It writes vehicle-level `X`/`Y`/`Z`/`Yaw` (`AirSimSettings.hpp:1061-1062`,
`createVectorSetting`/`createRotationSetting`) into a **run-time copy** of `settings.json`, not
into the committed one — spawn is per-world and per-run, so baking it into a reviewed repo
artifact would be wrong.

**Why this shape.** Two footguns must be handled loudly or the feature is worse than nothing:
- **`Z` is NED — negative is UP.** An operator who types `10` expecting 10 m altitude gets 10 m
  *underground*, which is the exact failure this task exists to fix.
- **Silent no-ops.** A malformed `--spawn` must abort, never fall through to origin.

**Acceptance:**

1. ✅ `--spawn 50,-30,-10,315` places the vehicle at exactly that X/Y —
   `simGetObjectPose("PX4")` returns `(50.0, -30.0, …)`.
2. ✅ **The pose holds without `simSetVehiclePose` re-assertion** — measured **drift 0.000 m
   over 6 s unheld**. This retires the holding-loop workaround, and was the criterion that
   mattered.
3. ✅ Malformed input fails with a message naming the problem and the stack does not start —
   `1,2` / `a,b,c` / `1,2,nan` / positive `Z` unacknowledged all exit 1 with no container
   created.
4. ✅ The committed `sim/ue5/settings.json` is byte-identical after a spawn run (md5 checked).
5. ✅ 25 off-target unit tests (`tests/test_apply_spawn.py`).
6. ⚠️ **Dropped as written.** The original criterion was "`min(depth) > 1.0 m` at spawn". It
   cannot distinguish *buried in terrain* from *legitimately landed on grass* — a landed
   drone's forward camera always sees near ground. Replaced by the judgement in `SIM-14`, which
   is where automatic siting belongs.

**Status: the mechanism is done and verified; picking a good coordinate is the operator's job
(and `SIM-14`'s).**

### Two findings from verifying it

**1. AirSim's NED frame is anchored at the SPAWN point, not at world origin.** So
`simGetVehiclePose` after a spawn reads *displacement since spawn*, not world position — it
returned `(0, 0, 25.9)` while the vehicle was demonstrably at world `(50, -30, 15.9)`. Use
**`simGetObjectPose("PX4")` for world coordinates**; reading the wrong one makes a working
spawn look like it was ignored, which is exactly how the first verification run was misread.

**2. A spawn Z is a RELEASE height, not a resting height.** The vehicle falls to whatever is
below. At City Park `(50, -30)` the terrain surface is world **Z ≈ +15.9**, so releasing from
`-10` drops it 25.9 m — and it lands in dense undergrowth (32% of the depth frame under 0.5 m;
releasing from just above ground instead gave 100% under 0.5 m, i.e. fully embedded). The
coordinate is bad, not the mechanism: that XY is scrub on a slope. This is the concrete
argument for `SIM-14`.

**Not a bug — a fuse-overlayfs constraint worth remembering.** The generated settings file
cannot live in `/tmp`: it is on the container's fuse-overlayfs and a read-only bind mount from
there is refused (`remount-ro …: operation not permitted`). It is written to
`sim/ue5/.settings.run.json` (gitignored) instead, beside the source, which is a host bind
mount and mounts identically.

**Blocks:** the rest of `SIM-11` (dynamic actors, `-startSeed`) — unblocked for any world where
the operator knows a good coordinate.

---

## `SIM-14` — automatic spawn derivation (deferred)

**Status:** `backlog` · **Deferred 2026-08-03** in favour of `SIM-13`'s manual coordinate.

**The change.** Derive a sane spawn from the world's own geometry so a user pointing the
simulator at an unfamiliar world needs no coordinates at all.

**`SIM-13` handed this a working probe, for free.** Verifying the manual spawn established that
**spawn-and-settle IS a ground-height measurement**: release the vehicle high at some `(X, Y)`,
let it fall, and read `simGetObjectPose("PX4")` — the resting Z is the terrain surface at that
XY, to within the vehicle's own height. That worked first time at City Park `(50, -30)` and
returned `Z ≈ +15.9`. It needs **no plugin change and no geometry API** — only settings plus
RPC, which was the open question this task was blocked on.

So the shape of `SIM-14` is now concrete rather than speculative:

1. Probe a coarse grid of `(X, Y)` by spawn-and-settle, recording resting Z per cell.
2. Score each cell for *openness*, not just for ground height — the `SIM-13` run landed exactly
   on its commanded XY and was still useless, because it was scrub on a slope. A depth frame at
   the resting pose gives this directly: `frac(depth < 0.5 m)` was **0.32** in undergrowth and
   **1.00** fully embedded, against a clear view where it should be near zero.
3. Pick the best cell and write it as the spawn.

Cost: one simulator start per probe unless several vehicles can be spawned at once — worth
checking, since `settings.json` supports multiple vehicles and that would turn a serial sweep
into a single run.

**Why.** An arbitrary world has no obligation to put usable ground at the origin or to ship a
`PlayerStart`. City Park has neither, so AirSim falls back to origin and the drone spawns **inside
the terrain**. This is intrinsic to the bring-your-own-world path, not a City Park quirk, and it
is the single most expensive defect this project has hit: **four separate investigations were
confounded by it** — three by the camera being buried, one by the `simPause` workaround adopted
to stop the vehicle falling. Every image measurement taken in a user world is suspect until it is
fixed, and the working altitude currently in use (`z = −10`) was found by sweeping, not derived.

Requiring users to add a `PlayerStart` to their level would defeat the point of the path.

**Acceptance — all four, or it is not done:**

1. `inject_airsim.py --spawn auto` writes a finite `X`/`Y`/`Z` into `settings.json` for a world
   it has never seen, **without** the operator supplying coordinates.
2. On City Park, the vehicle spawns **above** the terrain: a `simGetImages` frame at spawn shows
   no depth-of-field concrete border, and `min(depth) > 1.0 m` (i.e. nothing is point-blank).
3. **The pose is stable without `simSetVehiclePose` re-assertion** — the drone rests on ground
   rather than falling, so captures no longer need a holding loop. This is the acceptance
   criterion that retires the workaround.
4. `scripts/verify_sensors.py` passes against City Park with rates re-measured, and the
   numbers recorded here.

**Verification.** `scripts/capture_pose_sweep.py` against the derived spawn (contrast should be
near its peak, not on the buried-camera shoulder), plus the sensor verifier above. Both already
exist.

**Open question to answer first, cheaply:** what does AirSim actually expose over RPC for world
geometry? `simListSceneObjects` is known to return the landscape; whether bounds/height are
reachable without a plugin change decides between a geometric derivation and a
raycast/probe-based one. **The binary stays stock** — if this cannot be done from settings + RPC,
say so rather than patching the plugin.

**Blocks:** nothing directly — `SIM-13`'s manual coordinate unblocks the work this would have.
Its value is that a user pointing the simulator at an unfamiliar world needs no coordinates at
all.

---

## `SIM-15` — the navigation command interface, confirmed end to end

**Status:** ✅ **done 2026-08-03** — all five capabilities confirmed by measurement, merged in
PR 33 · **SITL only — nothing real is armed or flown.**

**Why this comes before navigation code.** Anything built on this simulator will sit on top of
a command interface that had only ever been exercised in one shape: `TrajectorySetpoint.position`
on a seeded square. Before any client starts issuing commands, each command *kind* must be
shown to move the actual aircraft — because the failure mode is not a crash, it is a client
that emits setpoints PX4 quietly ignores while the flight looks "fine".

**The five capabilities to confirm, each by measurement rather than by topic presence:**

| # | capability | mechanism | state |
|---|---|---|---|
| 1 | local waypoint | `TrajectorySetpoint.position` + `OffboardControlMode.position` | ✅ proven — 4/4 waypoints, max error 0.79 m (`SIM-03`) |
| 2 | **GPS waypoint** | `VehicleCommand` `VEHICLE_CMD_DO_REPOSITION` (192) — lat/lon/alt in params 5/6/7 | ❓ **never exercised** |
| 3 | velocity | `TrajectorySetpoint.velocity` with `position = NaN` + `OffboardControlMode.velocity` | ❓ never exercised |
| 4 | sensors in | camera, depth, GPU-LiDAR, GPS, IMU, mag, odom | ✅ all publish and pass value checks (2026-08-03) |
| 5 | all of it over ROS 2 | `px4_msgs` over uXRCE-DDS + `airsim_node` | ✅ for 1 and 4; 2 and 3 unproven |

**The finding that shapes this: there is NO global setpoint message.** `GotoSetpoint` is local
NED (`position # [m] NED local world frame`) and `VehicleGlobalPosition` is an *estimate output*,
not a command. So a GPS waypoint cannot be streamed the way a local one is — it goes as a
**one-shot `VehicleCommand`**, and PX4 executes it in its own navigation mode rather than in
offboard. **That is an architectural difference, not a detail**: offboard streaming and
`DO_REPOSITION` are different control paths with different failsafes, and a planner cannot mix
them casually. Confirming this is the main point of the task.

**Acceptance — each proven by the vehicle MOVING, not by a publisher existing:**

1. **Local waypoint:** commanded a position, reaches it within 1.0 m and holds.
2. **GPS waypoint:** given a lat/lon ~30 m away, `VehicleGlobalPosition` converges to within
   ~2 m of the commanded point, and the *local* position moves consistently with it.
3. **Velocity:** commanded 2 m/s on one axis, measured velocity matches within 0.5 m/s for
   ≥3 s **and** position integrates in the right direction — velocity alone can be satisfied by
   a stationary vehicle reporting noise, so both are required.
4. **Sensors:** `scripts/verify_sensors.py` passes (already automated).
5. **Every command and reading crosses the ROS 2 graph** — no MAVLink shortcut, no RPC.
   Recorded as an MCAP bag so the evidence is reviewable rather than asserted.

**Verification.** One script, `scripts/verify_nav_interface.py`, run against a `sim_up.sh`
stack; each capability isolated so a failure names which one. Rejections and timeouts are
failures, not warnings.

**Known risk to check while doing it:** mode transitions. `DO_REPOSITION` requires PX4 to be in
a nav mode that accepts it, while offboard streaming requires `OFFBOARD` — so the script must
leave the vehicle in a defined mode between checks or the second check inherits the first's
state and fails for the wrong reason.

**Blocks:** everything anyone builds on this simulator — a planner, a language-driven client,
the eval harness — all of it emits through this interface.


---

## `SIM-16` — an example mission: fly a circuit of the park over ROS 2, recorded

**Status:** ✅ **done 2026-08-03** — waypoint + smooth-orbit modes, recorded to the gate's
artifact layout with video and ground-track plot, merged in PR 33 · **SITL only.**

**The change.** A runnable example that flies the drone a closed circuit of a world using
**only** the ROS 2 interface, and records the run in the **same artifact layout the flight
gate writes**, so a demo run and a gate run are directly comparable.

**Why.** `SIM-15` confirmed the command interface one capability at a time, in isolation. Nothing
yet shows the interface driving a *whole mission* — takeoff, a multi-leg route, yaw control,
return, land — with the perception graph running alongside and the whole thing captured. That is
also the first artifact that makes the simulator demonstrable to someone who is not reading
logs.

**Deliberately an example, not a planner.** It flies a fixed geometric circuit. No obstacle
avoidance, no perception in the loop — those are `SIM-11` actors and the planner work. Its job is
to be the reference for *how you drive this vehicle from ROS 2*, short enough to read in one sitting.

**Artifact layout — matches `run_gate.py`, which writes `out/<name>-seed<N>/`:**

```
out/park-tour-<UTC timestamp>/
  park-tour_0.mcap     all /fmu/* + /airsim_node/* + /tf + /clock
  metadata.yaml        ros2 bag's own
  summary.json         waypoints, per-leg error, verdict, versions
  mission.log          the node's stdout
```

**Acceptance:**

1. Completes a closed circuit and lands, **using only ROS 2** — no RPC, no MAVLink.
2. Every leg reaches its waypoint within tolerance; worst-case error recorded, not just the mean.
3. The MCAP contains imagery **and** `/fmu/*` for the whole run — replayable evidence.
4. `summary.json` carries a machine-readable verdict, matching the gate's shape.
5. **Verified in Blocks first**, then run in City Park. Blocks is the known-good control: if the
   park run fails, that ordering says whether it is the mission or the world.

**Known risk.** City Park's ground at the surveyed spawn is scrub on a slope, and the vehicle
rests embedded there (`SIM-13`). Whether PX4 will arm from that pose is **unverified** — if it does
not, the mission is fine and the spawn needs `SIM-14`. Test in Blocks first precisely so that
distinction stays visible.

**Extended 2026-08-03 — a smooth orbit, and the path visualised.** The first implementation flew
discrete corners and *stopped* at each (arrival required speed < 0.7 m/s), which is correct for a
waypoint test and looks terrible as a demo: accelerate, brake, rotate, repeat. Added a
**`mode:=circle`** that streams a continuously moving setpoint around a parametric circle with
**velocity feed-forward**, so PX4 tracks a smooth arc instead of chasing a stationary target.

Note this uses position **and** velocity together, which is *not* the same as the pure-velocity
case: with both finite and both flags set, velocity is a feed-forward term. The "position must be
NaN" rule applies only when commanding velocity alone.

Also produces, from the bag rather than live:
- `path.png` — the flown ground track against the commanded circle, plus altitude and speed traces
- `rosgraph.png` — the ROS 2 node/topic graph (headless, via graphviz — `rqt_graph` needs a GUI)


---

## `SIM-17` — 1080p60 video via Pixel Streaming (NVENC), off the perception path

**Status:** 🚫 `blocked` · **Planned and blocked the same day, 2026-08-04**

> **BLOCKED — NVENC cannot open an encode session on driver 610.43.03.** UE 5.8's only
> NVIDIA encoder backend is NVENC, so PixelStreaming2 would fall back to *software* VP8,
> which is CPU-bound **and** needs frames in system memory — reintroducing the readback
> this task exists to remove. Evidence, ruled-out causes and options:
> [`nvenc-driver-blocker.md`](nvenc-driver-blocker.md). Resolving it is an **owner
> decision** (host driver rebase).
>
> **Interim:** 960×540 at ~14 Hz — measured, works today, ~3× smoother than 1080p's 4.69 Hz.

**The problem, measured.** Every capture route AirSim offers goes through
`RenderRequest::getScreenshot`, which waits for the next rendered frame and then does a
**blocking GPU→CPU readback**. Measured cost is ~**71 ms fixed** plus ~5 ms/MB:

| capture | data | time | rate |
|---|---|---|---|
| 960×540 | 1.56 MB | 71.1 ms | 14.1 Hz |
| 1920×1080 | 6.22 MB | 96.9 ms | 10.3 Hz |

4× the data costs only 26 ms more, so this is **latency-bound, not bandwidth-bound** — the
ceiling is ~13–14 Hz at *any* resolution. Through the ROS 2 wrapper and rosbag it fell to
**4.69 Hz**. Real-time factor stayed 1.0 throughout, so the engine is not the limit; the round
trip is.

**AirSim's built-in `startRecording()` does not help** — `FRecordingThread::Run()`
(`Recording/RecordingThread.cpp:124`) calls the same `getImages()` path. It removes the RPC and
rosbag hops, not the stall. Checked before testing, which saved the experiment.

**The change.** Use **`PixelStreaming2`** (present in this engine build) to encode the viewport
with **NVENC on the 3080**. Frames never cross PCIe uncompressed, so the readback disappears and
60 fps becomes reachable.

**The trick that makes the chase view streamable:** Pixel Streaming encodes the *viewport*, not
an arbitrary `PIPCamera`. But `SimModeBase.cpp:2120` attaches the viewport to the camera named
**`"fpv"`** — so naming the chase camera `fpv` and setting `ViewMode: "Fpv"` puts the chase view
on the viewport, which is what gets encoded. Same discovery that made the vs-native comparison
possible in `SIM-11`.

**This keeps the two paths separate, which is the real point.** Perception keeps 640×480 raw in
ROS 2 at ~20 Hz, undisturbed and in the bag. Video becomes a GPU-side concern that never touches
DDS. Pushing a presentation artifact down a perception path is what cost the frame rate.

**Acceptance:**

1. The simulator starts headless (`-RenderOffScreen`) with PixelStreaming2 loaded and **NVENC
   initialised** — confirmed in the log, not assumed.
2. A recorded file at **1920×1080, ≥30 fps sustained** (60 preferred), of the chase view.
3. **Perception is unaffected**: `verify_sensors.py` still passes and RGB still measures
   ~17–20 Hz *while streaming*. If video costs perception, the design has failed.
4. Output is **H.264 yuv420p +faststart** — playable on a phone. (`mp4v` from `cv2` is MPEG-4
   Part 2 and renders black on most phones; this cost a delivery already.)
5. Reproducible from a script, not a sequence of manual steps.

**Risks, stated up front:**

- **Headless viewport.** Pixel Streaming is designed for headless cloud rendering, so this
  *should* work with `-RenderOffScreen`, but it is unverified here.
- **Consuming the stream is the hard part.** WebRTC needs a signalling server and a client. A
  browser is not available headless; the candidates are the bundled signalling server plus a
  gstreamer/`webrtcbin` sink, or `PixelStreamingPlayer`.
- **New dependency.** A signalling server is Node.js infrastructure the stack does not currently
  carry, which works against the "reproducible as Docker" goal. It must end up in a Dockerfile,
  not in someone's shell history.

**Go/no-go spike first:** get the plugin to load headless and NVENC to initialise. If that
fails, the remaining honest options are 960×540 at 14 Hz, or interpolating frames and labelling
them as synthesised.

## `SIM-18` — collapse the project onto one simulator

**Status:** ✅ **`done` (2026-08-04)** — **filed retroactively.** The plan-first rule says a
non-trivial change is written down before it is built; this one was requested and executed
in a single session, so the entry is the record rather than the plan. Saying so is the point:
an undocumented exception silently becomes the norm.
Evidence: [`../worklog/2026-08-04-one-simulator.md`](worklog/2026-08-04-one-simulator.md)
([HTML](worklog/html/2026-08-04-one-simulator.html))

**What.** The repo carried three parallel stacks — a Gazebo baseline, a deferred Isaac Sim
path, and this one. It now carries one, and that one has no qualifier in front of it: it is
*the simulator*.

**Why.** Two of the three were dead weight with an ongoing cost. The Isaac path had never
run on this machine's driver. The Gazebo baseline still worked, but its only remaining job
was to be a comparison for a stack that had since outgrown it — while its compose file, its
smoke test, its world-overlay generator and its seeded-wind harness all had to keep building,
keep passing CI, and keep being read by anyone trying to understand which of two flight gates
was the real one. The pivot removes a fork in every doc and every script.

**What landed:**

| | |
|---|---|
| Deleted | the compose stack, the container smoke test, the Gazebo world/wind overlay generator and its tests, the demo recorder, the `vlm/` and `vlm_client` placeholders, the Gazebo and Isaac asset stubs |
| Renamed | `sim_up.sh`, `verify_sensors.py`, `measure_sensor_rates.sh`, `record_flight.py`, `perception.launch.py`; images `drone-sim/px4`, `drone-sim/unreal`, `drone-sim/video`; containers `sim-*`; volume `sim-ddc` |
| Rebuilt | `docker/px4.Dockerfile` with `--no-sim-tools` — **11.6 GB → 11.0 GB measured**, with a build-time assertion that Gazebo is absent. NuttX kept: real Pixhawk 6C firmware is flashed from that tree |
| Rewired | `run_scenario.py` drives `sim_up.sh` instead of compose; `run_gate.py` keeps its VOID/FAIL scoring and becomes this simulator's gate |
| Archived | the Gazebo and Isaac backlogs and the four research reports, under `history/`, banner-stamped as frozen |
| Renumbered | `C-NN` → `SIM-NN`, with the mapping in [`history/id-map.md`](history/id-map.md) so old commit messages stay traceable |

**Two things were deliberately NOT done, and both are decisions rather than omissions:**

- **`docs/worklog/` is frozen.** The worklogs keep their original wording and filenames,
  including the retired terminology. They are dated records of what was actually done; a
  worklog edited to match a later decision is no longer evidence. The one consequence to
  know: `docker/px4.Dockerfile` cites a worklog whose *filename* still carries the old
  scheme, and that link is correct.
- **Wind and mass are no longer seeded.** They came from a Gazebo world overlay that is
  gone, so a seed now moves the spawn pose and nothing else. `run_scenario.py` says so at
  the top rather than reporting a wind speed nothing applies — the previous gate printed one
  for every run while every run flew in still air. Restoring real environmental diversity
  needs Cosys-AirSim's wind API, and belongs to `SIM-07`.

**Two defects the pivot exposed, both real and both fixed:**

1. **`versions.lock` never recorded the Unreal image.** It had been built and flown for days
   without an entry under `images:`. Found by `scripts/check_image_refs.py` on its first
   run — a new tier-1 check, written to replace the `docker compose config` step that went
   with the compose file. It asserts every `drone-sim/...` reference names an image the lock
   declares, which is the same class of defect the old step caught.
2. **The bring-up could not survive a cold shader cache.** Renaming the derived-data volume
   orphaned the warm cache, and the next cold start took **199 s** to initialise the engine
   against a 120 s telemetry budget — so `sim_up.sh` declared a perfectly healthy stack dead,
   80 s before it came up. That is precisely the fresh-machine case the reproducibility goal
   exists for, and it had been invisible because every previous run inherited a warm cache.
   Fixed with a separate `wait_for_sim_link` that waits for the *event* (PX4 reporting
   `Simulator connected on TCP port 4560`) with a 900 s budget and progress output, ahead of
   the origin wait. **Migrating an existing cache is one command:**
   `docker run --rm -v <old>:/from -v sim-ddc:/to alpine cp -a /from/. /to/`.

**Verified by running it**, not by a clean build: cold start to `origin verified` with the
repair loop firing on a genuinely stale origin (9.116 m → 0.000 m), then the example mission
flown end to end over ROS 2 with its MCAP kept.

---

## `SIM-19` — review the Dockerfiles properly

**Status:** `done` — **slices 1 and 2 both done and verified 2026-08-06/07**, the second with
collision detection on (`SIM-22`), which the first round did not have.

**Slice 2 result — every image off `ubuntu:24.04`, none deriving from another:**

| Image | Original | Slice 1 | **Slice 2** |
|---|---|---|---|
| `px4` | 11.0 GB | 4.73 GB | **466 MB** |
| `ros2` | 11.1 GB | 4.78 GB | **4.39 GB** |
| `qgc` | 12.1 GB | 5.75 GB | **1.43 GB** |
| `video` | 11.1 GB | 4.80 GB | **530 MB** |

`px4` is **23x** smaller than it started. It carries no ROS at all, which became possible only
once `PR 35` moved the uXRCE-DDS agent into `sim-ros2`; the audit found the dependency had been
vestigial ever since. `ros2` absorbed ROS, the agent and `px4_msgs` — the inheritance inverted
rather than a shared base being added, because after the agent moved the two share nothing.

`px4-entrypoint.sh` had to stop sourcing ROS unconditionally: it runs under `set -e`, so a
missing `setup.bash` would have killed the container outright.

**Both slices re-verified at 20 m with the collision witness**, since their original acceptance
evidence predates `SIM-22` and every earlier park-tour PASS at 8 m is suspect:

```
slice 1   verdict PASS  worst 1.350 m  0 collisions  exit 0
slice 2   verdict PASS  worst 1.331 m  0 collisions  exit 0
          + three bring-up barriers, nav interface 5/5, wrapper build
```

---

**Superseded status line, kept for the record:** `in progress` — slice 1 done and verified
2026-08-06 (PX4 image 11.0 -> 4.73 GB,
all four images rebuilt, stack flies). Slice 2 not started, blocked on the naming decision.
An illustrated version of this entry is at [`sim19-docker-images.html`](sim19-docker-images.html).

Originally filed by the owner right
after `SIM-18` merged; the measurement pass below was run the same day and **corrected the
first lead this entry originally carried.** Nothing is broken: all six images build and the
stack flies on them. This is about what they contain.

### Measured, inside the images

```
drone-sim/px4:v1.16.0      11.0 GB   FROM ubuntu:24.04
drone-sim/ros2:v1.16.0     11.1 GB   FROM drone-sim/px4       (+ wrapper deps, ros-profile)
drone-sim/qgc:v1.16.0      12.1 GB   FROM drone-sim/px4       (+ Xvfb, Qt xcb deps, AppImage)
drone-sim/video:v1.16.0    11.1 GB   FROM drone-sim/px4       (+ ffmpeg)
drone-sim/unreal:ue5.8     57.5 GB   FROM ghcr.io/epicgames/unreal-engine (credential-gated)
drone-sim/airsim-client:1   0.4 GB   FROM python:3.11-slim    (already independent, already lean)
```

Docker counts a shared layer once **per image** in that listing, so the four derived images do
not cost 46 GB of disk — `docker system df` reports ~80 GB total, dominated by the engine. The
cost that is real is **pull and build time on a fresh machine**, which is the reproducibility
goal's exact scenario.

Where the 11.0 GB actually sits:

| | |
|---|---|
| `/usr` | **7.3 GB** — and `/usr/lib/arm-none-eabi` alone is 2.4 GB |
| `/opt/px4` | 2.9 GB — `.git` **1.5 GB**, `docs` 312 MB, `Tools` 383 MB, `platforms` 239 MB |
| `/opt/ros/jazzy` | **236 MB** |
| `/opt/xrce` | 106 MB |

Largest packages: `libstdc++-arm-none-eabi-newlib` **2014 MB**, `gcc-arm-none-eabi` 493 MB,
`libnewlib-arm-none-eabi` 417 MB, `openjdk-21-jre-headless` 194 MB, `openjdk-21-jdk-headless`
92 MB.

### Correction to this entry's original first lead

**It said `ros-jazzy-desktop` was "almost certainly the wrong metapackage" and probably the
biggest win. That was wrong, and it was written from a guess.** `/opt/ros/jazzy` is 236 MB, and
the wrapper genuinely needs perception-side packages that `ros-base` does not carry —
`cv_bridge`, `image_transport`, `pcl`, `pcl_conversions` are all declared by
`vendor/Cosys-AirSim/ros2/src/airsim_ros_pkgs/package.xml`. Dropping to `ros-base` means adding
them back explicitly, and `pcl` is not small. What genuinely has no consumer is the **rviz-only
system deps** — VTK (105 MB), `python3-vtk9` (47 MB), `libqt5webkit5` (46 MB) — a few hundred
MB, not gigabytes.

### The three real wins, in order, all verified

1. **The NuttX / ARM toolchain: ~2.9 GB in every running container.** It exists so real Pixhawk
   6C firmware can be flashed from the same tree — a capability worth keeping, and the reason
   `--no-nuttx` was deliberately not passed. But no simulator container ever flashes anything.
   `apt-cache rdepends` confirms the three packages are leaves.
2. **The PX4 source tree: 2.5 GB, and SITL does not need it.** Verified by running it: with
   `.git`, `docs`, `Tools`, `src`, `platforms` and `boards` deleted, `./bin/px4 -s
   etc/init.d-posix/rcS` still starts, runs its preflight checks and exits normally.
   `/opt/px4` goes 2.9 GB -> **386 MB**. The build needs the tree; the runtime does not.
3. **`openjdk-21` (jre + jdk headless): ~286 MB**, installed by PX4's setup script, with no
   reverse dependency outside its own family and nothing in this project invoking it.

Together **~5.7 GB of 11.0 GB**, with nothing that runs losing anything.

### Slice 1 — DONE 2026-08-06: strip the PX4 image

Split `docker/px4.Dockerfile` into stages. `firmware` clones the full PX4 tree, installs the
NuttX/ARM toolchain and builds SITL; `runtime` returns to `base` and copies **only**
`/opt/px4/build`. A stage split rather than a delete, because **`apt purge` in a later layer
reclaims nothing** — the bytes stay in the earlier layer.

The flashing capability is kept, not dropped: `docker build --target firmware` still produces an
image with the full tree and toolchain. It is simply not inside every container that flies.

**Measured, all four images rebuilt:**

| Image | Before | After |
|---|---|---|
| `drone-sim/px4` | 11.0 GB | **4.73 GB** |
| `drone-sim/ros2` | 11.1 GB | **4.78 GB** |
| `drone-sim/qgc` | 12.1 GB | **5.75 GB** |
| `drone-sim/video` | 11.1 GB | **4.8 GB** |

`/opt/px4` goes 2.9 GB -> **377 MB**; the ARM toolchain, `arm-none-eabi-gcc`, the source tree and
`.git` are all asserted absent in the runtime stage so the strip cannot silently regress.

**What this buys is PULL time and disk, not BUILD time.** The `firmware` stage still clones
`--recursive` and compiles the whole tree, so a cold `docker build` costs what it always did. Both
this entry and the original framing said "pull and build time"; only half of that is true, and the
build half is unchanged by any stage split -- it would need a different intervention entirely.

**Acceptance met in full** — not just a size: the wrapper builds, the stack passes all three
bring-up barriers, and `verify_nav_interface.py` passes telemetry / takeoff / waypoint / velocity /
gps_waypoint.

**Two corrections this slice produced.**

1. **openjdk is NOT PX4's, and is NOT part of the win.** This entry listed 286 MB of openjdk as
   installed by `Tools/setup/ubuntu.sh`. It is present in the **base stage, before PX4 is cloned** —
   it arrives via ROS (`default-jre-headless` <- `default-jdk-headless`). A stage split cannot
   remove it. That is the second time this entry's stated cause was wrong; the first was
   `ros-jazzy-desktop`, also killed by measurement.
2. **`build_airsim_wrapper.sh` was broken by patch `0005`, and this caught it.** That script applies
   *every* patch in `patches/cosys-airsim/`, but `0005` is an **Unreal plugin** patch and the
   wrapper build root has no `Unreal/` tree — so `patch` prompted on stdin and the build died. It
   now skips Unreal-side patches (`convert_world.sh` applies those) and passes `--batch` so a
   mismatch fails instead of hanging. **This was a live regression on `main`**, shipped in the
   `SIM-21` work and found only by running the wrapper build.

---

### Dependency audit — 2026-08-06, and slice 2 re-scoped

Run before building slice 2, because the entry's remaining leads were guesses. Two of them died.

**`apt-get autoremove` finds ZERO orphans, and it was right.** Nothing in the image is unreferenced.
That was the first command run and the correct answer; the rest of this audit was spent
disbelieving it.

**The weight is one required chain, not bloat:**

```
ros-jazzy-desktop -> ros-jazzy-pcl-conversions -> libpcl-dev -> { VTK, Boost-dev, LLVM x3 }
```

`/usr/lib` is 2.8 GB and its largest items are four LLVM runtimes (~485 MB), `libboost1.83-dev`
(154 MB), `libvtk9.1t64` (105 MB) and openjdk twice (286 MB). All of it arrives through
`libpcl-dev`, which `pcl_conversions` depends on — and the AirSim wrapper genuinely declares
`pcl_conversions`.

**DEAD LEAD (the third): swapping the metapackage does not help.** Measured by simulating both
installs:

| | Packages |
|---|---|
| `ros-base` + `cv_bridge` + `image_transport` + `pcl_conversions` + `tf2_*` | **1103** |
| `ros-jazzy-desktop` | **1301** |

198 packages of difference, and the `ros-base` variant **still pulls** `libpcl-dev`, `libvtk9`,
`libboost1`, `libllvm15`, `libllvm17`, `libllvm20`. The weight is structural. This is the third
lead in this entry killed by measurement, after `ros-jazzy-desktop` itself and openjdk.

**`libllvm17t64` is NOT an orphan**, though it looks exactly like one: marked `auto`, and
`apt-cache rdepends` reports no installed dependents. `apt-get -s remove libllvm17t64` shows the
truth — it cascades **54 packages including `ros-jazzy-desktop` and `libpcl-dev`**. Use a simulated
removal, not `rdepends`, which is incomplete here.

### The one big win the audit DID find — measured, not estimated

**The px4 image does not need ROS at all**, and has not since the uXRCE-DDS agent moved into
`sim-ros2`. Nothing ever `docker exec`s into `sim-px4`; the container is start-and-forget, and
`ldd` on the SITL binary resolves to `libc`, `libstdc++`, `libgcc` and `libm` alone.

Built as a probe — `ubuntu:24.04` + `libstdc++6` + the copied `build/` tree:

```
px4 with ROS (today)     4.73 GB
px4 without ROS          466 MB      <- 10x, and it BOOTS:
                                        "simulator_mavlink: Waiting for simulator ... TCP 4560"
```

### Slice 2, re-scoped

1. **`px4` drops ROS entirely — 4.73 GB -> 466 MB.** Prerequisites: move the XRCE agent build from
   `px4.Dockerfile` into `ros2.Dockerfile` (the agent runs in `sim-ros2`, not `sim-px4`), and make
   `docker/px4-entrypoint.sh` stop sourcing ROS unconditionally — it runs under `set -e`, so a
   missing `/opt/ros/jazzy/setup.bash` would kill the container.
2. **`qgc` and `video` off the PX4 base.** Both use nothing from it. `qgc` must re-add `curl`,
   `ca-certificates` and `/etc/drone-sim-versions`, which it currently inherits.
3. **No shared base image and no new name.** The `base` in this entry's original sketch was
   predicated on `px4` and `ros2` sharing ROS. They do not.

**Still true, and worth repeating:** what any of this buys is **pull time and disk, not build
time**. The firmware stage still compiles the whole tree.

---

### The structural question — what should actually be separate

`qgc` and `video` inherit the PX4 base and use **nothing** from it. QGroundControl needs Xvfb,
Qt xcb libraries and its AppImage; the video image needs `ffmpeg`. Neither touches PX4, NuttX,
ROS or `px4_msgs`. `ros2` and `px4` genuinely do share ROS 2 and the branch-matched `px4_msgs`,
so a shared base is right for those two and only those two.

Proposed shape, to be confirmed by building it:

(names below are PROPOSED, not built — deliberately written without the `drone-sim/` prefix,
because `scripts/check_image_refs.py` correctly fails on a reference to an image the repo does
not build, and it caught exactly that when this entry was first written)

```
ubuntu:24.04
 |- base      ROS 2 Jazzy + px4_msgs + px4_ros_com            (shared, and genuinely so)
 |   |- px4     + PX4 SITL BUILD OUTPUT only + XRCE agent
 |   \- ros2    + wrapper deps + ros-profile.sh
 |- qgc       Xvfb + Qt xcb deps + QGC AppImage               (independent of PX4)
 \- video     ffmpeg                                          (independent of PX4)

 firmware    the full PX4 tree + NuttX toolchain, as a multi-stage `--target` rather than a
             separate file — the flashing capability kept, but out of every container that
             flies.
```

**Estimated** px4 ~5 GB (from 11.0), qgc ~1 GB (from 12.1), video ~0.2 GB (from 11.1). Those
are arithmetic on the measurements above, **not** a built result — a multi-stage rewrite can
surprise you, and the numbers are worth nothing until an image exists.

### One item that is not about size

**`docker/unreal.Dockerfile:76-98` bakes a host assumption into an image.** The Vulkan ICD
symlink (`/usr/lib64/libGLX_nvidia.so.0`) exists because this host is Bazzite/Fedora-family and
its CDI spec injects an ICD naming a Fedora path absent from an Ubuntu container — without it
UE's renderer cannot start at all. Documented, load-bearing, and verified with `vulkaninfo`. But
a Debian-family host would not need it and a different driver injection might need something
else, which is exactly what the reproducible-as-Docker goal exists to remove. Decide whether it
belongs in the image, in the run command, or behind a detection step.

### Acceptance

Unchanged by any of this: the wrapper builds, the stack comes up through all three barriers,
and the example mission flies. **A smaller image that does not fly is a regression, and image
size has never been this project's constraint** — pull time on a fresh machine is the thing
being bought, and it should be stated as such rather than dressed up as disk.

---

## SIM-08 — Cesium georeferenced terrain

**Status:** `todo` · **Rescoped 2026-08-02** — the photoreal-scene and dynamic-actor work
moved to `SIM-11`, which is the near-term need. What stays here is georeferencing.

**What.** Cesium for Unreal georeferenced terrain and OSM/StreetMap as the low-altitude
alternative. **Wind, time-of-day and weather are also available over the AirSim RPC**
(`simSetWind`, `simSetTimeOfDay`, `simEnableWeather`) — recorded here because that is where
the RPC surface was first written down, and because `SIM-07` needs `simSetWind` before a gate
run can honestly claim varied conditions.

**Why it is deliberately last.** Cluttered scenes for obstacle avoidance are the near-term
need and they belong to `SIM-11`; **georeferenced real-world terrain is a different
capability**, wanted by anyone reproducing a published benchmark rather than by the simulator
itself. Building Cesium terrain before the stack flew would have been scene work on an
unproven simulator.

**Traps.**
- **The Omniverse FSD-vs-PhysX exclusion does NOT automatically apply here.** That is a
  Cesium-for-*Omniverse* constraint; Cesium for Unreal is a different plugin. Whether UE5
  gives georeferenced terrain *with* physics is **open — verify, do not inherit the answer**
  (`versions.lock` records this as a scope correction).
- **Google 3D Tiles go black at drone altitude.** Documented at 10–500 ft AGL in both
  plugins. `04`'s decision threshold: if tiles render black below ~150 ft AGL for an AOI,
  switch that scenario to OSM+PCG or photogrammetry meshes.
- **Coordinate reconciliation bites here.** AirSim NED origin vs `CesiumGeoreference` vs UE
  centimetres. Set PX4 `LPE_LAT`/`LPE_LON` and AirSim `OriginGeopoint` to the same lat/lon.
- **Licensing:** Google 3D Tiles need a Cesium ion token and fall under Google Maps Platform
  terms; OSM data is ODbL; the City Sample building generator needs a SideFX licence.

---

## Open questions

The first four were the ones this stack was built to answer, and all four are now closed.
They are kept with their answers because the *reasoning* was the expensive part.

1. ~~**Does Cesium for Unreal support UE5.8?**~~ ✅ **ANSWERED 2026-07-31 — yes.** Cesium for
   Unreal v2.28.0 adds UE5.8, with a downloadable UE5.8 binary. The Epic image tag
   `dev-slim-5.8.0` also exists, and UE5.8 is a released engine. **Consequence beyond the
   answer:** Cesium v2.29.0 drops UE5.5, so the UE5.5 fallback inverted from *safe* to
   *worst* — UE5.8 is the only forward-supported path. (`SIM-01`)
2. ~~**Does the ROS 2 wrapper build on Jazzy / Ubuntu 24.04?**~~ ✅ **ANSWERED 2026-08-01 —
   yes**, in 1m21s with warnings only. Upstream *documents* it and never *tests* it — its CI
   has never once invoked `colcon` — so this had to be run, not read. (`SIM-06`)
3. ~~**Can the simulator use PX4 v1.16.0?**~~ ✅ **ANSWERED 2026-08-01 — yes.** The `/fmu/*`
   parity diff and the 4/4 flight were both taken against v1.16.0. **The two-PX4-tree
   architecture — the development plan's dominant risk — collapses to one tree**, and it is
   the tree the real Pixhawk 6C is flashed from. (`SIM-03`)
4. ~~**Is lockstep actually engaged?**~~ ✅ **ANSWERED 2026-08-01 — no, and it cannot be.**
   `resetState()` clears the flag `initialize()` sets, twice, and nothing sets it again, so the
   guard can never pass. **Every timing number from this simulator is free-running.**
   (`SIM-03`, `SIM-09`)

Still open:

5. **Is 10 GB VRAM enough** for a heavy user-supplied world at useful resolution? The hardware
   assessment says 10 GB is workable for modest scenes but stutters on large Cesium terrain,
   and rendering cost has to be re-measured per world in any case. Note the render card is the
   **3080**, and the 5060 Ti's 16 GB is reserved for inference — swapping them is forbidden.
6. **Does Cesium-in-UE5 give georeferenced terrain *with* physics?** The FSD/PhysX exclusion
   is Omniverse-specific — verify rather than inherit the answer. Distinct from question 1:
   that one is "does it build at all". (`SIM-08`)
7. ~~**What does a flight gate cost in wall-clock,** and does a UE5 stack restart make a 10-seed
   gate impractical?~~ ✅ **ANSWERED 2026-08-07 — no, it is entirely practical.** A 10-seed gate
   with a full stack restart per seed took **1944 s (32 min)**, at **193–195 s per seed** with
   remarkably little spread. The restart dominates but does not disqualify: this is a
   coffee-break gate, not an overnight one. (`SIM-07`)

---

## `SIM-20` — reach the graph across a link with no multicast

**Status:** `done` — **2026-08-05.** `DISCOVERY_SERVER=<ip>:<port>` in `sim_up.sh`, verified
against a second host.

`NET_MODE=host` answered how another machine subscribes to this simulator, but only where the
path forwards UDP multicast. A VPN or routed subnet does not: `mc_forwarding=0` on every
interface here and no mroute daemon, so the SPDP announcements that introduce peers never arrive.

`DISCOVERY_SERVER` makes every DDS participant in `sim-ros2` a discovery client of a server they
can both reach, announcing over plain unicast. `ROS_SUPER_CLIENT=true` is set alongside it and is
**not optional** — see the trap below.

### Verified, 2026-08-05 — second host, with a control

Subscriber on `carbonite-noble`, a separate machine on the NetBird overlay: no shared namespaces,
no shared `/dev/shm`, reachable only over a routed link with no multicast. Every run paired with
the same probe **without** the server:

```
                     with discovery server      control: multicast only
topics visible       total=53  fmu=51  (x3)     total=2  fmu=0  (x3)
/fmu/out delivery    pos=1936  imu=1936         pos=0    imu=0
                     ref_alt 123.282 m -- matches the stack's verified EKF origin
```

`fmu=51` equals the stack's own local view. The control's `total=2` is the probe's own
`/parameter_events` and `/rosout` — a correctly isolated node that found nothing. The server is
unambiguously what carried the graph.

### How this was got wrong three times — worth reading before re-testing

The feature was built, rejected, un-rejected, re-doubted and only then proven. Every wrong turn
came from **testing a different topology than the one being claimed**, and the fix each time was
a control experiment that was never run.

1. **"Tested and rejected — takes /fmu offline."** Both containers shared `--network host` but
   not `/dev/shm`, which is the discovery-without-delivery trap `attach.sh` exists to teach.
   `count_publishers` returned 1 throughout: discovery had worked, delivery had not. A delivery
   failure was read as a discovery failure. Compounding it, `wait_for_fmu` runs `ros2 topic echo`,
   which without `ROS_SUPER_CLIENT` fails on type resolution — so the bring-up reported
   `no finite EKF origin` and blamed the EKF for a graph-introspection problem.
2. **"It works" — but the subscriber ran with `--network host` against a stack in `NET_MODE=host`,
   i.e. the SAME network namespace**, where multicast works over loopback and could have carried
   everything. The numbers were real; they proved nothing about the server.
3. **"Unproven / doesn't work off-host"** — from a docker-bridge container to the host netns,
   which returned 0 with and without the server. That is a **false negative**: bridge NAT breaks
   the locator exchange in a way a routable peer does not. Do not use the docker bridge as a
   stand-in for a remote host.

The only valid test is a genuinely separate host with a routable address — and it must be run
alongside a no-server control, or a pass proves nothing.

### Notes for whoever touches this next

- `ros2 node list` is **0** for `/fmu/*` in every mode, multicast included — the agent creates raw
  DDS participants with no ROS 2 node metadata. Not a symptom.
- The server is a **rendezvous, not a relay**: data still flows peer-to-peer, so the two machines
  need direct routable reachability. It removes the multicast requirement, not the routing one.
- The verified link was NetBird **relayed** (~27 ms RTT, MTU 1280). Good enough to prove discovery
  and delivery; useless for throughput claims, and large samples fragment at that MTU.
- `/fmu/out/*` is BEST_EFFORT + TRANSIENT_LOCAL. A default RELIABLE subscription matches nothing
  and reads as silence on a healthy stack.
- The reusable test rig is staged at `vendor/ros-portable/` (gitignored): a relocatable ROS 2
  Jazzy tree, `px4_msgs`, and `probe.py` / `deliver.py`. `/home/deck` is shared between the hosts,
  so no copying is needed.

---

## `SIM-21` — fly CitySample: World Partition streams nothing without a streaming source

**Status:** `done` — **2026-08-05.** Diagnosed, patched and flown, **but the fix is only 2-in-3
reliable**: a startup race remains (see below). Two follow-ups.

Epic's **CitySample** (`Small_City_LVL`) now flies.

### Two independent problems, both fixed

**1 — It would not build.** CitySample is an **A2** project (ships `Source/`, no Linux binaries),
so it had to compile against UE5.8 first. Two fixes, both in `Source/CitySampleEditor.Target.cs`
(backups `.pre-airsim` alongside):

- UE5.8's newer clang promotes `-Wunreachable-code-break` and `-Wunreachable-code-loop-increment`
  to errors under `-Werror`; 14 files trip it across Epic's RuleProcessor/Traffic plugins **and**
  Cosys-AirSim. Downgraded exactly those two rather than patching upstream source.
- UBT then refused per-target compiler args because the target shares build products with
  `UnrealEditor`. Fixed with `bOverrideBuildEnvironment = true`; the documented alternative,
  `TargetBuildEnvironment.Unique`, forces a **full engine rebuild** for two warnings.

Result: **396/396, exit 0.**

**2 — It had no ground.** World Partition activates cells around a registered **streaming
source**, normally the player pawn. AirSim spawns its vehicle without one, so no cell ever loaded
and the vehicle fell forever. Fixed by `patches/cosys-airsim/0005-worldpartition-streaming-source.patch`,
which adds a `UWorldPartitionStreamingSourceComponent` to `AFlyingPawn`. Full evidence in
[`vendor/cosys-airsim.md`](vendor/cosys-airsim.md).

### Before and after — resting `z`, NED, +z is DOWN

| Spawn | Before | After |
|---|---|---|
| `0,0,0` | +332 m | — |
| `0,0,-150` | +139.5 m | **-8.4e-05 m** |
| `0,0,-150` + `wp.Runtime.EnableStreaming=0` | +1481 m | — |

Releasing higher only bought more fall, so this was never a spawn-height problem. The cvar
workaround does not work and was reverted.

```
EKF origin  ref_alt 123.322 m vs GPS 123.322 m = 0.000 m apart
flight      telemetry / takeoff / waypoint / velocity / gps_waypoint -- ALL PASS
              waypoint  commanded (10.0, -0.0)  reached (9.27, -0.69)  error 1.00 m
              velocity  commanded +2.0 m/s      measured +1.89 m/s over 808 samples
              gps       commanded +30 m north   moved +27.0 m, remaining 3.00 m
```

### Traps this cost time to learn

- **`cell load lines` is a bad proxy.** UE does not log per-cell activation at default verbosity,
  so the count stayed 0 even after the fix worked. Judge it by **resting `z`**, not by log greps.
- **AirSim spawns a Blueprint subclass** (`BP_FlyingPawn_C`), not the C++ class. A rebuild that
  never reached it looks identical from outside. A temporary runtime probe confirmed it did:
  `AIRSIM_WP_PROBE class=BP_FlyingPawn_C comp=yes enabled=1 partition=1`.
- **`GlobalDefaultGameMode` was `BP_CitySampleGameMode`** — the silent-never-loads trap. Set by
  `inject_airsim.py --map /Game/Map/Small_City_LVL`.

### The fix is necessary but NOT sufficient — a startup race remains

Cell streaming takes seconds (`GenerateStreaming` measured 7-22 s) while the vehicle falls
immediately. If it passes the ground plane before the cells beneath it activate, the run is lost
exactly as before. Measured on identical builds, same spawn `0,0,-150`:

| Run | Resting `z` | Result |
|---|---|---|
| 1 | -8.4e-05 m | flew, 5/5 nav checks |
| 2 | **+1697 m** | fell through |
| 3 | -1.0e-03 m | flew, 4/4 waypoints, worst 0.855 m, recorded to video |

**2 of 3.** I first reported this as working off run 1 alone — the same one-sample mistake as
`SIM-20`. A proper fix must hold the vehicle until streaming around it completes rather than
racing it; candidates are `UWorldPartitionSubsystem::IsStreamingCompleted()` gating, or freezing
physics until the first cells activate. Unsolved.

Until then: **retry on failure, and judge a run by resting `z`**, never by whether the level
appeared to load.

### Follow-up — the patch does not reach users yet

`inject_airsim.py` copies the **built** plugin from Blocks, so `0005` only propagates once Blocks'
plugin is rebuilt with it applied. Verified here by patching the injected copy and rebuilding the
project. Wiring the Unreal-plugin patches into a build step is genuinely unsolved: the existing
`build_airsim_wrapper.sh` patch flow covers the **ROS 2 wrapper** only, not the UE plugin.

Also untouched: `CarPawn` has the same gap. Left alone because nothing here drives a car.

Worth knowing before going further: the render GPU is the **3080 at 10 GB**, so `Big_City_LVL`
is likely out of reach even now; `Small_City_LVL` is the verified world.

---

## `SIM-22` — the harness could not detect a collision

**Status:** `done` — **2026-08-06.** Raised by the owner, who suspected the intermittent park-tour
failures were impacts the scoring could not see. They were.

### What was wrong

**Nothing in the harness detected collisions.** Leg scoring measures distance-to-waypoint and
arrival speed; neither notices an impact, because after one the vehicle merely looks *badly
tracked*. The two are indistinguishable in the summary — a 48 m miss over 92 s is equally
consistent with a crash and with poor control.

Measured at the default 8 m altitude in Blocks, with a witness attached for the first time:

```
verdict: PASS   worst 1.906 m   mean 1.489 m   landed=True
collisions: 8-9 sustained contacts vs TemplateCube_Rounded_7 and _49
```

The vehicle flew into two buildings and the run scored **PASS on every leg**. So every prior
park-tour PASS at 8 m is suspect — including the runs used as acceptance evidence for `SIM-19`
slices 1 and 2, which now need re-earning at a safe altitude.

It also gives the FAIL/PASS/PASS flakiness seen during `SIM-19` a likely physical cause. It was
attributed to free-running non-determinism (lockstep is dead, so nothing is repeatable). It was
probably geometry.

### What was built

`scripts/watch_collisions.py` — an **independent** witness polling `simGetCollisionInfo` at 20 Hz.
Separate from the mission node on purpose: a node reporting on its own crash is not a witness. It
writes `collisions.json` continuously, so a run killed mid-flight still leaves evidence.

`run_park_tour.sh` brackets the flight with it, folds `collisions.json` into the run directory,
and **a collision now fails the verdict and forces a non-zero exit** regardless of leg scoring.

Default altitude raised **8 m -> 20 m**. At 20 m the legs are also visibly cleaner: uniform
14-15 s and 1.0-1.35 m, against a ragged 9-92 s and 0.98-1.91 m at 8 m.

### Two traps worth keeping

1. **`has_collided` alone is useless.** It reports *current* contact, and a parked drone is in
   contact with the floor: `has_collided=True, object_name=Ground` before it has even armed.
   Ground is separated by `object_name`, and the name list is world-specific — an unrecognised
   name is reported as a collision rather than ignored, because a visible false positive beats a
   silent false negative.
2. **`time_stamp` is the wrong de-duplication key.** It looks right and is not: it keeps
   advancing while the vehicle *drags* along a surface, so keying on it logged one scrape as
   **56** collisions. Keying on contact continuity — an event ends when a poll sees no collision
   or a different object — gives 8-9, with durations.

### Verified both directions

```
8 m   verdict: FAIL (COLLISION)   8 contacts    exit 1
20 m  verdict: PASS               0 collisions  exit 0   (3 ground contacts, takeoff + landing)
```

### An intermittent leg TIMEOUT, separate from collisions — found 2026-08-07

Raising the altitude removed the collisions and did **not** remove the intermittent failure. With
the witness reporting **zero** collisions, a 20 m run still failed:

```
leg 1: ok    1.25 m  13.7s      leg 1: ok  1.25 m  13.7s
leg 2: ok    1.01 m  14.9s      leg 2: ok  1.01 m  14.9s
leg 3: ok    1.09 m  14.8s      leg 3: ok  1.09 m  14.8s
leg 4: ok    1.00 m  14.7s      leg 4: ok  1.00 m  14.7s
leg 5: MISS 25.16 m  92.0s      leg 5: ok  1.34 m  14.4s
verdict FAIL, landed=False      verdict PASS
```

Two things stand out. **92.0 s is exactly the leg timeout**, so the vehicle did not drift — it
stopped making progress and ran out the clock, then never landed. And **legs 1-4 are near
identical between runs** (times within 0.1 s, errors within 0.01 m), so the divergence is
specific to the final leg rather than accumulated noise.

The same 92.0 s signature appeared in the 8 m runs, where it coincided with collisions. It is now
clear these are **two separate failure modes**, and altitude only fixed one. Attributing the
flakiness wholly to geometry was premature.

Belongs to `SIM-07`: the gate cannot report an honest success rate while a leg can time out for
an unexplained reason. Start from the MCAP of a failing run — the bag brackets the flight, so the
setpoint stream and `/fmu/out` during that 92 s are already recorded.

### Follow-ups

- **`run_gate.py` does not use this yet.** The gate scores VOID/PASS/FAIL over N seeds and is
  still blind to impacts; a colliding seed will inflate its success rate.
- `ros2_ws/src/evaluation/README.md` lists "collision count / CR" as a planned metric — the data
  now exists to populate it.
- **Re-verify `SIM-19` slices 1 and 2** at 20 m with the witness on, since their acceptance
  evidence predates it.

---

## `SIM-23` — the renderer dies mid-flight on an empty GPU-LiDAR readback

**Status:** ✅ **done** — **2026-08-08.** Raised by the owner ("lets chase the render issue now")
after the renderer was seen crashing during runs.

### What is wrong

`vendor/Cosys-AirSim/Unreal/Environments/Blocks/Saved/Crashes/` holds 18 reports. **13 of them,
from 2026-08-02 to 2026-08-07 and still occurring, are one bug** — always on AirSim's physics
thread, never the game thread:

```
Assertion failed: (Index >= 0) & (Index < ArrayNum)  [Array.h] [Line: 1339]
Array index out of bounds: 42257 into an array of size 0

ALidarCamera::ProcessCapturedBuffers
ALidarCamera::UpdateAsync             LidarCamera.cpp:371
UnrealGPULidarSensor::getPointCloud   UnrealGPULidarSensor.cpp:49
GPULidarSimple::updateOutput -> World::worldUpdatorAsync
```

**`into an array of size 0` is the diagnosis.** All eleven distinct recorded indices (2948 …
147705) are legal offsets into `resolution_²`, so the `h_pixel`/`v_pixel` guard is working. The
buffer it guards is empty.

`ServiceAsyncCapture` discards the `bool` that `ReadPixels` returns
(`UnrealClient.h:113`) and sets `async_capture_ready_ = true` unconditionally. A readback that
failed and left the array empty is therefore advertised to the physics thread as a good frame.
Nothing in `LidarCamera.cpp` ever calls `Num()` on these three buffers.

Not a data race over the buffer: a mid-loop reallocation would report varied nonzero sizes;
all eleven say `0`.

The path is not optional. `GPULidarSimpleParams.hpp:62` sets
`async_capture_mode = (simmode_name == kSimModeTypeMultirotor)` **before** the JSON is parsed,
so it is hardcoded on for every multirotor and there is no settings key to disable it. Our
`sim/ue5/settings.json` runs a multirotor with `SensorType 8` enabled.

Likely the same root cause as the `rpc::timeout … getGPULidarData` failures seen during
`SIM-20`.

### The fix

`patches/cosys-airsim/0006-gpulidar-empty-readback.patch` — gate `async_capture_ready_` on
every `ReadPixels` result *and* the resulting `Num()`, and refuse to enter
`ProcessCapturedBuffers` unless each buffer the loop will index holds `resolution_²` pixels. A
frame that could not be read becomes a dropped scan and a `Warning` naming the size, instead of
a dead process.

### How it was verified

Waiting on a 1–5-a-day fault is not evidence, so the fault was injected — one
`async_buffer_2D_depth_.Empty()` — and built both ways:

| build | response | renderer |
|---|---|---|
| upstream + fault | `Array index out of bounds: 260098 into an array of size 0` | **dead in 20 ms** |
| 0006 + fault | `GPU-LiDAR readback incomplete (depth 0 px, need 262144), dropping frame` | **alive** |

The unpatched arm reproduced the production signature exactly. Then a real flight on the shipping
artifact: park tour **PASS**, worst 1.335 m, no collisions, 0 assertions, 18 crash dirs (baseline
18), `getGPULidarData` returning full 8192-point clouds.

Then a **90-minute soak** (`soak_full_stack.sh`, extended with a GPU-LiDAR arm and a continuous
flight loop — the old harness only drove the image path, which is why its 2026-08-03 run could
not reproduce this):

```
survived 5405 s · 45/45 flights, every leg ok, all landed
worst 1.996 m (0 over the 2.0 m tolerance) · 4,991,456 LiDAR calls, 0 errors, 0 short
readback drops 0 · assertions 0 · crash dirs 18 (baseline 18)
```

**This is absence evidence and is labelled as such.** 90 minutes past the historical ~57-minute
failure point, on a stack that produced 1–5 crashes a day, is strong evidence the crash is gone —
but no fault occurred naturally, so the fix catching one is proven *only* by injection.

### It also closed an older investigation

`docs/vendor/cosys-airsim.md` carried a "known upstream instability, **uncharacterised**" — a
segfault recorded as `n = 1` after 57 minutes, with index `18823`. That index is one of these
thirteen, and its report's stack is `ProcessCapturedBuffers`, with **no `RenderRequest` frame at
all**. The 2026-08-03 analysis reasoned from the message *shape* to a similar defect in the image
path and correctly refuted itself by soak — the soak exercised the wrong path. Two call sites,
one assertion text; the stack distinguished them all along.

### And it exposed a missing build route

Patching Blocks by hand fixes one machine. Quickstart 0.2 builds the plugin from **pristine**
vendor source and nothing applies `patches/cosys-airsim/*` to it; `convert_world.sh` only serves
*user* worlds. So Blocks — the default world and the gate world — never received an Unreal-side
patch. **`scripts/build_blocks.sh`** (new) closes it, and running it showed **0005 had also been
missing from Blocks for five days**, exactly as that patch's own "not yet wired into the build"
note had warned.

---

## `SIM-24` — a dropped GPU-LiDAR scan is invisible to every normal run

**Status:** 🟡 **open** — raised **2026-08-08** by the review of `SIM-23`, deferred out of that PR
because it changes the gate's output.

`SIM-23` traded a crash for a dropped scan. That is the right trade — but it moved the failure
from *loud* to *silent*, and nothing in the normal flight path is listening.

When a readback comes back empty the simulator now logs:

```
GPU-LiDAR readback incomplete (depth 0 px, need 262144), dropping frame
```

The **only** thing that greps for that line is `soak_full_stack.sh`. Neither `run_gate.py` nor
`run_park_tour.sh` looks at it, and neither writes it into `summary.json`. So a stack that began
dropping scans regularly would keep scoring **PASS** on waypoint error while its LiDAR data
quietly thinned, and the first sign would be a perception result nobody could explain.

This is the same shape as `SIM-22`: the harness could not see the thing that was actually going
wrong, so it reported the run clean. The fix there was an independent witness; here the signal
already exists and is simply not collected.

### What to do

- Count `readback incomplete` in the renderer log across a run and put it in `summary.json`,
  next to the collision count.
- Decide the scoring rule deliberately. A handful of drops in a long flight is not a failed
  flight; a run dropping continuously has no usable LiDAR and should not read as PASS. Whatever
  the threshold, **VOID is probably the right verdict rather than FAIL** — the vehicle flew fine,
  the sensor did not, and `run_gate.py` already distinguishes those.
- The count belongs with the *evidence*, not just the log: a bag without it cannot be re-scored.

The drop count is also a proxy for how often the underlying readback fails, which is the one
number `SIM-23` never obtained — its 90-minute soak saw **zero** natural occurrences.

---

## `SIM-25` — one implementation of the Unreal-side patch routing rule, not three

**Status:** 🟡 **open** — raised **2026-08-08** by the review of `SIM-23`.

The rule deciding whether a patch under `patches/cosys-airsim/` belongs to the Unreal plugin or
the ROS 2 wrapper now exists in **three** scripts:

| script | routing predicate | apply / already / die |
|---|---|---|
| `build_airsim_wrapper.sh` | `grep -qE '^\+\+\+ b/Unreal/'` | skips Unreal-side patches |
| `convert_world.sh` | same | full three-way block |
| `build_blocks.sh` | same | full three-way block, copy-pasted |

**This repo has already paid for this exact mistake.** `collision_witness.py` exists because the
"an absent witness is UNKNOWN" rule was written twice — and the two copies disagreed within one
commit, one scoring a run PASS that the other failed.

The routing rule is more load-bearing than that one. Misroute `0005` and a vehicle falls through
a World Partition world forever; misroute `0006` and the renderer dies mid-flight. Both scripts
already carry a `die` for the drift case precisely because silence here is dangerous — which is
the argument for the rule having one owner rather than three.

### What to do

- Extract it to one place both callers use — a small `scripts/apply_vendor_patches.sh` taking a
  target directory and a side, or a Python helper if the shell gets awkward.
- **Unit-test the predicate.** It is a pure function of a patch file's header and needs no
  simulator; `tests/` already covers `inject_airsim` and `apply_spawn` the same way. A silently
  misrouted patch is the failure with the worst consequence in this repo and currently has zero
  test coverage.
- Do it as its own change — it touches the world-conversion path, so it wants its own
  verification run rather than riding along with something else.

---

## Not in this backlog

Recorded so they are not smuggled in:

- **Reviving the Gazebo baseline or Isaac Sim.** Both are retired. Their backlogs, and the
  measurements that retired them, are preserved in [`history/`](history/) — nothing here
  depends on either.
- **A second ROS 2 distro**, except via the documented, evidence-gated `ros2_distro_fallback`
  entry in `versions.lock`.
- **The applications people build on the simulator** — planners, language-driven navigation
  clients, benchmark harnesses. They are what the simulator is *for*; they are not what this
  repo ships.
- **Anything touching the real aircraft.** A real flight needs explicit per-run operator
  approval, every time (`.ai/AGENTS.md:120`).
