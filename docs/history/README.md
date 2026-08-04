# Archive — the stacks this repo no longer contains

Everything under `docs/history/` is a **frozen record**, preserved exactly as it was
written. It documents work that was planned, built and measured, and then retired when the
project narrowed to a single simulator. **Do not follow any of it as instructions.** The
file paths, image tags, container names, compose services and task IDs these documents name
have since been renamed or deleted, and several of the links inside them no longer resolve.

They are kept because the measurements are real and the reasoning is not reconstructable
from the code that survived. A decision that cost a day is worth more written down than
re-derived.

**What the repo is now:** one simulator — **Unreal Engine 5.8 + Cosys-AirSim + PX4 SITL +
ROS 2 Jazzy**. Bring your own Unreal world, place the vehicle, choose your sensors, fly it
over ROS 2. Start at the repo [`README.md`](../../README.md); the live backlog is
[`../todo.md`](../todo.md).

---

## Why there were three stacks

The project opened with a simulator landscape survey (`reference/01`) and an executable
build plan (`reference/02`) that deliberately ran **three simulator stacks in parallel**,
because no single one met every requirement at once. Photorealism, PX4/ROS 2 fidelity and
cheap headless CI pulled in different directions, so each was given a job:

| Stack | The job it was given | How it ended |
|---|---|---|
| **PX4 v1.16 + Gazebo Harmonic + ROS 2 Jazzy** | fast, headless, CPU-only regression and CI baseline; controls ground truth | **Built, flew, retired.** Passed a 10/10 seeded flight gate. |
| **Isaac Sim 5.1 + Pegasus Simulator v5.1.0** | photorealistic RTX perception, domain randomization, RL | **Never ran here.** Isaac Sim 5.1 SIGSEGVs on this machine's NVIDIA driver. |
| **Unreal Engine + Cosys-AirSim** | photorealism and benchmark reproduction | **Promoted, then became the whole repo.** |

The third one is what you are looking at today. The other two are what this directory is
for.

### The Gazebo baseline — it worked, and it was retired anyway

This was the only stack in the project that flew for most of its life, and it was retired
from a position of strength rather than failure. Its measured results:

- **A 300 s headless SITL smoke run with 0 sensor TIMEOUTs, real-time factor 1.000, and
  24 `/fmu/out/*` topics at 100.02 Hz** — the first moment the ROS 2 graph existed end to
  end.
- **The flight gate: SR 10/10 over ten seeded runs**, each recording its own MCAP. Worst
  waypoint error spread **0.195–0.235 m against a 1.0 m accept radius**, wall clock 974 s.
- **Re-run with seeded wind: SR 10/10 again**, 1132 s, wind sampled 0.40–2.87 m/s of a
  3.0 m/s cap, with **waypoint error correlating with wind speed at Pearson r = 0.957**,
  slope 0.070 m of error per m/s. Worst error rose to 0.555 m — the margin narrowed from
  4.3x to 1.8x, deliberately, because that is what makes a gate discriminate.

It was retired because the project stopped needing a second simulator to compare against.
The claim that a photoreal result should be measured against a Gazebo run of the same
mission no longer holds — **there is no second stack to compare against, and the archived
documents that assume one are wrong about the repo as it stands today.** Said plainly
rather than deleted, because the comparison was a real design choice and its removal is a
real cost.

Two things from it survived into the current repo rather than being archived: the PX4
v1.16.0 tree (the same firmware the real Pixhawk 6C is flashed from) and the flight gate's
scoring semantics.

### Isaac Sim — deferred on a driver it never got past

Isaac Sim 5.1 **segfaults on startup** on this machine, and the cause was isolated rather
than guessed:

| | |
|---|---|
| Driver installed on `carbonite` | **610.43.03** |
| Isaac Sim 5.1's validated driver | **580.65.06** |
| Result | **SIGSEGV in `rtx.scenedb.plugin`, `docker rc=139`** |

The crash was reproduced on the **Ampere RTX 3080 with the Blackwell RTX 5060 Ti excluded
from the container entirely** (`--device nvidia.com/gpu=0`). That test design is the whole
point: it rules out the documented Blackwell instability and leaves only the driver
version, so no amount of reassigning GPU roles can fix it. The driver is injected from the
ostree-immutable host and cannot be changed with apt from inside the container.

Isaac Sim 6.0 avoids that crash — it reaches `app ready` at ~156 s with GPU 0 doing real
work (27% utilisation, 1613 MiB VRAM) and ships an internal `rclpy` for ROS 2 Jazzy, which
would have dissolved the Python-3.11-vs-3.12 split entirely. **What decided it against 6.0
was Pegasus:** upstream tags stop at **v5.1.0**, which pins Isaac 5.1.0 explicitly. Isaac
6.0 therefore means no PX4 bridge at all, which would have to be written — and writing a
Pegasus replacement is a project, not glue.

**One driver, two capabilities lost.** A later finding, recorded in the same document:
NVENC cannot open an encode session on 610.43.03 either (`OpenEncodeSessionEx: unsupported
device (2)`), while CUDA initialises fine. A single host rebase to an R580 driver would
plausibly restore both hardware video encoding and Isaac Sim. The live record of the
encoder half is [`../nvenc-driver-blocker.md`](../nvenc-driver-blocker.md), which is **not**
archived — it still blocks work.

### Unreal Engine + Cosys-AirSim — the survivor

`reference/04_ue5_stack_architecture.md` is the document that chose this stack, and it is
the only archived design doc whose central recommendation is still in force. Everything it
says about the *simulator choice* held; almost everything it says about UE5.5, ROS 2 Humble
and the phase calendar did not, and the corrections are recorded inside the document itself
rather than edited over the original. See its entry below.

---

## What is in here, and what each is still worth reading for

### `gazebo/todo.md` — the Gazebo stack's Phase 1 backlog

The full build record for the retired baseline: offboard controller, launch composition,
seeded scenario runner, MCAP recording, the 10-seed gate, and the CI that gated it.

Still worth reading for:

- **A negative result that is easy to repeat.** `gz sim --seed` is accepted by the binary
  and the plumbing works, and it has **no measurable effect** on the noise stream. Compared
  at identical simulation timestamps on Gazebo's own IMU topic: same seed, 716 aligned
  samples, **0 identical**, mean |Δaccel_x| **0.00726**; different seeds, 1911 samples, 0
  identical, **0.00718**. Two runs at the same seed differ by as much as two runs at
  different seeds. The plumbing was reverted rather than left in place implying control the
  stack did not have.
- **What replaced it — seed the conditions, not the RNG** — and three findings that cost
  time: a `<plugin>` element in a world SDF makes Gazebo load *only* those plugins, dropping
  the core systems, on a file `gz sdf -k` calls Valid; `enable_wind` must be set on the
  vehicle link or the wind applies to nothing and the flight looks normal; and the overlay
  changes the physics **even at zero wind** (0.16 → 0.358 m mean error), so the honest
  baseline is "overlay, wind 0", not the stock stack.
- **The NaN that passed a gate.** The controller recorded `NaN` when a position sample was
  invalid at the moment of arrival, and because every comparison against NaN is False,
  `worst > radius` passed it while `max()` dropped it. **The one case where the error was
  unknown was the one that looked clean.** The same class of bug recurred later in the
  current simulator's EKF-origin check.
- **Why the flight gate is not automated**, with the arithmetic: an 11.6 GB image against a
  hosted runner's ~14 GB of free disk, 20–40 min to build, and 2 vCPU against an
  aggregate-RTF floor of 0.95 — which would make the gate measure the runner rather than the
  code. Plus the security posture of a self-hosted runner on a public repo.
- **`P1-08` — the gate could not tell a void run from a real failure.** A gate run reported
  SR 5/10 that looked exactly like a controller regression; a second project on the same
  machine had started a UE4 simulator at 283% CPU partway through. Re-run on a quiet box,
  same seeds: **SR 10/10**. The current simulator's VOID-versus-FAIL scoring is the
  descendant of this entry.

### `gazebo/architecture.html` — container topology of the retired stack

What ran where and how it was wired, observed from the running stack rather than read off
the compose file. **The compose stack it describes has been deleted** — the current
simulator is brought up with raw `docker run` from `scripts/sim_up.sh` and has never used
compose — so treat the container and service names as historical.

Still worth reading for the facts that outlived it:

- **Four containers sharing one network namespace and one `/dev/shm`.** Sharing the network
  namespace alone was not enough: topics appeared in `ros2 topic list` while
  `ros2 topic echo` returned nothing, because each container had its own `/dev/shm` and
  Fast-DDS delivers over shared memory.
- **PX4's GCS MAVLink port is 18570, not 14550**, and the handshake explains the silence:
  PX4 has no preconfigured remote, so the ground station discovers by sending *to* 18570 and
  only then does PX4 learn its address. Proven both ways — binding 14550 on a fresh stack
  received nothing in 8 s, while binding 18570 failed `EADDRINUSE`. A heartbeat aimed at
  14550 is discarded with **no error and no log line**.
- **Never recreate the namespace donor alone.** Every container still reports *healthy*
  while `ros2 topic list` returns zero topics. Measured: **0 topics** after recreating the
  donor by itself, **24** after recreating everything.

### `isaac/driver-decision.md` — the Isaac Sim investigation, blocker and decision

The decision record described above, in full: the crash signature, the GPU-exclusion test
design, the three fallbacks evaluated (NGC container — exhausted, same host kernel driver;
Isaac 6.0 — no Pegasus; host rebase to R580 — open, owner-only), what deferring cost, and
what would reopen it.

Still worth reading for: the **reversibility** section, which is why the pins were kept
rather than deleted; and three reusable findings — `nvcr.io/nvidia/isaac-sim` images are
public with no NGC key, `--device nvidia.com/gpu=0` pins the GPU at the container boundary
(more reliable than application-level flags), and Isaac's container default is the streaming
app, so a headless probe that reports nothing is not evidence of "no crash".

The Isaac images were deleted 2026-07-30 to reclaim **~36 GB**; the document records the two
`docker pull` commands that restore them, because the evidence is the document, not the
images.

### `phase-0/todo.md` — environment and version lock

The toolchain backlog: drivers, ROS 2 Jazzy, PX4, the uXRCE-DDS agent, `px4_msgs`,
QGroundControl, and the Isaac items that were deferred with the stack.

Still worth reading for the traps, several of which still apply to the current stack:

- **The build plan's airframe target names were wrong**, enumerated from the built tree
  rather than trusted: every PX4 Gazebo target is `gz_`-prefixed, so `make px4_sitl
  x500_lidar_2d` simply fails. The corrected table of ten `gz_x500*` variants is there.
- **PX4's `pxh` shell clears `ICANON` without setting `VMIN`**, so behind a pipe or a
  `screen` pty it busy-spins its prompt — **4.1 GB of escape codes per 300 s run** plus a
  burned CPU core, which fills tmpfs and causes sensor TIMEOUTs by I/O saturation. With
  `stty min 1`: ~28 KB.
- **`px4_msgs` must be branch-matched to the firmware**, and the failure is silent: topics
  appear and never populate. A green `colcon build` does not close that item.
- **Verify a GUI by screenshot, not by assertion** — the Xvfb + `QT_QUICK_BACKEND=software`
  recipe that got QGroundControl visually confirmed in a container with no usable `DISPLAY`.

### `reference/01_sim_stack_report.md` and `reference/01_sim_stack_architecture.html`

The simulator landscape survey that opened the project, with its HTML render. (The two
filenames disagree; they are the same document.)

Still worth reading for:

- **The fork genealogy, with dates.** Microsoft AirSim discontinued 2023-12-15; Colosseum,
  Cosys-AirSim and Project AirSim as the living successors, with what each supports.
- **The finding that shaped everything after it: none of the three target papers uses
  Isaac.** Fly0 runs UE4 + AirSim, OnFly runs UE 4.27, and SPF's "DRL benchmark" is the
  **Drone Racing League Simulator, not a deep-RL AirSim benchmark** — a misreading the
  document calls out explicitly.
- **The PX4 ↔ ROS 2 facts that are still current**: uXRCE-DDS rather than MAVROS, the
  branch-match rule, the Simulator MAVLink API on TCP 4560, and the port map (14550 GCS,
  14540 offboard, 8888 agent).

Superseded: the recommended three-stack architecture, the monorepo layout, and the phase
plan at the end.

### `reference/02_development_plan.md` and `.html`

The executable build plan — phase gates, exact setup commands, containerization, CI,
evaluation harness, risk register. Budgeted at 18–22 weeks for one engineer, 9–11 for three.

Still worth reading for:

- **The risk register**, which is the most useful page in the archive: twelve risks, each
  with a likelihood, an impact, a mitigation and a stated *trigger → fallback*. It is also a
  lesson in how fallbacks decay — its answer to "the Unreal stack proves unbuildable" was
  "fall back to Colosseum", and Colosseum was **archived read-only on 2026-07-11**, as
  `reference/04` records. A fallback is only as good as the last time someone checked it was
  alive.
- **The two version-coupling landmines** the architecture was built around: Pegasus v5.1.0
  was developed and tested against **PX4 v1.14.3** rather than v1.16.x, and Isaac Sim 5.1
  ships **Python 3.11** while ROS 2 Jazzy on Ubuntu 24.04 is **Python 3.12**, so `rclpy`
  cannot be shared.
- **The evaluation framework and the published comparison targets** — SR, SPL, NE, OSR, CR
  defined, with Fly0 at 70.43% SR / 27.19 m NE on AerialVLN and 64.67% on OpenFly, OnFly at
  67.8% on its own 150-task set, SPF at 93.9% on the DRL simulator. **And the caveat that
  matters most: the papers disagree on the success threshold — 20 m for AerialVLN/OpenFly,
  5 m for Fly0 and OnFly — so the threshold must be a parameter, and results across
  thresholds are not comparable.**

Superseded: the phase order, the parallel-stack sequencing, the two-PX4-tree requirement
(the second tree existed for Pegasus), and the container/compose topology.

### `reference/03_hardware_assessment.md` and `.html`

The go/no-go on this workstation: Core i9, RTX 3080 10 GB, RTX 5060 Ti 16 GB, 64 GB RAM.
Verdict: **qualified yes**, conditional on the GPU assignment.

Still worth reading for — and this one is still an operating rule:

- **Render on the RTX 3080, infer on the RTX 5060 Ti.** The 3080 has ~1.9x the CUDA cores
  and ~1.7x the memory bandwidth, and being Ampere it carries zero Blackwell driver-crash
  exposure; the 16 GB 5060 Ti holds an 8B AWQ model with KV headroom. Swapping them
  reintroduces the instability the split exists to avoid.
- **`CUDA_VISIBLE_DEVICES` does not control the Vulkan-based RTX renderer.** vLLM respects
  it; the renderer does not. This is why GPU selection is enforced at the container
  boundary.
- **Heterogeneous multi-GPU *rendering* is documented-buggy** (corrupted output across
  dissimilar cards). The strategy is task partitioning, never split-rendering.
- **Do not run a UE5 shader compile concurrently with a heavy simulator** — 64 GB will not
  comfortably hold both. Still true, and still the reason the current build steps are
  staggered.
- The VRAM budget table per component, and the honest caveat attached to it: NVIDIA
  publishes no per-sensor or absolute scene VRAM figures, so those numbers are
  order-of-magnitude planning numbers from forum reports, not measurements.

### `reference/04_ue5_stack_architecture.md` — **the origin of the surviving architecture**

This is the document that chose Unreal Engine + Cosys-AirSim, and its central argument is
still the project's: it was the only actively maintained AirSim-lineage option meeting every
hard requirement — Unreal photorealism, PX4 SITL with a `uxrce_dds_client` publishing the
same `/fmu/*` topics the real Pixhawk 6C produces, the richest sensor suite (GPU-LiDAR,
event, segmentation, thermal), Cesium real-world maps, UE dynamic actors, and a frame-grab +
velocity API — under MIT. Read it for **why the stack is what it is**.

**Its UE5.5 specifics are superseded by UE5.8**, and the correction is recorded inside the
document rather than edited over the original, along with two others taken the same day:

- **UE5.5 and ROS 2 Jazzy cannot be had from one upstream tag.** The last UE5.5 release
  predates the Jazzy fix (commit `83d1b81c`, rewriting `cv_bridge`/`tf2_geometry_msgs`
  includes from `.h` to `.hpp`), which landed in the v3.4 line targeting **UE5.8**. Measured
  rather than inferred: the installed `cv_bridge` ships **no `.h` shim at all**, so the
  UE5.5 tag was measurably unbuildable here. The pin became `5.8-v3.4.1`, SHA
  `a552dd6cd517b8d5d26629ad88004356c3007326`.
- **Its ROS 2 Humble recommendation was inverted, not merely overridden.** Current upstream
  includes `<cv_bridge/cv_bridge.hpp>`, which Humble's `vision_opencv` does not ship — so
  acting on the Humble advice would fail to compile immediately. Jazzy everywhere.
- **The Epic engine base image is Ubuntu 22.04**, read from its config blob, and ROS 2 Jazzy
  has no jammy packages. That makes the separate `sim` and `ros2` containers **mandatory
  rather than a deployment preference**, and the boundary between them stays the RPC /
  MAVLink socket.

Also still live from this document, and now backlog items rather than research: Cesium
georeferenced terrain, UE MassAI / City Sample dynamic actors, and wind through the AirSim
API. Its known pitfalls — Cesium tiles going black at 10–500 ft AGL, the AirSim NED origin
versus Cesium georeference versus UE centimetres mismatch, `-RenderOffScreen` defaulting to
GPU 0 — are unresolved rather than obsolete.

---

## What is **not** in here

**The worklogs are not archived.** [`../worklog/`](../worklog/) (rendered index:
[`../worklog/html/index.html`](../worklog/html/index.html)) is the dated, append-as-it-happens
record of how the work actually went — findings, measurements, dead ends and corrections at
the moment each was made. It is frozen in a different sense: **it is never edited, including
its original terminology and filenames**, because rewording a dated record falsifies it. If
you want to know what was actually known on a given day, read the worklog for that day, not
a backlog written afterwards.

**Three documents were promoted out of the retired set rather than archived**, because they
are still active specifications:

| Now at | Was | Why it survived |
|---|---|---|
| [`../conventions.md`](../conventions.md) | the Gazebo stack's frozen topic/namespace/frame conventions | It is the ROS 2 graph's public surface, and the sim-to-real parity claim rests on it |
| [`../todo.md`](../todo.md) | the Unreal + Cosys-AirSim backlog | It is now *the* backlog |
| [`../nvenc-driver-blocker.md`](../nvenc-driver-blocker.md) | filed alongside the Unreal work | The encoder is still blocked on the same driver as Isaac Sim |

---

## Reading these safely

1. **Every path, image tag, container name and compose service named in here may be gone.**
   The renaming that came with the pivot is not tracked inside the archived files.
2. **Task IDs were renumbered.** The `C-NN` IDs these documents and the git history use are
   now `SIM-NN`, same number. The mapping is [`id-map.md`](id-map.md); `P0-*`, `P1-*` and
   `D-*` were **not** renumbered.
3. **Numbers are preserved exactly and are still true of the runs that produced them.** They
   are not claims about the current stack. Where a measurement was later refuted, the
   refutation is recorded next to it rather than replacing it — that pattern is deliberate
   and appears throughout.
4. **Where an archived document contradicts the repo as it stands, the repo wins.** These
   files are evidence about decisions, not documentation of the software.
