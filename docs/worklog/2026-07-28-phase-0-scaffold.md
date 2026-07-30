# 2026-07-28 — Phase 0 scaffold, backlog, and drafted version lock

**Task.** Plan and scaffold only — no installs. Create the master backlog index and a
Phase 0 area doc, lay out the monorepo from the development plan, and draft
`versions.lock` with every pin the plan calls out.

**Outcome.** Scaffold complete. One significant unplanned finding (the NVIDIA driver
conflict, `P0-09`) and one favourable one (GPU 1 is the 16 GB variant). Nothing
installed; nothing committed — weekday work hours.

---

## Context

Repo was freshly `git init`'d on `main` with **no commits**, holding only `.ai/`,
`AGENTS.md`, `CLAUDE.md`, and `docs/` (bench + three reference docs). Read
`docs/bench.md`, `.ai/AGENTS.md`, and all three `docs/reference/` docs before touching
anything, per the project rules.

---

## Findings

### F1 — The box is genuinely greenfield

Probed rather than assumed. As of 2026-07-28 there is **no ROS 2** (`/opt/ros` absent),
**no PX4**, **no Gazebo**, **no colcon**, and **no `nvcc`** in the container. System
Python is **3.12.3**. Phase 0 is starting from zero, which matches the plan's assumption.

```
$ ls /opt/ros                 → no /opt/ros
$ which gz ros2 MicroXRCEAgent colcon → (none)
$ python3 --version           → Python 3.12.3
```

### F2 — ⚠ NVIDIA driver 610.43.03 vs Isaac Sim 5.1's validated 580.65.06

**The single most consequential finding today.** Observed:

```
$ nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
0, NVIDIA GeForce RTX 3080,   10240 MiB, 610.43.03
1, NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 610.43.03
```

Isaac Sim 5.1's validated Linux driver is **580.65.06**, and Isaac is documented to break
on drivers that are **too new**, not merely too old:

- `isaac-sim/IsaacSim#537` — "Isaac Sim fails to detect CUDA device with NVIDIA driver
  595.79 (works with 580)."
- `isaac-sim/IsaacSim#229` — a 5070 on 580.65.06 hitting `Warp CUDA error: Failed to get
  driver entry point 'cuDeviceGetUuid'`, diagnosed as *"my driver is too new for this
  version of Isaac Sim."*
- NVIDIA forum staff: *"there's been report of invalidated driver past 580 on 5.1.0."*

(All three are already cited in `docs/reference/03_hardware_assessment.md:12` — this is
not new research, it is the existing analysis colliding with the machine's actual state.)

**610.43.03 is four branches past validated.** The hardware assessment's Stage 0
instruction is explicit: *"Verify Isaac Sim launches before installing anything else. Pin
the driver and hold it."*

**Why it is hard to fix here:** the driver is injected from the **ostree-immutable host**
via `--nvidia`. It cannot be changed with apt inside the container. A downgrade means
rebasing the host image, which needs host `sudo` — an owner action.

**Why it is not a project-stopper:** the blast radius is **Lane B only** (Isaac + Pegasus,
Phase 3). Lane A (PX4 + Gazebo, Phases 0–2) and the vLLM server do not touch Isaac.
Sequenced Phase 0 so this cannot stall the critical path — `P0-09` is deliberately last
on its own branch of the dependency graph.

Filed as `P0-09` with three ranked fallbacks (NGC container → Isaac 6.0 → host rebase).
Recorded in `versions.lock` as `status: CONFLICT`, **not** as a pin — pinning a version
that may not run would be a fabricated lock.

### F3 — GPU 1 is the 16 GB RTX 5060 Ti, not the 8 GB variant

`16311 MiB` observed. `docs/reference/03_hardware_assessment.md:83` left this open
(*"If your RTX 5060 Ti is the 8 GB variant: serve only Qwen3-VL-2B/4B locally"*).

**Implication:** **Qwen3-VL-8B AWQ is viable locally** with KV headroom — the VLM plan
gets the better branch, not the constrained one. Qwen3-VL-30B-A3B still does not fit
(~17 GB at INT4 before the vision tower and KV cache). Recorded in `versions.lock` and
`P0-13`.

### F4 — 24 cores / 62 GB RAM — ample for SITL lockstep

PX4 SITL lockstep is single-thread-latency-sensitive and CPU starvation is the documented
cause of the `Accel #0 fail: TIMEOUT!` / `MAG #0 failed: TIMEOUT!` failures. 24 cores is
comfortable headroom, so if TIMEOUTs appear in `P0-07` the cause is more likely the
Gazebo multicast issue (`PX4/PX4-Autopilot#24595`) or the launch method than raw CPU.

### F5 — Minor drift in `docs/bench.md`

bench.md says internal NVMe has "~279 GB free"; observed **312 GB**. External drive
confirmed at 5.5 TB free (`/var/mnt/11d5da46-aef5-4b40-a085-40c23f52cc30`). Noise, not a
correction — recorded in `versions.lock` with both numbers rather than silently editing
bench.md. Driver version and GPU roles in bench.md all matched observation.

---

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | `versions.lock` uses an explicit **status vocabulary** — `observed` / `pinned` / `TODO-verify` / `CONFLICT` | A lockfile that cannot distinguish "measured today" from "copied from a doc" is worse than none. Phase 0's whole job is turning the second into the first. |
| D2 | An entry is **not locked** until it has a version **and** a SHA **and** a passing smoke test | Tags move; SHAs do not. Prevents a nominal lock that is not reproducible. |
| D3 | `.repos` is **phase-gated** — only Lane A active, Lane B/C commented out | `vcs import` would otherwise pull tens of GB of Isaac/UE5 trees before those lanes are being built. |
| D4 | Task IDs are `P0-01`, never `#1` | A bare `#N` auto-links to an unrelated same-repo issue on GitHub (`.ai/AGENTS.md:153`). `P0-01` structurally cannot mis-link. |
| D5 | Phase 0 dependency graph puts `P0-09` (Isaac/driver) on an isolated branch | So a Lane B failure cannot stall Lane A. `P0-13` (vLLM) is likewise independent and can run in parallel. |
| D6 | Added `.gitignore` and `.repos`, which were not explicitly requested | Both are in the plan's monorepo layout (`02_development_plan.md:141`), and scaffolding `vendor/` without ignore rules invites committing a multi-GB PX4 tree on the first `git add`. |
| D7 | Worklog HTML **drops the Google Fonts `<link>`** used by `docs/reference/*.html` | Worklog renders must be strictly self-contained — "no external `.css`, JS, fonts, images" (`.ai/AGENTS.md:400`). Kept the identical CSS variables, layout, and three-theme toggle; substituted local font stacks. This is a deliberate divergence from the reference docs, not drift. |
| D8 | Area docs for Phases 1–4 **not** created | An empty backlog doc is noise. The master index names them and marks them "not yet created". |
| D9 | `.gitignore` vendor rules keep component directories **traversable** before excluding their contents | See F6 — the obvious form of the rule silently loses the vendoring notes. |

### F6 — the obvious `vendor/` ignore rule silently loses `LOCAL_PATCHES.md`

Caught by testing the rule instead of trusting it. The natural first draft was:

```gitignore
vendor/*
!vendor/README.md
!vendor/*/LOCAL_PATCHES.md
```

**This does not work.** Git cannot re-include a file whose parent *directory* is
excluded, and `vendor/*` excludes `vendor/PX4-Autopilot-v1.16/` as a directory — so the
negation never gets evaluated. Proven with a simulated tree:

```
$ git status --porcelain --ignored=matching vendor/
?? vendor/
!! vendor/PX4-Autopilot-v1.16/          ← whole tree ignored, notes included
```

That matters because `.ai/AGENTS.md:313` requires every deviation from upstream to be
recorded in `vendor/<component>/LOCAL_PATCHES.md` **so the divergence is auditable** —
which means committed. The rule as first written would have made the audit trail
uncommittable, and nobody would have noticed until the first vendor patch.

Corrected form keeps the component directories traversable, then excludes their contents:

```gitignore
vendor/*
!vendor/README.md
!vendor/*/
vendor/*/*
!vendor/*/LOCAL_PATCHES.md
```

Re-tested with the same simulated tree:

```
?? vendor/PX4-Autopilot-v1.16/LOCAL_PATCHES.md    ← trackable
?? vendor/README.md                               ← trackable
!! vendor/PX4-Autopilot-v1.16/Makefile            ← ignored
!! vendor/PX4-Autopilot-v1.16/src/                ← ignored
```

> **SUPERSEDED 2026-07-30.** This fix was itself wrong. `vendor/*` holds **nested git
> clones**, so `!vendor/*/` makes git record each component as a **gitlink** ("warning:
> adding embedded git repository") — broken submodule refs with no `.gitmodules`. And a
> file inside a nested clone is owned by *that* clone, so `LOCAL_PATCHES.md` could never be
> committed here anyway. The ignore rule is now simply `vendor/*` + `!vendor/README.md`,
> and vendoring notes live in **`docs/vendor/<component>.md`**. Caught by
> `git add -A --dry-run` before the first commit.

---

## What was NOT done (and why)

- **Nothing was installed.** The task was explicitly planning and scaffolding. No apt,
  no clone, no build.
- **No SHAs resolved.** Every `sha:` in `versions.lock` is `TODO-verify` because
  resolving one honestly requires an actual checkout. Reading a SHA off a webpage and
  writing it into a lockfile would look like verification without being it.
- **Nothing committed or pushed.** Wall clock at scaffold time: **Tue 2026-07-28
  13:54 PDT** — inside the Mon–Fri 08:00–18:00 Pacific no-commit window. Everything is
  left in the working tree. No back-dating (explicitly forbidden).
- **No smoke tests run**, so no Phase 0 exit criterion is met. The scaffold is a plan,
  not evidence.

---

## Next steps

Lane A first, in dependency order: `P0-01` (GPU/Docker check) → `P0-02` (ROS 2 Jazzy) →
`P0-03` (PX4 v1.16.0 + Harmonic) → `P0-04` (enumerate real model targets) → `P0-05`
(XRCE agent) → `P0-06` (`px4_msgs release/1.16`) → `P0-07` (5-min headless SITL, no
TIMEOUT, RTF recorded) → `P0-08` (QGC).

`P0-13` (vLLM on GPU 1) can run in parallel — it shares no dependency with PX4.

`P0-09` (does Isaac launch on driver 610?) is the one to answer before *any* Lane B
install, and its answer may re-open the Lane B architecture.

**Awaiting owner approval of the Lane A install sequence before proceeding.**

---

## Evidence

Commands whose output backs the findings above, all run 2026-07-28 in the `drone-sim`
container:

```bash
date -u; TZ=America/Los_Angeles date
. /etc/os-release; echo "$PRETTY_NAME"; uname -r
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
nproc; free -g
python3 --version
ls /opt/ros; which gz ros2 MicroXRCEAgent colcon
df -h / /var/mnt/*
```

Results: Ubuntu 24.04.4 LTS · kernel 7.1.3-ogc5.1.fc44.x86_64 · driver 610.43.03 ·
GPU0 RTX 3080 10240 MiB · GPU1 RTX 5060 Ti 16311 MiB · 24 cores · 62 GB RAM ·
Python 3.12.3 · no ROS/PX4/gz/colcon · `/` 312 G avail · external 5.5 T avail.
