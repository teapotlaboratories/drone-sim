# `control` — the offboard controller, and the project's frame conversion

**Status:** ✅ working. The first flying code in the project (2026-07-30), and the code that
flew the simulator 4/4 waypoints on 2026-08-01.

Publishes `OffboardControlMode` + `TrajectorySetpoint` and sends `VehicleCommand` over
uXRCE-DDS (PX4 v1.16.x + `px4_msgs release/1.16`). **This package is the part that does not
know it is in a simulator** — the same node, the same topics and the same QoS reach a real
Pixhawk; only the transport is swapped.

Conventions this package must obey:
[`docs/conventions.md`](../../../docs/conventions.md).

## Layout

| File | What it is |
|---|---|
| `control/offboard_control.py` | the state machine: arm → offboard → takeoff → waypoints → land, publishing `MissionStatus` throughout and `MissionResult` at the end |
| `control/frames.py` | **the project's single ENU↔NED (and NWU→ENU) conversion point** — `docs/conventions.md` §3 |
| `control/park_tour.py` | an example mission (`SIM-16`): fly a closed circuit of the world over ROS 2, yaw facing along each leg. Meant to be read start to finish |
| `test/test_frames.py` | off-target unit tests for the conversion (no simulator needed) |

## Run it

With the stack up (`./scripts/sim_up.sh`):

```bash
docker exec sim-ros2 bash -lc '
  cd /ros2_ws && . install/setup.bash
  ros2 run control offboard_control --ros-args -p result_path:=/out/mission-result.json'
```

Parameters: `px4_ns` (default `''`), `takeoff_altitude` (10.0 m, ENU), `square_side` (10.0 m),
`accept_radius` (1.0 m), `hold_seconds` (2.0), `setpoint_rate_hz` (20.0),
`state_timeout_s` (60.0), `waypoints_enu`, `result_path`. `scripts/run_scenario.py` drives
exactly this command with the scenario's values, and `scripts/run_park_tour.sh` does the same
for `park_tour`.

## Test it

```bash
colcon test --packages-select control --python-testing pytest
colcon test-result --verbose
```

> **`--python-testing pytest` is required, not optional.** Without it colcon falls back to
> `python3 -m unittest`, which cannot collect pytest-style test *functions* and reports
> **"NO TESTS RAN"** while exiting non-zero — zero coverage that looks like a broken build
> rather than a missing flag. Plain `pytest test/test_frames.py` from this directory works
> either way, which is what makes the gap easy to miss.

## Behaviours this node exists downstream of — each one fails silently

- **QoS must match or nothing moves.** PX4's `/fmu/in` subscribers are `BEST_EFFORT` +
  `VOLATILE`; a `RELIABLE` publisher matches nothing and every command is dropped against a
  perfectly healthy stack. `/fmu/out` is `BEST_EFFORT` + `TRANSIENT_LOCAL`, and a default
  `RELIABLE` subscription sees silence.
- **It is `vehicle_status_v1`, not `vehicle_status`.** PX4 v1.16 renamed it; the old name
  matches nothing while looking entirely correct.
- **Setpoints must stream *before* OFFBOARD is requested, and keep streaming.** PX4 rejects
  the mode change without an existing stream, and drops out of OFFBOARD if it stops for
  ~0.5 s.
- **Arming requires a MAVLink ground-station datalink**, and that check is deliberately left
  enforced (`NAV_DLL_ACT` stays at the airframe's 2). The stack satisfies it with the
  `sim-qgc` container rather than disabling it — a config that arms without an operator link
  is not one to carry to real hardware. Stop that container and this node fails at `arm`, by
  design.
- **Landing means stopping the setpoint stream.** Continuing to publish offboard setpoints
  after `VEHICLE_CMD_NAV_LAND` fights `AUTO.LAND`: the vehicle holds altitude and the state
  times out.
- **Position and velocity are mutually exclusive per setpoint.** For velocity control the
  position fields must be NaN, or PX4 prefers position and silently ignores the velocity.

**`COM_OF_LOSS_T` stays at the v1.16.0 firmware default of 1.0 s.** The retired reference plan
suggested raising it to 15 s to survive a starved setpoint stream; raising it hides the
starvation rather than fixing it, and on real hardware that is 15 s of uncommanded aircraft.
Reasoning: `docs/conventions.md` §6.

**One failure mode is not this node's, and looks exactly like it.** A stale PX4 EKF origin
makes the vehicle report tens of metres of altitude while sitting on the ground — measured
once at 35.167 m — so the controller commands a descent into the ground and nothing moves.
That cost a full day before it was understood. `scripts/sim_up.sh` verifies and repairs the
origin before declaring a stack usable, and `scripts/run_gate.py` scores such a run **VOID**
rather than FAIL, because it never measured the flight code.

Full account:
[`docs/worklog/2026-07-30-phase-1-offboard.md`](../../../docs/worklog/2026-07-30-phase-1-offboard.md)
and
[`docs/worklog/2026-08-01-c09-lockstep-dead-and-the-35m-offset.md`](../../../docs/worklog/2026-08-01-c09-lockstep-dead-and-the-35m-offset.md).
