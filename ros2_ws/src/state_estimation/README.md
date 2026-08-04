# `state_estimation` — a sketch, not a package

**There is no package here.** This directory contains this README and nothing else — no
`package.xml`, no source, no tests. **colcon has never built it.**

**Today the estimator is PX4's, unmodified.** EKF2 runs inside PX4 SITL with GPS, and the
graph consumes its output as the 24 `/fmu/out/*` topics — byte-identical to what real
hardware publishes. Nothing in this repo feeds PX4 an external estimate.

## What would go here

Publishing visual odometry to `/fmu/in/vehicle_visual_odometry` at **30–50 Hz**, and owning
the EKF2 parameter sets that make PX4 believe it: `EKF2_EV_CTRL`, `EKF2_HGT_REF`,
`EKF2_EV_DELAY`, `EKF2_EV_NOISE_MD`, `EKF2_EVP/EVV/EVA_NOISE`
(`../../../docs/history/reference/02_development_plan.md:221`). That is what GPS-denied flight
needs, and it is the one parameter set that must be **identical in sim and on the aircraft** —
which is why it belongs in a package rather than in a simulator settings file.

## Three things measured here that any such work inherits

- **A stale EKF origin looks exactly like a control bug.** PX4 sets its local origin once; if
  it initialises before the simulated vehicle has settled, every altitude PX4 reports is
  silently offset for the whole session — measured once at **35.167 m** while the vehicle sat
  on the ground. `scripts/sim_up.sh` verifies and repairs the origin, and `run_gate.py` scores
  an unrepaired run **VOID** rather than FAIL.
- **EKF2 "drift-to-origin" on EV-only input is a frame or parameter misconfiguration, not
  sensor noise** (`PX4/PX4-Autopilot#19859`). Verify with the QGC MAVLink Inspector
  (`MAV_ODOM_LP=1`) before blaming the estimator.
- **The frame conversion is the likeliest defect.** The wrapper's topics are **NWU**, PX4 is
  **NED**, and the project's interfaces are **ENU** — three frames, and the conversion happens
  in `control/frames.py` and nowhere else (`docs/conventions.md` §3). A double conversion is
  the identity on x and a sign flip on z: the vehicle accepts the command and flies into the
  ground.

**The reference to score against is the simulator's ground truth**, not a lockstep control run
— `"LockStep": true` is dead code in Cosys-AirSim, so there is no deterministic stepping to
compare against. `simGetGroundTruthKinematics` over RPC gives the true pose (it is what
`sim_up.sh` polls to detect a settled vehicle). An acceptance bar in the spirit of the
original plan — **VIO-only hover drift < 1 m over 60 s** — is measurable that way.
