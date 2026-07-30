# `perception` — depth, VIO glue, mapping glue

**Status:** placeholder. Created in **Phase 2**.

Thin wrappers around pinned upstreams — **do not reimplement SLAM or mapping**:

- `isaac_ros_visual_slam` (cuVSLAM) — VIO, ROS 2 Jazzy, Jetson-supported
- `isaac_ros_nvblox` — GPU mapping → Nav2 costmap + 3D reconstruction
- **OctoMap** — CPU fallback so CI can run without a GPU runner

Depth back-projection (2D VLM waypoint + depth + intrinsics → 3D goal) lives here and
is unit-tested off-target. Use **sensor depth, not VLM-estimated depth** — Fly0's
ablation drops SR from 70.43% to 56.47% without it
(`docs/reference/02_development_plan.md:193`, Standing Order 4).

Measure before trusting: IMU–camera timestamp jitter and rate stability are the
recurring failure mode across all sims (`docs/reference/01_sim_stack_report.md:31`).
