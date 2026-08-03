# Lane C — UE5.8 + Cosys-AirSim — backlog

**Area:** the project's **primary simulator**. Photorealistic perception, obstacle
avoidance, and benchmark reproduction.
**Indexed from:** [`../drone-sim-todo.md`](../drone-sim-todo.md).
**Decision doc:** [`../reference/04_ue5_stack_architecture.md`](../reference/04_ue5_stack_architecture.md).

**Promoted:** 2026-07-29, replacing Lane B as the photoreal lane — see
[`../lane-b/isaac-driver-decision.md`](../lane-b/isaac-driver-decision.md).
**Made PRIMARY:** 2026-07-31. **Phase 2 — perception and obstacle avoidance — is built
here, not in Gazebo.**

---

## What changed on 2026-07-31, and what did not

**Changed.** Lane C stopped being the lane that Phase 3 would eventually need and became the
lane everything after Phase 1 is built in. Three questions `04` left open were settled:

| Question | Decision | Consequence for this backlog |
|---|---|---|
| Retire Lane A, or demote it? | **Demote.** Always-on regression baseline. | Lane C must eventually earn its own flight gate (`C-07`) — it does not inherit Lane A's. |
| ROS 2 Humble (per `04`) or Jazzy (per `versions.lock`)? | **Jazzy, everywhere.** | Makes `C-06` the make-or-break task, and it now runs **first**. |
| Adopt `04`'s weeks-based phases? | **Map, don't renumber.** | `04`'s "Phase 1" is `C-01`–`C-04` here, a prerequisite inside project Phase 2. |
| **UE5.5 (per `04`) or UE5.8?** | **UE5.8, tag `5.8-v3.4.1`.** *(added after research, same day)* | `C-01` changes from "find a commit that builds" to "confirm Cesium supports UE5.8". |

### The UE5.5 → UE5.8 change, and why it happened within hours of the decision

Research against upstream turned up a conflict `04` could not have known about, because `04`
was written when UE5.5 *was* upstream's target:

**UE5.5 and ROS 2 Jazzy cannot be had from one upstream tag.** The last UE5.5 release is
`5.5-v3.3` (2025-04-16, SHA `e029c244…`). It predates the Jazzy fix, and upstream's v3.4
CHANGELOG says the 5.5 branch "will no longer be receive updates or be actively maintained".
The Jazzy fix — commit `83d1b81c`, rewriting `cv_bridge`/`tf2_geometry_msgs` includes from
`.h` to `.hpp` — landed in v3.4, which targets **UE5.8**.

**Measured on this machine rather than inferred:**

```
$ ls /opt/ros/jazzy/include/cv_bridge/cv_bridge/
cv_bridge.hpp  cv_bridge_export.h  cv_mat_sensor_msgs_image_type_adapter.hpp
rgb_colors.hpp  visibility_control.h
```

`ros-jazzy-cv-bridge 4.1.0-1noble.20260615.144656` ships **no `cv_bridge.h` shim at all**,
and `tf2_geometry_msgs` likewise ships only `.hpp`. So `5.5-v3.3` was not merely
unsupported-on-paper — **it was measurably unbuildable here.**

Two further marks against UE5.5, both open upstream issues:
`Cosys-Lab/Cosys-AirSim#135` reports segmentation and annotation rendering **all black** on
exactly UE5.5 + Ubuntu 24.04 + clang-18 — and segmentation ground truth is load-bearing for
the VLM work. `Cosys-Lab/Cosys-AirSim#129` reports PX4 SITL lockstep desync against
`5.5-v3.3` with PX4 v1.16.0, the exact PX4 line Lane A pins.

**This also inverted `04`'s ROS 2 advice rather than merely overriding it.** Upstream now
documents Jazzy on Ubuntu 24.04 (`docs/ros2.md`: *"The following was tested with ROS2
Jazzy"*, changed 2026-07-10; `install_linux.md`: *"The current recommended and tested
environment is **Ubuntu 24.04 LTS**"*). Humble is now the **riskier** choice: current
upstream includes `<cv_bridge/cv_bridge.hpp>`, and Humble's `vision_opencv` ships only
`cv_bridge.h`, so `04`'s Humble recommendation would fail to compile immediately. The
stay-on-Jazzy decision was made on the reasoning that `04`'s Humble was inherited rather
than measured; the evidence then showed it is simply out of date.

> **One gate remains before the UE5.8 pin hardens.** **Cesium for Unreal's UE5.8 support is
> unverified**, and Cesium is a hard requirement in `04` (real-world maps). UE5.8 is newer
> than most third-party plugins target, and Cesium is exactly the class of plugin that lags.
> `versions.lock` keeps `lane_c.cosys_airsim.status: TODO-verify` until this is checked. If
> Cesium does not support 5.8, the trade reopens — `5.5-v3.3` plus a ~6-line header
> backport, against waiting on Cesium.

**Did not change: this lane has never been built.** Every entry in `versions.lock` under
`lane_c` is `pinned` or `TODO-verify`; not one has a passing smoke test. The plan rates this
lane **High likelihood / Med impact** for build fragility
(`02_development_plan.md:234`). Being declared primary does not reduce that risk — **it
raises the cost of it**, because there is no longer a second lane that Phase 2 could fall
back to without a replan.

Treat every step below as "may not build first try", and pin aggressively.

**Status legend:** `todo` · `in progress` · `done` · `blocked`

---

## Execution order — deliberately not ID order

IDs are stable references (`versions.lock` and the roadmap cite `C-01`, `C-02`, `C-03`), so
new tasks take new numbers rather than renumbering the old ones. Execution order is a
separate question, and it changed:

| Order | Task | Why here |
|---|---|---|
| 1 | **`C-06`** ROS 2 wrapper on Jazzy | **Cheapest decisive test in the lane** — ~30 min, no engine image. Resolves the distro decision before anything expensive is committed to. |
| 2 | `C-01` Harden the pin (Cesium-on-UE5.8 gate) | Release chosen; the gate blocks `pinned` status, not the work. |
| 3 | `C-02` UE5.8 base image + source build | The expensive one — hours, and tens of GB. |
| 4 | `C-03` PX4 ↔ Cosys-AirSim + `/fmu/*` parity | Where the sim-to-real claim is proved or lost. |
| 5 | ~~**`C-09`** Make Lane C actually fly~~ ✅ | **Done 2026-08-01 — 4/4 waypoints.** |
| 6 | **`C-10`** Deterministic EKF-origin ordering | `C-09`'s fix is a manual restart. Until the bring-up enforces it, Lane C flies by luck of container start order. |
| 7 | ~~`C-04` Sensors into the ROS 2 graph~~ ✅ | **Done 2026-08-02** — RGB/depth/LiDAR/GPS/IMU published and verified by value. |
| 8 | **`C-11`** Photorealistic scene + dynamic actors | **Re-prioritised 2026-08-02 by the owner.** The reason scene work was last — *"scene work on an unproven simulator"* — no longer holds: the simulator flies and its sensors are verified. |
| 9 | `C-07` Lane C flight gate | Lane C earns its own success rate. Wants a real scene to be worth gating. |
| 10 | `C-05` Isaac ROS perception on Lane C imagery | **Deprioritised 2026-08-02.** Not abandoned — the imagery is ready for it whenever it comes back. |
| 11 | `C-08` Cesium georeferenced terrain | Still Phase 4. Split from the scene/actor work below, because georeferencing is benchmark reproduction, not photorealism. |

**Why `C-06` moved to the front.** The stay-on-Jazzy decision rests entirely on the
Cosys-AirSim ROS 2 wrapper building against Jazzy, and the wrapper is *an ordinary colcon
package*. It does not need Unreal Engine, a GPU, or a running simulator to attempt a
compile. Discovering a distro incompatibility after a multi-hour UE5 engine build would be
the most expensive possible ordering of the same two facts.

---

## C-06 — Build the Cosys-AirSim ROS 2 wrapper against Jazzy

**Status:** ✅ **`done` (2026-08-01)** — **it builds.** Evidence:
[`../worklog/2026-08-01-c06-wrapper-on-jazzy.md`](../worklog/2026-08-01-c06-wrapper-on-jazzy.md)

```
colcon build --symlink-install   # ROS 2 Jazzy / Ubuntu 24.04 / g++ 13.3.0
Summary: 2 packages finished [1min 21s]     exit 0, 0 errors, warnings only
```

**The stay-on-Jazzy decision is now evidence rather than reasoning** — which was the entire
point of running this first, for 1m21s instead of after a 24 GB engine pull.

**Artifacts asserted, not assumed** (the `D-01` rule): `airsim_node` is 12 MB, `ldd` reports
**0 unresolved libraries**, it links `/opt/ros/jazzy`, and 24 `airsim_interfaces` types
register. No clang needed — `colcon` builds AirLib itself via `add_subdirectory`.

**Two findings that outlived the task:**

- **A CARLA UE4 instance on this host is bound to `0.0.0.0:41451`** — the exact AirSim RPC
  port. The node connected to *it*, negotiated a version handshake and failed. The log line
  before the failure said `Connected!`. **A port conflict is waiting for `C-03`**, and the
  failure mode looks like success. Recorded as coupling `airsim-rpc-port-conflict`.
- **The wrapper crashes rather than degrades** when an API call fails — the ROS context is
  torn down and a timer handle throws (`exit 250`). Worth knowing before `C-04` relies on it
  surviving a simulator hiccup.

**Still unproven:** it has never run against an actual Cosys-AirSim server. That needs
`C-02`.

**Original definition:** ~~`todo` · RUN THIS FIRST · Blocks: the distro decision, and therefore `C-02`~~

**What.** Check out Cosys-AirSim at **`5.8-v3.4.1`** (SHA `a552dd6cd517b8d5d26629ad88004356c3007326`)
and attempt `colcon build` of its ROS 2 wrapper package alone, on the existing Jazzy /
Ubuntu 24.04 container. No Unreal Engine, no GPU, no simulator — just the bridge package and
its dependencies. **~30 minutes, and it needs no 12 GB engine image.**

**Why this is a task and not an assumption.** Upstream's own CI never builds this. The only
workflow (`build-linux.yml`) runs `setup.sh`, `build.sh` and a `MavLinkTest --help` smoke
check — **`colcon` is never invoked and `ros2/` is never built, on any distro or tag.**
"Tested with ROS2 Jazzy" is a maintainer assertion with no automated signal behind it, and a
GitHub issue search for "jazzy" in the repo returns **zero** results. That is ambiguity, not
health: we are plausibly an early adopter of this path.

**Acceptance.** One of two recorded outcomes, both useful:

- **Builds** → record the dependency list and any patches needed, and set
  `lane_c.cosys_airsim_ros2_wrapper.builds_against: jazzy` with the evidence.
- **Does not build** → record the *specific* failure (missing package, API change, message
  incompatibility), not "it failed". The escape hatch is a small header patch carried as a
  patch file in this repo — **not** a distro flip, and **not** a Humble sidecar
  (`versions.lock: lane_c.ros2_distro_fallback` explains why that idea was withdrawn the
  same day it was written).

**Traps.**
- **Do not judge this by a green `colcon build` alone.** An `ament_cmake` build succeeding
  says nothing about whether the node runs — this project already learned that an
  `ament_python` build reports success while the package fails at import (`P1-01`).
- **Do not copy upstream's ROS 2 container tooling.** `docker/build_ros2.sh` hardcodes
  `ROS_DISTRO=humble` and `tools/Dockerfile-ROS2` is `FROM ros:foxy-ros-base` — both stale
  and both contradict upstream's own docs. Write our own Jazzy/24.04 image, which the
  reproducible-as-Docker goal wants anyway.
- **`docs/ros2.md` still says `source /opt/ros/iron/setup.bash`.** The Jazzy update touched
  one line; the rest of that page was not re-validated. Expect other stale build steps.
- **Do not patch the vendored tree to make it build** without recording it. Least-destructive
  vendor edits: push integration into the build layer and write the deviation down.
- **`tf2/LinearMath/*.h` shims are still used upstream** — deprecation warnings on Jazzy.
  `CMAKE_CXX_FLAGS` sets `-Wall -Wextra` but **not** `-Werror`, so they will not fail the
  build. Do not "fix" them by adding `-Werror`.

---

## C-01 — Harden the Cosys-AirSim / UE pin

**Status:** `todo` — **release chosen, gate outstanding** · **Blocks:** every build in this lane

**Chosen 2026-07-31:** tag **`5.8-v3.4.1`**, SHA **`a552dd6cd517b8d5d26629ad88004356c3007326`**,
targeting **UE5.8**. Reasoning and the measured evidence are above; the full record is in
`versions.lock: lane_c.cosys_airsim`.

### Both gates cleared — 2026-07-31

**Gate 1 — Cesium for Unreal on UE5.8: PASSED.** Cesium for Unreal **v2.28.0** (2026-07-01,
current `releases/latest`) adds UE5.8. `CHANGES.md`: *"Added support for Unreal Engine 5.8."*
PR `CesiumGS/cesium-unreal#1856` merged 2026-06-26. The UE5.8 asset is a real 1.23 GB
download returning HTTP 200 — an artifact, not a roadmap promise.

**Gate 2 — the Epic image tag: EXISTS, and this one was re-confirmed by hand.**
`dev-slim-5.8.0`, digest `sha256:daac0262…4a0c46`, published 2026-06-17 — the day UE5.8
shipped. Found by an authenticated tag listing (85 tags, four carrying 5.8), then
**independently re-queried from this container**: the manifest resolves, 30 layers,
**24.0 GB compressed**, and the config blob reads back
`org.opencontainers.image.version 22.04`.

**Inspection costs ~17 KB and needs no `docker login`.** The `gh` CLI token exchanges for a
read-only ghcr bearer, so the pin can be checked without touching the 24 GB — worth wiring
into `C-02` as a preflight rather than discovering a 403 partway through a build.

**Sanity check that could have sunk the whole pin: UE5.8 is a released engine**, shipped
2026-06-17 as current stable, with hotfix 5.8.1 on 2026-07-28. The `5.8-v3.4.1` upstream tag
targets a production engine ~6 weeks old, not a preview.

> **The fallback has inverted — this reverses what this document said hours ago.** Cesium
> **v2.29.0 removes UE5.5 support** (*"Unreal Engine 5.6 or later is now required"*), and
> v2.28.0 is the terminal Cesium release for 5.5. Falling back to UE5.5 would now mean an
> end-of-line Cosys-AirSim branch **plus** a header patch we own forever **plus** a Cesium
> frozen permanently at v2.28.0. **UE5.5 is no longer a safe fallback and must not be
> described as one.** UE5.8 is the only forward-supported path.

**Still `TODO-verify`, deliberately.** The tag was *observed*, not *pulled* — and the
credential used for that lookup is being rotated, so the query cannot be re-run as-is.
Existing is not working. Re-confirm on a fresh token, then let `C-02` prove it:

```bash
docker login ghcr.io      # Epic-org account, PAT with read:packages
docker manifest inspect ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0
```

**Pin the three-component tag, never `dev-slim-5.8`.** Both resolve to the same digest
today, but the two-component form is a moving alias — the registry shows `dev-slim-5.5` and
`dev-slim-5.5.4` sharing one digest, i.e. `-5.5` tracked four patch releases. Same rule as
`lane-c-sha-not-branch`, applied to an image. And do not write a `5.8.1` tag on the
assumption it will appear: `dev-slim-5.8.1` is a 404, and Epic does not image every hotfix
(there is no `dev-5.7.1` at all).

**Pin a SHA, not a branch — and here that is not stylistic.** There is **no `5.5` branch in
the repo at all** (only a stale `5.5dev` last touched 2026-01-14), and `main` has already
migrated 5.5 → 5.6dev → 5.7pdev → 5.8. The exact branch-evaporation failure this rule exists
for has already happened upstream. `main` currently points at `a552dd6c` — **do not pin
`main`**, it will move to 5.9. Recorded as coupling `lane-c-sha-not-branch`, which this
project earned twice: eProsima deleted the Fast-DDS branch the XRCE agent's `v2.4.2` tag
depended on, and QGroundControl's `latest` channel had to be repinned to an exact release.

**Also verify before pinning: the Epic base image tag — and note the ground is softer than it
looked.** *(corrected 2026-07-31)* **Neither tag has ever been confirmed to exist.** What was
verified on 2026-07-28 is EpicGames **org membership** — the access hurdle — not the tag. The
string `dev-slim-5.5.4` traces to `02_development_plan.md:127`: it came from a reference doc,
never from a registry query or a pull, and no UE image has ever been pulled here.

That makes it a documentation artifact, not a precedent — so it cannot be used to infer the
5.8 naming pattern. **List the registry's tags.**

Checked and blocked 2026-07-31: `ghcr.io` denies anonymous reads for this repository
(HTTP 403, `DENIED: invalid token`), and no ghcr credentials are configured in the container.
Confirming the tag needs an authenticated `docker manifest inspect` as the Epic-org account.

**Acceptance.** `versions.lock` carries the SHA, the confirmed engine image tag, and the
Cesium answer — with the evidence each was chosen for.

**Fallback, with a caveat that has changed.** Colosseum (UE5.6) — but Colosseum was
**archived read-only on 2026-07-11** (`04`). It is a *frozen* fallback, not a maintained
one, and building on it is technical debt from day one. Prefer an older Cosys-AirSim release
over a dead fork.

---

## C-02 — UE5.8 base image and source build

**Status:** `todo` · **Blocked by:** `C-01`, and informed by `C-06`

**What.** Build from `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0`, digest
`sha256:daac0262…4a0c46` — the tag `C-01` found in the registry. Pin the three-component
form, never `dev-slim-5.8`.

> ### The engine image is Ubuntu 22.04, not 24.04
>
> **Confirmed by reading the image config blob directly (2026-07-31) — measured, not
> inferred:**
>
> ```
> org.opencontainers.image.version   22.04
> org.opencontainers.image.ref.name  ubuntu
> maintainer                         NVIDIA CORPORATION <cudatools@nvidia.com>
> ```
>
> **ROS 2 Jazzy has no jammy packages**, so **nothing Jazzy can be installed inside the
> engine image.**
>
> This makes `04`'s separate `sim` and `ros2` containers **mandatory rather than stylistic**.
> The AirSim ↔ ROS 2 boundary must stay the RPC / MAVLink socket; it cannot become a shared
> filesystem or a single container.
>
> **The plan already survives this** — `C-06` builds the wrapper in the existing 24.04/Jazzy
> container, which is now the only place it *can* be built. But anyone who assumed one image
> would hold both halves needs to stop assuming it. Verify the label at first pull.

**Why.** Cosys-AirSim must be built from source against the engine. The precompiled Linux
plugin is **not** an option: the UE5.5 one was built on Ubuntu 22.04 / CMake 3.22.1 /
clang-14 with upstream's explicit warning to "use the same toolchains … otherwise it is
better to build the plugin from source", and we are on Ubuntu 24.04.

**Access** — EpicGames org membership (verified 2026-07-28) **plus a PAT with
`read:packages`**. Org membership alone is not enough, which is the distinction that caused
the earlier "the tag was verified" error.

**Reproducibility hazard, flag it now:** CI will need its own credential with the same org
membership. That is a real gap against the *"fresh machine from the repo alone"* goal — a
clone plus a Dockerfile is not sufficient to build this lane. Document the credential step in
`docker/README.md` before `D-05`.

**Check the CUDA runtime against the host driver at first pull**, not after a failed build.
The image sits on an NVIDIA CUDA base and this bench runs driver 610.43.03 — the same class
of mismatch that deferred Lane B.

**Acceptance.** Engine image pulls and a trivial project packages.

**Traps.**
- **Do not run a UE5 shader compile concurrently with other GPU work** — the hardware
  assessment is explicit that 64 GB will not comfortably hold UE5 compilation alongside a
  heavy sim (`03_hardware_assessment.md:66`). Recorded as a `versions.lock` rule.
- **Budget disk before starting.** The internal NVMe is the constrained volume — currently
  ~262 GB free. Isaac's images were already deleted to reclaim ~36 GB (Lane B deferred).
  UE5 projects and assets belong on the **external drive**, under
  `<drive-root>/Developments/projects/drone-sim/`, never in `~`.
- **Headless rendering needs the explicit flag.** Vulkan requires `-RenderOffScreen`;
  without it UE falls back to OpenGL silently. Mount the Vulkan/EGL ICD JSONs.
  **`NVIDIA_DRIVER_CAPABILITIES` is already set correctly by the base image**
  (`graphics,compat32,utility,compute,display,video`, read from the config blob) — do not
  override it with a narrower list, which is the more likely mistake here.
- **Budget the download before starting it: 24.0 GB compressed, 30 layers.** Docker's
  data-root is on the internal NVMe. Settle the storage question in `docker/todo.md` `D-04`
  *first* — this is the actual blocker for `C-02`, not the credential.
- **GPU selection under `-RenderOffScreen` has historically ignored `SDL_HINT_CUDA_DEVICE`
  and defaulted to GPU 0.** The project's GPU work split (render on the 3080, infer on the
  5060 Ti) is a hard rule — enforce it at the **container boundary** with
  `--device nvidia.com/gpu=0`, the way the Isaac probe did, rather than trusting an
  application-level flag.
- **Toolchain ABI mismatch is a real link failure, not a theoretical one.** Building AirLib
  with the plain system `clang` can produce a library incompatible with UE's linker.
  `5.8-v3.4.1`'s `build.sh` supports `--ue-root`/`UE_ROOT` to use the engine's bundled
  toolchain — **use it.** (The 5.5 line has no such flag at all, which was one more reason
  to move off it.)
- **This is a `docker/todo.md` `D-04` dependency** — build it containerized from the start,
  per the reproducibility goal.

---

## C-03 — PX4 ↔ Cosys-AirSim, and `/fmu/*` parity

**Status:** ✅ **`done` (2026-08-01)** — parity proved. **The vehicle arms but does not
climb; that is [`C-09`](#c-09--make-lane-c-actually-fly-lockstep-first), not a reopening of
this task.** Evidence:
[`../worklog/2026-08-01-c03-px4-airsim-link.md`](../worklog/2026-08-01-c03-px4-airsim-link.md)

```
Lane A: 51 /fmu/ topics (24 /fmu/out)
Lane C: 51 /fmu/ topics (24 /fmu/out)
diff  : IDENTICAL          out/lane-c/fmu-topics-lane-{a,c}.txt
```

**The acceptance criterion as written is met** — identical topic names, transport swapped
only, verified by diffing rather than inspection. `Simulator connected on TCP port 4560`,
`lockstep_scheduler` initialised, `uxrce_dds_client` publishing.

**What the criterion did NOT ask for, and is therefore still open:** the vehicle has never
armed, taken off or moved. Identical *names* is necessary, not sufficient — the real test is
flying the Lane A controller unchanged against Lane C. Filed as the next step rather than
folded into this task, because the stated bar was met and moving the bar retroactively hides
what was actually proved.

**Also unresolved here:** `/fmu/out/vehicle_status` was silent in the sample; lockstep
initialises but was not characterised (do not quote an RTF from this run); sensor values are
unchecked for physical sanity.

> **Port collision, found the hard way.** Lane A publishes 4560, 8888, 14540, 14550 and 18570
> — exactly what Lane C needs. **The two lanes cannot run simultaneously**, so the parity
> diff had to be captured sequentially. This blocks `C-07`, whose purpose is comparing a Lane
> C run against a Lane A baseline. Fix with distinct `ROS_DOMAIN_ID`s plus non-overlapping
> published ports, or accept sequential comparison and document it.

**Original definition:** ~~`todo` · Blocked by: `C-02`~~

**What.** Connect Cosys-AirSim to PX4 SITL via the Simulator MAVLink API (TCP 4560),
external-autopilot mode, **with PX4 also running `uxrce_dds_client`**.

**Why.** This is Lane C's equivalent of what Pegasus does for Lane B — and unlike Pegasus it
is a **documented upstream capability of AirSim**, not something we would write.

**The parity requirement is the whole point, and it is easy to get subtly wrong.** MAVLink
lockstep drives the *sim physics handshake*; XRCE-DDS drives the *autonomy code*. The ROS 2
graph must see the same `/fmu/out/*` topics the real Pixhawk 6C produces, so the controller
from `P1-02` ports across **unchanged**. If Lane C's autonomy ends up subscribing to
`/airsim/*` poses instead, the code that flies in sim is not the code that flies on the
aircraft, and that divergence will not surface until Phase 4. Recorded as coupling
`lane-c-topic-parity`.

**Which PX4 tree?** The open question this task exists to answer. Lane A's **v1.16.0** is
already built and working. Cosys-AirSim talks MAVLink rather than uXRCE-DDS for the physics
handshake, so the v1.14.3 tree Lane B needed for Pegasus may not be required at all. **If
Lane C can use v1.16.0, the project drops from two PX4 trees to one** — collapsing the
development plan's dominant architectural risk.

**Do not assume it.** AirSim-lineage sims have broken across PX4 releases before (the
documented 1.10-vs-1.11 breakage), and Project AirSim pins v1.12.3 verbatim. Whatever the
answer, **keep Lane B's v1.14.3 pin** — it belongs to Pegasus, and Lane B reopens unchanged
on an R580 host rebase.

**Acceptance.** Vehicle spawns in a UE5 scene, PX4 arms, and
`ros2 topic list | grep '^/fmu/'` **diffs clean against a Lane A run** — identical topic
names, transport swapped only. Verified by diffing, not by inspection.

**Three defects to expect, found by research before the first build.** None is a blocker;
each costs a day if hit blind:

1. **`PX4Scripts/run_airsim_sitl.sh` is broken for any PX4 ≥ v1.14.** It exports
   `PX4_SIM_MODEL=iris`, which no longer matches an airframe filename after the rename to
   `10016_none_iris` — `rcS` exits 1 with "Unknown model". Upstream's multi-vehicle docs
   also reference `Tools/sitl_multiple_run.sh`, which 404s at v1.16.0. **Lane C needs its
   own launch scripts**, which is fine — we already have a `bringup` package.
2. **`LockStep` may be dead code.** `initialize()` sets `lock_step_enabled_`
   (`MavLinkMultirotorApi.hpp:66`) and then `openAllConnections()` → `resetState()` clears
   it back to false. If that holds, **every documented "LockStep: true" setup is actually
   running free-running** — precisely the mode that degrades under LiDAR + multi-camera +
   VLM load, i.e. our exact target workload. **Verify empirically before trusting any timing
   result.** The fix is a small patch; the danger is not knowing.
3. **The "disabling lockstep" docs point at `boards/px4/sitl/default.cmake`**, which 404s in
   v1.16.0 — it is `sitl.cmake` now.

**Known upstream defect that lands squarely on Phase 2.**
`Cosys-Lab/Cosys-AirSim#129` reports lockstep desync with ~1.2 s stale timestamps causing
PX4 to **reject `/fmu/in/obstacle_distance` as too old**. Obstacle avoidance writes exactly
that topic. Any graph mixing `/airsim_node/*` and `/fmu/*` needs an explicit time-alignment
design — do not assume both are on the same clock.

**Traps.**
- **PX4 lockstep is fragile against slow UE frames** — a slow render can trip SITL timeouts.
  Set `LockStep:true`, `UseTcp:true`, `SteppableClock`, and the `PressureFactorSigma`
  barometer tweak for fast GPS lock — then confirm lockstep is *actually engaged*, per
  defect 2 above.
- **Assert an AGGREGATE real-time metric, never an instantaneous one.** Lane A already paid
  for this lesson twice: the instantaneous `real_time_factor` field is a short-window
  estimate that swings 0.14–1.01 while the true ratio is 0.977. See `couplings.rtf-floor`.
- **Arming needs a GCS datalink** (`NAV_DLL_ACT=2`). Lane A supplies it via the `qgc`
  service and the check is deliberately left enforced — Lane C will need the same, and the
  failure mode is "refuses to arm with no useful error".
- **Verify hover thrust and motor ordering empirically.** Actuator-output semantics changed
  at PX4 v1.13 (control allocator): pre-1.13 PX4 normalised PWM internally, v1.14+ forwards
  `actuator_outputs_sim` directly. Both are nominally 0..1 for multirotors and AirSim applies
  `0.8*x + 0.20`, so it *should* hold — but nothing upstream tests thrust fidelity against
  v1.16.
- **PX4 classifies AirSim as community-supported** and explicitly disclaims that it "may or
  may not work with current versions of PX4". No upstream regression test protects this
  integration. That is an argument for the exact tag pin we already have — and for keeping
  Lane A, which *does* have first-class PX4 support, as the baseline.

---

## C-09 — Make Lane C actually fly (lockstep first)

**Status:** ✅ **`done` (2026-08-01) — LANE C FLIES.** The unmodified Lane A controller reached
4/4 waypoints (errors 0.78 / 0.79 / 0.78 / 0.78 m), landed and disarmed. Filed from `C-03`'s
evidence: [`../worklog/2026-08-01-c03-px4-airsim-link.md`](../worklog/2026-08-01-c03-px4-airsim-link.md)

**Symptom, reproduced twice.** The *unmodified* Lane A `offboard_control` node arms against
Lane C and then never climbs:

```
wait_for_fcu -> stream_setpoints -> request_offboard -> armed     ✓
FAILED: timeout in state takeoff                                  ✗   0/4 waypoints
PX4:  Preflight: GPS Vertical Pos Drift too high
PX4:  Ready for takeoff!  ->  Disarmed by auto preflight disarming
```

**What is already ruled out.** The first version of this failure was a `settings.json` bug —
a `Sensors` block *replaces* the defaults rather than extending them, so listing only the
barometer left the vehicle with no IMU, GPS or magnetometer. Fixed; the complaint moved from
`ekf2 missing data` to `GPS Vertical Pos Drift too high`, which is the difference between
having no GPS and having one that will not settle. **The controller is not at fault** — it is
byte-identical to the one that scores 10/10 in Lane A.

### Diagnosed 2026-08-01 — root cause found. Evidence: [`../worklog/2026-08-01-c09-lockstep-dead-and-the-35m-offset.md`](../worklog/2026-08-01-c09-lockstep-dead-and-the-35m-offset.md)

**Two real defects; only one causes the failure.**

**(a) Lockstep is dead code — CONFIRMED, and NOT the cause.** `initialize()` sets
`lock_step_enabled_` (`:66`) and `openAllConnections()` (`:68`) clears it *twice* before
returning — `close()`→`disconnect()`→`resetState()` (`:957`) and directly (`:992`). Nothing
sets it again, so the `:1613` guard can never pass. Runtime confirms: **zero** `"Enabling
lockstep mode"` across a full session while another message from the *same* `addStatusMessage`
path does appear; measured RTF 0.9193 tracks wall time. **`"LockStep": true` is silently
ineffective, so every Lane C timing number is free-running** — recorded in `versions.lock` and
`sim/ue5/settings.json`. It does not explain the takeoff failure; free-running SITL flies fine.

**(b) The vehicle thinks it is already at 35 m — THIS is the cause.**

```
z (NED, negative = up):  min=-35.168  max=-35.166  samples=9415   <- 2 mm spread
ref_alt          88.113 m   (EKF local origin)
altitude_msl_m  123.280 m   (GPS)          difference = 35.167 m  <- exactly the stuck z
dist_bottom       0.0999 m  z_valid true  fix_type 3  sats 15     <- it is ON THE GROUND
```

The EKF's local origin sits 35.17 m below where GPS says the vehicle is. Nothing is drifting;
`GPS Vertical Pos Drift too high` is that 35 m disagreement under a misleading name.

**Why it stops the flight:** `offboard_control.py:344` captures home as **x,y only (z dropped)**
and `:395` targets **absolute** ENU z = 10 m. In Lane A the vehicle rests at z ≈ 0 so that is
10 m AGL; in Lane C it reports +35.17 m, so **the controller commands a 25 m descent into the
ground**. `_reached()` measures 25.17 m against a 1 m radius, never passes, times out — and PX4,
having armed without taking off, auto-disarms via `COM_DISARM_PRFLT`. Every symptom accounted for.

**The controller is not at fault and must not be patched.** It is byte-identical to the one
scoring 10/10 in Lane A and correct for any sim whose origin is at ground level. The fix belongs
in the sim — a controller needing per-lane altitude fudging is not the same controller, which is
the whole parity claim.

**Open question, deliberately not silently fixed:** capturing `cur[2]` at `:344` would make the
controller origin-agnostic, but it changes what `takeoff_altitude` means (AGL vs local-frame
absolute). Decide it; do not patch it mid-diagnosis.

**(c) CORRECTION — the sensor diagnosis above was inferred, and measuring refuted it.**
Querying directly: `getBarometerData` → 122.883 m, `getGpsData` → 123.280 m. **They agree.**
There is no sensor disagreement; the 35 m lives entirely in PX4's `ref_alt`, an origin set at
88.113 m and never revised while both sensors read ~123 m throughout. So it is a **stale EKF
origin**, i.e. a **startup-ordering** problem, not a sensor one — and `OriginGeopoint` would
have fixed nothing.

**Confirmed by restarting `lane-c-px4` alone, sim untouched:**

```
before:   ref_alt  88.113 m    z = -35.167 m
after:    ref_alt 123.280 m    z =  -0.0002 m     <- matches GPS exactly; no config change
```

**This also explains the intermittent bring-up** that `C-03` recorded and could not pin down —
an order-dependent origin works some runs and not others. Same root, two symptoms.

**(d) RESULT — it flies.**

```
reached takeoff altitude 10.0 m
waypoint 1/4 (0.78 m)  2/4 (0.79 m)  3/4 (0.78 m)  4/4 (0.78 m)
landed and disarmed          outcome: success   4/4
```

Peak −10.233 m NED against a 10 m target; AirSim truth and PX4 EKF tracked within 0.8 m.
Video: `out/lane-c/lane-c-flight-SUCCESS-2026-08-01.mp4`. **The controller was never patched**,
which is what makes the parity claim mean anything.

**Remaining work:**

1. **Make the ordering deterministic in the bring-up** so this cannot regress — PX4 must
   initialise its EKF origin only after the vehicle has settled. The restart is the *diagnosis*,
   not the fix; the launch layer should enforce it, and a gate should assert `ref_alt` matches
   GPS before a run counts. **Filed as `C-10`.**
2. **Patch lockstep separately** — restore `lock_step_enabled_` from `connection_info_` rather
   than forcing false in `resetState()`, which also survives reconnects. Vendored C++: needs a
   recorded patch plus a plugin rebuild, and **two** copies of the header exist
   (`AirLib/…` and `Unreal/Plugins/AirSim/Source/AirLib/…`) — both must be patched.
3. **Decide the AGL-vs-absolute question** above.

<details><summary>Original hypotheses as filed (kept — 1 and 2 both proved real)</summary>

1. **Settle whether lockstep is actually engaged.** Highest-value question in the lane.
   `initialize()` sets `lock_step_enabled_`; `openAllConnections()` → `resetState()` appears
   to clear it. If AirSim free-runs while PX4's `lockstep_scheduler` is active, sensor cadence
   and sim time diverge — and GPS vertical drift is exactly what an EKF would then report.
   **The same defect would explain the intermittent bring-up deadlock already observed**, so
   two open symptoms may have one cause. Measure it; do not read it off the config.
2. **Set `OriginGeopoint`.** Currently unset, so AirSim's GPS origin and PX4's `LPE_LAT`/
   `LPE_LON` may disagree — `04` flags this as where AirSim+Cesium coordinate mismatches bite.
3. **Confirm AirSim physics steps at all**, by commanding the vehicle over AirSim's own RPC
   with PX4 out of the loop. If it does not move there either, the problem is upstream of PX4
   entirely.

</details>

**Done when:** the Lane A controller, unchanged, reaches all four waypoints in Lane C — and
the lockstep question is answered with a measurement, not an inference.

**Blocks:** `C-07` (a flight gate needs a flight), and therefore `C-05`.

---

## C-10 — Make the EKF-origin ordering deterministic

**Status:** 🟡 **built and verified once (2026-08-01); "N times in a row" not yet run.**
Evidence: [`../worklog/2026-08-01-c10-deterministic-bringup.md`](../worklog/2026-08-01-c10-deterministic-bringup.md)

`scripts/lane_c_up.sh` cold-starts the stack in 83 s unattended and `scripts/check_ekf_origin.py`
asserts the origin before anything flies. On its first honest cold start the check **caught a real
stale origin at 9.069 m** — not a replay of `C-09`'s 35.167 m but a fresh race at a different
magnitude — restarted PX4, re-verified at 0.000 m apart, and the stack then flew **4/4**
(errors 0.772 / 0.786 / 0.773 / 0.773).

**Two things recorded against the obvious write-up:**

- **The settle-wait alone was NOT sufficient** — the origin still came up 9 m stale. What saves
  the run is the verify-then-restart loop. The honest description is *"verify and repair"*, not
  *"order it correctly"*; deleting the retry loop as redundant would break it.
- **The check reported `OK` on a `NaN`.** `abs(nan - x)` is `nan` and `nan > tol` is False, so it
  green-lit the one state it existed to catch — PX4 publishes `ref_alt` as NaN until the EKF has
  any origin. Guarded, regression-tested, and `UNKNOWN` now differs from `STALE` in the exit code.
  This repo already had `test_nan_error_must_not_pass` for the same class; the lesson was not
  applied to new code.

**Gate integration landed 2026-08-01.** `run_gate.py` now asserts the EKF origin before each
run. A void run is **excluded from the success rate** (it never measured the flight code) and
**separately blocks the criterion** — excluding without blocking would let a gate where 9 of 10
runs were void report 100%. Scoring moved into a pure `score()` so the semantics are unit-tested:
all-void is not a pass, an empty run list is not a pass, and a real failure is still a failure.
The pre-existing `--reuse` caveat survives the rework. `--no-origin-check` exists as an escape
hatch for stacks without `/fmu/out/vehicle_gps_position`.

**One bug caught before it shipped:** the first version ran the checker with `sys.executable` on
the gate host — where `ros2` does not exist — which would have made **every** run void and left
the gate permanently INCONCLUSIVE. A check that fails closed on its own plumbing disables a gate
as surely as one that fails open. It now execs into the `ros2` service the same way
`run_scenario.py` does, piped over stdin rather than assuming a mount path, and an AST test pins
the call site.

**Still open:**

- ~~The live Lane A gate run is UNVERIFIED.~~ **Verified 2026-08-01.** I had written the port
  collision up as if it blocked this; it does not. The lanes only conflict when run
  *simultaneously*, and running them one at a time is the same sequencing already used to capture
  the `C-03` parity diff. Tore Lane C down, freed all five ports, ran the Lane A gate: seed 1
  **PASS, worst 0.375 m, `void: false`** — the origin check exec'd into the `ros2` service and
  correctly did **not** void a healthy run, which is the exact regression that mattered. Report
  carries `valid_total`, `voids: 0`, and the updated criterion string.
  **Full 10-seed gate then re-run under the new code: 10/10, SR 100%, `met: true`,
  `voids: 0`, worst error 0.555 m across all seeds, 1350 s wall.** So the origin check adds a
  per-run assertion without disturbing the Phase 1 result.

  **Lesson: "blocked by a resource conflict" deserves one second of thought about whether the
  conflict is concurrent or absolute.** It was concurrent, and the work was twenty minutes away.
- Run the cold start N times; the 5-reads-at-0.05 m settle heuristic was chosen, not derived.

`C-09` proved the vehicle only flies when PX4 initialises its EKF origin **after** the sim
vehicle has settled. Today that is achieved by restarting `lane-c-px4` by hand once the sim is
up — a diagnosis, not a fix. Left as is, Lane C flies or does not depending on container start
order, which is exactly the intermittency `C-03` could not pin down.

**Do:**

1. **Enforce the ordering in the bring-up** rather than relying on luck or a manual restart —
   PX4 waits until AirSim reports the vehicle settled before connecting.
2. **Assert it in the gate.** A run must not count unless `ref_alt` agrees with
   `vehicle_gps_position.altitude_msl_m` to within a metre at start. The failure mode is silent
   and looks like a control bug, so it needs a check that names it. This is the Lane C analogue
   of `P1-08` — distinguishing a void run from a real one.

**Done when:** cold-starting the whole stack from nothing produces a flyable vehicle N times in
a row with no manual intervention, and a deliberately mis-ordered start is *failed by the gate*
rather than scored.

**Blocks:** `C-07` — a flight gate that can silently score a mis-ordered stack is worse than none.

### Review fixes verified live — 2026-08-02

`/review` on both stacked PRs found three defects; all fixed, and the Lane A gate re-run to
prove the gate change did not regress:

```
10/10  SR 100%  voids 0  met true  worst 0.565 m   1220 s
```

**Faster than before the fix** (1220 s vs 1350 s; 124 s/seed vs 135 s) — the barrier returns
immediately when the origin is already sane, so it costs nothing on a healthy stack and only
spends time when it is actually preventing a bad run. Worth measuring rather than assuming: a
per-seed wait is exactly the kind of change that quietly triples a gate.

- **The gate had no barrier before the origin check.** `restart_stack()` returns on *container
  health*, not on the EKF establishing an origin, so the check raced the estimator. Any void
  blocks the criterion, so one slow start would have turned the whole gate INCONCLUSIVE. It
  passed 10/10 before the fix **by timing coincidence, not by construction.** Now waits up to
  90 s on `VOID_UNKNOWN` and voids immediately on `VOID_STALE` — the first actual use of a
  distinction those exit codes always carried.
- **The depth assertion could not fail.** `max(pos) > 0.5` passes on a frame where every pixel
  is the 16312 m no-return sentinel. Replaced with `depth_is_usable()`, requiring bounded
  returns.
- **`versions.lock` contradicted itself** — `status: LOCKED` alongside `why_not_LOCKED_yet`.
  Fixed on both branches, and `check_versions_conflicts.py` now fails CI on the whole class.

56 tests, each new one verified by breaking the code it guards.

---

## C-04 — Camera/depth/LiDAR into the existing ROS 2 graph

**Status:** ✅ **`done` (2026-08-02)** — **sensor data is properly published to ROS 2, verified
by value.** Closed on the owner's criterion: the acceptance is that the modalities reach the
ROS 2 graph usably, which they do.

```
RGB     31.2 Hz  640x480 rgb8     Depth   29.6 Hz  32FC1, metric, real geometry
LiDAR   17.4 Hz  8192 points      IMU    366 Hz published / 311 Hz distinct
GPS / magnetometer / odometry  365 Hz    camera_info resolves in TF    /clock advancing
```

Verified with `scripts/verify_lane_c_sensors.py`, which asserts **values** rather than topic
presence — IMU reads 9.807 m/s² at rest, depth contains bounded returns, LiDAR points are not
all at the origin, RGB is not a blank frame.

**Carried forward rather than blocking** (all recorded in
[`../vendor/cosys-airsim.md`](../vendor/cosys-airsim.md)): the IMU is a polled snapshot with
~15% duplicate timestamps; frames are NWU with a tested conversion in `control/frames.py` that
nothing calls yet; and the unexplained 7.342° yaw residual. None of these stop the data being
published and usable — they are `C-05`'s problem, and `C-05` is deprioritised.

### Where it actually stands

`airsim_node` builds (both packages, 1 min 22 s — matching `C-06`), connects to AirSim, logs
`Connected!` and `AirsimROSWrapper Initialized!` — **then dies before publishing anything:**

```
terminate called after throwing an instance of 'eprosima::fastcdr::exception::BadParamException'
  what():  The string contains null characters
[ros2run]: Aborted          ->  0 /airsim_node/* topics
```

A Fast-CDR serialisation fault: a string field carrying embedded NULs.

**Localised by backtrace (gdb, in-container), not by guessing:**

```
#9  geometry_msgs::msg::TransformStamped cdr_serialize
#10 tf2_msgs::msg::TFMessage cdr_serialize
#16 tf2_ros::TransformBroadcaster::sendTransform
#18 AirsimROSWrapper::publish_odom_tf(nav_msgs::msg::Odometry const&)   <- HERE
#19 AirsimROSWrapper::publish_vehicle_state()
#20 AirsimROSWrapper::drone_state_timer_cb()
```

So it is the **odometry TF** — `header.frame_id` or `child_frame_id` in `publish_odom_tf`
(`airsim_ros_wrapper.cpp:1232`) — not an image, sensor or segmentation topic.

**Three candidate sources ruled out empirically:**

| Hypothesis | Checked | Result |
|---|---|---|
| Segmentation object names carry NULs | `simListInstanceSegmentationObjects` | **clean** — 255 names, 0 NULs, 0 empties |
| The RPC settings string carries NULs | `getSettingsString` | **clean** — 4827 chars, 0 NULs |
| The frame-id constants are malformed | header `AIRSIM_ODOM_FRAME_ID` / `AIRSIM_FRAME_ID` | **clean** — plain `"odom_local"` / `"world"` |

`listVehicles` returns `['PX4']`, so `vehicle_name_` — which feeds `child_frame_id` at `:1452`
— looks clean from the outside too.

### ROOT CAUSE — a data race, not a bad string. FIXED and verified.

Instrumenting `publish_odom_tf` to hex-dump both frame ids killed the fourth hypothesis too:

```
ODOMTF frame_id=[PX4/odom_local] size=14 hex=50 58 34 2f 6f 64 6f 6d 5f 6c 6f 63 61 6c
ODOMTF child=[PX4]               size=3  hex=50 58 34
862 prints, ZERO containing a 00 byte  ->  and it still aborted
```

**Clean strings that still serialise as containing NULs means the value changes between the
copy and the write** — a race. The log timestamps proved it directly: **30% of consecutive
`publish_odom_tf` prints were out of order**, i.e. the callback was executing concurrently on
several threads for a single vehicle.

```
airsim_node.cpp:22   create_callback_group(rclcpp::CallbackGroupType::Reentrant)
airsim_node.cpp:25   rclcpp::executors::MultiThreadedExecutor
```

**`Reentrant` + `MultiThreadedExecutor` lets `drone_state_timer_cb` re-enter concurrently**,
so threads race on the shared per-vehicle `curr_odom_`. Copying a `std::string` while another
thread reassigns it is a torn read, and Fast-CDR sees the result as embedded NULs. That explains
every observation at once: the strings log clean, 862 publishes succeed, then one aborts, and it
is non-deterministic.

**Fix — one word:** `Reentrant` → `MutuallyExclusive` at `airsim_node.cpp:22`.

```
node alive: yes    crashes: 0    /airsim_node topics: 14    odom_local flowing
/airsim_node/PX4/{imu/imu, gps/gps, magnetometer/magnetometer, altimeter/barometer,
                  odom_local, environment, global_gps}  + segmentation, object_transforms
```

**Method note worth keeping:** three guesses at "which string has a NUL" all died, and the
answer was that no string ever did. The backtrace narrowed the site; the hex dump refuted the
whole *class* of hypothesis and forced the race explanation. **Measuring the thing I was sure
about is what broke the deadlock** — the same lesson as `ref_alt` in `C-09`.

### The fix is NOT yet in the tree — this is vendored C++

Applied to a container-local copy only; `vendor/Cosys-AirSim` is untouched and pristine. Per
least-destructive-vendor-edits it must land as a **recorded patch plus vendoring notes**, not an
in-place edit:

1. Write the one-line change as a patch file and apply it in the build, not by editing `vendor/`.
2. Start `docs/vendor/cosys-airsim.md` (still missing) and record this as deviation #1 — it is
   an upstream defect, worth reporting to Cosys-Lab.
3. **Clean the 163 MB of build artifacts out of `vendor/Cosys-AirSim/ros2/`** and keep builds
   out-of-tree, so the tree stays diffable against upstream.
4. ~~Re-check the trap list against a running node~~ — **done 2026-08-02**, three of four
   confirmed. Evidence:
   [`../worklog/2026-08-02-c04-trap-list-measured.md`](../worklog/2026-08-02-c04-trap-list-measured.md)

   | # | Trap | Verdict |
   |---|---|---|
   | 1 | Frames NWU not ENU | **CONFIRMED** — `convert_tf_msg_to_enu()` has 0 call sites; measured yaw missed ENU by 97.3° and NWU by 7.3° |
   | 2 | `/clock` wrong topic | **CONFIRMED, double defect** — `publish_clock` is never *declared*, and it publishes to `~/clock`. Fix: `-r /airsim_node/clock:=/clock`, in the launch |
   | 3 | Polled IMU | **CONFIRMED, quantified** — 1501 Hz published, 6630 distinct, **77.9% duplicates**, real rate ~333 Hz, gaps to 3× base |
   | 4 | `camera_info` frame_id | **CONFIRMED and FIXED** — `camera_info` said `front_center_optical` while TF and the image said `PX4/front_center_optical`. Patch `0002`; verified |

   **Cameras and GPU-LiDAR are now in the graph** — `sim/ue5/settings.json` gained a `Cameras`
   block (RGB + `DepthPlanar`, 640×480) and a GPU-LiDAR (`SensorType: 8`). **19 topics, up from
   14**, carrying real data: 640×480 rgb8 and an 8192-point cloud. Parsing semantics were checked
   *before* editing — `loadCameraSettings` clears but defaults to an empty map (additive), while
   per-vehicle `Sensors` is iterated by key (so the LiDAR was added alongside the existing four,
   with an assertion that all five survive).

   **The build is also no longer ephemeral:** `patches/cosys-airsim/*.patch` +
   `scripts/build_airsim_wrapper.sh` reproduce it in ~2 min with `vendor/` pristine. The script
   applies every patch in numbered order and asserts each one's artifact.

   **Trap 2 is now fixed properly** — `ros2_ws/src/bringup/launch/lane_c_perception.launch.py`
   sets `publish_clock:=true` and remaps `/airsim_node/clock` → `/clock` unconditionally.
   `ros2 launch bringup lane_c_perception.launch.py` with **no flags** gives a ticking `/clock`.

   **Trap 1 now has a conversion, in the frozen place.** `nwu_to_enu` / `enu_to_nwu` /
   `yaw_nwu_to_enu` / `yaw_enu_to_nwu` were added to `control/frames.py` — the single conversion
   point `conventions.md` §3 mandates — not to a Lane C node. **Unlike `enu_to_ned`, this pair is
   NOT an involution** (90° rotation, so twice = 180°), which breaks the intuition the rest of
   that module earns; pinned by a dedicated test. 7 new tests, 15 in the file, verified by
   breaking the implementation. *Nothing consumes it yet* — that is `C-05`'s job.

   **Navigation-readiness verified 2026-08-02** — `scripts/verify_lane_c_sensors.py` checks
   sensor **values**, not topic presence, and all required checks pass:

   | | before | after |
   |---|---|---|
   | RGB | 1.1 Hz | **31.2 Hz** |
   | Depth | 1.1 Hz | **29.6 Hz** |
   | GPU-LiDAR | 1.6 Hz | **17.4 Hz** |
   | IMU | 1328 Hz, 77.3% dup | **366 Hz, 311 Hz distinct, 14.6% dup** |
   | GPS / mag / odom | 1330 Hz (dup) | **365 Hz** |

   The gap was **five uninitialized `double` timer periods** in the wrapper — `get_parameter`
   returns false for an undeclared name and leaves the value untouched, so every sensor rate was
   stack garbage. Fixed in the launch (with `value_type=float` forced, or it silently stays
   uninitialized). **That also re-explains trap 3:** the IMU duplicates were never inherent to
   the polled design, just a garbage poll period. Patch `0003` then removed the serialisation
   that patch `0001` had introduced.

   **Artifacts delivered (filed retroactively — these were built before being written down,
   which the plan-first rule says should not happen):** `scripts/verify_lane_c_sensors.py`,
   `scripts/build_airsim_wrapper.sh`, `patches/cosys-airsim/000{1,2,3}`,
   `ros2_ws/src/bringup/launch/lane_c_perception.launch.py`, and
   [`../vendor/cosys-airsim.md`](../vendor/cosys-airsim.md).

   **Still open on `C-04`:**
   - the unexplained 7.342° yaw residual;
   - **the simulator segfaults after ~57 minutes** — `Array index out of bounds: 18823 into an
     array of size 0`, preceded by a MAVLink `hil` EPIPE. Uncharacterised, and a real ceiling on
     long missions.

**Blocked by:** nothing — the crash is fixed; the work is now the trap list.

**What.** Bring Cosys-AirSim's sensors up on the ROS 2 C++ wrapper: RGB, depth,
GPU-LiDAR, and the annotation/segmentation cameras.

**Why.** This is what Lane B was for. Cosys-AirSim's sensor set is the richest of the living
AirSim forks — GPU-LiDAR with tunable noise and ground-truth labels, echo/radar sensors,
event cameras, camera distortion (`01_sim_stack_report.md:14`).

**The topics are a new surface, not a renamed one.** The wrapper publishes under
`/airsim_node/<vehicle>/…` with `airsim_interfaces/*` custom message types. Lane A has no
equivalent — "only the transport is swapped" was always a claim about the **controller**
(`/fmu/*`, `C-03`), never about perception. Budget for genuinely new integration here.

**Traps — four of these are documented wrong upstream, so read carefully.**

- **FRAMES ARE NWU, NOT ENU — AND THE DOCS SAY OTHERWISE.** `docs/ros2.md` claims *"the
  right-handed coordinate frame of the ROS standard and not in NED"*. The code negates only
  y and z, which is **NED→NWU** (and FRD→FLU). `convert_tf_msg_to_enu()` exists at
  `airsim_ros_wrapper.cpp:1600` and is **never called** — every path calls
  `convert_tf_msg_to_ros()` instead. **Anything written against REP-103 or `px4_ros_com`'s
  ENU assumption will be yaw-rotated 90°.** Verify empirically against a known heading
  before trusting a single pose. This is exactly the silent-frame-error class
  [`../lane-a/conventions.md`](../lane-a/conventions.md) exists to prevent, and the frozen
  convention (ENU/FLU outside, NED inside, converted in one tested place) still governs —
  Lane C does not get to invent a second convention, but it does have to *reach* the first
  one from NWU.
- **`/clock` IS PUBLISHED ON THE WRONG TOPIC.** `publish_clock` publishes to `~/clock`,
  which resolves to `/airsim_node/clock` on a node named `airsim_node` — **not** `/clock` —
  and it defaults to `False` in the launch file. **Lane A already paid for this exact
  failure shape in `P1-03a`:** `use_sim_time: true` with nothing publishing `/clock` freezes
  every node's timers at zero and looks precisely like a deadlocked controller. Remap it.
- **IMU IS A POLLED SNAPSHOT, NOT A STREAM.** `publish_vehicle_state()` calls `getImuData()`
  over RPC once per `drone_state_timer` tick (default 0.01 s) and publishes only the latest
  sample — intermediate samples are dropped and spacing is set by RPC/executor jitter, not
  the sensor. cuVSLAM and any preintegrating VIO expect a dense, evenly-spaced stream.
  **Measure the actual arrival distribution.** IMU messages also ship with zero covariances
  (`// todo covariances` in the source).
- **`camera_info.header.frame_id` does not match the TF tree** — it is `<camera>_optical`
  while the image and the static TF use `<vehicle>/<camera>_optical`, so `image_proc`,
  `depth_image_proc` and any TF-aware perception node cannot resolve it. Trivial patch, but
  it bites on first integration.
- **Sensor cadence versus lockstep.** `04`'s own decision threshold: if UE5 lockstep cannot
  sustain sensor cadence without PX4 timeouts, split across GPUs/hosts or fall back to
  Isaac/Pegasus for physics-critical experiments. Note this interacts with `C-03`'s defect 2
  — confirm lockstep is genuinely engaged before concluding anything about cadence.
- **`odom_local` is sim ground truth, not an estimate.** It comes from
  `getMultirotorState()`/`kinematics_estimated` — the physics engine's truth, not PX4's
  EKF2. On the real Pixhawk 6C the equivalent `/fmu/out/vehicle_odometry` is a *noisy
  estimate*. **Tuning or training against noiseless odometry is a sim-to-real gap that will
  not surface until the real flight.** Prefer `/fmu/out/vehicle_odometry` wherever both
  exist.

**Acceptance.** Depth and LiDAR topics at stable rates, with **measured** timestamp jitter
and IMU inter-arrival distribution recorded — not asserted — and a heading check proving
which frame convention the poses actually arrive in.

---

## C-07 — A Lane C flight gate

**Status:** `todo` · **Blocked by:** `C-04`

**What.** Port `P1-06`'s success-rate gate to Lane C: the same 4-waypoint square, run across
10 seeded conditions, scored the same way, with an MCAP per run.

**Why this is now necessary.** Lane A was demoted to the regression baseline, which means it
proves *Lane A* keeps working — it says nothing about Lane C. Without its own gate, Lane C
is a simulator with no acceptance criterion, and every later result rests on an unmeasured
foundation. **Lane A's SR 10/10 does not transfer.**

**Reuse, do not rewrite.** `scripts/run_gate.py` already re-derives pass/fail from the
numbers rather than trusting the controller's `outcome` field, and rejects non-finite errors
— a check that was missing in its first version and caught a real NaN-laundering bug. That
logic is lane-independent; what changes is the stack it brings up and what "seeded
conditions" means (AirSim's wind API rather than a Gazebo world overlay).

**Acceptance.** SR reported over 10 seeded Lane C runs, with the per-seed table and MCAP
paths, **plus a stated comparison against the Lane A baseline** for the same mission.
A gap is a finding, not a failure — but an unexplained gap blocks Phase 2.

**Trap.** Lane A's gate takes ~19 min and already misses the 10-minute CI budget; a UE5
stack will be slower to start, not faster. Decide the seed count with evidence, and **do not
fit a budget by quietly weakening the gate** (`P1-06`).

---

## C-05 — Isaac ROS perception on Lane C imagery

**Status:** ⏸️ **deprioritised 2026-08-02 by the owner** in favour of `C-11` (photorealistic
scene + dynamic actors). **Unblocked, not abandoned** — `C-04` is done and the imagery is ready
whenever this resumes.

**What it will inherit when it does**, so the next person does not rediscover it: cuVSLAM
preintegrates IMU between frames and wants a dense, evenly-spaced stream, and Lane C's IMU
carries ~15% duplicate timestamps by upstream design. Starting **visual-only** and adding
inertial afterwards is the lower-risk order. Frames are also NWU, and `control/frames.py` has a
tested conversion that nothing calls yet.

**Originally:** `todo` · **Blocked by:** `C-04`

**What.** Run `isaac_ros_visual_slam` (cuVSLAM) and `isaac_ros_nvblox` against Lane C
camera/depth topics.

**Why — worth stating clearly:** **Isaac ROS is not Isaac Sim.** cuVSLAM and nvblox are
ROS 2 **Jazzy** packages that consume image and depth topics from *any* source. Deferring
Lane B does **not** cost us the GPU perception stack; it only costs the renderer. The
Phase 2–3 perception plan stands unchanged.

**This is also a second, independent argument for the Jazzy decision** (`C-06`): these
packages are Jazzy, and moving the project to Humble to satisfy a documentation preference
would put the perception stack on the wrong side of the split.

**Acceptance.** cuVSLAM produces odometry from Lane C imagery; nvblox produces a costmap.

---

## C-11 — Load the user's own world (bring-your-own `.uproject`)

**Status:** 🔴 **open — the current focus.** Filed 2026-08-02; **rescoped 2026-08-03** after the
owner corrected the goal.

**The goal is a MECHANISM, not a scene.** The user builds or buys a photorealistic world
wherever they like — Fab on a Windows box, their own Unreal work, a colleague — and tells this
simulator to load it. Picking one world for them is not the deliverable; **the pipeline that
accepts theirs is.**

**Corrections that produced this rescope** — the first survey answered a narrower question and
got several constraints wrong:

- **Authenticating on a non-Linux machine is acceptable.** The "Fab needs the Epic Games
  Launcher, which has no Linux build" finding is therefore *not* disqualifying. Users can fetch
  on Windows/macOS and copy the project across; upstream's own docs say exactly this.
- **Rule 6 was applied too strictly.** The project already carries a documented amendment —
  *"from the repo alone, plus one documented credential step"* (`drone-sim-todo.md:105`). A
  Cesium ion token or an Epic account fits that precedent. Cesium's real cost is that tiles
  **stream at runtime** (a run needs network), not a licence conflict.
- **The NoAI/`isAiForbidden` clause was over-read.** It targets training generative models on
  the assets. Running SLAM, optical flow or 3D mapping over rendered frames is not that. Worth
  a second look only before *fine-tuning* a model on rendered frames.
- **This project is not only VLM.** It is drone simulation with a photorealistic world for 3D
  mapping, optical flow, visual SLAM and similar. The earlier emphasis on per-object
  segmentation reflected the narrower reading.
- **"Fused mesh hurts perception" was overstated.** VSLAM consumes images and cannot see mesh
  topology; optical flow likewise; for 3D mapping a fused mesh is a natural ground-truth
  reference. Fused geometry also still has *collision* — you simply cannot move or delete an
  individual building. It costs per-object semantic labels, and nothing else on that list.

### There is no upstream world to adopt — verified

Cosys-AirSim ships **exactly one environment: `Blocks`** (1.0 GB of untextured grey boxes).
Every upstream release from 5.2 to 5.8 publishes `Blocks_packaged_*` / `Blocks_editor_project_*`
and **nothing else, ever**. `DynamicObjects` (19 MB) is a *library*, not a world. The classic
Neighborhood / Landscape Mountains / Africa environments belong to Microsoft AirSim, are
**UE4.27 cooked binaries with no `.uproject` or `Content/`**, and cannot take a UE5.8 plugin.

### The mechanism: inject AirSim into the user's project

Upstream documents this manually; **all of it is text**, and one step that the docs present as
a GUI action is avoidable:

1. copy the prebuilt `Plugins/AirSim` folder into their project
2. add `AirSim` + `ChaosVehiclesPlugin` to the `"Plugins"` array of their `.uproject`
3. `Config/DefaultEngine.ini` → `GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode` and
   `GameDefaultMap=<their map>`
4. `Config/DefaultGame.ini` → the `+DirectoriesToAlwaysCook` entries for AirSim content

**Step 3 is the finding that makes this scriptable at all.** Upstream's step 9 says to set
`GameMode Override` in `Window/World Settings` — a GUI operation. But `AAirSimGameMode` is a
**plugin** class (`AIRSIM_API`, `Plugins/AirSim/Source/AirSimGameMode.h`), so it can be named
directly in config and applied globally. `Blocks` proves the pattern works, using its own
project class: `GlobalDefaultGameMode=/Script/Blocks.BlocksGameMode`.

### Two tiers, and the sequencing matters

| | what it is | cost |
|---|---|---|
| **A1** | user's project is **content/Blueprint-only** | pure text edits + a folder copy. No compile, no GUI. Most Marketplace environment projects. |
| **A2** | user's project has its own **`Source/` C++** | UnrealBuildTool must compile *their* module against UE5.8, inheriting their engine-version assumptions. |

**Do A1 first.** It is the common case, fully scriptable, and testable today. A2 and
engine-version conversion carry the unknowns, and they are better discovered against a working
A1 pipeline than treated as a prerequisite.

### ✅ A1 IS BUILT AND PROVEN END TO END — 2026-08-03

`scripts/inject_airsim.py` takes a user's `.uproject` and injects AirSim with **no compile, no
editor, no GUI, no display**. Verified against a project that was never ours:

```
server version: 4              vehicles: ['PX4']
position: x=0.00 y=0.00 z=2.29 scene objects: 32
from OUR level: ['PlayerStart_0']
sim log: "Waiting for mavlink vehicle..."
         WeatherActor_C /Game/Maps/TheirMap.TheirMap:PersistentLevel.WeatherActor_C_0
```

The second log line is the load-bearing one: **the AirSim plugin spawned its own actors into
the user's map**, and the PX4 vehicle from *our* `settings.json` appeared in *their* project.

**What the script does** — four text edits and a folder copy:
1. copies the **built** plugin (`Blocks/Plugins/AirSim`, 506 MB, has `Binaries/Linux/*.so`)
2. enables `AirSim` + `ChaosVehiclesPlugin` in their `.uproject`
3. `DefaultEngine.ini` → `GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode` (+ optional map)
4. `DefaultGame.ini` → 9 cook directives

**Design decisions worth keeping:**

- **It refuses the source-only plugin copy.** `Unreal/Plugins/AirSim` (330 MB) has no
  `Binaries/`, so injecting it would silently force a UnrealBuildTool compile — turning A1 into
  A2, which is the entire distinction this path exists to remove. The script hard-fails with
  that explanation rather than producing a project that mysteriously wants to build.
- **It refuses to inject into anything inside this repo**, so `vendor/` cannot be dirtied.
- **Idempotent**, verified by running twice: one `GlobalDefaultGameMode`, one `AirSim` entry,
  8 cook directives, and the user's own settings (`RendererSettings`, their existing plugins)
  **preserved**.
- **Hand-rolled ini editing rather than `configparser`**, which mangles Unreal's `+Key=` repeat
  syntax and `[/Script/Foo.Bar]` section names on write. Losing a user's settings while "adding"
  ours would be a bad trade.
- **Asserts its artifacts** — plugin descriptor, `.so` present, plugins enabled, and exactly
  **one** `GlobalDefaultGameMode` (two would mean a later one wins silently).
- Warns, but continues, when `EngineAssociation` is not 5.8 or when `Source/` exists (A2).

### The engine-version question, answered — 2026-08-03

**A project declaring an older engine opens in UE5.8 headless and unattended. No conversion
step is required to launch it.** Measured: a project with `EngineAssociation: "5.2"`, injected
and launched with `-game -RenderOffScreen -unattended`, loaded its map and initialised AirSim:

```
Waiting for mavlink vehicle...
WeatherActor_C /Game/Maps/TheirMap.TheirMap:PersistentLevel.WeatherActor_C_0
```

No conversion dialog, no refusal, no prompt. This was flagged as the likely dealbreaker for
bring-your-own-world — the concern being that UE's "this project was made with an older
version" dialog would block automation. It does not, under `-unattended`.

**What this does NOT prove, stated precisely:** the test project's assets were authored *by
5.8*; only the version *declaration* was changed. So the `EngineAssociation` mismatch is proven
harmless, but genuinely 5.2-era `.uasset` files loading in 5.8 is **not** proven. Those are
different claims and the second still needs a real old project. UE reads older package versions
by design (backward compatibility is the supported direction; *forward* is what breaks), so it
is likely — but "likely" is not "measured".

**Practical consequence:** a user downloads whatever version Fab offers and copies it across.
An editor pass on Windows/macOS is **optional**, and worth doing only to absorb first-run shader
compilation (the City Park seller quotes ~2.5 h) rather than because conversion is required.

**The one case that still needs Windows:** a project shipping its own `Source/` C++ is A2, and
that C++ must compile against UE5.8. `inject_airsim.py` detects this and warns rather than
pretending it is A1.

### Tested against a REAL Fab project — 2026-08-03. Both open threads closed.

**City Park Environment Collection** (SilverTm, free on Fab, 3.5 GB zip / 4.1 GB extracted),
downloaded on a non-Linux machine and copied across. It is **A1** — 2,364 zip entries and
**zero C++ source files** — so the no-compile path applies.

**1. The engine-version question is answered, and far more strongly than the synthetic test
managed. City Park declares `EngineAssociation: "4.24"` — a UE4 project from 2019.** It loaded
in UE5.8 headless with no conversion, no dialog, and no loader errors:

```
server version : 4        vehicles: ['PX4']
scene objects  : 856      park geometry: Landscape_0, TennisBenchHISMA, Bench03HISMA, ...
```

856 objects against 32 in the synthetic test, and camera frames show real trees, foliage and a
stone stairway. **A UE4→UE5 major-version jump loaded fine, so no Windows editor pass is needed
for conversion.** (The earlier synthetic test only changed a version *declaration*; this loaded
genuinely 7-year-old assets.)

**2. A gap in the mechanism, and it will affect every user world: THE DRONE SPAWNED
UNDERGROUND.** Reported `z = +8.25` in NED — positive is *down*, so 8.25 m below origin. The
first camera frame was the underside of the terrain with light bleeding through.

Cause: **the Showcase level contains no `PlayerStart` and no `TargetPoint`** — verified via
`simListSceneObjects`. AirSim therefore spawns at world origin, and City Park's ground sits
*above* origin. Lifting the vehicle with `simSetVehiclePose` confirmed it — the park rendered
correctly for the moment it was above ground, then gravity pulled it back under.

**This is not a City Park quirk; it is intrinsic to bring-your-own-world.** An arbitrary user
world has no obligation to put its ground at the origin or to ship a `PlayerStart`. Spawn
placement therefore belongs in the mechanism, not left to the world.

**Next on this thread:** give `inject_airsim.py` a spawn-position option that writes the
per-vehicle `X`/`Y`/`Z` into `settings.json` (AirSim supports it), and work out how to *find* a
sane spawn automatically — the landscape bounds are available over RPC. Requiring users to edit
their level to add a `PlayerStart` would defeat the point of the path.

### 3. "No conversion needed" needs qualifying — it LOADS correctly, it does not RENDER correctly

Frames captured from altitude show the park is really there — plaza, steps, mature trees, a
road, a pond — but **everything is hazy and blown out** (mean pixel 156–191 where ~110–140 is
normal exposure), with a cyan cast.

**A wrong hypothesis, recorded because the measurement is the useful part.** Upstream's
`unreal_custenv.md` step 7 warns of a "camera scene rendering bug" on UE5.3+, fixed by copying
`DefaultScalability.ini` (`r.DetailMode=2` at every quality level). `Blocks` ships one; City
Park did not; **`inject_airsim.py` was not copying it.** That was a genuine missing step and is
now fixed — but it did **not** fix the washout: re-measured means came back
156.4 / 182.9 / 176.1 / 191.1 against 156.5 / 182.8 / 175.9 / 191.0. Identical to three
significant figures. The step is still correct to apply; it just is not this bug.

**The likely actual cause, from the level's own actor list:**

```
Fog        : AtmosphericFog_1, ExponentialHeightFog_1
Atmosphere : 0   <- no SkyAtmosphere
Light      : 18  PostProcess: 4
```

**`AtmosphericFog` is the UE4 fog actor, deprecated in UE5 and superseded by `SkyAtmosphere`.**
This map carries the deprecated one with no replacement, and its four `PostProcessVolume`s hold
UE4-era settings. That is a very plausible source of a hazy, over-bright sky.

**So the earlier conclusion is right but incomplete, and the distinction matters:**

| claim | verdict |
|---|---|
| A UE4.24 project **loads and runs** in UE5.8 headless, no conversion | **true, proven** — 856 objects, AirSim up, world navigable |
| It **renders correctly** without conversion | **false for this project** — deprecated fog, UE4 post-process |

For a project whose whole point is *photorealism*, "loads" is not the bar. **An editor pass on
Windows/macOS is worth doing after all** — not to make it load, but to let UE replace deprecated
actors and rebuild lighting. That is a much smaller claim than "conversion is required to run",
and it is the one supported by evidence.

**Also worth saying plainly: City Park is a park, not a cityscape.** Paths, trees, playgrounds,
benches, a pond. There are no street canyons or building interiors. Fine for low-altitude
obstacle avoidance and visual SLAM over natural clutter; wrong if the goal is urban navigation.

### Why it does not look photorealistic — two real config gaps found, cause still open

Chasing the washed-out render turned up **two genuine gaps in `inject_airsim.py`**, both worth
having regardless of whether they explain the look:

1. **`DefaultScalability.ini` was not being copied** — upstream's step 7 fix for the UE5.3+
   "scene camera bug" (`r.DetailMode=2` at every quality level). Blocks ships one; a user
   project will not.
2. **`+TargetedRHIs=SF_VULKAN_SM6` was not being set, and this one is significant.** UE5's
   photorealism *is* Lumen and Nanite, and **both require Shader Model 6**. On Linux/Vulkan the
   engine falls back to SM5 unless the project asks for SM6. Measured: Blocks sets it and runs
   `rhifeaturelevel="SM6"`; City Park had no such line and came up `VULKAN_SM5` with *"Vulkan
   RayTracing disabled because SM6 shader platform is required."* Blocks also **removes** SM5
   (`-TargetedRHIs=SF_VULKAN_SM5`) — adding SM6 alone is not enough. Both lines now injected.

   **Consequence for earlier results:** Blocks has been SM6 all along, so `C-04`'s sensor-rate
   numbers were measured on the full renderer and remain valid.

**Neither fixed the appearance, and the measurements say so.** Frame means across four vantage
points, before → after scalability → after SM6:

```
156.5 / 182.8 / 175.9 / 191.0     (original)
156.4 / 182.9 / 176.1 / 191.1     (+ DefaultScalability.ini)
156.5 / 182.7 / 176.2 / 191.3     (+ SM6)
```

Identical to three significant figures. Both fixes are correct to keep; neither is this bug.

**Ruled out by measurement, not assumption:** the scalability fix; SM5-vs-SM6; a missing
`ExtendDefaultLuminanceRange` (neither project sets it); and missing baked lighting —
`Showcase_BuiltData.uasset` (8.4 MB) *is* shipped with the map.

**Still open.** Geometry and textures render correctly — trees, plaza steps, road and pond are
all clearly visible — so this is not a loading or asset problem. It is a **lighting/tonemapping**
problem. The remaining suspects, none confirmed: the map's **four `PostProcessVolume`s carrying
UE4-era settings**, the deprecated **`AtmosphericFog`** actor (UE5 replaced it with
`SkyAtmosphere`, and the level has no `SkyAtmosphere`), or baked lighting that is present but
version-stale for UE5.

### PROVED BY A SIDE-BY-SIDE: the world renders beautifully, AirSim's capture does not

The decisive test was to bypass AirSim entirely and take **Unreal's own** screenshot of the same
scene, from the same viewpoint, in the same process — triggered over RPC with
`simRunConsoleCommand("HighResShot 1920x1080")`, which goes through UE's normal render path
including tonemapping.

```
UE native capture : mean 172.9   std 41.4   min 28   max 255
AirSim capture    : mean ~176-191, washed out, cyan cast, flat
```

**The native screenshot is photorealistic**: green grass with cast shadows, a turquoise lake
with visible depth, a terracotta paved plaza and steps, trees with correct shadowing, the drone
hovering over the water. Saved as `out/lane-c/citypark_UE_native.png`.

Same scene, same frame, same GPU, same process. **The only difference is which capture path
produced the pixels.** The environment was never the problem, and neither was the UE4 origin of
the assets — a conclusion that took three wrong hypotheses to reach and one direct comparison to
prove.

### ROOT CAUSE — it is the AirSim capture source, not the world

`Unreal/Plugins/AirSim/Source/PIPCamera.cpp:178`:

```cpp
if (image_type == Scene || image_type == Lighting)
    captures_[image_type]->CaptureSource = SCS_FinalToneCurveHDR;   // <-- HDR
else
    captures_[image_type]->CaptureSource = SCS_FinalColorLDR;
```

**The Scene camera captures `FinalToneCurveHDR`** — values after the tone curve but *before*
the final LDR/sRGB encode — and AirSim packs that into 8-bit RGB. That is a gamma-encoding
mismatch, not an exposure fault.

**It explains every observation, including the ones that refuted my earlier hypotheses:**

- **Why nothing in the world mattered.** Six interventions — `DefaultScalability.ini`, SM5→SM6,
  the UE4→UE5 package conversion, and replacing `AtmosphericFog` with `SkyAtmosphere` — all
  landed *upstream* of an encoding fault that happens afterward. Frame means stayed within
  ±0.7 across all six.
- **Why Blocks looks fine.** Grey boxes are low-dynamic-range, so the mis-encode barely shows.
  A bright outdoor HDR scene makes it glaring.
- **The histogram:** `min=13 max=251`, **0% saturated**. Nothing is clipped — the range is
  simply distributed wrongly, which is a gamma signature, not an overexposure one.

### The plan to fix it, and an honest correction to the confidence above

**Written as `patches/cosys-airsim/0004-scene-capture-ldr.patch` but NOT yet validated.**

| # | step | status |
|---|---|---|
| 1 | Characterise the fault quantitatively | **abandoned — kept getting confounded** |
| 2 | Write the one-line patch (`SCS_FinalToneCurveHDR` → `SCS_FinalColorLDR`) | done |
| 3 | Rebuild the plugin inside `Blocks`, re-inject, capture, compare visually | **plugin BUILT and running; comparison BLOCKED** |
| 4 | Decide the HDR-vs-LDR policy and record it | after 3 |

**Step 1 was abandoned deliberately, and that is worth recording.** Four attempts to build a
controlled A/B each acquired a new confound: comparing different viewpoints (onboard camera vs
chase camera); the vehicle falling between the two captures because `simSetVehiclePose` does
not hold it; `ViewMode: Fpv` not matching `front_center`; and the native screenshot being
letterboxed, which skews every percentile. Even with `simPause` freezing the frame to 0.000 m
drift, correlation between the two paths was 0.35 — i.e. still not the same view.

Two post-hoc corrections also failed: applying an sRGB **encode** made contrast worse
(std 39.9 → 11.4), and a **decode** overshot (std → 57.0 against a 36.8 reference). Neither is
evidence about the true cause, because the images being compared were not the same frame.

**So the earlier claim that this is "a gamma mis-encode with a one-line fix" is not established.**
What *is* established: AirSim's Scene capture of this world looks visibly worse than Unreal's own
render of the same world, and six world-side interventions changed the AirSim output not at all —
which places the fault downstream of the world. The capture-source asymmetry at `:178` is the
obvious candidate, and building it is a cheaper way to find out than more measurement gymnastics.

**It is also a decision, not purely a fix.** HDR capture is arguably right for perception work
that wants dynamic range. What is not defensible is packing an HDR buffer into 8 bits, which
both discards range and mis-encodes it — if HDR is wanted, the right form is float output
(`pixels_as_float`), not `uint8`.

**The one-line change** — use `SCS_FinalColorLDR` for `Scene` as every other image type already
does.

### Step 3 result: the patch builds and runs; evaluating it is blocked on spawn placement

`Blocks` was copied to a writable location (**`vendor/` verified pristine, 0 modifications**),
patched, and rebuilt with UnrealBuildTool:

```
[79/81] Link libUnrealEditor-AirSim.so
Result: Succeeded          70.28 s
```

The rebuilt plugin was injected into City Park via `inject_airsim.py --plugin ... --force`
(which correctly moved the previous one aside to `AirSim.bak.<ts>`), and the sim came up with
AirSim serving. **So the patch is real and deployable.**

**What could not be done: a clean before/after image.** Repeated attempts produced frames of
the underside of the terrain, because of the spawn problem already recorded above — City Park's
ground sits above world origin, its height varies across the map, and `simSetVehiclePose` does
not hold the vehicle against gravity. Freezing with `simPause` fixes the falling but not the
placement: z = −9 m was still below the surface at that location.

### The "pavement border" in the captures — explained, and a methodology bug found

Several captures showed a concrete-block border framing a blurred centre, as if the image were
matted. Two separate causes, and neither is a rendering defect:

**1. The camera was inside geometry.** The blocks are the material of whatever surface the
camera is embedded in, seen at point-blank range with depth-of-field blur. Confirmed by
elimination: at 250 m in open air the border is **completely absent**; every frame that showed
it was taken at an altitude below City Park's terrain surface. It is the spawn problem again,
wearing a different disguise.

**2. `simPause` makes `simGetImages` return a STALE FRAME.** Proven accidentally: three captures
at 300 m, 120 m and 9 m altitude returned border/centre means of 137.4/206.7, 102.6/206.7 and
102.6/206.7 — **byte-identical for the last two at completely different positions**. Pausing
stops the scene capture re-rendering, so the RPC hands back the previous frame.

**This invalidates part of the earlier investigation.** The `simPause`-based A/B that produced a
0.35 correlation was comparing a fresh native screenshot against a stale AirSim frame, so that
number means nothing. `simPause` was introduced to stop the vehicle falling between captures;
it solves that and silently breaks the capture instead.

**Rule for any future capture work here: never `simPause` before `simGetImages`.** Hold the
vehicle another way — or better, fix the spawn so it does not need holding.

**So the honest dependency is the other way round from how this was sequenced.** The capture
fix cannot be evaluated until the drone can be *reliably placed somewhere with a view* — which
is the spawn-position work already filed above as the next A1 task. Measurements taken before
that are comparing whatever the camera happened to be buried in.

**Numbers gathered, and why they are not yet conclusive:** the LDR capture read mean 76.5 /
std 43.6 / max 229 against the HDR path's 176.0 / 39.9 / 255 — consistent with less clipping and
a darker, more contrasty image, which is the expected direction. But the two frames show
different content, so this is a hint, not a result. It is a vendored C++ change, so it needs a recorded patch plus a plugin rebuild, and it is
**a decision rather than an obvious win**: HDR capture is arguably the *right* choice for some
perception work (tone-mapped LDR discards dynamic range that HDR-aware pipelines may want).
What is not defensible is the current state, where the HDR buffer is silently packed into 8 bits.

**Everything below remains true and worth keeping** — the conversion, SM6 and scalability fixes
are all correct — they simply were not this bug.

**Practical position:** all three of those are exactly what an editor conversion pass fixes —
UE offers to replace deprecated actors and rebuild lighting on open. So the earlier
qualification stands and is now better evidenced: **a UE4 project loads and runs headless without conversion.** Converting it is
still worth doing for correctness — and **it can be done entirely on Linux**, headless, with the
engine already in the `lane-c` image: `UnrealEditor-Cmd -run=ResavePackages -IGNORECHANGELIST`
upgraded all 11 packages from UE version 518 (UE4.24) to 1018 (UE5.8) in ~2 minutes, and a
Python commandlet swapped the deprecated fog actor. **No Windows machine is required for any of
it.** The `-IGNORECHANGELIST` flag is essential: `dev-slim` reports `BuiltFromCL: 0`, so the
commandlet's default filter considers every package newer than the editor and skips them all.

**Housekeeping:** the 3.5 GB zip landed in `assets/`, which was **not gitignored** — a stray
`git add -A` would have tried to commit it. `/assets/` is ignored now, and the extracted world
lives on the 7 TB drive under the mirrored project path per the repo's storage rule.

**Why not the alternative** — having the user drop a *level* into our `Blocks` project — even
though it looks simpler: a `.umap` carries path-encoded references to its materials, meshes and
blueprints, so moving one between projects means UE's editor **Migrate** dependency walk. That
is a GUI operation on the user's machine, and getting it wrong yields a map that loads with
everything silently missing. It does not remove the hard part; it relocates it onto the user
with worse tools. Note that this option is a *subset* of A — building A gets it nearly free.

### Actors work in the user's project. Three paths, one caveat

| path | needs project C++? | provides |
|---|---|---|
| **Plugin RPC API** | **no** — works in any project | `simSpawnObject`, `simDestroyObject`, `simSetObjectPose`, `simSetObjectScale`, `simGetObjectPose`, `simListSceneObjects` |
| **`DynamicObjects` Blueprints** | mostly no | `GroupedAI` (human_ai, controller, spawner, target points, animations), spline animations, conveyor belts |
| **`DynamicObjects` C++** (4 files) | **yes** | `-startSeed` / `-spawnAI` / `-isStatic` / `-startPoint`, random prop spawning |

**Caveat, measured rather than assumed:** `strings` across the `DynamicObjects` uassets finds
`RandomPropSpawner` **0** times but `LaunchParameterHelper` **2** times (control:
`GroupedAIController` 14, `Character` 114 — the method works). So at least one Blueprint *does*
depend on the C++. An A1 project either copies in those four small files — which means adding a
`Source/` module and becoming A2 — or accepts one broken asset reference. **The seeded
determinism sits on the C++ side of that line**, which matters because the gate wants it.

### Acceptance

1. A user-supplied `.uproject` (A1) is loaded by `lane_c_up.sh` **without editing the repo** —
   pointed at by path/parameter — and the drone flies in it.
2. `scripts/verify_lane_c_sensors.py` passes against that world, with **re-measured** rates.
3. Actors are present and moving, and the cameras see them.
4. A bundled **example world** exists so the simulator is useful out of the box (see below).
5. The steps a user must perform on a non-Linux machine are **documented**, not folklore.

### The bundled example world

The CC0 route from the first survey stays — **demoted from "the answer" to "the default"**, so
the thing works with no downloads or accounts. Poly Haven (**521 CC0 models**) + ambientCG
(**2,876 CC0 assets**), both plain HTTPS with no auth, and headless glTF import was verified in
the pinned image: `Success - 0 error(s)`, 5 uassets on disk from a 4.79 MB source. It yields a
natural/cluttered-outdoor scene, not a city — acceptable for a default.

### Still unknown, and each wants an experiment rather than a document

- **Can a UE 5.3-era project be converted to 5.8 headlessly?** If not, users need an editor
  pass on their own machine first — acceptable, but it must be documented rather than
  discovered.
- **Does the prebuilt plugin drop cleanly into a foreign project**, or does UBT insist on
  rebuilding? Upstream's precompiled path says it works when engine and platform match; ours
  match by construction.
- **Rendering cost is entirely unmeasured.** The 31 Hz RGB / 29.6 Hz depth / 17.4 Hz LiDAR
  baseline was measured on grey boxes. Re-measure per world; a heavy user world may not hold it.
- **The ~57-minute segfault** is uncharacterised and gets more likely with more actors.

**Blocks:** `C-07` — a flight gate is worth more against a real world than against grey boxes.

---

## C-08 — Cesium georeferenced terrain

**Status:** `todo` · **Still Phase 4** · **Rescoped 2026-08-02** — the photoreal-scene and
dynamic-actor work moved to `C-11`, which is the near-term need. What stays here is
georeferencing, which is benchmark reproduction rather than photorealism.

**What.** Cesium for Unreal georeferenced terrain and OSM/StreetMap as the low-altitude
alternative. Wind, time-of-day and weather are available over the AirSim RPC
(`simSetWind`, `simSetTimeOfDay`, `simEnableWeather`) and may be picked up by either task.

**Why it is deliberately last.** `04`'s Phase 2 bundles this with bring-up, but the project
splits it: **cluttered scenes for obstacle avoidance are project Phase 2** (`C-05` feeds
them), while **georeferenced real-world environments are project Phase 4**, where
AerialVLN/OpenFly reproduction actually needs them. Building Cesium terrain before the
stack flies would be scene work on an unproven simulator.

**Traps.**
- **The Omniverse FSD-vs-PhysX exclusion does NOT automatically apply here.** That is a
  Cesium-for-*Omniverse* constraint; Cesium for Unreal is a different plugin. Whether UE5
  gives georeferenced terrain *with* physics is **open — verify, do not inherit the answer**
  (`versions.lock` records this as a scope correction).
- **Google 3D Tiles go black at drone altitude.** Documented at 10–500 ft AGL in both
  plugins. `04`'s decision threshold: if tiles render black below ~150 ft AGL for an AOI,
  switch that scenario to OSM+PCG or photogrammetry meshes.
- **Coordinate reconciliation bites here.** AirSim NED origin vs `CesiumGeoreference` vs UE
  centimetres. Set PX4 `LPE_LAT`/`LPE_LON` and AirSim `OriginGeopoint` to the same lat/lon.
- **Licensing:** Google 3D Tiles need a Cesium ion token and fall under Google Maps Platform
  terms; OSM data is ODbL; the City Sample building generator needs a SideFX licence.

---

## Open questions this lane must answer

All three of the originals have been reframed by the 2026-07-31 decisions and the research
behind them.

1. ~~**Does Cesium for Unreal support UE5.8?**~~ ✅ **ANSWERED 2026-07-31 — yes.** Cesium for
   Unreal v2.28.0 adds UE5.8, with a downloadable UE5.8 binary. The Epic image tag
   `dev-slim-5.8.0` also exists, and UE5.8 is a released engine. **Consequence beyond the
   answer:** Cesium v2.29.0 drops UE5.5, so the UE5.5 fallback inverted from *safe* to
   *worst* — UE5.8 is the only forward-supported path. (`C-01`)
2. **Does the ROS 2 wrapper build on Jazzy / Ubuntu 24.04?** Upstream *documents* it and
   never *tests* it — its CI has never once invoked `colcon`. Expected to pass on
   `5.8-v3.4.1`; the point is to find out for ~30 minutes rather than after an engine build.
   (`C-06`)
3. **Can Lane C use PX4 v1.16.0?** *Probably yes* — the wire protocol is unchanged v1.12
   through v1.16, and a third party is on record running `/fmu/*` traffic while AirSim drives
   PX4 v1.16.0. But the evidence is two bug reports, not a green test, and upstream pins
   nothing. **Confidence: medium.** If it holds, the two-PX4-tree architecture — the plan's
   dominant risk — collapses to one tree. (`C-03`)
4. **Is lockstep actually engaged?** Reading the source suggests `resetState()` clears the
   flag that `initialize()` sets, which would mean every documented lockstep configuration
   runs free-running. Everything about timing fidelity depends on the answer. (`C-03`)
5. **Is 10 GB VRAM enough** for AerialVLN/OpenFly scenes at useful resolution? The
   assessment says 10 GB is workable for modest scenes but stutters on large Cesium terrain.
   Note the render card is the **3080**, and the 5060 Ti's 16 GB is reserved for inference —
   swapping them is forbidden.
6. **Does Cesium-in-UE5 give georeferenced terrain *with* physics?** The FSD/PhysX exclusion
   is Omniverse-specific and the plan routes georeferenced physics here — verify rather than
   assume. Distinct from question 1: that one is "does it build at all". (`C-08`)
7. **What does Lane C's flight gate cost in wall-clock,** and does a UE5 stack restart make a
   10-seed gate impractical? (`C-07`)

---

## Not in this lane

Recorded so they are not smuggled in:

- **Retiring Lane A.** It is the regression baseline and it runs the real-hardware PX4 tree.
  See [`../lane-a/todo.md`](../lane-a/todo.md).
- **A second ROS 2 distro**, except via the documented, evidence-gated
  `lane_c.ros2_distro_fallback` sidecar.
- **Anything touching the real aircraft.** Real flight is Phase 4 and needs explicit
  per-run operator approval, every time (`.ai/AGENTS.md:39`).
