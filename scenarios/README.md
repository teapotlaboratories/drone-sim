# `scenarios/` — seeded missions the runner and the gate consume

A scenario is one YAML file describing a mission, what to record, and what counts as
reaching a waypoint. It is the input to two things and nothing else:

```bash
./scripts/run_scenario.py scenarios/square-10m.yaml --seed 3 --outdir out   # one run, one JSON result + MCAP
./scripts/run_gate.py     scenarios/square-10m.yaml --outdir out   # 10 seeded runs, SR must be 100%
```

Both drive [`../scripts/sim_up.sh`](../scripts/sim_up.sh) — the simulator's only supported
bring-up. There is no compose file to point them at.

| Key | Meaning |
|---|---|
| `world` | the `.uproject` this mission is written for — **enforced**, see below |
| `mission.takeoff_altitude_m`, `mission.waypoints_enu` | the flight, in **ENU relative to HOME** (`docs/conventions.md` §3) |
| `record_topics` | what each run writes to MCAP — a property of the scenario, not of the runner |
| `tolerances` | `accept_radius_m`, `hold_seconds`, `state_timeout_s` — a waypoint scores only after settling, so a fly-through does not count |

Shipped today: `square-10m.yaml` — takeoff, a 4-waypoint square, land.

## A scenario belongs to a world, and the pairing is enforced          (`SIM-36`)

`world:` is not decoration. A scenario's waypoints are chosen **for** its world, and they are
**ENU relative to HOME** — so flying them somewhere else does not fail, it stops meaning
anything. A 10 m square is geometrically valid over an empty test box, a city street or a
motorway; only one of those makes the result worth quoting.

So **`--world` disagreeing with a scenario's `world:` is an error**:

```
scenario/world mismatch: this scenario declares
    world: vendor/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject
and --world says
    …/worlds/CitySample/CitySample.uproject
```

`--force-world` overrides it and is recorded in the run's provenance (`world_forced` in the gate
report), because flying one mission across several worlds is a legitimate thing to want — it just
has to be said out loud.

**This was measured, not imagined.** On 2026-08-17 a 10-seed gate ran `square-10m.yaml` — which
declares Blocks and describes itself as *"an empty world. No obstacles by design"* — against
CitySample via `--world`, logged **nothing**, and produced ten failures whose collision counts were
partly an artefact of landing an empty-box mission on a busy pavement. See
`docs/worklog/2026-08-17-two-gates-blocks-passes-city-does-not.md`.

### Bringing your own world means bringing your own scenario

If you supply a `.uproject`, supply a scenario for it. Copy `square-10m.yaml` and change:

- **`world:`** — your `.uproject`.
- **`mission.waypoints_enu`** — a route that means something *there*. Clear the buildings; do not
  fly through the geometry the world's own designer put in your way.
- **the landing point** — the last waypoint is where the aircraft comes down. Put it somewhere a
  real aircraft could land: not a traffic lane, not a crowd. `square-10m.yaml` returns to its
  takeoff corner, which is fine in an empty box and is how a landed drone ends up under
  pedestrians in a city.
- **`spawn:` and `origin_geopoint:`** — where the vehicle starts and where the world sits on Earth.
- **`tolerances.state_timeout_s`** — a bigger world streams for longer.

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
