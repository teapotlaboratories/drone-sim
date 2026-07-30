# `vendor/` — pinned third-party checkouts

Third-party trees land here via **vcstool**, driven by [`../.repos`](../.repos). Prefer
`.repos` over git submodules (`.ai/AGENTS.md:316`).

```bash
vcs import vendor < .repos
```

**Least-destructive vendor edits.** Keep every vendored tree **byte-identical to
upstream** wherever possible and push integration into the *build*, *launch*, or
*config* layer instead:

- Exclude at the build layer rather than deleting a unit.
- Guard target-specific behaviour behind a build flag or launch arg.
- Leave upstream's README, license, tests, and build scripts in place.

**Every deviation is recorded** in `docs/vendor/<component>.md`, so upstream rebases stay
clean and the divergence is auditable. Notes live there rather than inside the vendored
tree because these are **nested git clones** — a file placed inside one is owned by that
clone and can never be committed to this repo.

Expected occupants: `PX4-Autopilot-v1.16`, `PX4-Autopilot-v1.14.3`, `px4_msgs`,
`px4_ros_com`, `Micro-XRCE-DDS-Agent`, `PegasusSimulator`, `ego-planner-swarm`,
`Cosys-AirSim`, `IsaacSim-ros_workspaces`.

This directory is git-ignored except for this README and per-component notes.
