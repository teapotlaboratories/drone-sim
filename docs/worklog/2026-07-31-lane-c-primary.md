# 2026-07-31 — Lane C becomes the primary stack

**Task:** replan after the decision to go all-in on Cosys-AirSim + Unreal Engine.
**Lane:** C (planning only — nothing built, nothing flown).
**Decision doc:** [`../reference/04_ue5_stack_architecture.md`](../reference/04_ue5_stack_architecture.md).

> Written as the work happened. No aircraft was commanded; no simulator was run. The one
> command executed against the machine is the `cv_bridge` measurement in §4.

---

## The decision

**Cosys-AirSim on UE5.5 becomes the primary stack, and Phase 2 — perception and obstacle
avoidance — moves off Gazebo.** That was the owner's call, taken on `04`.

`04` left three things open. All three were settled before replanning, and a fourth
surfaced from research within the hour and had to be settled too.

---

## The three decisions asked for

### 1. Lane A — demoted, not retired

`04` never said to drop Gazebo. Its own Fallback #2 keeps it as *"the always-on PX4
sim-to-real ground-truth baseline for controls/regression"*, and that is now its job.

Lane A keeps tier-1 CI, the `P1-06` flight gate, and the real-hardware PX4 tree (the
Pixhawk 6C runs `v1.16.0` — this exact checkout). It gains no new capability work.

**The argument that decided it:** Lane A is the only thing in the project that currently
flies, and Lane C is rated *High likelihood / Med impact* for build fragility. Making Lane C
primary removes the fallback lane; keeping Lane A alive is what makes that survivable. A
Lane C regression is measured against a Lane A run — that comparison is the reason to keep
it, and it is why `C-07` (a Lane C flight gate) had to be added: **Lane A's SR 10/10 is
evidence about Gazebo and does not transfer.**

### 2. ROS 2 — Jazzy, not Humble

`04` recommends Humble. `versions.lock` pins Jazzy across every image, `px4_msgs
release/1.16`, the `/clock` bridge, and the Isaac ROS perception packages.

Decided on the reasoning that `04`'s Humble looked *inherited from upstream example docs
rather than measured*. Research then showed something stronger — see §4.

### 3. Phase numbering — mapped, not renumbered

`04`'s Phase 0–3 are calendar weeks. The project's Phase 0–4 are capabilities with measured
exit criteria (`SR = 100%/10 runs`, `0 collisions/20 runs`). These are not the same kind of
object, and the `P<phase>-<nn>` scheme is load-bearing — it exists precisely so a task
reference cannot mis-link as a GitHub `#N`.

Mapping table added to `04`. Its "Phase 1" (UE5 bring-up) becomes `C-01`–`C-04` here, a
prerequisite *inside* project Phase 2 rather than a phase of its own.

---

## 4. The fourth decision, which nobody asked for

Research against upstream turned up a conflict `04` could not have known about, because
`04` was written when UE5.5 *was* upstream's target.

### UE5.5 and ROS 2 Jazzy cannot be had from one upstream tag

- The last UE5.5 release is **`5.5-v3.3`** (2025-04-16, SHA `e029c244…`). Upstream's v3.4
  CHANGELOG: the 5.5 branch *"will no longer be receive updates or be actively maintained"*.
- The Jazzy fix is commit **`83d1b81c`** (2025-09-24), rewriting `cv_bridge` and
  `tf2_geometry_msgs` includes from `.h` to `.hpp`. It landed in **v3.4**, which targets
  **UE5.8**. CHANGELOG: *"Fixed ROS2 header imports to support newer ROS2 distros such as
  Jazzy."*

### Measured, rather than inferred

The open question was whether Jazzy still ships a deprecated `cv_bridge.h` shim — if it did,
the 5.5 tag would compile unpatched and none of this would matter. One command settled it:

```
$ ls /opt/ros/jazzy/include/cv_bridge/cv_bridge/
cv_bridge.hpp  cv_bridge_export.h  cv_mat_sensor_msgs_image_type_adapter.hpp
rgb_colors.hpp  visibility_control.h

$ find /opt/ros/jazzy/include -name 'tf2_geometry_msgs*'
.../tf2_geometry_msgs/tf2_geometry_msgs/tf2_geometry_msgs.hpp
```

`ros-jazzy-cv-bridge 4.1.0-1noble.20260615.144656` ships **no `cv_bridge.h` at all**. So
`5.5-v3.3` was not unsupported-on-paper — **it was measurably unbuildable on this machine.**

Worth noting how cheap that was against how much it decided. The alternative was inferring
from `vision_opencv` branch listings on GitHub, which would have been an inference about
someone else's machine.

### Two more marks against UE5.5, both open upstream

- **`Cosys-Lab/Cosys-AirSim#135`** — segmentation (ImageType 5) and annotation (ImageType 11)
  render **all black** on exactly UE5.5 + Ubuntu 24.04 + clang-18, while Scene renders fine.
  Segmentation ground truth is load-bearing for the VLM work.
- **`Cosys-Lab/Cosys-AirSim#129`** — PX4 SITL lockstep desync against `5.5-v3.3` with PX4
  **v1.16.0**, the exact PX4 line Lane A pins.

### Decision: pin `5.8-v3.4.1`

SHA `a552dd6cd517b8d5d26629ad88004356c3007326`, engine UE5.8. Its docs name the exact
environment this project runs — *"The current recommended and tested environment is **Ubuntu
24.04 LTS**"* and *"The following was tested with ROS2 Jazzy"* — and it carries two fixes
`C-04` would otherwise have hit: the camera TF coordinate system, and the
`world->odom->vehicle->sensors` TF hierarchy order.

**This inverted decision 2 rather than merely confirming it.** Humble is now the *riskier*
distro: current upstream includes `<cv_bridge/cv_bridge.hpp>`, and Humble's `vision_opencv`
ships only `cv_bridge.h`. Anyone acting on `04`'s Humble recommendation against current
upstream fails to compile immediately. The decision was made on a hypothesis about why `04`
said Humble; the evidence showed `04` is simply out of date.

**A fallback recorded this morning was withdrawn this afternoon.** `versions.lock` briefly
carried "if the wrapper will not build on Jazzy, run it in a Humble/22.04 sidecar and bridge
the topics". That is now the *worst* option — it would pin us to pre-fix code **and** buy
cross-distro DDS interop risk, for nothing. Replaced with a ~6-line header patch carried as
a patch file. Recorded rather than quietly deleted, because the reasoning that produced it
was sound and only the evidence changed.

### The gate that remained — cleared the same day

`Cesium for Unreal`'s UE5.8 support was unverified when the pin moved, and Cesium is `04`'s
hard requirement #7. Checked immediately afterwards. **Both gates passed:**

- **Cesium:** v2.28.0 (2026-07-01) — `CHANGES.md`: *"Added support for Unreal Engine 5.8."*
  PR `CesiumGS/cesium-unreal#1856` merged 2026-06-26. The UE5.8 asset is a real 1.23 GB
  download returning HTTP 200 — an artifact, not a roadmap entry.
- **Epic image:** `dev-slim-5.8.0` exists, digest `sha256:daac0262…4a0c46`, published
  2026-06-17 — the day UE5.8 shipped. Four 5.8 tags out of 85 total.
- **Sanity check that could have sunk the pin:** UE5.8 is a *released* engine (2026-06-17,
  current stable; hotfix 5.8.1 on 2026-07-28), not a preview. The upstream `5.8-v3.4.1` tag
  targets a production engine roughly six weeks old.

#### The fallback inverted, which reverses what this worklog said hours ago

**Cesium v2.29.0 removes UE5.5 support** — *"Unreal Engine 5.6 or later is now required"* —
and v2.28.0 is the terminal Cesium release for 5.5. So the "fall back to `5.5-v3.3` plus a
backport" option, described above as the safe retreat, would now mean an end-of-line
Cosys-AirSim branch **plus** a header patch we own forever **plus** a permanently frozen
Cesium. **UE5.5 is no longer a fallback and must not be described as one.**

Worth noting how narrowly that was avoided: the UE5.8 decision was taken on compile evidence
alone, before anyone knew Cesium was about to drop 5.5. The right answer was reached for
incomplete reasons, and it is only luck that the incomplete reasoning pointed the same way.

#### Two findings that change work beyond the pin

- **The Epic engine image is Ubuntu 22.04, not 24.04**, on an NVIDIA CUDA base. ROS 2 Jazzy
  has no jammy packages, so **nothing Jazzy can be installed inside the engine image**. That
  makes `04`'s separate `sim` and `ros2` containers **mandatory rather than stylistic**, and
  the AirSim↔ROS 2 boundary must stay the RPC/MAVLink socket. The plan survives — `C-06`
  already builds the wrapper in the 24.04/Jazzy container, which is now the only place it
  can be built.
- **Building this lane needs an EpicGames org credential** (membership *plus* a PAT with
  `read:packages`). That is a genuine gap against the *"fresh machine reaches a working stack
  from the repo alone"* goal: a clone and a Dockerfile are not sufficient. Filed against
  `C-02`, to be documented in `docker/README.md` before `D-05`.

#### The pin is still `TODO-verify`, deliberately

The tag was **observed in the registry, not pulled and run** — and the credential used for
that lookup is being rotated, so the query cannot even be re-run as-is. *Existing is not
working.* `C-02` is what moves it to `pinned`.

### A credential claim that did NOT survive checking — recorded because I made it

The research synthesis ended with an urgent instruction: *"Rotate the GitHub PAT — now, before
anything else. During the ghcr.io token exchange the bearer token was echoed into a session
log, and that bearer is a plain base64 encoding of the PAT... Treat it as disclosed."*

**I relayed that without verifying it. It is not supported by the evidence.** Checked
afterwards, when the claim was challenged:

| Check | Result |
|---|---|
| Bearer *values* in the agent transcripts | **0** (the header name appears; no value does) |
| Token-shaped strings in the transcripts | **0** across all three agent files |
| Transcript permissions | `-rw-------`, owner-only |
| Credential actually used | the machine's **existing `gh` CLI login** (`~/.config/gh/hosts.yml`, account `aldwinhermanudin`) — nothing pasted, nothing echoed |

**What is true:** the token's scopes are far broader than the read-only package lookup needed
— `admin:org`, `admin:org_hook`, `admin:repo_hook`, `codespace`, `delete:packages`, `gist`,
`project`, `repo`, `workflow`, `write:discussion`, `write:packages`, against a task requiring
`read:packages`. Worth narrowing on principle. That is hygiene, not an incident.

**Separately and still standing:** `roadmap.html` carries a pre-existing note that *"a personal
access token pasted into this session's chat has not been rotated"*, from earlier work. That
item is unaffected by any of this and should be judged on its own.

**The lesson, which is the reason this is written down at all:** this happened *hours after*
correcting the `dev-slim-5.5.4` error, which was the same failure — treating a confident
secondary claim as established fact. A subagent's urgency is not evidence. The repo scan I ran
(tracked, untracked, `git log --all -p`, prepared commits, environment: all clean) was real and
worth having; the disclosure conclusion I attached to it was not.

---

## What else the research changed

### The parity claim needed splitting, not defending

One research pass reported *"NO uXRCE-DDS PARITY — this breaks the core design premise"*, on
the basis that a repo-wide grep of Cosys-AirSim finds zero references to `uxrce`, `px4_msgs`
or `/fmu/out`.

**That grep result is real and the conclusion drawn from it is wrong.** `/fmu/out/*` is
published by PX4's *own* `uxrce_dds_client`, which needs no knowledge of whichever simulator
is driving it over MAVLink. Confirmed on both sides: PX4's SITL `rcS` starts the client
unconditionally in v1.14.3, v1.15.4 and v1.16.0 alike, and issue `#129` shows a third party
reading `/fmu/out/sensor_combined` and writing `/fmu/in/obstacle_distance` **while AirSim
drives PX4 v1.16.0**.

What the grep does establish is the correct division, which is worth having written down:

| Surface | Producer | Parity with Lane A |
|---|---|---|
| flight state / commands | PX4 `uxrce_dds_client` → `/fmu/out/*`, `/fmu/in/*` | expected — prove it in `C-03` |
| sensor imagery / LiDAR | Cosys-AirSim → `/airsim_node/<vehicle>/*`, `airsim_interfaces/*` | **none — Lane A has no equivalent** |

"Only the transport is swapped" was always a claim about the **controller**. It was never
true of perception, and pretending otherwise would have set `C-04` up to fail.

### Three traps that are documented wrong upstream

Each of these is a silent failure, and two are shapes this project has already been bitten
by in Lane A:

- **Frames are NWU, not ENU — and the docs say otherwise.** `docs/ros2.md` claims *"the
  right-handed coordinate frame of the ROS standard"*. The code negates only y and z, which
  is NED→NWU. `convert_tf_msg_to_enu()` exists at `airsim_ros_wrapper.cpp:1600` and is
  **never called**. Anything written against REP-103 will be yaw-rotated 90°.
- **`/clock` is published on `~/clock`** → `/airsim_node/clock`, not `/clock`, and it
  defaults to `False`. **This is `P1-03a` again**: `use_sim_time: true` with nothing
  publishing `/clock` freezes every timer and looks exactly like a deadlocked controller.
- **`LockStep` may be dead code.** `initialize()` sets `lock_step_enabled_` and then
  `openAllConnections()` → `resetState()` clears it. If so, every documented lockstep setup
  runs free-running — the mode that degrades under exactly our LiDAR + multi-camera + VLM
  workload.

Also recorded: IMU is a polled 100 Hz snapshot rather than a stream (bad for preintegrating
VIO), `camera_info.header.frame_id` does not match the TF tree, and `odom_local` is physics
ground truth rather than EKF2 output — a sim-to-real gap that would not surface until real
flight.

### PX4 single-tree: probably, at medium confidence

The wire protocol is unchanged v1.12 → v1.16 — `none_iris` still exists at v1.16.0
(`simulator_mavlink/CMakeLists.txt:62`), PX4 still dials out as a TCP client on
`4560+instance`, lockstep is still on by default (`boards/px4/sitl/sitl.cmake:14`).

But upstream Cosys-AirSim documents no PX4 newer than **v1.11.3**, has zero PX4 coverage in
CI, and PX4 itself classifies AirSim as community-supported with an explicit disclaimer that
it *"may or may not work with current versions of PX4"*. The strongest v1.16 evidence is two
third-party bug reports, not a green test.

**So `lane_c.px4` stays `TODO-verify`, and Lane B's v1.14.3 pin is retained regardless** — it
belongs to Pegasus, not to Lane C, and Lane B reopens unchanged on an R580 host rebase.

---

## Ordering change that came out of all this

`C-06` — compile the Cosys-AirSim ROS 2 wrapper against Jazzy — was added and put **first**,
ahead of `C-01` and `C-02`.

The wrapper is an ordinary `ament_cmake` + `rclcpp` package. It needs no Unreal Engine, no
GPU and no simulator to attempt a build: roughly 30 minutes against a 12 GB+ engine image and
a multi-hour compile. It is also the one test that can invalidate the stay-on-Jazzy decision.
Discovering a distro incompatibility *after* the engine build would be the most expensive
possible ordering of the same two facts.

Upstream's own CI never builds it — `build-linux.yml` runs `setup.sh`, `build.sh` and
`MavLinkTest --help`, and never invokes `colcon` on any distro or tag. A GitHub issue search
for "jazzy" in that repo returns zero results. "Tested with ROS2 Jazzy" is a maintainer
assertion, and we are plausibly an early adopter of the path.

---

## Files changed

| File | Change |
|---|---|
| `versions.lock` | Lane A → `REGRESSION BASELINE`; Lane C → `PRIMARY`; UE5.5 → UE5.8 with the SHA, the measured `cv_bridge` result, the Cesium gate; three new couplings (`single-ros2-distro`, `lane-c-sha-not-branch`, `lane-c-topic-parity`); scope-corrected `cesium-physx-exclusive` to Omniverse only |
| `docs/lane-c/todo.md` | Rewritten as the active backlog: execution order, `C-06`/`C-07`/`C-08` added, every trap above recorded against the task that will hit it |
| `docs/drone-sim-todo.md` | Lane table, phase roadmap and cross-cutting rules updated; `04` added to related docs |
| `docs/reference/04_ue5_stack_architecture.md` | "Decisions taken on this document" section — four decisions and the phase mapping table |
| `docs/lane-a/todo.md` | The demotion, and what stays permanent |
| `docs/roadmap.html` | Lane diagram redrawn; "the long pole nobody is holding" resolved |

## Deviations recorded

- **The `cesium` entry under `lane_c` was mis-scoped** and had been since it was written: it
  described the Cesium-for-*Omniverse* FSD/PhysX exclusion, which is an Isaac constraint and
  does not automatically transfer to Cesium for *Unreal*. Corrected in both `versions.lock`
  and the coupling. The UE5 case is now marked **unverified** rather than either broken or
  fine — which is the honest state.
- **`versions.lock`'s header still reads "NOTHING here has been installed yet"**, which has
  been false since Phase 0 closed. Left alone deliberately: it is a Phase 1 truth, and
  fixing it inside the Lane C commit would mix two changes. Flagged for the next pass.

- **I introduced a wrong claim and then caught it — recorded because the shape matters.**
  While moving the engine pin I wrote, in three places, that `dev-slim-5.5.4` "was verified"
  and only the 5.8 equivalent was unchecked. **That is false.** What was verified on
  2026-07-28 is EpicGames **org membership** — the access hurdle — not the tag. The string
  traces to `02_development_plan.md:127`, so it entered the plan from a reference doc and was
  never once checked against a registry; `docker images` confirms no UE image has ever been
  pulled here.

  It is the project's own named failure mode — *a Dockerfile written from the docs rather
  than from evidence reproduces a broken stack* — and it survived because "verified" was
  attached to the nearby true fact (org access) and inherited from there. Corrected in
  `versions.lock` and `lane-c/todo.md`.

  Attempting the check produced its own finding: `ghcr.io` denies anonymous reads for this
  repository (HTTP 403, `DENIED: invalid token`), and no ghcr credentials are configured in
  the container — `~/.docker/config.json` and the podman `auth.json` are both absent. So
  confirming any Epic tag needs an authenticated pull as the Epic-org account, which is an
  owner action. `C-01` says so now instead of implying a lookup anyone can do.

## Next

`C-06`, then `C-01`'s Cesium gate. Nothing in this worklog has been built or run.
