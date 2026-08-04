# Task ID map — `C-NN` → `SIM-NN`

**Why this file exists.** When the repo narrowed to a single simulator, the backlog IDs for
that simulator's work were renamed from `C-NN` to `SIM-NN`, keeping the number. **Git
history was not rewritten and must not be** — commit subjects still say `C-03`, `C-11`,
`C-16`. This table is the only thing that makes those commits traceable to the backlog they
belong to.

The rename was mechanical: **same number, new prefix.** Nothing was renumbered, merged or
split. `C-07` is `SIM-07` and always was.

The live backlog is [`../todo.md`](../todo.md). The archive index is
[`README.md`](README.md).

---

## The map

| Old ID | New ID | What it is |
|---|---|---|
| `C-01` | `SIM-01` | Harden the Cosys-AirSim / Unreal Engine pin |
| `C-02` | `SIM-02` | UE5.8 base image and source build |
| `C-03` | `SIM-03` | PX4 ↔ Cosys-AirSim, and `/fmu/*` topic parity |
| `C-04` | `SIM-04` | Camera / depth / LiDAR into the existing ROS 2 graph |
| `C-05` | `SIM-05` | Isaac ROS perception on the simulator's imagery |
| `C-06` | `SIM-06` | Build the Cosys-AirSim ROS 2 wrapper against Jazzy |
| `C-07` | `SIM-07` | The simulator's flight gate |
| `C-08` | `SIM-08` | Cesium georeferenced terrain |
| `C-09` | `SIM-09` | Make the simulator actually fly (lockstep first) |
| `C-10` | `SIM-10` | Make the EKF-origin ordering deterministic |
| `C-11` | `SIM-11` | Load the user's own world (bring-your-own `.uproject`) |
| `C-12` | `SIM-12` | The capture is noisier than Unreal's own render (deferred) |
| `C-13` | `SIM-13` | Operator-supplied spawn coordinates |
| `C-14` | `SIM-14` | Automatic spawn derivation (deferred) |
| `C-15` | `SIM-15` | The navigation command interface, confirmed end to end |
| `C-16` | `SIM-16` | An example mission: fly a circuit of the park over ROS 2, recorded |
| `C-17` | `SIM-17` | 1080p60 video via Pixel Streaming (NVENC), off the perception path |

**`C-14` appears in no commit message.** It was filed as deferred in favour of `C-13`'s
manual coordinate and has never been built, so the only place the old ID appears is in the
backlog prose that survives under its new number.

Every other row has at least one commit behind it — `git log --grep='C-11'` and similar
still work, and are the reason the left-hand column is worth keeping.

---

## IDs that were **not** renumbered

Three other schemes exist in this repo's history. **None of them was touched.** Renumbering
them would have broken the same traceability this file exists to protect, and unlike the
simulator backlog they do not describe work that survived the pivot under a new name.

| Prefix | What it covered | Where it lives now |
|---|---|---|
| `P0-*` | environment and version lock — toolchain, drivers, pinned upstreams, smoke tests | [`phase-0/todo.md`](phase-0/todo.md) — archived |
| `P1-*` | the retired Gazebo baseline — offboard controller, launch, seeded runner, MCAP, the 10-seed gate, CI | [`gazebo/todo.md`](gazebo/todo.md) — archived |
| `D-*` | reproducible builds and containerization | [`../docker/todo.md`](../docker/todo.md) — **still active** |

`P0-*` and `P1-*` are closed history: they describe stacks the repo no longer contains, and
an active document that still cites one should either drop the reference or point into this
archive. `D-*` is the exception — the Docker backlog was never archived, because
*reproducible as Docker* remains a project goal.

---

## A note on writing these IDs down

Backtick every ID in Markdown (`` `SIM-07` ``, `` `P1-06` ``) and drop the `#` entirely in
commit messages. A bare `#N` in GitHub text auto-links to a same-repo issue or pull request,
which is exactly the mis-link this ID scheme was designed to avoid. Cross-repo references
must be qualified as `owner/repo#N`.
