# drone-sim — master backlog index

**This file only points.** The detail — the change, the reason, and the acceptance
criterion — lives in the per-area TODO doc. Every feature or non-trivial change must
exist as a documented TODO in its area doc *before* it is built, and be marked done when
it lands (`.ai/AGENTS.md:72`).

**Current position: Phase 1 — Lane A Baseline. The exit criterion is MET; the phase is not
finished.**

As of 2026-07-31 the aircraft flies under its own ROS 2 controller over uXRCE-DDS, and the
gate that proves it passes **SR 10/10 across ten genuinely different wind conditions**
(waypoint error tracking wind speed at r = 0.921), with an MCAP kept per run.

| | |
|---|---|
| Done | **every Phase 1 task**: `P1-00` conventions · `P1-01` contracts · `P1-02` controller · `P1-03`/`P1-03a` launch + `/clock` · `P1-04`/`P1-04a` seeded runner and conditions · `P1-05` MCAP · `P1-06` gate · `P1-07` tier 1 |
| Open | `D-06` container boundaries · `D-07` automated flight gate — both **deferred by decision**, not outstanding work |

**Two things are true at once and both matter.** The exit criterion is met — and the flight
gate is **not automated**: it runs when someone runs it. Tier-1 CI (25 off-target tests,
parse checks, `compose config`) runs on every push in 24 s and `main` requires it, but the
simulator cannot run on a hosted runner and a self-hosted one on a public repo would execute
fork code on the workstation. `./scripts/run_local_ci.sh --gate` is the accepted substitute.
See `P1-07` and `D-07`.

Phase 0 remains at **4 of 5 exit criteria** — the outstanding one is Isaac Sim, which is
deferred with `P0-09`; see [`lane-b/isaac-driver-decision.md`](lane-b/isaac-driver-decision.md).

## Next: Lane C bring-up — decided 2026-07-31

**The project is going all-in on Lane C.** Cosys-AirSim on UE5.5 is now the **primary**
simulator, and **Phase 2 (perception + obstacle avoidance) is built there, not in Gazebo.**
Decision doc: [`reference/04_ue5_stack_architecture.md`](reference/04_ue5_stack_architecture.md).
Backlog: [`lane-c/todo.md`](lane-c/todo.md).

Three questions that doc left open were settled at the same time:

| Question | Decision |
|---|---|
| Lane A — retire or demote? | **Demote to an always-on regression baseline.** It keeps tier-1 CI, the `P1-06` flight gate and the real-hardware PX4 tree. No new capability work. |
| ROS 2 Humble (per `04`) or Jazzy (per `versions.lock`)? | **Jazzy, everywhere** — a deliberate deviation from `04`, whose Humble recommendation is inherited from upstream example docs rather than measured. |
| Adopt `04`'s weeks-based Phase 0–3? | **Map, don't renumber.** Our phases are capabilities with measured exit criteria; `04`'s are calendar weeks. The mapping table lives in `04`. |
| **UE5.5 (per `04`) or UE5.8?** *(surfaced by research, same day)* | **UE5.8, tag `5.8-v3.4.1`.** UE5.5 and Jazzy cannot be had from one upstream tag — the last UE5.5 release predates the Jazzy fix and its branch is end-of-life. Measured here: `ros-jazzy-cv-bridge 4.1.0` ships no `cv_bridge.h`, so that tag is unbuildable on this machine. ✅ **Cesium gate cleared same day** — Cesium v2.28.0 supports UE5.8, and v2.29.0 *drops* UE5.5, so the fallback inverted: UE5.8 is now the only forward-supported path. |

**The immediate next task is `C-06` — build the Cosys-AirSim ROS 2 wrapper against Jazzy** —
chosen first because it is the cheapest test that can invalidate the distro decision, and it
needs no Unreal Engine, no GPU and no simulator to run.

**State this plainly: Lane C has never been built.** Every `lane_c` entry in `versions.lock`
is `pinned` or `TODO-verify` with no passing smoke test, and the plan rates the lane **High
likelihood / Med impact** for build fragility. Making it primary does not lower that risk —
it removes the fallback lane, which is why Lane A stays alive.

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

   **Amended 2026-07-31 — one documented exception, forced by upstream licensing.** Lane C
   is now the primary stack, and its engine base image
   (`ghcr.io/epicgames/unreal-engine`) is **credential-gated**: anonymous pulls are HTTP
   403, and building needs EpicGames GitHub org membership plus a PAT with
   `read:packages`. A clone plus a Dockerfile is therefore **not sufficient** for Lane C,
   and no amount of pinning changes that. The goal is restated rather than quietly failed:
   **from the repo alone, plus one documented credential step** — documented in
   `docker/README.md`, failing with a readable message rather than a registry 403, and
   named up front. Lane A is unaffected and still meets the original wording. See
   [`docker/todo.md`](docker/todo.md) `D-04`.

---

## Task ID convention

Tasks are `P<phase>-<nn>` — `P0-01`, `P2-07`. **Never write them as `#N`.** A bare `#N`
in a PR body, issue, or commit message auto-links to an unrelated same-repo issue
(`.ai/AGENTS.md:153`). `P0-01` cannot mis-link, which is why the scheme exists.

Cross-repo references are always fully qualified: `PX4/PX4-Autopilot#25089`, never
`PX4-Autopilot#25089` (which does not link at all) and never a bare `#25089`.

---

## Phase roadmap

Phases 0 and 1 were built in **strict lane order** — Lane A first, because a CI/iteration
backbone had to be solid before rendering complexity was added. **That is done, and the
order now inverts:** Phase 2 onward is built in Lane C, with Lane A held behind it as the
regression baseline.

| Phase | Area doc | Lane | Goal | Unlocks | Status |
|---|---|---|---|---|---|
| **0** | [`phase-0/todo.md`](phase-0/todo.md) | A | Reproducible pinned toolchain; every component passes its smoke test; `versions.lock` committed | — | **in progress** — 4/5 exit criteria met |
| — | [`docker/todo.md`](docker/todo.md) | all | **Cross-cutting:** the stack reproducible as Docker | **reproducibility** | in progress — Lane A ✅ native-equivalent (`D-01`); **Lane C not met** — `D-04` promoted off the back burner, and the credential gate above |
| 1 | [`lane-a/todo.md`](lane-a/todo.md) | A | Deterministic headless PX4+Gazebo SITL; ROS 2 offboard controller flying GPS waypoints in lockstep; MCAP; CI <10 min | **GPS navigation** | ✅ **exit criterion MET** (SR 10/10); `D-06`/`D-07` deferred by decision |
| 2 | [`lane-c/todo.md`](lane-c/todo.md) → then `planning/todo.md`, `perception/todo.md` | **C** | **Lane C bring-up first** (`C-06`, `C-01`–`C-04`, `C-07`), then depth/LiDAR mapping + EGO-Planner collision-free flight through clutter | **obstacle & collision avoidance** | **next** |
| 3 | `vlm/todo.md`, `perception/todo.md` *(not yet created)* | **C** | Slow VLM target-generator + fast tracker; depth back-projection; cuVSLAM VIO → EKF2; GPS-denied | **vision-based navigation, VLM experimentation** | not started |
| 4 | `eval/todo.md`, `hardware/todo.md` *(not yet created)* | **C** + real | AerialVLN/OpenFly reproduction; onboard Jetson VLM; PX4 HITL then real flight | **sim-to-real** | not started |

**Phase 2's area docs are deliberately not created yet.** Area docs are written when their
phase starts, and Phase 2 starts with Lane C bring-up — which lives in `lane-c/todo.md`.
Writing a perception backlog against a simulator that has not been proven to build is
exactly the "plan written from the docs rather than from evidence" failure this project has
a rule against. They get created once `C-03` proves the stack flies.

### Lane assignment — changed 2026-07-29, and again 2026-07-31

| Lane | Stack | Role | Status |
|---|---|---|---|
| **A** | PX4 v1.16 + Gazebo Harmonic + ROS 2 Jazzy | **regression baseline** — tier-1 CI, the `P1-06` flight gate, controls ground truth, and the real-hardware PX4 tree | ✅ **working, frozen in scope** |
| **B** | Isaac Sim 5.1 + Pegasus v5.1.0 | photoreal perception | ⛔ **deferred** — see [`lane-b/isaac-driver-decision.md`](lane-b/isaac-driver-decision.md) |
| **C** | UE5.5 + Cosys-AirSim | ⭐ **PRIMARY** — Phase 2 perception + obstacle avoidance, Phase 3 VLM, Phase 4 benchmark reproduction | **next up — never built** |

**Why the swap (2026-07-29).** Isaac Sim 5.1 SIGSEGVs on this machine's NVIDIA driver
(610.43.03 vs its validated 580.65.06) — reproduced on the *Ampere* card with Blackwell
excluded, so it is the driver, not the GPU. Isaac 6.0 avoids the crash but **no Pegasus
release exists for it**, which would mean writing the PX4↔Isaac bridge ourselves — against
the reuse-upstream rule. Meanwhile **none of the three target papers (SPF, Fly0, OnFly) uses
Isaac**; they all evaluate on Unreal/AirSim-family simulators, so Lane C was always where
benchmark reproduction had to happen. Lane B is **deferred, not abandoned** — a host driver
rebase to R580 reopens it at any time.

**Why Lane C became primary (2026-07-31).** The same argument, followed to its conclusion:
if the benchmarks must run on an Unreal/AirSim stack anyway, then building Phase 2's
perception and obstacle avoidance in Gazebo means building it twice. `04` makes the case
that Cosys-AirSim is the only actively-maintained option meeting every hard requirement —
Microsoft AirSim was archived 2023-12-15 and Colosseum 2026-07-11.

**Why Lane A survives it.** Lane C has never been built and is rated High-likelihood for
build fragility. Lane A is the only thing in the project that currently flies, it holds the
only working flight gate, and it runs the exact PX4 tree the real Pixhawk 6C will run. A
Lane C regression is measured against a Lane A run — that comparison is the reason to keep
it, and it is why the demotion is not a retirement.

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

**These criteria are unchanged by the lane switch — but from Phase 2 on they are measured in
Lane C.** That adds one prerequisite the table does not show: Lane C must first pass its own
flight gate (`C-07`), because Lane A's SR 10/10 is evidence about Gazebo and does not
transfer. `04`'s decision thresholds (lockstep sustaining sensor cadence; Cesium tiles not
going black below ~150 ft AGL) are adopted **in addition**, as Lane C acceptance criteria —
they do not replace anything above.

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
  branch-matched message set, **one ROS 2 distro (Jazzy)**. See
  [`versions.lock`](../versions.lock). The two-tree count is now *in question* in a good
  way: if Lane C drives PX4 v1.16.0, it collapses to one (`C-03`).
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
| [`roadmap.html`](roadmap.html) | Phases and timeline, as a single page |
| [`bench.md`](bench.md) | The machine and container being worked in |
| [`reference/01_sim_stack_report.md`](reference/01_sim_stack_report.md) | Simulator landscape, why dual-sim, the three target papers |
| [`reference/02_development_plan.md`](reference/02_development_plan.md) | Phased build plan, version-coupling landmines, CI, repo layout |
| [`reference/03_hardware_assessment.md`](reference/03_hardware_assessment.md) | Go/no-go on this exact machine, GPU work-split, VRAM budgets |
| [`reference/04_ue5_stack_architecture.md`](reference/04_ue5_stack_architecture.md) | **The Lane C decision** — simulator survey, why Cosys-AirSim/UE5.5, container topology, and the three decisions taken on it 2026-07-31 |
| [`worklog/`](worklog/) | Running record of each non-trivial investigation, updated as it happens |
