# CLAUDE.md

Project guidance for AI coding agents lives in [AGENTS.md](AGENTS.md) — read it.
Before touching anything, also read [`docs/bench.md`](../docs/bench.md) (the machine
and container you are working in) and the design docs in
[`docs/reference/`](../docs/reference/).

`drone-sim` is a **triple-lane drone simulation framework** — PX4 · ROS 2 Jazzy ·
Isaac Sim · Gazebo · Unreal/AirSim — built toward **VLM-based sim-to-real drone
navigation** (reproducing the SPF / Fly0 / OnFly line of work). It runs on the
`carbonite` workstation inside the `drone-sim` container (2 GPUs). It is an
**integration** project: most changes are judged by a simulator or hardware run, not
by a clean build.

Most important rules:
- **Never command the real aircraft without asking first.** Anything that puts the
  *real* Pixhawk 6C / X500 in motion — arming, a HITL run with motors live, a real
  offboard/trajectory stream, a real flight test — needs the operator's explicit
  go-ahead **for that specific run**, every time; approval never carries over.
  **SITL/pure-sim is exempt and safe** — but say which you're doing, and never let a
  "sim" command reach real hardware (the sim↔real boundary is the transport swap).
  Observing (telemetry, rosbags, QGC, logs) needs no permission. See
  [AGENTS.md → Never command the real aircraft](AGENTS.md#never-command-the-real-aircraft-without-asking-first).
- **GOAL — the whole setup must be easily reproducible as Docker.** *(added 2026-07-29)*
  A fresh machine must reach a working stack from this repo alone — no undocumented
  manual steps. Pin the versions you actually built and smoke-tested, and record
  deviations from the reference docs; a Dockerfile written from the docs rather than from
  evidence reproduces a *broken* stack. Backlog: `docs/docker/todo.md`.
- **PRIMARY GOAL — reuse and integrate upstream, don't reinvent.** The stack is
  assembled from proven upstream components — PX4, Pegasus, Isaac ROS (cuVSLAM,
  nvblox), EGO-Planner, Cosys-AirSim, vLLM — with the *glue* (ROS 2 graph, launch,
  scenario/eval harness, VLM client) as the original work. **Don't reimplement what a
  component already provides**; pin it, wrap it, and follow the phased plan. See
  [AGENTS.md → About this project](AGENTS.md) and
  [→ Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Version coupling is the architecture, not a detail.** Two PX4 trees (v1.16.x for
  Lane A + real; v1.14.3 for Pegasus/Lane B), Isaac's Python 3.11 vs ROS 2 Jazzy's
  3.12, `px4_msgs` branch-matched to firmware. Lock every version in `versions.lock`
  before writing code. See [AGENTS.md → Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Every feature starts as a documented TODO** in its area backlog (indexed from
  `docs/drone-sim-todo.md`) before you build it. See
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
- **Verify every change in a simulator or with a test** — a clean build is never
  enough for flight/perception behaviour: run it headless in SITL, on a seeded
  scenario, or against a unit test, and record the evidence (MCAP bag, metric,
  measured latency). If you can't verify, document why. See
  [AGENTS.md → Verifying changes](AGENTS.md#verifying-changes).
- **A substantial port/adaptation ships a code-map doc** — a function-level,
  side-by-side new-code ↔ upstream mapping (`file:line` ↔ `file:line`) + a
  deliberate-divergences section, with every cited line grepped in both trees, never
  from memory (e.g. the EGO-Planner ROS 2 port). See
  [AGENTS.md → Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Least-destructive vendor edits** — keep vendored trees (PX4, planner,
  Cosys-AirSim) byte-identical to upstream and push integration into the build/launch
  layer; record every deviation in the component's vendoring notes. See
  [AGENTS.md → Adapting upstream code & version pinning](AGENTS.md#adapting-upstream-code--version-pinning).
- **Keep a worklog and update it AS YOU GO** — for any non-trivial, multi-step
  investigation/implementation, maintain `docs/worklog/YYYY-MM-DD-<slug>.md` and append
  each finding/measurement/decision/dead-end/next-step at the time it happens. See
  [AGENTS.md → Worklogs](AGENTS.md#worklogs--write-and-update-as-you-go).
- **Every worklog gets an HTML render** — self-contained, hand-authored (no
  Markdown→HTML converter), with a content-driven diagram; update the index. This
  matches the house style of the docs in `docs/reference/`. See
  [AGENTS.md → Worklog HTML renders](AGENTS.md#worklog-html-renders).
- **Cite sources** when finding, researching, or comparing. See
  [AGENTS.md → Research & citations](AGENTS.md#research--citations).
- **Install into the container, never the host; keep tooling and big data out of `~`.**
  The host is immutable; large datasets/rosbags/assets go on the 7 TB external drive,
  project tooling stays in the repo (`vendor/`), scratch goes to `/tmp`. **On any other
  drive, write only under `<drive-root>/Developments/projects/drone-sim/`** — mirror the
  project path from that drive's root; never create a top-level directory on a drive you
  don't own. **Ask the
  operator first — every time — before any command that escapes the container**
  (`distrobox-host-exec`, `flatpak-spawn --host`, `chroot`/`nsenter` into `/run/host`,
  host-side `podman`/`distrobox`); approval is per command and never carries over. See
  [AGENTS.md → Simulation & hardware notes](AGENTS.md#simulation--hardware-notes).
