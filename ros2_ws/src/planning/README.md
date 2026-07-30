# `planning` — EGO-Planner ROS 2 port + PX4 offboard bridge

**Status:** placeholder. Created in **Phase 2**. Highest-effort item in the phase.

EGO-Planner is what Fly0 uses: 50 Hz control loop, ~0.5 Hz MLLM re-grounding,
`d_safe=0.5 m`, `v_max=4.0 m/s`. Start from the **EGO-Swarm ROS 2 branch** with
`drone_id=0` — the canonical `ZJU-FAST-Lab/ego-planner` is ROS 1 / catkin / Ubuntu ≤20.04
(`docs/reference/02_development_plan.md:19`).

**This port ships a code-map doc.** A function-level, side-by-side new-code ↔ upstream
mapping (`file:line` ↔ `file:line`) at `docs/planning/ego-planner-ros2-code-map.md`, with
a deliberate-divergences section. **Every cited line is grepped in both trees, never
written from memory** (`.ai/AGENTS.md:319`).

Fallback if the port slips >1 week: a ROS 1 bridge container.
