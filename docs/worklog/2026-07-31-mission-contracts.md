# 2026-07-31 — `P1-01` mission contracts

**Task:** `P1-01` — the `drone_interfaces` package.
**Lane:** A. SITL only.

> Kept as the work happens.

---

## A scope judgment, made explicitly

The task says the eval harness, the scenario runner and the VLM client all need "a stable
way to say *fly this* and *here is what happened*", and that a dict-over-a-string contract
will not survive Phase 3. Both true. But taken literally it implies replacing the JSON
result with ROS messages, and **that would not serve the actual consumer**:

`scripts/run_scenario.py` is a **host** script that drives `docker compose`. It has no ROS
environment and cannot subscribe to a topic. It reads `/out/<tag>.json`. Rewriting the
result as a ROS message and leaving the runner to parse it would mean bridging ROS back out
to the host — more machinery, no benefit, for a consumer that is happy.

So the JSON stays as the **host-side** transport, documented as such rather than left
looking provisional.

## What genuinely needs a contract now

**The MCAP cannot describe itself.** A recorded run contains only PX4 topics, so a bag from
a failed seed cannot tell you which waypoint the controller thought it was on, what state it
was in, or why it gave up. That matters more here than in most projects because **runs are
not reproducible** — measured, back-to-back identical configuration gives different
trajectories. The bag is the only evidence a failure leaves, and today it is missing the
controller's side of the story.

So `P1-01` is scoped to the graph-internal contract:

- `MissionStatus` — published continuously: state, waypoint index, distance to target.
  Recorded into the MCAP, so a bag explains itself.
- `MissionResult` — published once at the end, the same summary the JSON carries.

Phase 3's `TargetWaypoint` / `VlmQuery` (`02_development_plan.md:162`) are deliberately NOT
invented now. Freezing a contract for a consumer that does not exist is how you get a
contract that fits nothing.

## Progress log

### Built and verified

`drone_interfaces` with two messages, `control` publishing both, and the runner recording
them. Three packages build clean.

**The acceptance test is stronger than "a round trip works".** The whole flight is now
reconstructable from the bag with no other input:

```
WAIT_FOR_FCU       wp 0/0  target (  0.00,  0.00, 0.00)  d=  0.07 m
STREAM_SETPOINTS   wp 0/4  target ( -0.02, -0.05, 0.03)  d=  0.00 m
REQUEST_OFFBOARD   wp 0/4  ...
ARM                wp 0/4  ...
TAKEOFF            wp 0/4  target ( -0.02, -0.05,10.00)  d= 10.00 m
WAYPOINTS          wp 0/4  target (  9.98, -0.05,10.00)  d=  9.98 m
WAYPOINTS          wp 1/4  target (  9.98,  9.95,10.00)  d= 10.19 m
WAYPOINTS          wp 2/4  target ( -0.02,  9.95,10.00)  d= 10.03 m
WAYPOINTS          wp 3/4  target ( -0.02, -0.05,10.00)  d= 10.04 m
LAND               wp 4/4  d=  0.20 m
verdict: success 4/4  errors [0.197, 0.071, 0.154, 0.2]  src=built-in-square
```

882 `/mission/status` messages and 1 `/mission/result` alongside 5,109 PX4 positions.

**Why that matters here specifically:** a PX4-only bag can show the vehicle in the wrong
place without showing *which waypoint the controller was aiming at*. "Aimed at the wrong
place" and "aimed correctly and did not get there" are different bugs and looked identical.
Since runs are not reproducible, a failing seed cannot be replayed — the bag is the evidence,
and now it carries the controller's side.

### Two small decisions worth recording

- **`-1.0`, not `NaN`, for an unknown distance.** NaN in a bag silently defeats every
  comparison — that is exactly the bug the gate shipped with once.
- **`TRANSIENT_LOCAL` on `/mission/result`**, so a recorder started late still gets the
  verdict instead of missing it by a second. `/mission/status` is plain RELIABLE; our own
  topics use ROS defaults unless there is a reason, per conventions §5.
- **`STATE_TO_MSG` sits beside the enum**, so a state added without a constant raises at
  publish rather than writing a wrong number into a bag read months later.

### Two bugs the contract work exposed — the second is serious

**1. The `ros2` service builds only `control`.** Its compose startup runs
`colcon build --packages-select control`, so `drone_interfaces` was never built and the
controller died at import:

```
Traceback ... load_entry_point('control', 'console_scripts', 'offboard_control')
```

An `ament_python` build does not check imports, so `colcon build` reported **success** and
the failure only appeared at run time.

**2. `run_flight()` read a STALE result file and reported it as this run's outcome.**

```
out/square-10m-seed2.json       18:01:03   <- written by an EARLIER run
out/square-10m-seed2-run.json   18:21:38   <- this run
now                             18:22:23
```

The run's controller never started, and the runner reported **`success 4/4`** — from a file
written twenty minutes earlier. `run_flight` looks for `/out/<tag>.json` after the flight
and returns it if present, without ever clearing it first.

This is the same class of bug as the recorder filming a simulator that never armed, which
was found and fixed *in the recorder* — and the identical hole sat in the runner the whole
time. **The gate calls `run_flight` for every seed**, so a seed whose flight failed to start
could have been scored from a previous run's artifact. It passed 10/10 honestly on those
runs, but the mechanism to launder a failure into a pass existed.

Fixing a bug in one place and not sweeping for the same shape elsewhere is the lesson.

### Two more races, found by the same run

Fixing the stale-result bug turned a false success into an honest failure, which then
exposed what was actually broken:

- **`colcon build` without `--packages-skip` rebuilds `px4_msgs`**, which takes minutes. The
  runner flew before `control` was built. Now skips the two packages already built into the
  image, while picking up ours automatically as they are added.
- **The `ros2` container reported "Up" while still building.** It now has a healthcheck on a
  `.build-ok` marker, and the runner waits for it. Without that the runner execs into a
  container mid-build and the race looks exactly like a flight failure.

Verified: seed 2, **success 4/4 in 128.7 s**, bag carrying 1,010 `/mission/status`, 1
`/mission/result` and 5,487 PX4 positions, and a result file written 15 s before it was read.
