# CLAUDE.md

Project guidance for AI coding agents lives in [AGENTS.md](AGENTS.md) — read it.
Before touching anything, also read [`../docs/bench.md`](../docs/bench.md) (the machine
and container you are working in), [`../docs/quickstart.md`](../docs/quickstart.md) (how
the simulator is launched, flown and read) and
[`../docs/conventions.md`](../docs/conventions.md) (the frozen ROS 2 graph spec).

`drone-sim` is a **photoreal drone simulator you can fly your own world in** — **Unreal
Engine 5.8 · Cosys-AirSim · PX4 v1.16.0 SITL · ROS 2 Jazzy**, brought up by
`./scripts/sim_up.sh`. Bring your own `.uproject`, place the vehicle, choose your sensors,
and fly it over ROS 2 — the same graph you would fly on real hardware. It runs on the
`carbonite` workstation inside the `drone-sim` container (2 GPUs). It is an **integration**
project: most changes are judged by a simulator run, not by a clean build. Applications
built *on* it — vision-based navigation, VLM agents, planners, benchmark reproduction — are
examples of what it is for, not the repo's purpose. The Gazebo baseline and the Isaac Sim
stack are retired; their docs are in `docs/history/`.

Most important rules:
- **Never command the real aircraft without asking first.** Anything that puts the
  *real* Pixhawk 6C / X500 in motion — arming, a HITL run with motors live, a real
  offboard/trajectory stream, a real flight test — needs the operator's explicit
  go-ahead **for that specific run**, every time; approval never carries over.
  **SITL/pure-sim is exempt and safe** — but say which you're doing, and never let a
  "sim" command reach real hardware (the sim↔real boundary is the transport swap).
  Observing (telemetry, rosbags, QGC, logs) needs no permission. See
  [AGENTS.md → Never command the real aircraft](AGENTS.md#never-command-the-real-aircraft-without-asking-first).
- **Know the traps before you debug something that "should work"** — the control interface
  is ROS 2 only; lockstep is dead code, so no timing is deterministic; a stale PX4 EKF
  origin looks exactly like a control bug; frames are NWU, not ENU; a stale plugin copy
  under `Plugins/` silently wins, so verify the artifact that *ran*. See
  [AGENTS.md → The simulator, concretely](AGENTS.md#the-simulator-concretely--the-facts-that-cost-days-to-learn).
- **GOAL — the whole setup must be easily reproducible as Docker.** *(added 2026-07-29)*
  A fresh machine must reach a working stack from this repo alone — no undocumented
  manual steps. Pin the versions you actually built and smoke-tested, and record
  deviations from the reference docs; a Dockerfile written from the docs rather than from
  evidence reproduces a *broken* stack. One documented exception: the Unreal engine base
  image is credential-gated (EpicGames org membership + a PAT with `read:packages`) — say
  so plainly, don't hide it. Backlog: `docs/docker/todo.md`.
- **PRIMARY GOAL — reuse and integrate upstream, don't reinvent.** The stack is
  assembled from proven upstream components — PX4, the Micro-XRCE-DDS Agent, `px4_msgs`,
  Cosys-AirSim, QGroundControl — with the *glue* (ROS 2 graph, launch, bring-up and repair
  logic, scenario/eval harness, containerisation) as the original work. **Don't reimplement
  what a component already provides**; pin it, wrap it. See
  [AGENTS.md → About this project](AGENTS.md) and
  [→ Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Version coupling is the architecture, not a detail.** **One** PX4 tree — v1.16.0, the
  same tree the real Pixhawk 6C is flashed from — `px4_msgs` branch-matched to it
  (`release/1.16`), ROS 2 Jazzy everywhere, and Cosys-AirSim pinned by **SHA** (`5.8-v3.4.1`)
  against UE5.8 with the engine image pinned by digest. The second PX4 tree and the Isaac
  Python-3.11 split are gone with Isaac. Lock every version in `versions.lock` before
  writing code. See [AGENTS.md → Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Every feature starts as a documented TODO** in the backlog `docs/todo.md`
  (`SIM-NN` IDs) before you build it, and is marked done when it lands. See
  [AGENTS.md → Plan first](AGENTS.md#plan-first--every-feature-starts-as-a-documented-todo).
- **Do not commit or push automatically** — only when explicitly asked, and
  **never during weekday work hours (Mon–Fri 8 AM–6 PM Pacific Time /
  `America/Los_Angeles`; the machine clock is UTC — convert first); no back-dating to
  dodge it.** See [AGENTS.md → Committing](AGENTS.md#committing).
- **No AI attribution anywhere** — not in code comments, docs, commit messages, or
  GitHub PR/issue text. Everything reads as the human owner's work; commits use the
  repo's git identity only. See
  [AGENTS.md → Attribution](AGENTS.md#attribution--no-ai-self-reference-anywhere).
- **Code/feature changes → branch + PR; doc-only changes → may push to `main`.**
  See [AGENTS.md → Branching & pull requests](AGENTS.md#branching--pull-requests).
- **Run a review before any merge — the built-in `/review` is sufficient** and the
  agent runs it itself (not billed, not owner-only); the merge gate is met once it's
  run and its findings addressed. `/code-review ultra` is an optional deeper, billed
  cloud review the agent can't launch — ask the owner for it on larger/riskier
  changes. Then **merge with rebase + merge by default** (`gh pr merge --rebase`);
  keep `main` linear. See
  [AGENTS.md → Merging pull requests](AGENTS.md#merging-pull-requests).
- **No mis-linking `#N` in PR/commit text** — a bare `#N` auto-links to a
  *same-repo* issue/PR, so cross-repo refs must be qualified as `owner/repo#N` and
  internal IDs (task/backlog/bug numbers) backticked in Markdown (`` `#20` ``; in
  commit messages drop the `#`). Scan before pushing. See
  [AGENTS.md → Cross-references in PR and commit text](AGENTS.md#cross-references-in-pr-and-commit-text).
- **Verify by running it, end to end** — a clean build is never enough for anything that
  flies or perceives: bring the stack up with `./scripts/sim_up.sh`, exercise the **full**
  ROS 2 graph on a seeded scenario, and record the evidence (MCAP bag, metric, measured
  rate/latency). A seed currently sets the **spawn pose only**, so never call a gate run
  "varied conditions"; `run_gate.py`'s **VOID ≠ FAIL** scoring is load-bearing. If you
  can't verify, document why. See
  [AGENTS.md → Verifying changes](AGENTS.md#verifying-changes).
- **A substantial port/adaptation ships a code-map doc** — a function-level,
  side-by-side new-code ↔ upstream mapping (`file:line` ↔ `file:line`) + a
  deliberate-divergences section, with every cited line grepped in both trees, never
  from memory. See
  [AGENTS.md → Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Least-destructive vendor edits** — keep vendored trees (PX4, Cosys-AirSim)
  byte-identical to upstream, carry deviations as patch files in `patches/<component>/`
  applied to a container-local copy at build time, and record every one in
  `docs/vendor/<component>.md`. See
  [AGENTS.md → Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Keep a worklog and update it AS YOU GO** — for any non-trivial, multi-step
  investigation/implementation, maintain `docs/worklog/YYYY-MM-DD-<slug>.md` and append
  each finding/measurement/decision/dead-end/next-step at the time it happens.
  **Worklogs already written are frozen** — never edit, rename or move one. See
  [AGENTS.md → Worklogs](AGENTS.md#worklogs--write-and-update-as-you-go).
- **Every worklog gets an HTML render** — self-contained, hand-authored (no
  Markdown→HTML converter), with a content-driven diagram; update the index. This
  matches the house style of the existing renders and of `docs/history/reference/`. See
  [AGENTS.md → Worklog HTML renders](AGENTS.md#worklog-html-renders).
- **Cite sources** when finding, researching, or comparing. See
  [AGENTS.md → Research & citations](AGENTS.md#research--citations).
- **Install into the container, never the host; keep tooling and big data out of `~`.**
  The host is immutable; archival data (rosbags, recordings, datasets) goes on the 7 TB
  external drive, project tooling stays in the repo (`vendor/`), scratch goes to `/tmp` —
  but the **simulator's live working set stays on the internal NVMe** (the 7 TB volume is
  a spinning disk, and UE5 is latency-sensitive random I/O). **On any other drive, write
  only under `<drive-root>/Developments/projects/drone-sim/`** — mirror the project path
  from that drive's root; never create a top-level directory on a drive you don't own.
  **Ask the operator first — every time — before any command that escapes the container**
  (`distrobox-host-exec`, `flatpak-spawn --host`, `chroot`/`nsenter` into `/run/host`,
  host-side `podman`/`distrobox`); approval is per command and never carries over. See
  [AGENTS.md → Simulation & hardware notes](AGENTS.md#simulation--hardware-notes).
