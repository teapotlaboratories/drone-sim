# `scenarios/` — seeded worlds & instruction sets

**Status:** placeholder. Populated in **Phase 1–2**, extended in **Phase 4**.

Scenario format (`docs/reference/02_development_plan.md:173`):

```yaml
{ world, seed, spawn, goal, instruction }
```

**Seeds are the point.** Acceptance is a success rate over N seeded runs, never a single
pass — SR=100% over 10 runs (Phase 1), 0 collisions over 20 cluttered runs (Phase 2),
SR≥50% over a 20-episode set (Phase 3).

Phase 4 ingests AerialVLN/OpenFly instruction sets here. Large scene payloads stay on
the external drive; this directory holds the YAML that references them.
