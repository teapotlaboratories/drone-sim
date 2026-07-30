# AGENTS.md

Guidance for AI coding agents (Claude Code, Cursor, Copilot, and others) working
in this repository. Follow these conventions in addition to anything a human
maintainer asks for.

**About this project.** `drone-sim` is a **triple-lane drone simulation framework**
built toward **VLM-based sim-to-real drone navigation**. Three simulation lanes feed
one shared ROS 2 graph:

- **Lane A — PX4 v1.16 + Gazebo Harmonic + ROS 2 Jazzy** — the fast, headless,
  lockstep, CI-friendly SITL baseline (GPS nav, controls, offboard).
- **Lane B — Isaac Sim 5.1 + Pegasus Simulator** — photorealistic RTX perception
  (camera/stereo/depth/Lidar), domain randomization, VLM-in-the-loop, RL.
- **Lane C — Unreal Engine 5.5 + Cosys-AirSim** — benchmark-reproduction lane for
  AerialVLN/OpenFly-style evaluation and Cesium georeferenced terrain.

The target is to reproduce and extend the **SPF / Fly0 / OnFly** line of VLM drone
navigation (slow VLM target-generator + fast geometric planner), first in sim, then
onboard a **Jetson Orin NX** on a **Pixhawk 6C / X500** airframe. It is an
**integration** project: most changes end up exercised by a simulator or on real
hardware, so "did it build" is never the whole story — see
[Verifying changes](#verifying-changes). The design is fully specified in
`docs/reference/` (sim-stack report, development plan, hardware assessment) and the
environment in `docs/bench.md`; **read those before making architecture decisions.**

**GOAL — the whole setup must be easily reproducible as Docker.** *(added 2026-07-29)* A
fresh machine must reach a working stack from this repo alone: no undocumented manual
steps, no "it works on `carbonite`". When you install or configure anything, assume the
next step is capturing it as a Dockerfile/compose service — pin the exact versions you
actually built and smoke-tested, and record deviations from upstream docs, because a
Dockerfile written from the reference docs rather than from evidence reproduces a *broken*
stack. Backlog: `docs/docker/todo.md`.

**PRIMARY GOAL — reuse and integrate upstream, don't reinvent.** The value of this
project is the *integration* — a single ROS 2 graph, launch composition, scenario/eval
harness, and VLM client that work identically across sim and real — **not**
re-implementations of the components it orchestrates. PX4, the Micro-XRCE-DDS Agent,
`px4_msgs`, Pegasus, Isaac ROS (cuVSLAM, nvblox), EGO-Planner, Cosys-AirSim, and vLLM
are consumed as pinned upstreams. **Do not hand-roll flight control, a DDS bridge, a
SLAM/mapping stack, a planner, or a serving engine that an upstream already provides —
take it from upstream, pin it, and wrap it.** The original work is the glue and the
experiment harness. This overrides any impulse to write a subsystem from scratch. See
also the version-coupling and porting rules in
[Adapting upstream code & version pinning](#adapting-upstream-code--version-pinning).

## Never command the real aircraft without asking first

This project's Phase 4 puts commands on a **real drone** — a Pixhawk 6C / X500
airframe, first in **HITL** (real PX4 firmware in the loop) and then in **real
flight** with the Jetson Orin NX. Anything that can put the *real* aircraft in
motion — arming the real Pixhawk, a HITL run with motors live, a real offboard /
trajectory / velocity setpoint stream to hardware, or a real flight test — needs the
operator's explicit go-ahead **for that specific run**, every time.

Approval never carries over. Not to a retry after a run that failed or timed out, not
to the next scenario/seed in a list, and not because the operator approved a plan that
mentioned flying, agreed to a sequence, or said "do all". Each real run is its own
question, asked immediately before it.

When asking, say plainly what the aircraft will do: the mission/profile and its
parameters, how long it lasts, how high it goes, the geofence, and what stops it
(failsafe, kill switch, RC override). Then wait for an answer.

**SITL and pure-sim runs are exempt — they are the whole point of Lanes A/B/C, and
safe to run.** But say which you are doing, and **never let a "sim" command reach real
hardware**: the sim↔real boundary is the **transport swap** (SITL MAVLink/uXRCE-DDS vs
a link to the real Pixhawk, `use_sim_time`, HITL enabled in QGC). Be certain which side
you are on before you stream setpoints or arm — a misdirected offboard stream or an
accidental HITL-enabled arm is a real-motion event, not a sim one.

Observing needs no permission — telemetry, rosbags, QGC, logs, `nvidia-smi`,
screenshots. Streaming setpoints to a **disarmed** aircraft is observation too, but say
you are doing it.

Assume nothing about the aircraft's state. Powered on a minute ago does not mean
powered on now, and an arm wait that times out is not a decline to be retried — it
usually means nobody was standing there.

## Plan first — every feature starts as a documented TODO

**Before implementing a feature or any non-trivial change, write it down as a TODO
first** — what it is, why, and how it will be verified — *then* build it. Don't start
undocumented feature work.

- **Put the item in the authoritative backlog for its area** — the per-area TODO/
  milestones doc under `docs/` (e.g. `docs/lane-a/`, `docs/perception/`,
  `docs/planning/`, `docs/vlm/`, `docs/eval/`) — and make sure it's reachable from the
  master index `docs/drone-sim-todo.md` (which only points; the detail lives in the
  area doc).
- **State it clearly:** the change, the reason, and the acceptance/verification (which
  simulator run, seeded scenario, metric threshold, or unit test will prove it — see
  [Verifying changes](#verifying-changes)).
- **Keep the status current:** mark it in progress when you start and done when it lands,
  and reflect it in the relevant milestones and the phase roadmap (Phase 0–4).
- **Trivial/mechanical changes don't need one** (typo/doc fixes, a rename) — this is for
  features and substantive work.

## Committing

**Do not commit or push automatically.** Make changes in the working tree and
stop there so the owner can review them. Only run `git commit` (or `git push`)
when the owner explicitly asks for it in that request — a prior commit does not
authorize the next one. When work is done, summarize what changed and leave it
staged or unstaged for review rather than committing on your own initiative.

**No commits or pushes during weekday work hours (Mon–Fri, 08:00–17:59 Pacific Time
— `America/Los_Angeles`, i.e. PST/PDT; the machine clock is UTC, so convert before
acting).** Even when the owner asks, hold both `git commit` and `git push` until after
18:00 Pacific (or the weekend) so the history carries no work-hours timestamps. The
commit date must reflect when the work actually happened — **never back-date, `git
commit --date=…`, or `--amend` a commit's timestamp** to disguise a work-hours commit
as off-hours; that falsifies the record. Do the work in the tree, tell the owner the
commit is held, and land it after the window (or when they explicitly override for a
specific commit).

## Branching & pull requests

Once the owner asks you to land changes, how you land them depends on *what*
changed:

- **Feature work / code changes → branch and open a PR.** Anything touching the ROS 2
  packages, launch/bringup, planner or perception glue, the VLM client, sim bringup,
  Docker/compose, or `versions.lock` — **especially large changes** — goes on a
  feature branch with a pull request, never a direct commit to the default branch.
  This keeps `main` reviewable and CI-gated.
- **Documentation-only changes → direct to `main` is fine.** Edits confined to
  docs, worklogs, READMEs, and `.ai/` guidance may be committed and pushed
  straight to `main` without a branch or PR.

When unsure whether a change counts as "doc-only," treat it as code and branch.

### Merging pull requests

**Run a code review before every merge — the built-in `/review` is sufficient.** Run
`/review <PR#>` (or `/review` on the local branch) at least once on the branch/PR being
merged and resolve what it surfaces before merging. It is the lightweight, in-session
review: **not** billed and **not** owner-only, so the agent runs it itself — no need to
wait on the owner. **The merge gate is satisfied once `/review` has run and its findings
are addressed**; the agent must not merge before then.

**`/code-review ultra` is an optional deeper pass, not required.** `/code-review ultra
<PR#>` (a GitHub PR) or `/code-review ultra` (the local branch) is the billed cloud
review — **user-triggered, so the agent cannot launch it**. Worth asking the owner for
on larger or riskier changes, but a merge does not wait on it.

**Default merge strategy: rebase + merge** (`gh pr merge --rebase`). Replay the
branch's commits onto the base so `main` stays linear — no merge bubbles. Prefer
this over a merge commit or squash unless there is a concrete reason not to.

- **Squash + merge** only when the branch is noisy work-in-progress that is
  clearer collapsed to a single commit.
- **Merge commit** only when the branch's individual history matters as-is, or you
  must preserve an *exact* commit SHA on the base.
- **Submodules / vendored trees.** This project pins third-party code (PX4 ×2,
  `px4_msgs`, planner, Cosys-AirSim, Pegasus) — prefer a `vcstool` `.repos` manifest
  over git submodules. If a submodule *is* used, remember a rebase replays commits as
  *new* SHAs: merge the submodule PR first, then update the superproject gitlink to the
  **post-rebase SHA now on the submodule's `main`** before merging the superproject PR.

### Cross-references in PR and commit text

On GitHub a bare `#N` in a PR description, issue, review comment, **or commit message**
auto-links to issue/PR **#N in the same repo** (the `drone-sim` repo). This project also
reuses `#N` as *internal* identifiers in its docs (task numbers, backlog items, bug
IDs). Pasted verbatim, an internal `#N` silently links to an unrelated PR/issue.
Classify every `#N` before you land PR/issue text or a commit message:

- **A real PR/issue in *this* repo** → leave the bare `#N` (the link is correct).
- **A PR/issue in *another* repo** → fully qualify it as `owner/repo#N` (e.g.
  `PX4/PX4-Autopilot#25089`). A bare `repo#N` **without the owner** does not link at
  all — always include the owner. (Upstream trackers you'll cite often: `PX4/PX4-Autopilot`,
  `PX4/px4_msgs`, `isaac-sim/IsaacSim`, `isaac-sim/IsaacLab`, `vllm-project/vllm`.)
- **An internal identifier that is not a GitHub issue** (task / backlog / bug
  number, …) → **kill the auto-link.** In Markdown (PR and issue bodies, review
  comments) wrap the token in backticks — `` `#20` ``. In **commit messages**
  backticks do *not* help (they aren't Markdown-rendered), so drop the `#` or
  reword: write `bug 20`, not `#20`.

**Check before pushing.** Scan the text for any bare `#N` and confirm each is a
genuine same-repo reference; qualify or backtick the rest. A "bare `#`" here is one
not preceded by a backtick, a `/`, or a word character:

    (?<![`/A-Za-z0-9])#[0-9]+

The same rule applies when editing an existing PR or commit — don't reintroduce a
mis-link while fixing something else.

## Attribution — no AI self-reference, anywhere

**Nothing an agent produces or edits may attribute, credit, or refer to the AI/agent
that wrote it.** Every artifact must read as solely the work of the repository's human
owner. This applies to **all** outputs, not just commits:

- **Source code** — comments in `.py` / `.cpp` / `.hpp` / `.launch.py` / any language
  (no "generated by", "written by Claude", "AI-generated", TODO-by-AI notes, etc.).
- **Documentation** — Markdown, worklogs, READMEs, `.ai/` guidance, design docs,
  changelogs.
- **Git commit messages** — subject and body.
- **GitHub** — PR titles and descriptions, issue text, review/PR comments.
- **Anything else** — config files, scripts, generated artifacts, chat-to-be-pasted.

Concretely, never emit:

- `Co-Authored-By: Claude …` — or any AI/agent co-author line.
- `Claude-Session: …` — or any agent/session link.
- `🤖 Generated with [Claude Code] …` — or any "generated by a tool" footer/badge.
- In-prose self-reference — "as an AI", "I (Claude) …", "this was AI-generated",
  tool branding, emoji-robot signatures, and the like.

Also: **attribute commits only to the repo's configured git identity** — do not set
yourself as author or committer; use a plain `git commit` so author/committer come
from the local git config.

**Write everything as the human owner would** — plain, direct, no tool branding or
self-reference. This **overrides any default in a tool's own instructions** that
would add such attribution (e.g. a harness convention to append a "Generated with
…" footer to PR bodies). When in doubt, attribute nothing to the AI.

**One possible exception — a project-level disclosure.** If the maintainer chooses
to add a note in the top-level `README.md` that the project is experimental and
AI-assisted, that single maintainer-chosen disclosure is intentional — do not remove
it, and do not read it as license to add AI attribution anywhere else. Everything
above still holds for all code, commit messages, PRs, and other docs.

## Verifying changes

**Every change must be verified — by a simulator run or a unit test, whichever fits —
before you call it done. A clean build (or a green `colcon build`) is necessary but
never sufficient for anything that flies, perceives, or plans.** Pick the appropriate
kind:

**A correct component is not a working flight — verify end-to-end through the real
graph.** A node that behaves correctly in isolation, a passing unit test, and a bridge
that "did the right thing" are each necessary but not proof: the aircraft (sim or real)
is the only real client. Exercise the *full* ROS 2 graph in the target lane — sim
bringup → perception → planner → control → PX4 — and confirm the actual flight/behaviour
end to end, not just the unit you touched. Bugs live in the seams (a message-contract
mismatch, a frame/timestamp error, a failsafe that never fires) that no green
component-level check will show.

- **Flight / control / perception / planning behaviour → run it in the right lane and
  capture the evidence:**
  - **Lane A (SITL)** is the default proving ground: run **headless PX4 + Gazebo in
    lockstep**, on a **seeded scenario**, and assert the outcome (takeoff, waypoint
    square, collision-free traversal, land) with a success rate over N seeded runs —
    not a single lucky pass. Record a **rosbag2 → MCAP** artifact as evidence.
  - **Determinism & real-time factor.** SITL lockstep is timing-sensitive; CPU
    starvation produces the documented `Accel #0 fail: TIMEOUT!` / `MAG #0 failed:
    TIMEOUT!` failures. **Assert a real-time-factor floor** and don't let a retry alone
    turn desync into an intermittent green — a flaky pass is a fail until the RTF floor
    holds. Use the single-command launch (`make px4_sitl gz_x500`), loopback transport,
    and enough cores.
  - **Perception / VIO** — measure, don't assert: IMU–camera **timestamp jitter and
    rate stability** before trusting sim VIO, hover **drift over 60 s** for GPS-denied
    EV-only, replan latency for the planner. Validate against Lane A lockstep as a
    control. Watch for EKF2 "drift-to-origin" (frame/param misconfig).
  - **VLM navigation** — report **SR / SPL / NE / OSR / collision-rate** on a seeded
    episode set, with the **success threshold recorded** (5 m vs 20 m are both valid
    depending on the benchmark — parameterise it). Timestamp image-in → target-out and
    report **p50/p95 decision latency**; the onboard budget is **≤1 s**.
  - **Metrics are ground truth; a returned tool call or a printed log line is
    supporting evidence, not a substitute.** Save the MCAP bag / metric table / latency
    numbers as the evidence in the worklog/PR.
- **Host-side logic, message contracts, parsers, pure functions, build-time
  invariants → a unit test** (or a host build that exercises the logic): the
  target-generator/tracker message contract and `ttl` watchdog, depth back-projection
  math, EKF2 param sets, scenario/eval parsing, metric computation. Run these
  off-target where they're fast and deterministic; gate GPU-only paths (nvblox,
  cuVSLAM, Isaac) on a self-hosted GPU runner with a CPU fallback (e.g. OctoMap) for
  CI.
- **HITL / real flight is a gate, not a step.** Before any real flight, PX4 **HITL on
  the Pixhawk 6C must pass the identical SITL suite** — no exceptions. HITL is
  community-supported in PX4; budget integration time.

**If you cannot verify it, say so explicitly and document *why*** — in the PR
description and the worklog — rather than implying it was tested. Name the concrete
blocker (e.g. "no GPU runner free to exercise the nvblox path", "Lane C UE5 build
pinned but not yet buildable, benchmark parity deferred"). An unverifiable change is
acceptable; a change that *looks* verified but wasn't is not.

**Leave the graph in a known-good state when a run ends.** After an experiment, restore
a known-good launch/scenario config and pinned versions rather than leaving a
half-broken setup, so the next session starts from a clean baseline. Note in the
worklog what's currently pinned and what's running.

## Adapting upstream code & version pinning

This stack is **assembled from pinned upstreams**, and the **dominant project risk is
version coupling**, not novel code. Treat both the pins and any port as reviewable
deliverables.

**Version-lock is the architecture.** Resolve these in Phase 0 and record every SHA/tag
in `versions.lock`; CI must assert the couplings hold:

- **Two PX4 trees.** Lane A and real hardware use **PX4 v1.16.x + uXRCE-DDS**; Pegasus
  (Lane B) is pinned to **PX4 v1.14.3** over the MAVLink SITL API. This is designed
  around, not worked around.
- **`px4_msgs` MUST be branch-matched to the firmware** (`release/1.16`) — a mismatch
  silently breaks topics; CI asserts topics populate.
- **Isaac Sim 5.1 ships Python 3.11; ROS 2 Jazzy is 3.12** → `rclpy` cannot be shared.
  Use NVIDIA's Python-3.11 ROS workspace and meet the app nodes over DDS.
- **Pegasus ↔ Isaac** is explicitly not backward-compatible (v5.1.0 ↔ Isaac 5.1.0).
- **Cosys-AirSim ↔ UE5** and **Cesium FSD ↔ PhysX** (mutually exclusive) — pin exact
  versions; Cesium is render/data-gen only.
- **NVIDIA driver.** Blackwell (RTX 5060 Ti) needs a recent branch but Isaac Sim breaks
  on too-new drivers — target the newest R580 that still launches Isaac Sim, verify
  Isaac launches *before* installing the rest, then pin and hold. See `docs/bench.md`
  and `docs/reference/03_hardware_assessment.md`.

**Least-destructive vendor edits.** When adapting a vendored/upstream tree (PX4, the
planner, Cosys-AirSim, a driver), change as little as possible — keep the source
**byte-identical to upstream** wherever you can and push integration into the *build*,
*launch*, or *config* layer, not the files:

- **Exclude at the build layer** — leave a unit out of the compiled/launched set rather
  than deleting it.
- **Guard** target-specific behaviour behind a build flag / launch arg rather than
  ripping code out.
- Keep upstream's own files (README, license, tests, build scripts) in place unless
  they actively break the build.
- Every deviation — and every source edit — is recorded in the component's vendoring
  notes at **`docs/vendor/<component>.md`**, so upstream rebases stay clean and the
  divergence is auditable. **Not** inside the vendored tree: `vendor/*` holds nested git
  clones, so a file placed there is owned by that clone and can never be committed to this
  repo (and un-ignoring the directory makes git record a broken gitlink). Prefer a **`.repos` (vcstool) manifest** over submodules
  for third-party trees.

**A substantial port MUST ship a code-map doc.** The known port in this project is
**EGO-Planner → ROS 2** (from EGO-Swarm, `drone_id=0`), but the rule applies to any
close adaptation of an upstream implementation. Provide a function-level, side-by-side
**new-code ↔ upstream** mapping so a reviewer can check it line by line:

- **Form.** A table, one row per ported function/structure: *new code (`file:line` +
  symbol)* ↔ *equivalent upstream code (`file:line` + symbol)*, grouped by sub-area,
  plus a final **"deliberate divergences"** section listing every intentional
  difference (ROS 1→2 API, message types, threading/executor model, dropped feature)
  **with the reason** — divergences are flagged, not hidden.
- **Where it lives.** A large port gets its own file, e.g.
  `docs/planning/ego-planner-ros2-code-map.md`, linked from the area's status doc.
- **Verify every cited `file:line` — do NOT cite from memory.** Grep both trees (the
  new working tree + the pinned upstream checkout) to confirm each symbol is at the line
  you cite and that it's the *definition*, not a call site. Pin the upstream commit SHA
  at the top and add a "verified <date>" stamp. Lines drift — a code map written from
  memory is reliably wrong.
- Root-cause and follow the upstream implementation; don't paper over a symptom with a
  local hack that silently diverges from the reference.

## Worklogs — write and update as you go

**For any non-trivial, multi-step investigation or implementation, keep a worklog
(`docs/worklog/YYYY-MM-DD-<slug>.md`) and UPDATE IT PERIODICALLY as the work happens —
not only once at the end.** The worklog is a running record, not a final report written
from memory.

- **Append at each meaningful checkpoint** — a confirmed finding, a measurement/number
  (success rate, replan latency, VIO drift, decision p95, RTF), a decision and its
  reason, a dead-end (and why it was abandoned), a refuted hypothesis, a sim/bench
  result, or a next-step. Write it while it's fresh, before moving on.
- **Why:** long agentic runs lose context (summarization, crashes, a new session). A
  worklog updated as you go means the thread survives — a resumed session (or a human)
  can pick up exactly where you were, with the evidence, instead of reconstructing it.
  It also stops the end-of-task write-up from quietly dropping the dead-ends and the
  *why*.
- **Standalone + honest:** each worklog is self-contained — never de-dup its findings
  into "see other doc" pointers — and records what was actually tried/measured,
  including what failed and what is still unverified, not a cleaned-up highlight reel.
- **Keep the companion HTML render current** at meaningful checkpoints (see below).
- Trivial one-shot changes don't need a worklog (same bar as the "Plan first" TODO rule).

## Agent memory — keep it current, and keep it a pointer

Agents with a persistent memory (Claude Code: `~/.claude/projects/<project>/memory/`)
**must keep it current as work happens** — not only at the end, and not only when asked.
A fresh session starts with memory and nothing else; what is not there is re-derived, or
re-broken.

**But memory is a pointer, not a second copy of the repo.** Progress belongs in
[the worklog](#worklogs--write-and-update-as-you-go) and `docs/drone-sim-todo.md`, which
are reviewed, diffed and shared. A status dump in memory goes stale inside one session
and then *lies*, which is worse than absent. So:

- **Record in the repo:** what happened, what was measured, what failed, what is still
  unknown.
- **Record in memory:** *where to look* (start here, read the newest worklog), and facts
  that are **not derivable from the repo** — the GPU work-split and driver pin, the two
  PX4 trees, owner preferences, tooling gotchas, the container's Docker/CDI workarounds.
- **Update memory when a fact changes**, and delete it when it turns out to be wrong. A
  confidently wrong memory is the most expensive artifact in this project.

**The rule that matters most — save the implication, not just the fact.** A memory that
records *"the 5060 Ti is Blackwell sm_120"* is trivia until it also says *"…therefore it
crashes Isaac Sim on too-new drivers, so render on the 3080 and keep the 5060 Ti for
vLLM only."* When writing a memory, state what it means for the work — a fact nobody can
act on is not saved, it is stored.

**End a session so the next one is cheap:** worklog updated as you went (dead ends
included), gate status honest (`unknown` and `void` are valid), pinned versions and the
running config noted, and memory pointing at the newest worklog.

## Worklog HTML renders

**Every worklog (`docs/worklog/*.md`) must have a companion HTML render at
`docs/worklog/html/<same-name>.html`.** When you add a new worklog — or substantially
edit an existing one — author/update its HTML in the same change and add/refresh its
card in `docs/worklog/html/index.html`. The reference docs in `docs/reference/*.html`
are the house style to match.

- **Hand-author it — do NOT run a Markdown→HTML converter.** Read the worklog and write
  the HTML directly. The goal is a thoughtfully laid-out, *visual* page, not a mechanical
  transform. (Scripting is fine for verification or metadata extraction — just not for
  generating the page content.)
- **Self-contained + shared design system.** Each page must render **standalone** — no
  external `.css`, JS, fonts, images, or other files; the CSS, diagram SVGs, and
  theme-toggle JS are all embedded inline. The **first** worklog HTML you author becomes
  the **canonical design source**: give it a clean topbar, a `.content` column, a
  per-page table of contents, and a light/dark theme, then copy that `<style>` block
  verbatim into later pages so they don't drift. To change the design, edit the source
  page's `<style>`, then re-embed it into the others.
- **Visuals + at least one diagram, built ONLY from the doc's real content.** Use
  callouts (ok/warn/bug), stat grids, before/after bars (widths **to scale** from the
  real numbers), and flow/topology diagrams (a small inline `<svg>`). Add a diagram
  wherever the doc has something structural or numeric to show — the ROS 2 data flow, a
  perception→planner→control loop, a lane/GPU topology, a before/after metric. **Never
  fabricate** nodes, edges, or values, and don't force a diagram onto pure prose.
- **Faithful.** The HTML must carry all the worklog's information — findings, numbers,
  `file:line`, caveats — never a summary.

## Research & citations

**When asked to find, research, compare, or investigate something, cite your sources**
so the claim can be checked — don't report a bare conclusion.

- **Code / repo facts** → `file:line` (or commit SHA).
- **Sim / bench findings** → the command run and the relevant output, or the
  measurement and how it was taken (which lane, which seed, how many runs).
- **External facts (docs, papers, forums, issue trackers)** → the URL(s), ideally as a
  "Sources:" list. The reference docs in `docs/reference/` already carry a large,
  verified source list — cite into it rather than re-deriving.
- **Prefer authoritative sources over marketing**, and say which is which (an upstream
  doc, a pinned source `#define`, or an issue-tracker confirmation is stronger evidence
  than a marketing spec) — and flag when something is unverified or unknown rather than
  guessing.

## Simulation & hardware notes

- **This runs in the `drone-sim` container on `carbonite`; the host is immutable.**
  Install software **inside the container**, never on the host (its OS is
  ostree-immutable and host `sudo` needs a password you don't have). See
  `docs/bench.md`.
- **Ask before running anything that escapes the container.** `distrobox-host-exec`,
  `flatpak-spawn --host`, `chroot`/`nsenter` into `/run/host`, host-side
  `podman`/`distrobox`, or any other command that executes on `carbonite` itself rather
  than inside `drone-sim` — **ask the operator first and wait**, saying what the command
  is and why it needs the host. Approval is **per command, every time**; it never carries
  over to the next one. Ordinary in-container work (`sudo`, `apt`, `pip`, `colcon`,
  in-container systemd) needs no permission.
- **VRAM is the binding constraint.** The 3080 is 10 GB — below Isaac Sim's 16 GB
  minimum. It runs, but cap scene complexity, RTX-sensor count, and resolution;
  Qwen3-VL-30B-A3B does not fit locally (serve 2B/4B/8B, or remote). Follow the
  downscale ladder in `docs/reference/03_hardware_assessment.md`.
- **Don't touch the container's load-bearing workarounds** unless they're already
  broken: the Docker `fuse-overlayfs` storage driver and the `/etc/cdi-local` CDI spec
  + `nvidia-cdi-local.service` that make GPU-in-Docker work. If `docker run --gpus all`
  fails on a missing `/usr/lib64/...` driver path, the spec is stale — regenerate per
  `docs/bench.md`.
- **Keep the ROS 2 graph identical across sim and real.** Freeze topic and namespace
  conventions early (`/fmu/*`, `/vlm/target`, `/planner/trajectory`); use
  `use_sim_time:=true` only in sim; swap only the transport. They must reach the
  aircraft unchanged.
- **Refer to hardware by a stable label, not a volatile device path.** The Pixhawk
  6C's `/dev/ttyACM*` / `/dev/ttyUSB*` and the Orin NX's interfaces change across
  replugs; identify each by a documented label/role and record the mapping and the
  Orin↔Pixhawk UART wiring in a `docs/hardware/` doc.
- **Capture the command you actually ran** (launch target, lane, seed, ports) as
  evidence in the worklog so a result can be reproduced.
- **Never use `~/` for tooling, caches, big data, or scratch without approval.** Large
  artifacts — Isaac assets, UE5 projects, rosbags, model weights, datasets — go on the
  **7 TB external drive** (`/var/mnt/…`), **not** the ~279 GB internal NVMe. Project
  tooling stays inside the repo (`vendor/`); throwaway scratch goes to `/tmp`.
  RGB-D at 640×480@30 Hz is ~tens of GB/hour — budget storage before a benchmark sweep.
- **On any other drive, write only under `<drive-root>/Developments/projects/drone-sim/`.**
  Mirror the project path from the root of that drive — e.g.
  `/var/mnt/<uuid>/Developments/projects/drone-sim/`. **Never create a top-level directory
  on a drive you do not own**; these volumes are shared with the host, other containers,
  and unrelated data (the 7 TB drive also holds ~1.1 TB of Steam libraries), so project
  files must stay in one predictable, self-identifying place that is obvious to delete or
  back up.
- **Secrets stay off the tree and off history** — pass Wi-Fi creds, setup keys, and
  tokens on the command line or via env/secret files, never committed.
