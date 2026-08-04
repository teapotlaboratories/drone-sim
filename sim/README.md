# `sim/` — simulator-side assets

Vehicle, sensor and scene configuration for the simulator this repo builds: **Unreal
Engine 5.8 + Cosys-AirSim**. Large binary assets (UE5 projects, Cesium tiles, captured
imagery) do **not** live here — they go on the 7 TB external drive under `/var/mnt/…` and
are referenced by path (`.ai/AGENTS.md` → "Simulation & hardware notes").

| Dir | Contents |
|---|---|
| `ue5/` | `settings.json` — which vehicle, which sensors, how they are tuned — plus worked examples |

That is the whole directory. The Gazebo and Isaac Sim trees that used to sit beside `ue5/`
are retired; their backlogs and design docs are archived under
[`../docs/history/`](../docs/history/).

**A world is an input, not a repo asset.** `scripts/sim_up.sh` defaults to the Blocks
environment vendored with Cosys-AirSim, and `--world PATH.uproject` points it at your own
Unreal project wherever that lives. Nothing here needs to change to fly a different map.
