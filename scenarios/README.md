# `scenarios/` — seeded missions the runner and the gate consume

A scenario is one YAML file describing a mission, what to record, and what counts as
reaching a waypoint. It is the input to two things and nothing else:

```bash
./scripts/run_scenario.py scenarios/square-10m.yaml --seed 3   # one run, one JSON result + MCAP
./scripts/run_gate.py     scenarios/square-10m.yaml            # 10 seeded runs, SR must be 100%
```

Both drive [`../scripts/sim_up.sh`](../scripts/sim_up.sh) — the simulator's only supported
bring-up. There is no compose file to point them at.

| Key | Meaning |
|---|---|
| `mission.takeoff_altitude_m`, `mission.waypoints_enu` | the flight, in **ENU relative to HOME** (`docs/conventions.md` §3) |
| `record_topics` | what each run writes to MCAP — a property of the scenario, not of the runner |
| `tolerances` | `accept_radius_m`, `hold_seconds`, `state_timeout_s` — a waypoint scores only after settling, so a fly-through does not count |

Shipped today: `square-10m.yaml` — takeoff, a 4-waypoint square, land.

## What a seed actually controls — read this before quoting a success rate

**A seed moves the spawn pose and nothing else.** The retired Gazebo harness seeded wind
and vehicle mass through a generated world overlay; this simulator has no equivalent yet,
because environmental diversity needs Cosys-AirSim's own wind API (`SIM-07` in
[`../docs/todo.md`](../docs/todo.md)). Ten seeded runs are therefore closer to ten
repeats — still worth running, because flaky failures surface under repetition, but
**do not describe a gate run as covering varied conditions.**

**And a seed is not a replay.** The stack is not bit-reproducible — measured, not assumed:
two back-to-back runs with identical configuration gave waypoint errors
`[0.225, 0.104, 0.154, 0.204]` and `[0.118, 0.076, 0.158, 0.187]`. A failing seed cannot be
re-run to reproduce its failure, which is exactly why every run keeps its MCAP: the bag is
the only evidence the failure leaves.

Large scene payloads stay on the external drive; this directory holds only the YAML that
references them.
