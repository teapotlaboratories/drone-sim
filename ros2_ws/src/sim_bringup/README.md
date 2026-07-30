# `sim_bringup` — simulator spawn & scenario wiring

**Status:** placeholder. Created in **Phase 1**.

Per-lane simulator bringup and vehicle spawn:

- **Lane A** — Gazebo Harmonic via `make px4_sitl gz_x500`. Use the **single-command**
  launch, not the manual split-terminal/standalone method: the latter reproducibly
  triggers "bobbing" plus `Accel #0 fail: TIMEOUT!`
  (`docs/reference/02_development_plan.md:15`).
- **Lane B** — Isaac Sim 5.1 + Pegasus standalone scripts (PX4 **v1.14.3**, MAVLink SITL).
- **Lane C** — UE5.5 + Cosys-AirSim (Phase 4, benchmark reproduction only).

Also owns the seeded scenario runner that reads `../../../scenarios/*.yaml`.
