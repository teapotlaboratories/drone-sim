# `state_estimation` — EKF2 params & external-vision odometry

**Status:** placeholder. Created in **Phase 3**.

Publishes VIO odometry to `/fmu/in/vehicle_visual_odometry` at **30–50 Hz** and owns the
EKF2 parameter sets for GPS-denied flight: `EKF2_EV_CTRL`, `EKF2_HGT_REF`,
`EKF2_EV_DELAY`, `EKF2_EV_NOISE_MD`, `EKF2_EVP/EVV/EVA_NOISE`
(`docs/reference/02_development_plan.md:203`).

**Known failure mode:** EKF2 "drift-to-origin" on EV-only input indicates a frame or
parameter misconfiguration, not sensor noise (`PX4/PX4-Autopilot#19859`). Verify with
QGC MAVLink Inspector (`MAV_ODOM_LP=1`).

Acceptance: VIO-only hover drift **< 1 m over 60 s**, validated against Lane A lockstep
as a control.
