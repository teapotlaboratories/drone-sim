# `planning` — a sketch, not a package

**There is no package here.** This directory contains this README and nothing else — no
`package.xml`, no source, no tests. **colcon has never built it.**

**A planner is an application built *on* the simulator, not part of it.** The simulator's job
is to make one credible: metric depth and GPU-LiDAR at known rates, a `/clock` the whole graph
can share, ground-truth object transforms to score against, and an offboard control path that
accepts setpoints at 20 Hz and is the same one a real Pixhawk accepts. All of that exists. A
planner does not.

## If you put one here

The obvious candidate is **EGO-Planner**, the local planner the Fly0 line of work uses — a
50 Hz control loop with `d_safe = 0.5 m` and `v_max = 4.0 m/s`. Two facts that decide how the
work starts:

- **Start from the EGO-Swarm ROS 2 branch with `drone_id=0`.** The canonical
  `ZJU-FAST-Lab/ego-planner` is ROS 1 / catkin / Ubuntu ≤20.04 and will not build against
  Jazzy. The tree is listed but not activated in [`../../../.repos`](../../../.repos).
- **A substantial port ships a code-map doc** — a function-level, side-by-side new-code ↔
  upstream mapping (`file:line` ↔ `file:line`) plus a deliberate-divergences section, with
  **every cited line grepped in both trees, never written from memory**
  (`.ai/AGENTS.md` → "Adapting upstream code & version pinning").

Whatever lands here consumes the perception topics as they actually are, not as upstream
documents them: **NWU frames**, a **polled** IMU with duplicate timestamps, and no lockstep —
so a control loop must be robust to a free-running simulator rather than assume a fixed step.
