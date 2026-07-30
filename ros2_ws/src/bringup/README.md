# `bringup` — top-level launch composition

**Status:** placeholder. Created in **Phase 1**.

`sim.launch.py` and `real.launch.py` plus shared includes, parameterized by
`use_sim_time` and namespace.

**The invariant this package exists to protect:** the ROS 2 graph must be *identical*
across sim and real — same topic names (`/fmu/*`, `/vlm/target`,
`/planner/trajectory`), same launch composition. **Only the transport is swapped.**
`use_sim_time:=true` is set in sim only. Topic and namespace conventions freeze in
Phase 1 and must reach the aircraft unchanged
(`docs/reference/02_development_plan.md:252`, Standing Order 2).
