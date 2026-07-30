# drone-sim — master backlog index

**This file only points.** The detail — the change, the reason, and the acceptance
criterion — lives in the per-area TODO doc. Every feature or non-trivial change must
exist as a documented TODO in its area doc *before* it is built, and be marked done when
it lands (`.ai/AGENTS.md:72`).

**Current position: Phase 0 — Environment & Version Lock. Lane A is up and verified.**
As of 2026-07-29: ROS 2 Jazzy, PX4 v1.16.0 + Gazebo Harmonic, the uXRCE-DDS agent and
branch-matched `px4_msgs` are installed and smoke-tested end to end — headless SITL for
300 s with 0 sensor TIMEOUTs, RTF 1.000, 24 `/fmu/out/*` topics at 100 Hz, and QGC
connected. **4 of 5 Phase 0 exit criteria met.** See
[`phase-0/todo.md`](phase-0/todo.md) and the worklogs.

---

## Project goals

1. **VLM-based sim-to-real drone navigation** — reproduce and extend the SPF / Fly0 /
   OnFly line of work, first in sim, then on a Pixhawk 6C / X500 with a Jetson Orin NX.
2. **Reuse and integrate upstream, don't reinvent** — the glue is the original work.
3. **The whole setup must be easily reproducible as Docker.** *(added 2026-07-29)* A
   fresh machine must reach a working stack from this repo alone — no undocumented manual
   steps, no "it works on carbonite". Backlog: [`docker/todo.md`](docker/todo.md).
   This goal is retroactive: Phase 0 was installed natively, and capturing that exact
   working recipe as a Dockerfile (`D-01`) is the highest-priority item in that area,
   because the recipe is perishable and three of its steps deviate from the reference
   docs.

---

## Task ID convention

Tasks are `P<phase>-<nn>` — `P0-01`, `P2-07`. **Never write them as `#N`.** A bare `#N`
in a PR body, issue, or commit message auto-links to an unrelated same-repo issue
(`.ai/AGENTS.md:153`). `P0-01` cannot mis-link, which is why the scheme exists.

Cross-repo references are always fully qualified: `PX4/PX4-Autopilot#25089`, never
`PX4-Autopilot#25089` (which does not link at all) and never a bare `#25089`.

---

## Phase roadmap

Build in **strict lane order**. Lane A is the CI/iteration backbone and must be solid
before rendering complexity is added.

| Phase | Area doc | Goal | Unlocks | Status |
|---|---|---|---|---|
| **0** | [`phase-0/todo.md`](phase-0/todo.md) | Reproducible pinned toolchain; every component passes its smoke test; `versions.lock` committed | — | **in progress** — 4/5 exit criteria met |
| — | [`docker/todo.md`](docker/todo.md) | **Cross-cutting:** the stack reproducible as Docker | **reproducibility** | in progress — `D-01` ✅ container is native-equivalent |
| 1 | `lane-a/todo.md` *(not yet created)* | Deterministic headless PX4+Gazebo SITL; ROS 2 offboard controller flying GPS waypoints in lockstep; MCAP; CI <10 min | **GPS navigation** | not started |
| 2 | `planning/todo.md`, `perception/todo.md` *(not yet created)* | Depth/LiDAR mapping + EGO-Planner collision-free flight through clutter | **obstacle & collision avoidance** | not started |
| 3 | `vlm/todo.md`, `perception/todo.md` *(not yet created)* | Slow VLM target-generator + fast tracker; depth back-projection; cuVSLAM VIO → EKF2; GPS-denied | **vision-based navigation, VLM experimentation** | not started |
| 4 | `eval/todo.md`, `hardware/todo.md` *(not yet created)* | AerialVLN/OpenFly reproduction; onboard Jetson VLM; PX4 HITL then real flight | **sim-to-real** | not started |

### Lane assignment — changed 2026-07-29

| Lane | Stack | Role | Status |
|---|---|---|---|
| **A** | PX4 v1.16 + Gazebo Harmonic + ROS 2 Jazzy | CI/iteration backbone | ✅ **working** |
| **B** | Isaac Sim 5.1 + Pegasus v5.1.0 | photoreal perception | ⛔ **deferred** — see [`lane-b/isaac-driver-decision.md`](lane-b/isaac-driver-decision.md) |
| **C** | UE5.5 + Cosys-AirSim | **promoted → photoreal perception + benchmark reproduction** | next up |

**Why the swap.** Isaac Sim 5.1 SIGSEGVs on this machine's NVIDIA driver (610.43.03 vs its
validated 580.65.06) — reproduced on the *Ampere* card with Blackwell excluded, so it is
the driver, not the GPU. Isaac 6.0 avoids the crash but **no Pegasus release exists for
it**, which would mean writing the PX4↔Isaac bridge ourselves — against the reuse-upstream
rule. Meanwhile **none of the three target papers (SPF, Fly0, OnFly) uses Isaac**; they all
evaluate on Unreal/AirSim-family simulators, so Lane C was always where benchmark
reproduction had to happen. Lane B is **deferred, not abandoned** — a host driver rebase to
R580 reopens it at any time.

Area docs are created when their phase starts — an empty backlog doc is noise. Phase 0
is the only one that exists today.

---

## Exit criteria per phase

Copied from `docs/reference/02_development_plan.md` so the bar is visible without
opening the plan. **Every one of these is a measured result, not a judgement call.**

| Phase | Exit criteria |
|---|---|
| 0 | `make px4_sitl gz_x500` flies headless with **no Accel/Mag TIMEOUT over a 5-min run**; `ros2 topic list` shows `/fmu/out/*`; QGC connects; Isaac Sim 5.1 launches and its Python-3.11 ROS bridge publishes `/clock`; a "hello VLM" call hits a vLLM OpenAI-compatible endpoint |
| 1 | Automated test takes off, flies a 4-waypoint square, lands — **SR = 100% over 10 seeded runs**; MCAP artifact uploaded |
| 2 | **0 collisions over 20 seeded cluttered-world runs**; reach-goal success **≥ 80%**; replan latency logged |
| 3 | VLM-commanded **SR ≥ 50% (SR@5m)** on a 20-episode internal set; **VIO-only hover drift < 1 m / 60 s**; end-to-end target-gen latency budget documented |
| 4 | AerialVLN SR within **~10 absolute points** of Fly0's 70.43% (or a documented gap analysis); **onboard decision latency ≤ 1 s**; one real GPS-waypoint flight + one VLM-nav flight |

---

## Cross-cutting rules that shape every task

These are not tasks; they are constraints every task inherits.

- **Verify by running it, end to end.** A clean `colcon build` proves nothing about
  flight. Exercise the full ROS 2 graph in the target lane and record the evidence —
  MCAP bag, metric table, measured latency. If you cannot verify, say so and name the
  blocker (`.ai/AGENTS.md:218`).
- **A success rate over N seeded runs, never a single pass.** A flaky green is a fail
  until the real-time-factor floor holds.
- **Reuse upstream; don't reinvent.** PX4, Pegasus, Isaac ROS, EGO-Planner,
  Cosys-AirSim, vLLM are pinned and wrapped. The original work is the glue and the
  experiment harness.
- **Version coupling is the architecture.** Two PX4 trees, two Python runtimes, one
  branch-matched message set. See [`versions.lock`](../versions.lock).
- **Never command the real aircraft without explicit per-run approval.** SITL is exempt
  and safe; say which you are doing. Approval never carries over
  (`.ai/AGENTS.md:39`).

---

## Open blockers

Tracked here because they cross phase boundaries. Detail lives in the area doc.

| ID | Blocker | Blocks | Detail |
|---|---|---|---|
| `P0-09` | **RESOLVED as a decision, not a fix.** Isaac Sim 5.1 SIGSEGVs on driver 610.43.03 (validated: 580.65.06); driver comes from the immutable host | Lane B — now **deferred** | [`lane-b/isaac-driver-decision.md`](lane-b/isaac-driver-decision.md) |

Lane A, Lane C and the VLM server are unaffected. The one open owner-decision is whether
to rebase the host to an R580 driver, which would reopen Lane B exactly as originally
planned.

---

## Related documents

| Doc | What it is |
|---|---|
| [`../versions.lock`](../versions.lock) | The pinned toolchain and the couplings CI must assert |
| [`bench.md`](bench.md) | The machine and container being worked in |
| [`reference/01_sim_stack_report.md`](reference/01_sim_stack_report.md) | Simulator landscape, why dual-sim, the three target papers |
| [`reference/02_development_plan.md`](reference/02_development_plan.md) | Phased build plan, version-coupling landmines, CI, repo layout |
| [`reference/03_hardware_assessment.md`](reference/03_hardware_assessment.md) | Go/no-go on this exact machine, GPU work-split, VRAM budgets |
| [`worklog/`](worklog/) | Running record of each non-trivial investigation, updated as it happens |
