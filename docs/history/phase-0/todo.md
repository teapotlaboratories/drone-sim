> **FROZEN — HISTORICAL RECORD. Preserved exactly as written; not maintained.**
>
> This is the environment and version-lock backlog from the project's opening phase, when it
> ran **three parallel simulator stacks**. Two of the three — the Gazebo baseline and Isaac
> Sim + Pegasus — **are stacks this repo no longer contains**, and the exit criteria below
> are stated in terms of them.
>
> **Read it as evidence, never as instructions.** The file paths, image tags, container
> names and task IDs it names have been renamed or deleted, and several of its links no
> longer resolve. Every measurement is preserved unchanged. Several of its *traps* still
> apply to the current stack — which is the main reason to read it; see
> [`../README.md`](../README.md) for which ones.
>
> **The repo today is one simulator: Unreal Engine 5.8 + Cosys-AirSim + PX4 SITL + ROS 2
> Jazzy.** Task-ID renames: [`../id-map.md`](../id-map.md). The repo:
> [`README.md`](../../../README.md).

# Phase 0 — Environment & Version Lock — backlog

**Area:** toolchain, drivers, pinned upstreams, smoke tests.
**Indexed from:** the project backlog, which is now [`docs/todo.md`](../../todo.md).
**Goal / DoD:** a reproducible, pinned toolchain where every component passes its own
smoke test, and a [`versions.lock`](../../versions.lock) with no `TODO-verify` left in the
Lane A section.
**Budgeted effort:** 8–12 engineer-days (`docs/reference/02_development_plan.md:32`).

> **Phase 0 exit criteria (all five):** `make px4_sitl gz_x500` flies headless with no
> Accel/Mag TIMEOUT over a 5-minute run · `ros2 topic list` shows `/fmu/out/*` · QGC
> connects · Isaac Sim 5.1 launches and its Python-3.11 ROS bridge publishes `/clock` ·
> a "hello VLM" call hits a vLLM OpenAI-compatible endpoint.

**Status legend:** `todo` · `in progress` · `done` · `blocked`

---

## Critical path

Lane A is the backbone and is **not blocked by anything**. Lane B sits behind a single
unresolved question (`P0-09`), so it is sequenced last deliberately — a failure there
must not stall Phases 1–2.

```
P0-00 ✔
  └─ P0-01 ── P0-02 ── P0-03 ── P0-04
                 │        └───────┴── P0-07 ── P0-08 ──┐
                 └─ P0-05 ── P0-06 ──┘                 ├── P0-14 (lock Lane A)
                                                       │
              P0-13 (independent, GPU 1) ──────────────┘

              P0-09 ?? ── P0-10 ── P0-11 ── P0-12   (Lane B; gated on P0-09)
```

---

## P0-00 — Repo scaffold, backlog, and drafted `versions.lock`

**Status:** `done` (2026-07-28) · **Blocks:** everything

**What.** Create the monorepo layout from the development plan (`ros2_ws/src/*`, `sim/*`,
`vendor/`, `docker/`, `configs/`, `scenarios/`, `vlm/`) with placeholder READMEs, the
master backlog index, this area doc, `.repos`, `.gitignore`, and a drafted
`versions.lock`.

**Why.** "Lock versions before writing code" is Standing Order 1. The scaffold exists so
that every later task has an obvious home and the pins have a single authority.

**Acceptance.** Layout matches `docs/reference/02_development_plan.md:141`; every pin the
plan names appears in `versions.lock` with a status; nothing claimed as installed that
is not. **Met** — no code was installed, and the lock says so.

---

## P0-01 — Verify the GPU baseline actually reaches CUDA and Docker

**Status:** `done` (2026-07-28) — both GPUs visible in-container and through nested Docker · **Blocked by:** — · **Blocks:** P0-09, P0-13

**What.** Confirm both GPUs are visible, CUDA works inside the container, and
`docker run --gpus all` reaches the GPU through the nested-Docker CDI path.

**Why.** `docs/bench.md` claims all three, but claims are not observations, and the CDI
spec is a documented failure point that goes stale across host driver updates. Everything
GPU-shaped in Phases 3–4 rests on this.

**Acceptance.**
```bash
nvidia-smi -L                                          # both cards
docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L  # GPU reaches nested Docker
```
Both listed, exit 0. **Already partially observed 2026-07-28:** `nvidia-smi` reports GPU 0
RTX 3080 (10240 MiB) and GPU 1 RTX 5060 Ti (**16311 MiB — the 16 GB variant**), driver
610.43.03. The Docker leg is untested.

**Trap.** If `docker run --gpus all` fails on a missing `libEGL_nvidia.so.<ver>` or a
`/usr/lib64/...` path, the CDI spec is stale — regenerate per `docs/bench.md:132`. Do not
"fix" the storage driver or CDI setup otherwise; both are load-bearing.

---

## P0-02 — Install ROS 2 Jazzy via the `ros2-apt-source` .deb

**Status:** `done` (2026-07-28) — ros-jazzy-desktop 0.11.0-1noble.20260616.084553, rclpy on 3.12.3 · **Blocked by:** P0-01 · **Blocks:** P0-03, P0-06

**What.** `ros-jazzy-desktop` + `ros-dev-tools` inside the container, using the
**`ros2-apt-source` .deb** method.

**Why.** The old apt-key method was retired 2025-06-01 and will fail. Jazzy is the LTS
pairing for Ubuntu 24.04 and the distro every downstream component targets (Isaac ROS
cuVSLAM/nvblox are Jazzy-tested).

**Acceptance.** `ros2 topic list` runs; `python3 -c "import rclpy"` succeeds on **Python
3.12**. Record the exact Jazzy sync date in `versions.lock`.

**Trap.** Install into the **container**, never the host. Confirmed absent as of
2026-07-28 (`no /opt/ros`).

---

## P0-03 — Clone and build PX4 v1.16.0 + Gazebo Harmonic

**Status:** `done` (2026-07-28) — PX4 v1.16.0 @ 6ea3539, gz-harmonic 1.0.0-1~noble, builds + flies headless · **Blocked by:** P0-02 · **Blocks:** P0-04, P0-07

**What.** Clone into `vendor/PX4-Autopilot-v1.16` via `.repos`, checkout `v1.16.0`,
`git submodule update --init --recursive`, run `Tools/setup/ubuntu.sh`, then
`make px4_sitl gz_x500`.

**Why.** This is Lane A *and* the firmware that will fly the real Pixhawk 6C. It is the
single most load-bearing pin in the project.

**Acceptance.** `make px4_sitl gz_x500` reaches the PX4 shell with Gazebo up. Record the
**built SHA** (not just the tag) and the exact Gazebo version in `versions.lock`.

**Traps.**
- **Do NOT append `_default`** to the make target.
- Use the **single-command** launch. The manual split-terminal/standalone method
  reproducibly triggers "bobbing" plus `Accel #0 fail: TIMEOUT!`
  (`docs/reference/02_development_plan.md:15`).
- Constrain Gazebo transport to **loopback** — multicast flooding the host network is a
  documented root cause (`PX4/PX4-Autopilot#24595`). This box has an isolated netns,
  which helps but does not substitute for the setting.
- Run **headless**.
- **Launch with a BLOCKING stdin** — `screen -dmS px4sitl bash -c "stty min 1 time 0; make
  px4_sitl gz_x500"`. PX4's pxh shell clears `ICANON` without setting `VMIN`, so a pipe or a
  `screen` pty makes it busy-spin its prompt: **4.1 GB of escape codes per 300 s run** plus a
  burned CPU core, which fills tmpfs and causes sensor TIMEOUTs via I/O saturation. With
  `stty min 1`: ~28 KB. See `docs/worklog/2026-07-29-d01-container-parity.md`.

---

## P0-04 — Enumerate the real PX4 Gazebo model targets

**Status:** `done` (2026-07-28) — enumerated from the built tree; **plan's names were wrong** · **Blocked by:** P0-03 · **Blocks:** P0-07, Phase 2

### Verified list (source: `ROMFS/px4fmu_common/init.d-posix/airframes/`, PX4 v1.16.0 @ `6ea3539`)

**Every target is `gz_`-prefixed.** The development plan names three of them *without* the
prefix (`x500_lidar_2d`, `x500_lidar_front`, `x500_lidar_down`), and those names do not
exist — `make px4_sitl x500_lidar_2d` fails. Corrected names below.

| Airframe ID | make target | Sensor | Phase |
|---|---|---|---|
| 4001 | `gz_x500` | baseline | 0–1 |
| 4002 | `gz_x500_depth` | RGB-D | 2 |
| 4005 | `gz_x500_vision` | **vision/VIO** | 3 |
| 4010 | `gz_x500_mono_cam` | mono camera | 3 |
| 4013 | `gz_x500_lidar_2d` | 2D LiDAR | 2 |
| 4014 | `gz_x500_mono_cam_down` | downward mono | 3 |
| 4016 | `gz_x500_lidar_down` | downward LiDAR | 2 |
| 4017 | `gz_x500_lidar_front` | forward LiDAR | 2 |
| 4019 | `gz_x500_gimbal` | gimbal | — |
| 4021 | `gz_x500_flow` | **optical flow** | 3 (GPS-denied) |

Non-x500 targets also present: `gz_rc_cessna`, `gz_standard_vtol`, `gz_px4vision`,
`gz_advanced_plane`, `gz_r1_rover`, `gz_r1_rover_mecanum`, `gz_rover_ackermann`,
`gz_lawnmower`, `gz_quadtailsitter`, `gz_tiltrotor`, `gz_omnicopter`,
`gz_spacecraft_2d`.

Matching model dirs under `Tools/simulation/gz/models/`: `x500`, `x500_base`,
`x500_depth`, `x500_flow`, `x500_gimbal`, `x500_lidar_2d`, `x500_lidar_down`,
`x500_lidar_front`, `x500_mono_cam`, `x500_mono_cam_down`, `x500_vision`, `lidar_2d_v2`.

**Two finds the plan does not mention, both relevant to Phase 3:**

- **`gz_x500_vision` (4005)** — a purpose-built vision/VIO airframe. Phase 3 needs EV
  odometry into EKF2; this is very likely a better starting point than bolting a camera
  onto `gz_x500`, and it should be evaluated before writing any custom airframe.
- **`gz_x500_flow` (4021)** — optical flow, directly relevant to the GPS-denied work.

All four Phase 2 sensor variants the plan depends on **do exist**, just under corrected
names. No gap; a naming fix.

**What.** Enumerate the actual available `gz_x500*` / lidar airframe targets from the
built tree — `Tools/simulation` plus PX4-gazebo-models — and record the verified list.

**Why.** The plan names `gz_x500`, `gz_x500_depth`, `x500_lidar_2d`, `x500_lidar_front`,
`x500_lidar_down` but explicitly flags the complete enumeration as **unverified**
(`docs/reference/02_development_plan.md:264`). Phase 2 depends on the depth and LiDAR
variants existing under the names we expect. Cite what is in the tree, never from memory.

**Acceptance.** A verified list in this doc with `file:line` or the `make list_config_targets`
output that produced it. Any target the plan names that does **not** exist is called out.

---

## P0-05 — Build and install Micro-XRCE-DDS-Agent v2.4.2

**Status:** `done` (2026-07-28) — agent **v2.4.3** (not v2.4.2, see docs/vendor/micro-xrce-dds-agent.md) @ 7362281 · **Blocked by:** P0-02 · **Blocks:** P0-06, P0-07

**What.** Clone at tag `v2.4.2`, `cmake && make && sudo make install && sudo ldconfig
/usr/local/lib/`.

**Why.** PX4 v1.15+ uses uXRCE-DDS, not MAVROS. This agent is the entire PX4↔ROS 2
transport for Lane A and for real hardware.

**Acceptance.** `MicroXRCEAgent udp4 -p 8888` starts and, once PX4 SITL is up, logs
client sessions creating topics.

---

## P0-06 — `px4_msgs` on `release/1.16` + `px4_ros_com`, colcon-built

**Status:** `done` (2026-07-28) — px4_msgs @ 392e831 on release/1.16; proven by populated topics, not the build · **Blocked by:** P0-02, P0-05 · **Blocks:** P0-07

**What.** Clone `px4_msgs` at branch **`release/1.16`** and `px4_ros_com` into
`ros2_ws/src/` (or a dedicated `px4_ros` workspace), `colcon build`, source.

**Why.** **The branch MUST match the firmware.** This is the highest-severity silent
failure in the stack: a mismatch does not error, it produces topics that never populate.

**Acceptance.** Build succeeds **and** `P0-07` observes `/fmu/out/*` carrying real data.
A green build alone does not close this item — that is precisely the failure mode.

**Trap.** v1.16 introduced uORB message versioning and a required ROS 2 Message
Translation Node. If topics appear but stay empty, suspect the branch match first.

---

## P0-07 — Lane A end-to-end smoke: 5-minute headless SITL, no TIMEOUT

**Status:** `done` (2026-07-28) — 300 s, 0 TIMEOUT, 0 ERROR, RTF 1.000, 24 topics @ 100.02 Hz · **Blocked by:** P0-03, P0-04, P0-05, P0-06 · **Blocks:** P0-14

**What.** Run headless PX4 SITL + Gazebo + XRCE agent + ROS 2 together for a **full 5
minutes**, and observe the ROS 2 side.

**Why.** This is the first exit criterion and the first moment the graph exists
end-to-end. A correct component is not a working flight — the seams are where the bugs
live.

**Acceptance.**
- No `ERROR [sensors] Accel #0 fail: TIMEOUT!` or `MAG #0 failed: TIMEOUT!` over 5 min.
- `ros2 topic list` shows `/fmu/out/*`, and `ros2 topic echo /fmu/out/vehicle_local_position`
  produces **moving, non-empty** data.
- **Record the real-time factor** and set the RTF floor that Phase 1 CI will assert.

**Trap.** Do not let a retry paper over desync. A flaky pass is a fail until the RTF
floor holds (Standing Order 5). If TIMEOUTs persist after headless + loopback + core
allocation, the documented fallback is running the CI lane on PX4 v1.15.

---

## P0-08 — QGroundControl AppImage connects

**Status:** `done` (2026-07-28) — connected, visually confirmed on a virtual display · **Blocked by:** P0-07 · **Blocks:** P0-14

**Result.** QGC connected to PX4 v1.16.0 SITL over UDP 14550 and reported **"Ready To
Fly"**, flight mode **Hold**, battery **100%**, autopilot detected as **PX4**, map at the
SITL default home (Irchelpark, Zurich — 47.397 N, 8.545 E). Screenshot evidence:
[`../worklog/assets/2026-07-28-p0-08-qgc-connected.png`](../worklog/assets/2026-07-28-p0-08-qgc-connected.png).

Wire-level check alongside it: 28 distinct MAVLink message types / 3724 messages in 10 s
on UDP 14550, with `ATTITUDE`, `GLOBAL_POSITION_INT` and `LOCAL_POSITION_NED` all at
~50 Hz.

**How, and the reusable part.** The container has **no usable `DISPLAY`** — an `X0` socket
exists but X authorization rejects us (`Authorization required, but no authorization
protocol specified`). Rather than declare the criterion unverifiable, run it on a
**virtual display**:

```bash
Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp &
DISPLAY=:99 QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb \
  ./vendor/tools/QGroundControl.AppImage &
DISPLAY=:99 import -window root shot.png
```

`QT_QUICK_BACKEND=software` is **required** — Xvfb has no hardware GL and QtQuick will not
start without it. **This recipe generalises**: it is how any GUI in this container (rviz,
Foxglove, Gazebo GUI) should be verified from now on — capture a screenshot rather than
asserting it works.

**What.** QGC AppImage, `chmod +x`, add user to `dialout`, remove `modemmanager`.

**Why.** Third exit criterion, and the tool used later to verify EV odometry ingestion
(`MAV_ODOM_LP=1`) and to enable HITL in Phase 4.

**Acceptance.** QGC connects to SITL on UDP 14550 and shows vehicle telemetry.

**Trap.** The container has an **isolated network namespace**. Confirm 14550 is reachable
from wherever QGC runs, or run QGC inside the container with a display.

---

## P0-09 — ⚠ Isaac Sim 5.1 on driver 610.43.03 · **RESOLVED as a decision**

**Status:** `closed` (2026-07-29) — **Isaac Sim 5.1 confirmed broken on this driver; Lane B
deferred, Lane C promoted.** · **Blocked:** P0-10, P0-11, P0-12 (now moot while deferred)

Isaac Sim 5.1 SIGSEGVs on driver 610.43.03 (validated: 580.65.06) — reproduced on the
**Ampere** RTX 3080 with the Blackwell card excluded, so it is the *driver*, not the GPU.
Isaac 6.0 avoids the crash but **no Pegasus release exists for it**, which would mean
writing the PX4↔Isaac bridge ourselves.

**Full evidence, fallbacks, costs and reversibility:**
[`../lane-b/isaac-driver-decision.md`](../lane-b/isaac-driver-decision.md).

**Reopens if:** the host is rebased to an R580 driver · Pegasus ships an Isaac 6.0 release ·
Isaac 6.0 is proven headless here *and* a maintained PX4 bridge appears.

---

## P0-10 — Isaac Sim 5.1 + the Python-3.11 ROS 2 workspace

**Status:** `deferred with Lane B` · **Blocked by:** P0-09 · **Blocks:** P0-12

**What.** Install Isaac Sim 5.1, then build a minimal Jazzy workspace against **Python
3.11** using the Isaac Sim ROS Workspaces repo (`build_ros.sh` / its Dockerfile). Launch
Isaac from that sourced terminal; run application nodes from a **separate** system-Jazzy
(3.12) terminal.

**Why.** Isaac Sim 5.1 ships Python 3.11; Jazzy debs are built for 3.12. Sourcing system
Jazzy into Isaac's Python throws `ModuleNotFoundError: No module named
'rclpy._rclpy_pybind11'` — a C-extension ABI mismatch, not a path problem. **`rclpy`
cannot be shared.** The two halves meet over DDS and nowhere else.

**Acceptance.** Fourth exit criterion: Isaac publishes `/clock` and
`ros2 topic echo /clock` receives it **from the system-Jazzy 3.12 terminal**. Verifying
inside Isaac's own terminal proves nothing — the DDS crossing is the whole point.

**Trap.** Isaac driver-detection false negatives: use a Production Branch `.run`, and
clear `~/.cache/ov` and `~/.local/share/ov` before re-testing a failed launch.

---

## P0-11 — Second PX4 checkout at v1.14.3 for Pegasus

**Status:** `deferred with Lane B` · **Blocked by:** P0-09 · **Blocks:** P0-12

**What.** A **separate** clone at `v1.14.3` in `vendor/PX4-Autopilot-v1.14.3`, built for
MAVLink SITL (TCP 4560), left entirely independent of the v1.16.0 tree.

**Why.** Pegasus v5.1.0 was developed and tested against PX4 v1.14.3, verbatim in its
docs. Lane A and real hardware need v1.16.x + uXRCE-DDS. **Two trees is the
architecture** — not a workaround to be cleaned up later (Risk register: "Designed
around, not mitigated after").

**Acceptance.** v1.14.3 builds and starts its MAVLink SITL without disturbing the v1.16.0
tree. Both SHAs recorded in `versions.lock`.

**Trap.** Do not share a build directory, `PX4_HOME`, or ports between the trees.

---

## P0-12 — Pegasus Simulator v5.1.0 editable install

**Status:** `deferred with Lane B` · **Blocked by:** P0-10, P0-11 · **Blocks:** Phase 3 Lane B

**What.** `ISAACSIM_PYTHON -m pip install --editable pegasus.simulator`, then run an
example.

**Why.** Pegasus is the de-facto PX4-on-Isaac extension and the whole reason Lane B can
reuse the same ROS 2 graph.

**Acceptance.** A Pegasus example spawns a vehicle in Isaac Sim and connects to the
v1.14.3 PX4 MAVLink SITL.

**Traps.**
- Launch examples with **`isaac_run`**, not `ISAACSIM_PYTHON` (v5.1.0 changelog).
- Pegasus v5.1.0 ↔ Isaac 5.1.0 **exactly**; explicitly not backward-compatible.
- Upstream tested on Ubuntu 22.04 + driver 550.163.01. We are on 24.04 + 610.43.03 —
  divergence on **both** axes. Expect friction and log it.

---

## P0-13 — vLLM on GPU 1 + "hello VLM"

**Status:** `todo` · **Blocked by:** P0-01 · **Blocks:** P0-14

**What.** Serve a Qwen3-VL model with vLLM pinned to **GPU 1** via
`CUDA_VISIBLE_DEVICES=1`, and make one OpenAI-compatible call with an image.

**Why.** Fifth exit criterion, and it de-risks the Phase 3 VLM client early. Independent
of every PX4 and Isaac item — **this can run in parallel with all of Lane A.**

**Acceptance.** A chat/completions call with an image returns a coherent response;
`nvidia-smi` confirms the process is on **GPU 1 only**; record tokens/s and VRAM used.

**Notes.**
- GPU 1 is confirmed the **16 GB** variant, so **Qwen3-VL-8B AWQ is viable** with KV
  headroom — better than the 2B/4B the hardware assessment assumed as a floor. Start at
  4B, then try 8B.
- Use `--quantization awq` (`int4` is not a valid vLLM value). Cap `max_pixels` — VL KV
  cache is heavy.
- **Qwen3-VL-30B-A3B will not fit.** Do not attempt it locally.
- Weights: reuse `/home/deck/Developments/models` (~335 GB already staged); new pulls go
  to the external drive, never `~`.

---

## P0-14 — Resolve every Lane A `TODO-verify` and commit `versions.lock`

**Status:** `todo` · **Blocked by:** P0-07, P0-08, P0-13 · **Blocks:** Phase 1

**What.** Replace every `TODO-verify` in the Lane A / platform / components-in-use
sections of `versions.lock` with the **SHA that was actually built and smoke-tested**.
Lane B entries may remain `TODO-verify` if `P0-09` is unresolved — but the reason must be
recorded, not left blank.

**Why.** "An entry is not locked until it has a version AND a SHA AND a passing smoke
test." A tag can move; a SHA cannot. This is what makes Phase 0 reproducible rather than
merely finished.

**Acceptance.** No `TODO-verify` remains in the Lane A section; each pin traces to a
smoke test that passed; the file is committed (**outside weekday work hours**).

---

## P0-15 — Coupling-assertion script for CI

**Status:** `todo` · **Blocked by:** P0-14 · **Blocks:** Phase 1 CI

**What.** A script that reads `versions.lock` and asserts the `couplings:` block —
starting with the `px4_msgs` branch match and the two-PX4-trees invariant.

**Why.** The plan requires CI to assert the couplings hold. The `px4_msgs` drift risk is
listed with "Silent failure → CI topic assertion is the detector" — meaning the
assertion *is* the mitigation. Without it, nothing catches the failure.

**Acceptance.** Script exits non-zero when a coupling is violated; unit-tested against a
deliberately broken fixture (host-side logic → unit test, not a sim run).

---

## Deferred out of Phase 0

Recorded so they are not silently lost:

| Item | Why deferred | Lands in |
|---|---|---|
| `docker-compose` service definitions | The plan's 10-service compose is Phase 1 work; Phase 0 proves the components natively first | Phase 1 |
| GitHub Actions `sitl-integration` job | Needs a working seeded scenario runner | Phase 1 |
| Self-hosted GPU runner | Only needed once nvblox/cuVSLAM/Isaac jobs exist | Phase 2–3 |
| Isaac ROS (cuVSLAM, nvblox) install | Phase 2–3 components; pinning them now would go stale | Phase 2 |
| EGO-Planner ROS 2 port | Largest single work item in the project; needs Lane A stable first | Phase 2 |
| `docs/hardware/` UART + device-label mapping | No real hardware in the loop until Phase 4 | Phase 4 |
