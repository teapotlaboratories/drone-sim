# ROS 2 graph conventions — frozen in Phase 1 (`P1-00`)

> **These names reach the aircraft unchanged.** The development plan calls this out
> directly (`../reference/02_development_plan.md:252`): the same ROS 2 graph runs against
> SITL and against the real Pixhawk, with only the transport swapped. That property only
> holds if the names hold. Changing anything here after Phase 1 means touching the
> planner, the VLM client, the eval harness and the hardware bring-up at once.
>
> **Status:** frozen 2026-07-30. Changes need a documented reason and a sweep of every
> consumer.

Everything below was verified against the running stack — PX4 v1.16.0, `px4_msgs`
`release/1.16` — not taken from memory or from examples written for other PX4 versions.
Evidence: [`../worklog/2026-07-30-phase-1-offboard.md`](../worklog/2026-07-30-phase-1-offboard.md).

---

## 1. The PX4 surface — bare, and not ours to rename

PX4's uXRCE-DDS bridge owns `/fmu/in/*` and `/fmu/out/*`. Observed on this stack:

```
$ ros2 topic list | grep -v '^/fmu/'
/parameter_events
/rosout
```

No prefix. That is PX4's single-vehicle default and it is identical on real hardware.

**Rule: never rename or wrap a PX4 topic.** If something needs a different shape, adapt it
in a node; do not republish `/fmu/out/vehicle_local_position` under a prettier name. A
second name for the same data is a second source of truth.

### Multi-vehicle is a parameter, not a refactor

Every node takes a **`px4_ns` parameter, default `''`**, prepended to the PX4 topic names.
Single vehicle resolves to `/fmu/in/trajectory_setpoint`; a second aircraft in a later
phase becomes `px4_ns:=/px4_1` with **no code change**.

**Do not hard-code `/fmu/...` string literals in node bodies.** They go through the
namespace helper. This is cheap now and expensive in Phase 2.

---

## 2. Our own topics — namespaced by function

**Two of these the reference plan already named** (`../reference/02_development_plan.md:155`
lists `/fmu/*`, `/vlm/target`, `/planner/trajectory` as the identical-sim↔real set). Those
are adopted verbatim rather than replaced with a parallel scheme of our own — the same
reuse-over-reinvent rule that governs code applies to names.

| Topic / namespace | Carries | Introduced | Source |
|---|---|---|---|
| `/planner/trajectory` | the reference the controller tracks | `P1-02` | plan `:155` |
| `/vlm/target` | VLM-generated target | Phase 3 | plan `:155` |
| `/mission/*` | mission spec in, status and result out | `P1-01` | new |
| `/eval/*` | scenario-runner and metric traffic | `P1-04` | new |

Named by **function, not by producer**. `/planner/trajectory` stays `/planner/trajectory`
when Phase 2 swaps Phase 1's hand-written waypoint sequencer for EGO-Planner — the
controller does not care who is upstream, which is the entire point of freezing it.

**Not** `/drone_sim/*` or any project-branded prefix: these names have to survive onto real
hardware, where nothing is a sim.

---

## 3. Frames — ENU/FLU outside, NED inside, converted once

**The rule: our interfaces are ROS REP-103 (ENU world, FLU body). PX4's NED exists only
inside the control node's PX4 adapter.**

| | Frame | Notes |
|---|---|---|
| PX4 (`/fmu/*`) | **NED**, `z` **down** | 10 m altitude is `z = -10.0` |
| Our topics (`/planner/*`, `/mission/*`) | **ENU**, `z` **up** | REP-103; 10 m altitude is `z = +10.0` |

**Why not just use NED everywhere and skip the conversion?** Because the upstream
components this project has committed to reusing are all ROS-frame: EGO-Planner (Phase 2),
cuVSLAM and nvblox (Phase 3), RViz. Adopting NED at our interfaces would push a conversion
into each of them — several places, each an opportunity for a sign error, and each one
diverging from what the upstream expects.

**Why not convert wherever convenient?** Because the failure mode is silent. A double
conversion is the identity on some axes and a sign flip on others, and the vehicle flies
confidently into the ground. **One conversion, in one function, with a unit test** — see
`ros2_ws/src/control/`.

Frame ids: `map` (ENU world, the origin PX4's EKF was initialised at), `base_link` (FLU
body). Yaw is ENU counter-clockwise-from-East for our topics; PX4 wants NED
clockwise-from-North, converted in the same adapter.

---

## 4. Time — `use_sim_time: true` is the target, and is NOT yet reachable

**Intended rule:** every node runs with `use_sim_time: true` in sim. Gazebo owns the clock,
and at RTF 0.9733 wall-clock and sim-clock diverge by ~2.7% — enough for a wall-clock
timeout to fire early in a way that reads as a flight failure.

**Observed reality on this stack (2026-07-30) — nothing publishes `/clock`:**

```
$ ros2 topic list | grep -c '^/clock$'
0
$ dpkg -l | grep -c ros-jazzy-ros-gz
0
```

PX4's uXRCE-DDS bridge publishes `/fmu/out/*` and nothing else; the ROS↔Gazebo clock
bridge is not installed in the Lane A image. Setting `use_sim_time:=true` today gives every
node a clock frozen at zero — **timers never fire and the node hangs**, which looks exactly
like a deadlocked controller.

**So, for now: nodes run on wall clock, and say so.** Standing up the `/clock` bridge is
`P1-03a`; `use_sim_time` flips on when that lands, which is why every node takes it as a
parameter rather than assuming either answer.

**The exception: `timestamp` fields on PX4 messages are PX4's own microsecond clock**, not
a ROS `Time`. They are filled from the node clock in microseconds and must not be
converted or interpreted as ROS time.

**Anything measuring real-world latency** — VLM inference (Phase 3), onboard decision
budget (Phase 4) — measures **wall clock deliberately**, and says so at the call site. A
latency budget in sim-seconds is not a latency budget.

---

## 5. QoS — PX4's publishers are BEST_EFFORT, and the default will not match

Observed:

```
$ ros2 topic info -v /fmu/out/vehicle_local_position
  Reliability: BEST_EFFORT
  Durability:  TRANSIENT_LOCAL
```

A default ROS 2 subscription (RELIABLE, VOLATILE) matches **nothing** here. The node then
sees zero messages on a completely healthy stack — the same false-negative shape as the
`compose exec` ROS-environment bug in `D-02`, and just as easy to misdiagnose as a broken
simulator.

**Rule: every `/fmu/out/*` subscription declares BEST_EFFORT + TRANSIENT_LOCAL, depth 1+.**
Our own topics use ROS defaults (RELIABLE) unless there is a measured reason not to.

---

## 6. Offboard control — the protocol PX4 actually enforces

Not style; PX4 rejects or fails the mission otherwise.

1. **Stream setpoints *before* requesting offboard mode.** PX4 refuses the mode switch
   without an existing stream and drops out if it lapses. Stream first, switch second.
2. **Keep streaming above 2 Hz for the whole flight.** `COM_OF_LOSS_T` is the timeout.
3. **`OffboardControlMode` booleans select which `TrajectorySetpoint` fields are honoured.**
   Wrong flag → PX4 ignores a perfectly good setpoint and holds position.
4. **Unused `TrajectorySetpoint` fields are `NaN`, not `0.0`.** The message says so:
   *"setting a value to NaN means the state should not be controlled."* A zeroed velocity
   array commands zero velocity; it does not leave velocity free.

### `COM_OF_LOSS_T` stays at its firmware default

The plan prescribes `param set-default COM_OF_LOSS_T 15` (`02_development_plan.md:41`).
The v1.16.0 firmware default is **1.0 s**:

```
$ grep -rn COM_OF_LOSS_T /opt/px4/src/modules/commander/*.c
322:PARAM_DEFINE_FLOAT(COM_OF_LOSS_T, 1.0f);
```

**Decision: leave it at 1.0.** Raising it to 15 does not fix a starved setpoint stream, it
hides one for fifteen seconds — and on real hardware that is fifteen seconds of an
uncommanded aircraft. What we guarantee instead is the stream rate. If CI shows the nested
Docker RTF deficit genuinely starves it, revisit **with the evidence**, and change it in
sim only.

---

## 7. What is deliberately not frozen yet

- **Mission/result message contracts** — `P1-01`, being designed now.
- **Scenario file format** — `P1-04`.
- **Anything Phase 2+** — planner and perception topics get frozen when their phase starts,
  under these same rules.
