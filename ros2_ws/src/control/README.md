# `control` — PX4 offboard setpoint node

**Status:** placeholder. Created in **Phase 1**. First flying code in the project.

Publishes `OffboardControlMode` + `TrajectorySetpoint` and sends `VehicleCommand` over
uXRCE-DDS (PX4 v1.16.x + `px4_msgs release/1.16`).

Acceptance: takeoff → 4-waypoint square → land, **SR = 100% over 10 seeded runs**,
headless, with an MCAP artifact. A single lucky pass is not a pass.

Lockstep desync trips the PX4 offboard-loss failsafe — set
`param set-default COM_OF_LOSS_T 15` (`docs/reference/02_development_plan.md:41`).
