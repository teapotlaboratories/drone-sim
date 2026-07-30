# `interfaces` — message & service definitions

**Status:** placeholder. Created in **Phase 3** (see `docs/drone-sim-todo.md`).

Owns the message contracts that decouple the slow VLM from the fast tracker:

- `TargetWaypoint{header, geometry_msgs/PointStamped goal_3d, float32 confidence,
  uint8 source(VLM|TRACK), builtin_interfaces/Duration ttl}`
- `TrackerGoal` — published at 50 Hz by the tracker
- `VlmQuery` / `VlmResult`

The `ttl` field is a **safety property**, not metadata: the tracker's watchdog
invalidates the goal after `ttl` expires and falls back to hover/hold, so the tracker
never blocks on the VLM (`docs/reference/02_development_plan.md:162`, Standing Order 3).

Verified by unit test — message contract and watchdog behaviour are host-side logic.
