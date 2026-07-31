# Lane A — Phase 1 — GPS Navigation & Offboard Control — backlog

**Area:** the ROS 2 offboard controller, launch composition, the seeded scenario runner,
MCAP recording, and the CI job that gates all of it.
**Indexed from:** [`../drone-sim-todo.md`](../drone-sim-todo.md).
**Plan source:** [`../reference/02_development_plan.md:36`](../reference/02_development_plan.md).

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

**Status:** `todo` · **Blocked by:** P1-02

**What.** `ros2_ws/src/bringup/` — launch files composing the controller, `use_sim_time`,
parameter files from `configs/`, and later the recorder. One entry point per lane, sharing
the node set.

**Why.** "Run these six commands in four terminals" is not reproducible, which is the
project goal this repo already committed to.

**Acceptance.** One `ros2 launch` brings up the ROS 2 side against the composed stack.

---

## P1-03a — `/clock` bridge, so `use_sim_time` actually works

**Status:** `todo` · **Blocked by:** P1-03 · **Found:** 2026-07-30, while writing `P1-02`

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

**Status:** `todo` · **Blocked by:** P1-03

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

**Status:** `todo` · **Blocked by:** P1-03

**What.** Record the flight to MCAP — `rosbag2` with the `mcap` storage plugin — with a
declared topic set, written to `/out` so artifacts survive `--rm` (the lesson `D-01` paid
for), and named by scenario + seed.

**Why.** The exit criterion requires an uploaded artifact. More practically: a failed run
you cannot replay is a run you will debug twice.

**Acceptance.** A run produces an MCAP that replays and contains the full trajectory.

---

## P1-06 — Success-rate gate: 10 seeded runs

**Status:** `todo` · **Blocked by:** P1-04, P1-05

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

## P1-07 — GitHub Actions headless SITL job

**Status:** `todo` · **Blocked by:** P1-06 · **Related:** `../docker/todo.md` `D-05`

**What.** CI that builds the Lane A image and runs the gate headless, under 10 minutes.
Overlaps `D-05` (CI builds the images) — coordinate rather than duplicating.

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
