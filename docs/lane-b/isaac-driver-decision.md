# Lane B — Isaac Sim: investigation, blocker, and decision to defer

**Status:** Lane B is **deferred**. Lane C (UE5.5 + Cosys-AirSim) is promoted to the
photorealistic-perception lane in its place.
**Decided:** 2026-07-29 · **Decided by:** owner, on the evidence below.
**Evidence:** [`../worklog/2026-07-28-phase-0-lane-a-install.md`](../worklog/2026-07-28-phase-0-lane-a-install.md) (`P0-09` section).

This document exists so the decision is not re-litigated from memory, and so the work is
recoverable if the driver situation changes.

---

## 1. What Lane B was for

Isaac Sim 5.1 + Pegasus Simulator v5.1.0 was the plan's photorealistic-perception lane:
RTX-raytraced camera/stereo/depth/Lidar, domain randomization, VLM-in-the-loop and RL,
with Pegasus bridging PX4 over MAVLink so the same ROS 2 graph is reused unchanged
(`docs/reference/02_development_plan.md:4`).

## 2. The blocker, established empirically

**Isaac Sim 5.1 does not run on this machine's NVIDIA driver.**

| | |
|---|---|
| Driver installed on `carbonite` | **610.43.03** |
| Isaac Sim 5.1 validated driver | **580.65.06** |
| Result | **SIGSEGV on startup** |

Test: `nvcr.io/nvidia/isaac-sim:5.1.0` (14.0 GB, public — no NGC login needed), headless
`SimulationApp({"headless": True})`, with **only GPU 0 (RTX 3080) exposed** via
`--device nvidia.com/gpu=0`.

```
docker rc=139
rtx.scenedb.plugin crash signature x5
[Fatal] librtx.scenedb.plugin.so!carbOnPluginStartup+0x3b4de
Segmentation fault (core dumped)
```

This is verbatim the failure documented in `docs/reference/03_hardware_assessment.md:41`
and in `isaac-sim/IsaacSim#537` / `#229`.

### The disambiguation that matters

The crash occurred on the **Ampere RTX 3080**, with the Blackwell RTX 5060 Ti excluded
from the container entirely. **This is not the documented Blackwell/Isaac instability — it
is purely the driver version.** Reassigning GPU roles cannot fix it. Exposing a single GPU
was deliberate test design: with both cards visible the result would have had two possible
causes.

### Why it cannot be fixed from inside the container

The driver is injected from the **ostree-immutable host** (`--nvidia`). It cannot be
changed with apt inside `drone-sim`; a downgrade means rebasing the host image, which needs
host `sudo` — an owner action. `carbonite` also serves ~1.1 TB of Steam libraries that may
expect a current driver, so the downgrade is not cost-free.

## 3. Fallbacks evaluated

| # | Option | Outcome |
|---|---|---|
| 1 | **NGC container** for Isaac 5.1 | ❌ **Exhausted.** The container bundles userspace but still uses the host kernel driver — the thing that is wrong. Same SIGSEGV. |
| 2 | **Isaac Sim 6.0** (`nvcr.io/nvidia/isaac-sim:6.0.0`, 19.6 GB, public) | 🔶 **Inconclusive but promising** — see below. |
| 3 | **Host image rebase to R580** | Open. Owner-only. Preserves the plan exactly. |

### On Isaac 6.0 — what is and is not known

**Known:** it does **not** hit the `rtx.scenedb` crash. It reaches `app ready` at ~156 s
with GPU 0 doing real work (27% utilisation, 1613 MiB VRAM). It also ships an **internal
`rclpy` for ROS 2 Jazzy** which loads successfully:

```
[59.292s] Attempting to load system rclpy
[59.292s] Could not import system rclpy: No module named 'rclpy'
[59.292s] Attempting to load internal rclpy for ROS Distro: jazzy
[59.437s] rclpy loaded
[ext: isaacsim.ros2.bridge-5.1.1] startup
```

That is significant: it would dissolve the plan's High-risk "Isaac ↔ Jazzy Python split"
(`02_development_plan.md:13`) and delete the `P0-10` Python-3.11-workspace workaround.

**Not known:** whether a headless stage-and-step cycle completes. Three probe attempts all
stalled at `app ready` with **zero probe output** — not even the marker printed *before*
the Isaac import. The container defaults to `isaacsim.exp.full.streaming.kit` (the Full
Streaming App), which runs its own event loop; passing
`experience=isaacsim.exp.base.python.kit` did not override it, and
`isaac-sim.compatibility_check.sh` loads the same streaming app. **The probe could not
report, so "no crash" must not be read as "verified working."**

### The fact that actually decided it

**There is no Pegasus release for Isaac Sim 6.0.** Upstream tags stop at **v5.1.0**
(checked 2026-07-28: `v4.5.0`, `v4.5.1`, `v5.1.0`; branches `main`, `dev`, `dev_cuda`,
`gh-pages`), and v5.1.0 pins to Isaac 5.1.0 explicitly.

So Isaac 6.0 does not merely defer a Pegasus upgrade — it means **no PX4↔Isaac bridge at
all**, which would have to be written. That runs directly against the project's primary
rule: *reuse and integrate upstream, don't reinvent* (`.ai/AGENTS.md:27`). Writing a
Pegasus replacement is a project, not glue.

## 4. Decision

**Defer Lane B. Promote Lane C (UE5.5 + Cosys-AirSim) as the photorealistic-perception
lane.**

Reasoning:

- **The target papers live in Unreal, not Isaac.** Fly0 uses UE4 + AirSim; OnFly uses
  UE 4.27; SPF uses the DRL simulator. `01_sim_stack_report.md:4` states plainly that
  **none of the three target papers uses Isaac**. Lane C is where AerialVLN/OpenFly
  reproduction was always going to happen.
- **Lane C is driver-agnostic.** UE5 does not care about 610.43.03, so the `P0-09` blocker
  simply does not exist there.
- **EpicGames GitHub org access is already in place** (confirmed 2026-07-28), which is
  normally the gating hurdle for `ghcr.io/epicgames/unreal-engine:dev-slim-5.5.4`.
- **Cesium georeferenced terrain works with physics in UE5.** The FSD/PhysX mutual
  exclusion is Omniverse-specific; the plan already routes georeferenced physics to Lane C
  (`02_development_plan.md:21`).
- **Isaac ROS is unaffected.** cuVSLAM and nvblox are ROS 2 packages consuming image and
  depth topics — they do not require Isaac *Sim*. The perception stack stays as designed
  regardless of which renderer feeds it.

### What this costs

Stated plainly, because deferring Lane B is not free:

- **RTX-raytraced sensor fidelity** — Isaac's raytraced Lidar and its GPU-CUDA-stream IMU
  codepath are the most defensible synthetic sensors available, and the sim-stack report
  rates them best-in-class for VIO work.
- **Isaac Lab RL** — thousands of parallel environments for RL training.
- **Lane C is rated High/Med risk for build fragility.** The precompiled Linux plugin
  targets UE5.2.1 and the Cosys-AirSim UE5.5 branch was a March-2025 pre-release; a
  known-good commit must be pinned and it may not build first try.
- **UE5 source builds are heavy** — hours of shader compilation, and the hardware
  assessment warns against running that concurrently with other GPU work.

## 5. Reversibility — what would bring Lane B back

Lane B is deferred, **not abandoned**. Any one of these reopens it:

1. **The host is rebased to an R580-branch driver.** Isaac 5.1 + Pegasus v5.1.0 then work
   exactly as the plan specifies. This is the cleanest path and remains available at any
   time.
2. **Pegasus ships an Isaac 6.0 release.** Combined with 6.0's internal Jazzy `rclpy`, Lane
   B would return *better* than planned — no Python-split workaround at all.
3. **Isaac 6.0 is proven to work headless here** *and* a maintained PX4 bridge appears.

`versions.lock` retains all Lane B pins with their status, so nothing about the decision
has to be re-derived.

**The Isaac images were deleted 2026-07-30** to reclaim ~36 GB, after `D-01` closed and
Lane B was deferred. They are **public — no NGC login — so re-pulling is a single command**:

```bash
docker pull nvcr.io/nvidia/isaac-sim:5.1.0   # 14.0 GB — the version Pegasus v5.1.0 pairs with
docker pull nvcr.io/nvidia/isaac-sim:6.0.0   # 19.6 GB — no crash on driver 610, but no Pegasus
```

The *evidence* for the decision is this document plus
`docs/worklog/2026-07-29-d01-container-parity.md`, not the images themselves — the crash
signature, the GPU table and the probe results are all recorded above.

## 6. Reusable findings from this investigation

Worth keeping even though Lane B is deferred:

- **`nvcr.io/nvidia/isaac-sim` images are public** — no NGC API key or login required.
  Resolves a `TODO-verify` in `versions.lock`.
- **`--device nvidia.com/gpu=0` pins the GPU at the container boundary.** Isaac's own
  `[gpu.foundation]` table then shows the 3080 as index 0, so the render-on-GPU-0 rule can
  be enforced by the container rather than by `--/renderer/activeGpu` flags that are easy
  to forget.
- **Isaac's container default app is the streaming app.** Any future headless automation
  must override the experience explicitly — and verify the override took effect, because
  passing `experience=` to `SimulationApp` did **not** work here.
