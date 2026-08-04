# `evaluation` — a sketch, not a package

**There is no package here.** This directory contains this README and nothing else — no
`package.xml`, no source, no tests. **colcon has never built it.** Said plainly so nobody
goes looking for a node that does not exist.

**Evaluation currently happens on the host, not in the graph.**
[`scripts/run_scenario.py`](../../../scripts/run_scenario.py) runs one seeded mission and
emits a JSON result beside its MCAP; [`scripts/run_gate.py`](../../../scripts/run_gate.py) is
the simulator's flight gate (`SIM-07`) — N seeded runs, a success rate, and a verdict per
run. The scoring semantics that matter are already there:

- **VOID is not FAIL.** A run whose PX4 EKF origin was stale never measured the flight code,
  so it is excluded from the success rate — and it still blocks the criterion, because a gate
  that can quietly score a mis-ordered stack is worse than none.
- **A waypoint scores only after settling**, not on first entry to the acceptance radius.
- **Every run keeps its bag**, because runs are not reproducible and a failing seed cannot be
  replayed.

## What would move in here, and what it would buy

An in-graph metrics node — subscribing to `/mission/*` and `/fmu/out/*` and publishing the
scoring live — so a bag scores itself and the same numbers are available during a run rather
than only after it. That is the piece the host scripts cannot do.

If you build benchmark reproduction on top of the simulator (an application, not part of it),
the metric definitions and the reason there are so many of them are recorded in
[`../../../docs/history/reference/`](../../../docs/history/reference/): SR, SPL, NE, OSR,
collision count / CR, time-to-target, path length, intervention rate.

**One rule survives from that work regardless of who implements it: the success threshold is
a parameter, not a constant.** AerialVLN/OpenFly use **20 m**; Fly0 and OnFly use **5 m**.
Both are correct, and a success rate quoted without its threshold is not a number.
