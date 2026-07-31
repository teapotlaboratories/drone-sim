# `control` — PX4 offboard setpoint node

**Status:** ✅ working (2026-07-30, `P1-02`). First flying code in the project.

Publishes `OffboardControlMode` + `TrajectorySetpoint` and sends `VehicleCommand` over
uXRCE-DDS (PX4 v1.16.x + `px4_msgs release/1.16`).

Acceptance: takeoff → 4-waypoint square → land, **SR = 100% over 10 seeded runs**,
headless, with an MCAP artifact. A single lucky pass is not a pass.

Lockstep desync trips the PX4 offboard-loss failsafe. The plan suggests
`param set-default COM_OF_LOSS_T 15` (`docs/reference/02_development_plan.md:41`); we
**keep the v1.16.0 firmware default of 1.0 s** instead — raising it hides a starved
setpoint stream rather than fixing it, and on real hardware that is 15 s of uncommanded
aircraft. Reasoning: `docs/lane-a/conventions.md` §6.

Conventions this package must obey: [`docs/lane-a/conventions.md`](../../../docs/lane-a/conventions.md).

---

## Layout

| File | What it is |
|---|---|
| `control/offboard_control.py` | the state machine: arm → offboard → takeoff → waypoints → land |
| `control/frames.py` | **the project's single ENU↔NED conversion point** — see `docs/lane-a/conventions.md` §3 |
| `test/test_frames.py` | off-target unit tests for the conversion (no simulator needed) |

## Run it

With the Lane A stack up (`docker compose -f docker/compose.yaml up -d`):

```bash
docker compose -f docker/compose.yaml exec ros2 bash -lc '
  cd /ros2_ws && . install/setup.bash
  ros2 run control offboard_control --ros-args -p result_path:=/out/mission-result.json'
```

Parameters: `px4_ns` (default `''`), `takeoff_altitude` (10.0 m, ENU), `square_side` (10.0 m),
`accept_radius` (1.0 m), `hold_seconds` (2.0), `setpoint_rate_hz` (20.0),
`state_timeout_s` (60.0), `result_path`.

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

## Two PX4 behaviours this node exists downstream of

- **Arming requires a MAVLink ground-station datalink**, and that check is deliberately
  left enforced (`NAV_DLL_ACT` stays at the airframe's 2). The stack satisfies it with the
  `qgc` compose service rather than disabling it — a config that arms without an
  operator link is not one to carry to real hardware. Stop `qgc` and this node fails
  at `arm`, by design.
- **Landing means stopping the setpoint stream.** Continuing to publish offboard setpoints
  after `VEHICLE_CMD_NAV_LAND` fights `AUTO.LAND`; the vehicle holds altitude and the state
  times out.

Full account: [`docs/worklog/2026-07-30-phase-1-offboard.md`](../../../docs/worklog/2026-07-30-phase-1-offboard.md).
