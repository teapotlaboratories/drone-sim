# `sim/` — per-lane simulator assets

Worlds, scenes, and simulator-side configuration for the three lanes. Large binary
assets (Isaac USD scenes, UE5 projects, Cesium tiles) do **not** live here — they go on
the 7 TB external drive under `/var/mnt/…` and are referenced by path
(`.ai/AGENTS.md:460`).

| Dir | Lane | Simulator | Phase |
|---|---|---|---|
| `gazebo/` | A | Gazebo Harmonic + PX4 v1.16.x | 0–2 |
| `isaac/` | B | Isaac Sim 5.1 + Pegasus v5.1.0 + PX4 v1.14.3 | 3 |
| `ue5/` | C | UE5.5 + Cosys-AirSim | 4 |

Build in strict lane order. Lane C is high-risk/optional
(`docs/reference/02_development_plan.md:4`).
