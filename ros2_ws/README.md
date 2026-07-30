# `ros2_ws/` — ROS 2 Jazzy workspace

The application-side colcon workspace. Everything here is **original glue code**: the
shared ROS 2 graph, launch composition, message contracts, and the scenario/eval
harness. Third-party trees are *not* vendored here — they are pinned in
[`../.repos`](../.repos) and checked out under [`../vendor/`](../vendor/).

Built with system ROS 2 Jazzy (**Python 3.12**). Isaac Sim's Python 3.11 workspace is a
*separate* build — the two halves meet over DDS, never in one interpreter
(`docs/reference/02_development_plan.md:13`).

```bash
cd ros2_ws && colcon build --symlink-install && source install/setup.bash
```

Package layout follows the monorepo structure in
`docs/reference/02_development_plan.md:141`. No package is created yet — Phase 0 is
environment and version lock only.
