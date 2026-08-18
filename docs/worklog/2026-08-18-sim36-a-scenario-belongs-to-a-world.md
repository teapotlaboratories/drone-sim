# SIM-36 — a scenario belongs to a world, and the harness now says so

`resolve_world()` was one line:

```python
world = (cli_world or "").strip() or str(scenario.get("world", "") or "").strip()
```

The CLI wins and nothing compares the two — which made the function's own docstring, *"its results
mean nothing if it is flown somewhere else"*, a statement of intent the code did not keep.

## The measured incident

On 2026-08-17 a 10-seed gate ran `square-10m.yaml` — which declares Blocks and describes itself as
*"an empty world. No obstacles by design"* — against CitySample via `--world`. It logged **nothing**,
and produced ten failures whose collision counts were partly an artefact of landing an empty-box
mission on a busy pavement.

**Why it was silent:** waypoints are ENU relative to HOME, so a 10 m square is geometrically valid
over a test box, a city street, or a motorway. The run does not fail. It stops meaning anything —
which is worse, because a failure gets investigated and a meaningless success gets quoted.

## What shipped

Disagreement exits, naming both worlds and explaining why it matters. `--force-world` overrides and
is recorded, because flying one mission across several worlds is legitimate; it just has to be said
out loud.

The false-alarm case got as much attention as the guard itself: the same world spelled relatively
and absolutely must **not** trip it. A guard that cries wolf on two spellings of one path trains
people to pass `--force-world` reflexively, which defeats it more thoroughly than not having it.

## What review found, and it was the important half

**The guard had another door.** It only fires when `--world` is passed. But `--no-restart` and
`--reuse` never pass it — and that is the flow the project's *own* flight-test rule prescribes
(`sim_up.sh --display`, then `run_scenario.py … --no-restart`). So a scenario could still be flown
in whatever happened to be running, silently, by the route the documentation recommends.

Closed by asking the container. `sim_up.sh` bind-mounts the world's directory at `/world`, so the
running world is recoverable via `docker inspect`; no `/world` mount means the vendored Blocks
environment, which is the script's own default. `running_world()` returns `""` when docker cannot
answer — an unknown must not masquerade as a match, and must not block a run either.

**`world_forced` reported the flag, not the fact.** `bool(a.force_world)` is true even with no
`--world` at all: nothing was overridden, the scenario's own world flew, and the artifact still
carried a marker inviting a reader to discount the run. It now means what it says — a `--world` was
given *and* it differed — and the report records `world` and `world_declared` so the field is
self-describing.

**`world: default` got misdirecting advice.** A legacy scenario hit the mismatch message, whose
remedy is *"fly the world the scenario declares"* — impossible, since `default` is rejected ten
lines later. The rejection moved above the comparison.

**One comparison, not two.** The guard and the provenance both had to answer "is this the same
world", and two copies of that answer is the exact class of drift `SIM-34` was made of. Extracted to
`same_world()`.

## The half that is documentation, deliberately

**Bring-your-own-world means bring-your-own-scenario** — but the harness cannot know where a landing
site is in someone else's world, so enforcing it would be guessing. `scenarios/README.md` gained a
section on what to change when adapting a mission; `docs/worlds.md` (and its render) send the
world-bringer there.

The item listed first is the **landing point**. `square-10m.yaml` returns to its takeoff corner,
which is harmless in an empty box and is exactly how a landed aircraft ends up under a crowd in a
city. That is the substance of `SIM-35`, addressed in the pairing rather than in the collision
scoring — which is where the owner parked it.

## Verified

| case | result |
|---|---|
| `square-10m.yaml` + `--world CitySample` | **exit 1**, mismatch message |
| same, `--force-world` | proceeds |
| `--world` matching the declared world | silent, including relative vs absolute |
| scenario-declared world, no `--world` | unchanged |
| `world: default` | its own message, not the mismatch |
| `running_world()` with docker unavailable | `""` — neither a match nor a block |

157 tests. A 1-seed Blocks gate then flew the normal path clean: PASS, worst 0.786 m,
`world_forced: false`, chase recorded. Torn down and verified afterwards.
