# Lane A — Phase 1 — GPS Navigation & Offboard Control — backlog

**Area:** the ROS 2 offboard controller, launch composition, the seeded scenario runner,
MCAP recording, and the CI job that gates all of it.
**Indexed from:** [`../drone-sim-todo.md`](../drone-sim-todo.md).
**Plan source:** [`../reference/02_development_plan.md:36`](../reference/02_development_plan.md).
**Stack topology:** [`architecture.html`](architecture.html) — what runs where, how it is
connected, and the two traps the shared-namespace design creates.

**Goal / DoD.** Deterministic headless PX4 + Gazebo SITL with a ROS 2 offboard controller
flying GPS waypoint missions in lockstep, recorded to MCAP, CI run under 10 minutes.

**Exit criterion (the one that matters).** An automated test takes off, flies a 4-waypoint
square, lands — **SR = 100% over 10 seeded runs**, with the MCAP artifact uploaded. A
single green run is not a pass.

---

## What Phase 0 leaves us

Everything below builds on a stack that already works and is already the acceptance gate:

- `docker compose -f docker/compose.yaml up -d` → PX4 v1.16.0 + Gazebo Harmonic, the
  uXRCE-DDS agent, and a `ros2` service to run nodes in.
- 24 `/fmu/out/*` topics at ~98 Hz, aggregate RTF 0.9733, 0 sensor TIMEOUTs.
- `tests/lane-a-smoke.sh` proves the *stack*. Phase 1 adds the test that proves the
  *flight*.

The `ros2` service exists precisely so Phase 1 nodes have somewhere to run. It currently
idles.

---

## The ordering constraint

`P1-00` comes first and is not a formality. The plan calls it out directly
(`02_development_plan.md:252`): **freeze topic and namespace conventions in Phase 1,
because they must reach the aircraft unchanged.** Every later phase — planner, VLM client,
VIO, the real Pixhawk — inherits whatever we pick here. Renaming a topic in Phase 3 means
touching the planner, the eval harness and the hardware bring-up at once.

---

## P1-00 — Freeze the topic and namespace conventions

**Status:** ✅ **`done` (2026-07-30)** — [`conventions.md`](conventions.md) · **Blocks:** every other Phase 1 task

Frozen: bare `/fmu/*` with a `px4_ns` parameter for multi-vehicle; `/planner/trajectory`
and `/vlm/target` adopted from the plan; ENU/FLU outside and NED inside with the conversion
in one tested place; BEST_EFFORT + TRANSIENT_LOCAL on every `/fmu/out` subscription.
`use_sim_time` is specified but **not yet reachable** — nothing publishes `/clock`, which is
now `P1-03a`.

**What.** A short, committed document defining the ROS 2 graph's public surface, plus the
`use_sim_time` policy and the frame conventions. Specifically:

- The vehicle namespace. PX4's uXRCE-DDS bridge defaults to bare `/fmu/in/*` and
  `/fmu/out/*`; multi-vehicle work uses `/px4_<id>/fmu/…`. **Decide now** whether Lane A
  runs bare or namespaced, because the single-vehicle choice is the one that silently
  breaks when Phase 2 adds a second aircraft.
- Names for **our** topics — the ones that are not PX4's. Setpoints in, state out, mission
  status, and whatever the eval harness consumes.
- **Frames.** PX4 is NED with **z negative up**; ROS convention is ENU. State where the
  conversion happens and never do it twice. Getting this wrong produces a vehicle that
  flies confidently into the ground.
- `use_sim_time: true` everywhere in sim, and what that implies for anything that measures
  wall-clock latency.

**Why.** Sim↔real parity is the entire architecture: the same graph runs against SITL and
against the Pixhawk, with only the transport swapped. That only holds if the names hold.

**Acceptance.** The document exists, `P1-02`'s node matches it, and a grep for hard-coded
topic strings outside the convention returns nothing.

---

## P1-01 — `drone_interfaces` — our message contracts

**Status:** `todo` · **Blocked by:** P1-00

**What.** The `interfaces` package: messages/services for a mission spec (waypoint list,
seed, tolerances), a mission result (outcome, per-waypoint errors, timings), and the
controller's state. Uses `px4_msgs` for everything PX4 already defines — **do not restate
PX4's messages**.

**Why.** The eval harness, the scenario runner and later the VLM client all need a stable
way to say "fly this" and "here is what happened". A dict-over-a-string contract will not
survive Phase 3.

**Acceptance.** `colcon build` produces the interfaces; a round-trip publish/echo works.

---

## P1-02 — Offboard control node

**Status:** ✅ **`done` (2026-07-30)** — `ros2_ws/src/control/` · **Blocked by:** ~~P1-00~~

**Verified by flying it**, three consecutive successes, uXRCE-DDS only — no MAVLink, no GCS:

```
armed -> takeoff 10 m -> waypoint 1..4 -> landed and disarmed
waypoint errors: 0.174, 0.056, 0.152, 0.214 m  (accept radius 1.0)
```

Confirmed **from the MCAP rather than the node's own logs** (5,360 position samples):
max altitude **10.18 m** (commanded 10.0), final altitude **-0.03 m**, square span
**10.28 x 10.25 m** (commanded 10 x 10).

**Three PX4 findings, all in [the worklog](../worklog/2026-07-30-phase-1-offboard.md):**
- Arming requires a **GCS datalink** (`NAV_DLL_ACT 2`, set by `4001_gz_x500:51`). The check
  is left **enforced**; the stack supplies the link via the `qgc` compose service —
  QGroundControl is the only component permitted to speak MAVLink over IP.
  Verified causally — stop the service and arming is denied.
- **PX4's GCS port is 18570, not 14550** (`px4-rc.mavlink:11`). Nothing is ever sent to
  14550; a heartbeat aimed there is discarded silently, with no error and no log line.
- Landing hung because the node kept streaming offboard setpoints at altitude, fighting
  `AUTO.LAND`.

**What.** The first real flight code, in `ros2_ws/src/control/`. Publishes
`OffboardControlMode`, `TrajectorySetpoint` and `VehicleCommand`; subscribes to
`VehicleLocalPosition` and `VehicleStatus`. Implements arm → offboard → takeoff →
waypoint sequence → land as an explicit state machine with per-state timeouts.

**Why.** Phase 0's takeoff was a MAVLink demo script (`docker/demo/lane-a-fly.py`) written
to produce a video. This replaces it with the real thing: **uXRCE-DDS, not MAVLink**,
because that is what runs onboard. The script has since been **deleted**, and the demo
recorders now drive this node — enforcing the project rule that **only QGroundControl
speaks MAVLink over IP; everything else is on the ROS graph.**

**The traps, all of which are documented PX4 behaviour — verify each by running:**

- **Offboard mode is refused unless setpoints are already streaming.** PX4 requires a
  setpoint stream at **>2 Hz** *before* the mode switch, and drops out of offboard if the
  stream lapses. Stream first, switch second.
- **`OffboardControlMode` selects which setpoint fields PX4 honours.** Setting the wrong
  flag makes PX4 ignore a perfectly good `TrajectorySetpoint` and hold position.
- **NED, z negative up.** A 10 m altitude is `z = -10.0`.
- **`timestamp` is PX4 microseconds**, not a ROS `Time`.
- **`/fmu/out/*` publishers are BEST_EFFORT.** A default (RELIABLE) subscription matches
  nothing and the node sees **zero messages on a healthy stack** — the same false-negative
  class as the `compose exec` ROS-env bug in `D-02`.
- **Lockstep desync trips a failsafe.** The plan prescribes
  `param set-default COM_OF_LOSS_T 15` (`02_development_plan.md:41`); confirm the value
  against v1.16.0 rather than trusting the plan.

**Acceptance.** With the composed stack up, the node arms, takes off to 10 m, flies a
4-waypoint square, lands and disarms — observed in `/fmu/out/vehicle_local_position`, not
merely logged by the node itself.

---

## P1-03 — `sim.launch.py` and bringup composition

**Status:** 🟡 **implemented on `feat/docker-runner`; target validation pending** ·
**Issue:** #9

**What.** `ros2_ws/src/bringup/` — launch files composing the controller, `use_sim_time`,
parameter files from `configs/`, and later the recorder. One entry point per lane, sharing
the node set.

**Why.** "Run these six commands in four terminals" is not reproducible, which is the
project goal this repo already committed to.

**Acceptance.** One `ros2 launch` brings up the ROS 2 side against the composed stack.

---

## P1-03a — `/clock` bridge, so `use_sim_time` actually works

**Status:** 🟡 **bridge and image dependency implemented; live `/clock` evidence pending** ·
**Issue:** #9 · **Found:** 2026-07-30, while writing `P1-02`

**What.** Publish Gazebo's clock onto ROS 2 `/clock` — `ros_gz_bridge`'s `parameter_bridge`
on `/world/default/clock`, or an equivalent — and add the package to the Lane A image.

**Why this is its own task.** `use_sim_time: true` is in the frozen conventions, but on the
current stack **nothing publishes `/clock`** (`ros2 topic list | grep -c '^/clock$'` → `0`,
and `ros-jazzy-ros-gz` is not installed). Turning `use_sim_time` on today freezes every
node's clock at zero: timers never fire, and the node hangs in a way that looks precisely
like a deadlocked controller rather than a missing dependency.

Until this lands, Phase 1 nodes run on **wall clock**, which is wrong by the ~2.7% the
nested-Docker RTF deficit costs — tolerable for a flight that finishes in ~90 s, not
tolerable for a latency budget in Phase 3.

**Trap.** Adding `ros-jazzy-ros-gz-bridge` changes the image, so it is a `versions.lock`
entry and a Dockerfile pin, not an `apt install` in a running container.

**Acceptance.** `/clock` advances at sim rate; a node with `use_sim_time:=true` runs its
timers and completes a flight; the pin is recorded.

---

## P1-04 — Seeded scenario runner

**Status:** ✅ **`done` (2026-07-31)** — `scripts/run_scenario.py` + `scenarios/square-10m.yaml`

```bash
./scripts/run_scenario.py scenarios/square-10m.yaml --seed 1
```

Restarts the stack with seed-derived environment, flies the scenario's waypoints, records
an MCAP named by scenario+seed, and writes a structured result. **Verified on two seeds,
both `success` 4/4**, with the derived spawn actually landing in Gazebo (seed 1 derived
`x=-3.656`, Gazebo reported `-3.712`).

### What the seed controls — and what it does not

**The seed selects a scenario VARIANT. It does NOT reproduce a trajectory.** Measured, not
assumed: two back-to-back runs with identical configuration against the *same* simulator
gave waypoint errors `[0.225, 0.104, 0.154, 0.204]` and `[0.118, 0.076, 0.158, 0.187]`.
Any claim of seed-exact replay here would be false.

That is precisely why the exit criterion is a success *rate*: one run is evidence about
conditions, and only the rate is evidence about reliability.

**The seed drives the spawn pose** (`PX4_GZ_MODEL_POSE`), and there is an honest caveat:
in an *empty* world it varies almost nothing the controller can see. PX4's local frame
origin is set wherever the EKF initialises, so a home-relative mission is unchanged — only
the initialisation transient and ground contact differ. Confirmed by spawning at `(7, -4)`
and watching the controller still report home `(-0.03, -0.02)`. It is wired now because it
becomes load-bearing in Phase 2, where obstacles sit at fixed world coordinates.

**Not yet seeded: sensor noise** — the genuinely stochastic element. `gz sim --seed` is the
knob, but reaching it means running Gazebo standalone (`PX4_GZ_STANDALONE=1`) as its own
compose service so we own the server command line rather than letting PX4's `gz_bridge`
start it. Filed as `P1-04a`.

### Trap found: never recreate `px4-sitl` alone

Every other service joins its network namespace, so `docker compose up -d --force-recreate
px4-sitl` leaves them attached to a namespace that no longer exists — **`ros2 topic list`
returns 0 topics against a stack that reports healthy**. Measured: 0 topics after
recreating the donor alone, 24 after recreating everything. The runner always recreates the
whole stack.

### Cost, which matters for `P1-07`

**97.3 s per run**, dominated by the stack restart. Ten seeds is ~16 minutes — **over the
10-minute CI budget** in the plan. Options for `P1-06`/`P1-07`: reuse one stack across
seeds (`--no-restart`, at the cost of not applying the spawn pose), shrink the mission, or
run fewer seeds in CI than locally. Decide with evidence rather than by trimming the gate.

---

## P1-04a — Seed the CONDITIONS, not the RNG

**Status:** ✅ **`done` (2026-07-31)** — wind seeded, and the 10-seed gate re-run with it on:
**SR 10/10**, with waypoint error correlating with wind speed at **Pearson r = 0.957**.
Redesigned after the original approach was measured and failed. Evidence: [`../worklog/2026-07-31-gz-seed-negative-result.md`](../worklog/2026-07-31-gz-seed-negative-result.md)

### The original plan does not work — measured, not assumed

`gz sim --seed` was going to seed the simulator's RNG so the noise stream became
reproducible. It is accepted by the binary and the plumbing works — PX4 attaches to a
world we start ourselves (`px4-rc.gzsim:63`), no `PX4_GZ_STANDALONE` needed — and it has
**no measurable effect.**

Compared at **identical simulation timestamps**, on Gazebo's own sim-stamped IMU topic:

| Comparison | Aligned samples | Identical | Mean \|Δ accel_x\| |
|---|---|---|---|
| **Same seed** (42 vs 42) | 716 | **0 (0.0%)** | **0.00726** |
| **Different seed** (42 vs 7) | 1911 | 0 (0.0%) | **0.00718** |

Two runs with the same seed differ by as much as two runs with different seeds. Not one
sample matches. **Do not retry this** without new evidence that `--seed` reaches
`gz-sensors`' noise models.

The plumbing was reverted — it added a branch to the PX4 startup and bought nothing, and
leaving it in would imply control the stack does not have.

### Wind — built and measured

`scripts/make_variant.py` generates a per-seed world + model overlay; the runner samples
speed and heading from the scenario's `wind_speed_max_ms` and points the stack at it. The
vendored PX4 tree is untouched — everything is copied.

| Configuration | Waypoint errors (m) | Mean |
|---|---|---|
| Stock, no overlay | 0.21, 0.08, 0.16, 0.19 | 0.16 |
| Overlay, wind 0 | 0.297, 0.399, 0.342, 0.393 | 0.358 |
| Overlay, **wind 3 m/s** | **0.634, 0.541**, 0.06, 0.298 | 0.383 |
| Overlay, wind 8 m/s | **vehicle blown 416 m — cannot arm** | — |

At 3 m/s the upwind legs cost an order of magnitude more than the downwind leg — a
**direction-dependent signature**, which is what wind should produce and what the spawn-pose
seed never gave. End-to-end: seed 2 derives 2.868 m/s at 2.81 rad and flies 4/4.

**Three findings worth keeping:**

1. **A `<plugin>` in the world SDF makes Gazebo load ONLY those plugins**, dropping the core
   systems from PX4's server config. The world came up with 6 topics, no `scene/info`
   service, the vehicle never spawned, and PX4 sat in "Waiting for Gazebo world..." until it
   timed out — with a world file that `gz sdf -k` calls **Valid**. The plugin goes in a copy
   of PX4's `server.config` instead; only `<wind>` goes in the world.
2. **`enable_wind` must be set on the vehicle link.** Upstream `x500_base` does not set it,
   so without the model overlay the wind applies to nothing and the flight looks normal.
3. **The overlay changes the physics even at zero wind** (0.16 → 0.358 mean error), because
   `enable_wind` + `WindEffects` introduces air-relative drag the stock model has none of.
   So "overlay, wind 0" is the correct baseline — not the stock stack.

**The bound is load-bearing.** At 8 m/s the *disarmed* vehicle slides 416 m downwind before
it can arm and the EKF gives up (`xy_valid: false`, position 5214 m); the run fails in
`wait_for_fcu` — a harness failure, not a controller failure. `wind_speed_max_ms: 3.0`.

### The gate, re-run with wind on — the acceptance evidence

**SR 10/10 · 1132 s · wind sampled 0.40–2.87 m/s of the 3.0 cap.**

| Wind (m/s) | 0.40 | 0.68 | 0.71 | 0.71 | 0.97 | 1.39 | 1.71 | 1.87 | 2.38 | 2.87 |
|---|---|---|---|---|---|---|---|---|---|---|
| Worst error (m) | 0.377 | 0.380 | 0.384 | 0.398 | 0.395 | 0.395 | 0.437 | 0.477 | 0.490 | 0.555 |

**Pearson r = 0.957, slope 0.070 m of error per m/s of wind.** This is the acceptance
criterion met with numbers rather than assertion: a measurable property of the flight now
varies *systematically* with the seed. Before, the seed moved a spawn point and the ten
runs were indistinguishable.

**The margin narrowed, and that is worth watching.** Worst error went from 0.235 m to
0.555 m against the 1.0 m accept radius — from a **4.3x** margin to **1.8x**. The radius
still discriminates, but a gate at 1.8x will start catching things, which is the point of
adding diversity. If the cap is raised toward 3 m/s the slope predicts ~0.58 m, still
inside. Raising it much further needs the accept radius restated deliberately, not
discovered by a red gate.

**Cost:** 113 s per run against 97 s without the overlay, so the gate is now ~19 min —
further over the 10-minute CI budget, which stays `P1-07`'s problem.

### Still to do

- Mass and sensor-noise `stddev` — the overlay mechanism now exists for both, and the same
  correlation test applies.

### What to build instead

**Seed the conditions, not the random stream.** A success rate does not need determinism;
it needs *diversity*. Each of these is a scenario-declared range sampled with
`random.Random(seed)` — the mechanism `scripts/run_scenario.py` already has, pointed at
knobs that actually change the physics:

| Knob | Why it is worth seeding |
|---|---|
| **Wind** speed + direction | The default world already has a `wind` element. The most physically meaningful disturbance for a multirotor, and the one a real flight actually meets. |
| **Vehicle mass / inertia**, within a band | The controller meets a slightly different aircraft each run — a direct test of how much its behaviour depends on exact tuning. |
| **Sensor noise `stddev`** in the model SDF | Not the stream, the *parameter*. Widening or narrowing the noise is a real robustness test even though the samples are not reproducible. |

**Acceptance.** Across seeds, a measurable property of the flight differs systematically
(e.g. mean waypoint error correlates with wind speed) — demonstrated with numbers, not
asserted. And the gate still passes with the diversity switched on, or the failures it
surfaces are real ones worth fixing.

**Traps.**
- **Two of these mutate the model or world SDF**, which lives inside the image. Mount an
  overlay or generate a per-run copy; do not edit the vendored tree (least-destructive
  vendor edits).
- **Diversity that is too wide turns a real gate into a flaky one.** Pick ranges the
  aircraft should genuinely handle, and record why those bounds.
- **The runs still are not reproducible**, so a failing seed cannot be replayed. Keep the
  per-run MCAP; it is the only evidence of a failure.

---

## P1-04 — original definition

**Status:** ~~`todo` · Blocked by: P1-03~~

**What.** A runner that takes a scenario (waypoints, world, tolerances) plus a **seed**,
executes one flight, and emits a structured result. Scenarios live in `scenarios/`,
which already exists and is already mounted read-only into the stack.

**Why.** The exit criterion is a success *rate* over 10 seeded runs. That requires runs to
be enumerable, repeatable and individually attributable — not a person watching a terminal.

**Trap.** "Seeded" must actually control something. Identify what is genuinely stochastic
here (sensor noise, wind, initial pose) and seed *that*; a seed that changes nothing gives
10 identical runs and a meaningless 100%.

**Acceptance.** The same seed twice gives the same trajectory within tolerance; different
seeds visibly differ.

---

## P1-05 — MCAP recording

**Status:** 🟡 **mostly done (2026-07-31)** — the scenario runner records one MCAP per run,
named `<scenario>-seed<n>`, written to `/out` so it survives `--rm`. 1.36 MB for a ~45 s flight;
replays and contains the full trajectory (verified by reading 5,360 position samples back
out of one). The Fern/Runpod runtime adds a declared topic set and durable workspace path
(#12, #13). `rosbag2-storage-mcap` is built into the image.

**Left to do:** live replay/persistence evidence and CI artifact attachment (`P1-07`).

**Original definition:** ~~`todo` · Blocked by: P1-03~~

**What.** Record the flight to MCAP — `rosbag2` with the `mcap` storage plugin — with a
declared topic set, written to `/out` so artifacts survive `--rm` (the lesson `D-01` paid
for), and named by scenario + seed.

**Why.** The exit criterion requires an uploaded artifact. More practically: a failed run
you cannot replay is a run you will debug twice.

**Acceptance.** A run produces an MCAP that replays and contains the full trajectory.

---

## P1-06 — Success-rate gate: 10 seeded runs

**Status:** ✅ **`done` (2026-07-31)** — `scripts/run_gate.py` · **SR = 10/10 (100%)**

```bash
./scripts/run_gate.py scenarios/square-10m.yaml          # the gate
./scripts/run_gate.py scenarios/square-10m.yaml --reuse  # faster, and NOT a gate run
```

| Seed | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Result | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Worst waypoint error (m) | 0.235 | 0.226 | 0.217 | 0.202 | 0.208 | 0.197 | 0.214 | 0.195 | 0.200 | 0.209 |

Spread **0.195–0.235 m**, mean 0.210, against a 1.0 m accept radius — a 4x margin, and
tight enough that a real regression should be obvious rather than marginal. **10/10 MCAPs
written**, ~1.4 MB each, named by scenario and seed. Wall clock **974 s**.

**The gate re-derives pass/fail from the numbers** rather than trusting the controller's own
`outcome` field: waypoints reached must equal waypoints total, the error list must have one
entry per waypoint, every error must be **finite**, and the worst must be inside the accept
radius. A controller bug that mislabelled a flyaway as success would still fail the gate.

**The finite check was missing in the first version, and review caught it.** The controller
recorded `NaN` when a position sample was invalid at the moment of arrival, and because
every comparison against NaN is False, `worst > radius` **passed it** — while `max()`
dropped it, so the reported worst error was wrong too. The one case where the error is
*unknown* was the one that looked clean. Fixed at both ends: the controller reuses the
distance that actually satisfied the check so no NaN is produced, and the gate rejects
non-finite values explicitly. `tests/test_gate_checks.py` pins it — 13 off-target tests
that need no simulator.

### What this SR does and does not mean

**It measures repeat-reliability, not seed-diversity.** The seed drives only the spawn pose,
which in an empty world changes almost nothing the controller sees. Until `P1-04a` seeds the
simulator's RNG, ten seeded runs are closer to ten repeats — still worth having, because
flaky failures surface under repetition, but the word "seeded" is doing less work than it
looks. The caveat is written into every report the gate emits.

**`--reuse` cannot claim the criterion.** It shares one stack across runs (54 s each instead
of 97 s) but never applies the spawn pose. An early version printed *"Phase 1 exit criterion
met"* directly above *"not a full gate run"* — fixed so `met` requires both a perfect rate
and a real gate run, and reuse mode reports `INCONCLUSIVE` instead.

### Over the CI budget

**974 s = 16.2 min against the plan's <10 min.** Roughly half is the per-run stack restart
(97 s per run, of which ~50 s is restart). `--reuse` would fit at ~9 min but is not a gate
run. This is `P1-07`'s problem to solve honestly — reduce the mission, publish a prebuilt
image so the restart is cheaper, or state plainly that CI runs fewer seeds than the local
gate. **Do not fit the budget by quietly weakening the gate.**

**Original definition:** ~~`todo` · Blocked by: P1-04, P1-05~~

**What.** The Phase 1 exit criterion as an executable test, alongside
`tests/lane-a-smoke.sh`. Runs the scenario across 10 seeds, computes SR, asserts 100%, and
emits a per-run table plus the MCAP paths.

**Why.** This *is* the phase gate. Everything else is a means to it.

**Trap.** Decide up front what counts as success — reaching each waypoint within a stated
radius, landing disarmed, no failsafe, and the RTF floor holding. A gate that silently
passes on a flyaway is worse than no gate. Note that `D-01` already established the RTF
floor must be **aggregate**, never the instantaneous field.

**Acceptance.** 10/10, reproducibly, on the composed stack.

---

## P1-07 — CI

**Status:** 🟡 **tier 1 done (2026-07-31)** — `.github/workflows/checks.yml`. The optional
Fern path has a main-only GHCR publisher on `feat/docker-runner` (#10), but the Tier 2 PR
flight gate still needs Fern wait/download/destroy and cancellation cleanup (#13, #14).
**Related:** `../docker/todo.md` `D-05`

### Why this is two tiers, not one

The original plan was "CI builds the Lane A image and runs the gate, under 10 minutes".
That is not achievable on GitHub-hosted runners, and the arithmetic is worth writing down
so it is not re-attempted:

| | Hosted runner | What Lane A needs |
|---|---|---|
| Disk | ~14 GB free | image is **11.6 GB** |
| Build time | — | **20–40 min** measured |
| CPU | 2 vCPU | gate asserts **aggregate RTF ≥ 0.95** |
| Gate itself | — | **~19 min** for 10 seeds |

Even with a prebuilt image, 2 vCPU would miss the RTF floor on *hardware*, turning a
physics gate into a runner-capacity gate — a red build that says nothing about the code.

**Tier 1 — hosted, every push, ~1 min.** Everything checkable without a simulator: the 25
off-target tests, `bash -n` and `py_compile` over every tracked script, `compose config`
(which is how the inert `FOLLOW_*` knobs were caught), an AI-attribution check, and an
assertion that every `versions.lock` `CONFLICT` carries a summary.

Two of those checks were **wrong on the first attempt and fixed before pushing**, which is
the argument for running a workflow's steps locally before trusting it:

- the attribution check failed on `.ai/AGENTS.md` and `CLAUDE.md` — the files that *define*
  the rule and quote the forbidden strings in order to forbid them;
- the conflict check asserted *no* `CONFLICT` in `versions.lock`, but there is a real,
  accepted one (the NVIDIA driver against Isaac's validated version, which is why Lane B is
  deferred). Failing on a known deliberate state makes a job people learn to ignore. It now
  asserts every conflict is *documented* instead.

**Tier 2 — the SITL gate, still to do.** Needs a self-hosted runner (this box qualifies:
the image is local and there are enough cores). Until it exists, `scripts/run_gate.py` is
run by hand, and the workflow says so rather than implying coverage it does not have.

**What.** ~~CI that builds the Lane A image and runs the gate headless, under 10 minutes.~~
Superseded by the two tiers above.

**Why.** A gate no one runs is documentation.

**Traps.**
- **SITL flakiness** — the plan prescribes retry ×2 plus an RTF-floor assertion
  (`02_development_plan.md:41`). Retries must be visible in the output; a silent retry
  turns a flaky stack into a green badge.
- **The 10-minute budget is tight.** An 11.6 GB image build does not fit; the image must be
  cached or prebuilt, which is exactly what `D-05` is for.
- GitHub's hosted runners have no GPU. Lane A does not need one — keep it that way, and
  keep GPU work out of this job.

**Acceptance.** A green CI run on a PR, under 10 minutes, with the MCAP artifact attached.

---

## Not in Phase 1

Recorded here so they are not smuggled in:

- **Obstacles, depth, LiDAR, EGO-Planner** — Phase 2. Phase 1 flies GPS waypoints in an
  empty world, on purpose.
- **Anything touching the real aircraft.** Phase 1 is SITL only. Real flight is Phase 4 and
  needs explicit per-run operator approval every single time (`.ai/AGENTS.md:39`).
- **The VLM.** Phase 3. `P0-13` (hello-VLM) is parked in the Phase 0 backlog.
