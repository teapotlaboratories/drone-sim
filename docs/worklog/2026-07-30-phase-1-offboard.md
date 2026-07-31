# 2026-07-30 — Phase 1 kickoff: conventions freeze + offboard control node

**Tasks:** `P1-00` (freeze topic/namespace conventions), `P1-02` (offboard control node).
**Lane:** A. **Everything here is SITL** — no real aircraft involved at any point.

---

## Why the conventions come first

`02_development_plan.md:252` is explicit: freeze topic and namespace conventions in
Phase 1, because they must reach the aircraft unchanged. Renaming a topic once the
planner (Phase 2), the VLM client (Phase 3) and the hardware bring-up (Phase 4) depend on
it means changing all of them at once. So `P1-00` blocks everything else in the phase.

---

## Ground truth gathered from the running stack (not from memory)

Brought the composed stack up and interrogated it rather than trusting the reference docs
or recalled PX4 conventions. Everything below is observed on **PX4 v1.16.0**,
`px4_msgs release/1.16`.

### Namespace: bare, no prefix

```
$ ros2 topic list | grep -v '^/fmu/'
/parameter_events
/rosout
```

The uXRCE-DDS bridge publishes bare `/fmu/in/*` and `/fmu/out/*` — no `/px4_1/` prefix.
That is the single-vehicle default and it is what a real Pixhawk does too.

### The three publish targets exist

`/fmu/in/offboard_control_mode`, `/fmu/in/trajectory_setpoint`, `/fmu/in/vehicle_command`
— all present in the 27 `/fmu/in/*` topics.

### QoS — the false-negative trap, confirmed

```
$ ros2 topic info -v /fmu/out/vehicle_local_position
  Reliability: BEST_EFFORT
  Durability:  TRANSIENT_LOCAL
```

A default ROS 2 subscription is RELIABLE/VOLATILE and **will not match**. The node would
then see zero messages against a perfectly healthy stack — the same shape of false
negative as the `compose exec` ROS-environment bug found in `D-02`. Subscribers must
declare BEST_EFFORT + TRANSIENT_LOCAL explicitly.

### Message shapes, as they actually are in v1.16

- `TrajectorySetpoint` uses **`float32[3] position/velocity/acceleration` arrays**, not
  scalar `x`/`y`/`z` fields. Examples written against other PX4 versions get this wrong.
  Its header states *"setting a value to NaN means the state should not be controlled"* —
  so unused fields must be **NaN, not 0.0**. Zeroing the velocity array commands a
  velocity of zero rather than leaving it free.
- `OffboardControlMode` is seven booleans (`position`, `velocity`, `acceleration`,
  `attitude`, `body_rate`, `thrust_and_torque`, `direct_actuator`) selecting which
  setpoint fields PX4 honours.
- `VehicleCommand`: `param1..param7` where **`param5`/`param6` are `float64`** (lat/lon)
  while the rest are `float32`.
- Constants read out of the actual message, not assumed:
  `VEHICLE_CMD_COMPONENT_ARM_DISARM = 400`, `VEHICLE_CMD_DO_SET_MODE = 176`,
  `VEHICLE_CMD_NAV_LAND = 21`, `ARMING_STATE_DISARMED = 1`, `ARMING_STATE_ARMED = 2`,
  `NAVIGATION_STATE_OFFBOARD = 14`, `NAVIGATION_STATE_AUTO_LAND = 18`.
- `VehicleLocalPosition` carries `x`/`y`/`z` in **NED — `z` is down**, plus `xy_valid` and
  `z_valid` flags worth checking before trusting a position.

### `COM_OF_LOSS_T` — the plan's value needs revisiting

```
$ grep -rn COM_OF_LOSS_T /opt/px4/src/modules/commander/*.c
322:PARAM_DEFINE_FLOAT(COM_OF_LOSS_T, 1.0f);
```

Firmware default is **1.0 s**, not the 15 the plan prescribes
(`02_development_plan.md:41`). 1 s is a reasonable safety behaviour and raising it to 15
mostly hides a setpoint stream that is failing. Decision recorded in the conventions doc:
**leave it at the default and make the streaming rate the thing we guarantee.** If CI
proves the nested-Docker RTF deficit genuinely starves the stream, revisit with evidence
rather than pre-emptively.

---

## Decisions taken (detail in `docs/lane-a/conventions.md`)

1. **PX4 surface stays bare and untouched** — `/fmu/in/*`, `/fmu/out/*`, PX4's own names.
2. **A `px4_ns` parameter defaulting to `''`** rather than hard-coded topic strings, so
   multi-vehicle in a later phase is configuration, not a refactor.
3. **Our own topics are namespaced by function** — adopting `/planner/trajectory` and
   `/vlm/target` verbatim from the plan (`02_development_plan.md:155`) rather than inventing
   a parallel scheme, plus `/mission/*` and `/eval/*` which the plan does not name.
4. **Our external interfaces are ROS REP-103 ENU/FLU; PX4's NED is converted in exactly
   one place** — the adapter inside the control node. Phase 2's EGO-Planner and Phase 3's
   cuVSLAM/nvblox are all ROS-frame components; making them each convert is how sign
   errors get in. One conversion, one place, tested.

---

## The arming investigation — four wrong answers before the right one

The node reached offboard mode immediately and then sat in `arm` until it timed out. PX4
said only:

```
WARN  [commander] Arming denied: Resolve system health failures first
```

That message is deliberately generic; **the specific reason is sent to the MAVLink log —
i.e. to the ground station that, in a headless stack, does not exist.** Debugging it meant
getting the reason out some other way.

| # | Hypothesis | How it died |
|---|---|---|
| 1 | `COM_RCL_EXCEPT` (RC-loss exemption for Offboard, bit 2 = 4) | Set it via `PX4_PARAM_`, confirmed applied (`x + COM_RCL_EXCEPT : 4`) — still denied |
| 2 | Stale params baked into the image | The image has **no** `parameters.bson`; it is created at runtime. Ruled out — but worth knowing |
| 3 | CPU starvation on this nested-Docker box | `listener cpuload` → **17.6%**, against a 90% limit. Nowhere near |
| 4 | "ekf2 missing data", seen in the log | Occurred **once**, at boot. A transient, not the blocker |

**What actually found it** was `listener health_report`, which exposes the check results as
bitfields instead of prose:

```
arming_check_error_flags: 1052672   -> 4096 (Communication links) + 1048576 (System)
can_arm_mode_flags:       0         -> cannot arm in ANY mode
```

Decoded against `src/lib/events/enums.json:148`. Two further facts fell out:

- The **System** error vanished on a freshly restarted stack — it was an artifact of a sim
  degraded by my repeated restarts (`ERROR [vehicle_imu] timestamp error`). A stale
  simulator produces failures that look like code bugs.
- **Communication links** survived, and that is the GCS check in
  `rcAndDataLinkCheck.cpp:78-98`.

### The controlled experiment

A heartbeat-only "GCS" — 1 Hz `HEARTBEAT`, no commands, so exactly one variable changes:

| Condition | `gcs_connection_lost` | Result |
|---|---|---|
| No GCS | `true` | denied |
| Heartbeat on **14550** (`udpout`) | `true` — never reached PX4 | denied |
| Heartbeat on **14540** (`udpin`, as `lane-a-fly.py` does) | **`false`** | **armed, flew all 4 waypoints** |

The 14550 attempt failing silently is itself the lesson: PX4's onboard link only answers an
address that has written to it first, so a send-only socket is invisible to it.

### Root cause

`rcAndDataLinkCheck.cpp:81` makes a GCS connection **mandatory for arming** whenever
`NAV_DLL_ACT > 0`:

```cpp
bool gcs_connection_required = _param_nav_dll_act.get() > 0;
NavModes affected_modes = gcs_connection_required ? NavModes::All : NavModes::None;
```

The firmware default is `0`. But the runtime value was **2**:

```
x   NAV_DLL_ACT [650,1131] : 2
```

**I got this wrong once before getting it right.** An earlier grep of the airframe file
showed no `NAV_DLL_ACT`, and I concluded the airframe did not set it — the grep was piped
through `head -20` and stopped at line 48. It is at line 51:

```
/opt/px4/build/px4_sitl_default/etc/init.d-posix/airframes/4001_gz_x500:51:
param set-default NAV_DLL_ACT 2
```

### The fix — supply the datalink, do not disable the check

**First attempt, rejected on review:** set `NAV_DLL_ACT 0` to switch the check off. It
worked, and it was the wrong answer. Requiring a ground-station datalink before arming is
*normal and correct* — it is the link a human uses to take over — and a configuration that
arms without one is not a configuration to carry toward real hardware in Phase 4. Disabling
a safety check because the test environment is missing a component treats the symptom.

(Worth recording that the disable is also non-obvious to implement, since the same trap
will bite anything else that tries to override an airframe default:
`PX4_PARAM_NAV_DLL_ACT=0` **silently fails** — the env hook runs at `rcS:134`, the airframe
at `rcS:231`, so setting a parameter that still holds its firmware default records nothing
and the airframe then wins. The supported hook is `<airframe>.post` at `rcS:351`. Not used
here, but the ordering is a real landmine.)

**What shipped instead:** a `gcs-link` service — a real MAVLink peer holding a real
datalink. `NAV_DLL_ACT` stays at the airframe's 2. It sends `HEARTBEAT` and nothing else:
it is a datalink, not a pilot. Every vehicle command still comes from the ROS 2 controller
over uXRCE-DDS.

### The port: 18570, not 14550 — and the silence that hides it

The service did not work at first, for a reason worth writing down:

```
udp_gcs_port_local=$((18570+px4_instance))              # px4-rc.mavlink:11
mavlink start -x -u $udp_gcs_port_local -r 4000000 -f   # px4-rc.mavlink:14
```

**PX4's GCS MAVLink instance binds 18570.** 14550 is what a ground station conventionally
*listens* on and what this repo publishes — but nothing in PX4 binds it. Proven both ways:

| Port | Result |
|---|---|
| 14550 | bound successfully, **nothing received in 8 s** — PX4 never sends there |
| 18570 | **bind failed, `EADDRINUSE`** — PX4 already holds it |

A heartbeat aimed at 14550 is discarded with no error and no log line; the datalink just
never comes up. That is exactly why the earlier heartbeat experiment on 14550 failed while
the one on 14540 succeeded — PX4's *offboard* instance actively sends to 14540
(`px4-rc.mavlink:4-5`), so binding it receives traffic by accident of configuration.

Compose now publishes 18570 alongside 14550.

### Verified causally, both directions

| Datalink | `gcs_connection_lost` | Flight |
|---|---|---|
| `gcs-link` running | `false` | **armed, 4/4 waypoints, landed, disarmed** |
| `gcs-link` stopped | `true` | **arming denied** — `timeout in state arm` |

The safety check is enforced end to end, on the airframe's own value, and the stack
satisfies it rather than evading it.

---

## The landing bug

With arming solved, the flight reached all four waypoints and then hung in `land`.

Cause: the node kept publishing `OffboardControlMode` + `TrajectorySetpoint` at 10 m while
`VEHICLE_CMD_NAV_LAND` handed control to `AUTO.LAND`. The two fought; the vehicle held
altitude and the state timed out. **Commanding a landing means stopping telling PX4 where to
be** — `LAND` is now excluded from the setpoint-streaming states.

---

## Result — `P1-00` and `P1-02` done

Two consecutive clean flights, then a third recorded to MCAP. **uXRCE-DDS only: no MAVLink,
no GCS, no heartbeat.**

```
armed -> takeoff 10 m -> waypoint 1..4 -> landed and disarmed
waypoint errors: 0.174, 0.056, 0.152, 0.214 m   (accept radius 1.0)
outcome: success
```

**Verified independently from the MCAP**, not from the node's own logging — 5,360 position
samples read back out of the bag:

| Measure | From the bag | Commanded |
|---|---|---|
| Max altitude | **10.18 m** | 10.0 |
| Final altitude | **-0.03 m** | landed |
| North extent | **10.28 m** span | 10.0 |
| East extent | **10.25 m** span | 10.0 |
| Armed samples | 88 / 112 | — |

That also proves `P1-05` is reachable: `rosbag2-storage-mcap` is already in the image and
`-s mcap` produced a 1.47 MB artifact with no extra work.

---

## Open, and deliberately not claimed

- **`COM_RCL_EXCEPT=4` was ablated — it is NOT needed.** Removed it, restarted, flew:
  success. Gone from the config rather than left in as cargo. **No PX4 parameter is
  overridden by this stack at all** — the airframe's values stand.
- **RESOLVED — how QGC connects, and why 14550 looked dead.** Decoding `/proc/net/udp` in
  both containers: QGC binds **14550** (`0x38D6`), PX4 binds **18570** (`0x488A`). QGC
  auto-discovers by sending *to* 18570; PX4 then learns QGC's address and replies *to*
  14550. So 14550 carries traffic only after a GCS has spoken first — which is why binding
  it on a fresh stack received nothing, and why the Phase 0 demo legitimately saw QGC on
  14550. Both ports are published; neither is redundant.
- **`colcon test` silently ran zero tests.** It falls back to `python3 -m unittest`, which
  cannot collect pytest-style test *functions*: "NO TESTS RAN", non-zero exit, no coverage.
  `colcon test --python-testing pytest` gives **8 tests, 0 failures**. Plain
  `pytest test/test_frames.py` always worked, which is exactly why the gap was invisible —
  worth remembering for `P1-07`, where a CI job that runs no tests still goes green-ish.
- **The `ros2` container loses the built workspace on every `compose down`.** The build
  lives in the container filesystem, not a volume, so it is rebuilt by hand each time.
  `P1-03` should make this a mount or a build step.
- **A degraded simulator fakes code bugs.** After many restarts PX4 emitted
  `ERROR [vehicle_imu] timestamp error` and an extra System arming failure that vanished on
  a fresh stack. Restart before believing a weird failure.
