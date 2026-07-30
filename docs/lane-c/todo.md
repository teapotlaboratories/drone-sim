# Lane C — UE5.5 + Cosys-AirSim — backlog

**Area:** photorealistic perception + benchmark reproduction.
**Indexed from:** [`../drone-sim-todo.md`](../drone-sim-todo.md).
**Promoted:** 2026-07-29, replacing Lane B as the photoreal lane — see
[`../lane-b/isaac-driver-decision.md`](../lane-b/isaac-driver-decision.md).

**Why this lane, now.** The three target papers all evaluate on Unreal/AirSim-family
simulators — **none uses Isaac** (`docs/reference/01_sim_stack_report.md:4`). Fly0 uses
UE4 + AirSim, OnFly uses UE 4.27, SPF uses the DRL simulator. Lane C was always where
AerialVLN/OpenFly reproduction had to happen; the Isaac driver blocker simply moved it
earlier. Lane C is also **driver-agnostic** — the `P0-09` blocker does not exist here.

**Standing risk.** The plan rates Lane C **High likelihood / Med impact** for build
fragility (`02_development_plan.md:234`). Treat every step below as "may not build first
try", and pin aggressively.

**Status legend:** `todo` · `in progress` · `done` · `blocked`

---

## C-01 — Pin a known-good Cosys-AirSim commit for UE5.5

**Status:** `todo` · **Blocks:** everything else in this lane

**What.** Identify and pin an exact Cosys-AirSim commit that builds against UE5.5, and
record it in `versions.lock` with the UE version it was built against.

**Why.** The precompiled Linux plugin targets **UE5.2.1**, and the UE5.5 branch was a
**March-2025 pre-release** (`02_development_plan.md:64`). A branch pin here would repeat
exactly the failure that broke the XRCE agent today — upstream deleted the branch a tagged
release depended on, making it retroactively unbuildable. **Pin a SHA, not a branch.**

**Acceptance.** A SHA in `versions.lock`, plus the evidence it was chosen for (a release
tag, a CI-green commit, or our own successful build).

**Fallback.** Colosseum (UE5.6) — semi-active, MIT, PX4 SITL/HITL capable. Note its README
says Ubuntu 22.04 is unsupported due to Vulkan, so Docker is the recommended route there.

---

## C-02 — UE5.5 base image and source build

**Status:** `todo` · **Blocked by:** C-01

**What.** Build from `ghcr.io/epicgames/unreal-engine:dev-slim-5.5.4`.

**Why.** Cosys-AirSim must be built from source against the engine.

**Access is already confirmed** — the GitHub account is a member of the **EpicGames** org
(verified 2026-07-28), which is the usual gating hurdle for that image.

**Acceptance.** Engine image pulls and a trivial project packages.

**Traps.**
- **Do not run a UE5 shader compile concurrently with other GPU work** — the hardware
  assessment is explicit that 64 GB will not comfortably hold UE5 compilation alongside a
  heavy sim (`03_hardware_assessment.md:66`).
- Image size and build time are both large; budget disk before starting (internal NVMe is
  the constrained volume — currently ~262 GB free, and the two Isaac images already hold
  ~34 GB that can be reclaimed if Lane B stays deferred).
- This lane is a **`docker/todo.md` D-04 dependency** — build it containerized from the
  start rather than natively, per the reproducibility goal.

---

## C-03 — PX4 ↔ Cosys-AirSim over the MAVLink SITL API

**Status:** `todo` · **Blocked by:** C-02

**What.** Connect Cosys-AirSim to PX4 SITL via the Simulator MAVLink API (TCP 4560),
external-autopilot mode.

**Why.** This is Lane C's equivalent of what Pegasus does for Lane B — and unlike Pegasus
it is a **documented upstream capability of AirSim**, not something we would write.

**Which PX4 tree?** Open question to resolve here: Lane A's **v1.16.0** is already built
and working. Cosys-AirSim talks MAVLink rather than uXRCE-DDS, so the v1.14.3 tree that
Lane B needed for Pegasus may not be required at all. **If Lane C can use v1.16.0, the
project drops from two PX4 trees to one** — a real simplification of the plan's dominant
risk. Verify before assuming.

**Acceptance.** Vehicle spawns in a UE5 scene, PX4 arms, and the ROS 2 graph sees the same
`/fmu/out/*` topics Lane A produces — **identical topic names, transport swapped only**.

---

## C-04 — Camera/depth/LiDAR into the existing ROS 2 graph

**Status:** `todo` · **Blocked by:** C-03

**What.** Bring Cosys-AirSim's sensors up on the ROS 2 C++ wrapper: RGB, depth,
GPU-LiDAR, and the annotation/segmentation cameras.

**Why.** This is what Lane B was for. Cosys-AirSim's sensor set is the richest of the
living AirSim forks — GPU-LiDAR with tunable noise and ground-truth labels, Echo sensors,
camera distortion (`01_sim_stack_report.md:14`).

**Traps.**
- **Coordinate frames:** Cosys-AirSim publishes in ROS-standard coordinates, **not NED by
  default**. Frame conversion is a known source of silent error — check it against Lane A
  as a control.
- **Measure IMU–camera timestamp jitter before trusting sim VIO.** This is the recurring
  pain point across all simulators, and Isaac's Hydra-time sensors — the most defensible
  option — are no longer on the table. Do not skip this measurement.

**Acceptance.** Depth and LiDAR topics at stable rates, with measured timestamp jitter
recorded — not asserted.

---

## C-05 — Isaac ROS perception on Lane C imagery

**Status:** `todo` · **Blocked by:** C-04

**What.** Run `isaac_ros_visual_slam` (cuVSLAM) and `isaac_ros_nvblox` against Lane C
camera/depth topics.

**Why — worth stating clearly:** **Isaac ROS is not Isaac Sim.** cuVSLAM and nvblox are
ROS 2 Jazzy packages that consume image and depth topics from *any* source. Deferring Lane
B does **not** cost us the GPU perception stack; it only costs the renderer. The Phase 2–3
perception plan stands unchanged.

**Acceptance.** cuVSLAM produces odometry from Lane C imagery; nvblox produces a costmap.

---

## Open questions this lane must answer

1. **Can Lane C use PX4 v1.16.0?** If yes, the two-PX4-tree architecture — the plan's
   single dominant risk — collapses to one tree. (`C-03`)
2. **Is 10 GB VRAM enough** for the AerialVLN/OpenFly scenes at useful resolution? The
   assessment says 10 GB is workable for modest scenes but stutters on large Cesium
   terrain.
3. **Does Cesium-in-UE5 give georeferenced terrain *with* physics?** The FSD/PhysX
   exclusion is Omniverse-specific, and the plan routes georeferenced physics here — but
   verify rather than assume.
