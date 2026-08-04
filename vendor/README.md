# `vendor/` — pinned third-party checkouts

Third-party trees land here via **vcstool**, driven by [`../.repos`](../.repos). Prefer
`.repos` over git submodules (`.ai/AGENTS.md` → "Adapting upstream code & version pinning").

```bash
vcs import vendor < .repos
```

| Tree | What it is |
|---|---|
| `PX4-Autopilot-v1.16` | PX4 v1.16.0 — SITL **and** the tree real Pixhawk 6C firmware is flashed from |
| `Cosys-AirSim` | the renderer plugin, the ROS 2 wrapper, the Python RPC client, and the Blocks world `sim_up.sh` defaults to |
| `Micro-XRCE-DDS-Agent` | v2.4.3 — the PX4↔ROS 2 bridge |
| `px4_msgs`, `px4_ros_com` | branch-matched to the firmware (`release/1.16`); a mismatch is silent |
| `tools/` | a QGroundControl AppImage, left from the original native install (2026-07-28). **Nothing in the stack uses it** — QGC is baked into `drone-sim/qgc:v1.16.0` and checksum-verified at build |

Isaac Sim, Pegasus and the second PX4 v1.14.3 tree are retired and stay commented out in
`.repos`; the EGO-Planner tree is listed but not started. Reopening either is an uncomment,
not an archaeology exercise.

**Least-destructive vendor edits.** Keep every vendored tree **byte-identical to upstream**
wherever possible and push integration into the *build*, *launch* or *config* layer instead:

- Exclude at the build layer rather than deleting a unit.
- Guard target-specific behaviour behind a build flag or launch arg.
- Leave upstream's README, license, tests and build scripts in place.

`Cosys-AirSim` is the live proof that this is workable rather than aspirational: it carries
three real upstream defect fixes, and `git status --porcelain vendor/` still reports zero
modifications — the patches live in `patches/cosys-airsim/` and are applied by
`scripts/build_airsim_wrapper.sh` to a **container-local copy** at `/airsim_root`. Two more
deviations that could have been patches (five uninitialized sensor-timer periods, and a
`/clock` topic published where nobody looks) are handled in the launch layer instead.

**Every deviation is recorded** in `docs/vendor/<component>.md` — today
[`cosys-airsim.md`](../docs/vendor/cosys-airsim.md) and
[`micro-xrce-dds-agent.md`](../docs/vendor/micro-xrce-dds-agent.md) — so upstream rebases
stay clean and the divergence is auditable. The notes live there rather than inside the
vendored tree because these are **nested git clones**: a file placed inside one is owned by
that clone and can never be committed to this repo.

This directory is git-ignored except for this README.
