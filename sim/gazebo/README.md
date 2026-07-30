# Lane A — Gazebo Harmonic worlds & models

**Status:** placeholder. Populated in **Phase 1–2**.

The CI/iteration backbone: fast, headless, lockstep, CPU-bound. This is the default
proving ground for every flight/control change.

Custom SDF worlds and the obstacle/clutter scenario library. PX4 airframe targets to
enumerate **at build time** (do not trust a remembered list) under `Tools/simulation` +
PX4-gazebo-models: `gz_x500`, `gz_x500_depth`, `x500_lidar_2d`, `x500_lidar_front`,
`x500_lidar_down` (`docs/reference/02_development_plan.md:48`).

**Transport must be constrained to loopback.** Gazebo multicast flooding the host
network is a documented root cause of the Accel/Mag TIMEOUT failures
(`PX4/PX4-Autopilot#24595`).
