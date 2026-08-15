# AGENTS.md

Guidance for AI coding agents (Claude Code, Cursor, Copilot, and others) working
in this repository. Follow these conventions in addition to anything a human
maintainer asks for.

**About this project.** `drone-sim` is a **photoreal drone simulator you can fly your own
world in** — **Unreal Engine 5.8 + Cosys-AirSim + PX4 v1.16.0 SITL + ROS 2 Jazzy**, brought
up as containers by `./scripts/sim_up.sh`. Bring your own Unreal world (`.uproject`), place
the vehicle where you want it, choose and tune your sensors, and fly it over ROS 2 — **the
same graph you would fly on real hardware**. The simulator *is* the deliverable; everything
else in the repo exists to make it start, fly, and be measured.

Goals, in priority order — when two of them pull against each other, the earlier one wins:

1. **A photoreal simulator that flies, on your world.** Not a demo scene that flies. The
   acceptance question is always "did the aircraft fly *in the user's world*, under the
   user's sensors, over ROS 2".
2. **Reproducible as Docker, from this repo alone** *(goal added 2026-07-29)*. A fresh
   machine reaches a working stack with no undocumented manual steps and no "it works on
   `carbonite`". Pin the versions you actually built and smoke-tested and record every
   deviation from upstream docs — a Dockerfile written from the reference docs rather than
   from evidence reproduces a *broken* stack. One documented exception exists and is not
   hidden: the Unreal engine base image is credential-gated (see below). Backlog:
   `docs/docker/todo.md`.
3. **Reuse upstream, don't reinvent — the glue is the original work.** PX4, the
   Micro-XRCE-DDS Agent, `px4_msgs`, Cosys-AirSim and QGroundControl are consumed as
   **pinned upstreams**. **Do not hand-roll flight control, a DDS bridge, a renderer
   bridge, a SLAM/mapping stack or a planner that an upstream already provides — take it,
   pin it, wrap it.** The original work is the ROS 2 graph, the launch composition, the
   bring-up and repair logic, the scenario/eval harness and the containerisation. This
   overrides any impulse to write a subsystem from scratch; see
   [Adapting upstream code & version pinning](#adapting-upstream-code--version-pinning).
4. **Sim-to-real parity — one ROS 2 graph, swap only the transport.** The controller that
   flies in SITL is the controller that flies on the Pixhawk 6C; the only thing that
   changes is the transport underneath it. This is not an aspiration — it has been
   demonstrated: an unmodified `offboard_control` node reached 4/4 waypoints under this
   simulator with **max error 0.79 m**, reproduced three times including from a cold start,
   with **no patch to the controller**.

**What this project is not.** It is not a research project with a single application
bolted to it. Vision-based navigation, VLM agents, planning, perception stacks and
benchmark reproduction are things people build **on** the simulator — each is an example of
what it is for, never the repo's purpose. The `vlm/` directory was deleted for exactly that
reason: one application had colonised the framing of a general tool.

**Where it is going** — capabilities, not research phases: dynamic actors in the world,
ground-truth labels, a seeded scenario/eval harness, the flight gate, wind and environment
control, and the HITL transport. The backlog is **`docs/todo.md`** (`SIM-NN` IDs); the
current position is in `docs/roadmap.html`.

**What was retired, and where it went.** The **Gazebo** regression baseline and the
**Isaac Sim + Pegasus** stack are both retired — Gazebo by the owner's decision to narrow
the project to one simulator, Isaac earlier and for a hard technical reason: it SIGSEGVs on
this host's driver `610.43.03` and no Pegasus release exists for Isaac 6.0
(`docs/history/isaac/driver-decision.md`). Their backlogs and design docs live under
**`docs/history/`** and are still worth reading for *why* a decision was made.
**`docs/worklog/` is the dated record of how the work actually happened and is frozen** —
see [Worklogs](#worklogs--write-and-update-as-you-go).

**Read before making architecture decisions:** `docs/bench.md` (the machine and container
you are working in), `docs/quickstart.md` (how the simulator is actually launched, flown
and read — every number in it was measured), `docs/conventions.md` (the frozen ROS 2 graph
spec), `docs/todo.md` (the backlog) and `versions.lock` (every pin). The historical design
reports are in `docs/history/reference/`.

## The simulator, concretely — the facts that cost days to learn

Each of these was found by running the thing, and each one has already been mistaken for a
different bug at least once. Read them before debugging something that "should work".

- **The control interface is ROS 2 only** — `px4_msgs` over uXRCE-DDS, exactly as on the
  aircraft. Cosys-AirSim's **RPC API is for simulator concerns** (placing objects,
  capturing frames for measurement, probing the world), and **MAVLink is an internal
  detail** of how PX4 and the renderer agree on physics. Neither is a control path, and
  autonomy must never reach for one.
- **Lockstep is dead code in Cosys-AirSim.** `"LockStep": true` in `settings.json` is
  accepted and silently ineffective, so **every timing number in this project is
  free-running**. Measure real-time factor as a health signal if you like, but **never
  quote an RTF, a frame interval or a step count as deterministic**, and never build a test
  that assumes reproducible stepping.
- **A stale PX4 EKF origin looks exactly like a control bug.** PX4 sets its EKF local
  origin **once**; if it initialises before the simulated vehicle has settled, every
  altitude PX4 reports is offset for the whole session — the vehicle claims tens of metres
  of altitude while sitting on the ground. That defect presented as a controller fault for
  a full day. `scripts/sim_up.sh` verifies the origin and repairs it by restarting PX4;
  `scripts/run_gate.py` scores an unrepairable run **VOID, not FAIL**, because such a run
  never measured the flight code at all.
- **Frames are NWU, not ENU**, despite what upstream documentation says.
  `ros2_ws/src/control/control/frames.py` carries a tested conversion.
- **Imagery matches Unreal's own render of the same view to 1.15 of 255** once three
  settings keys are right (`LumenGIEnable`, `LumenReflectionEnable`, `ForceUpdate`) — on
  the **stock** upstream plugin. The washout that cost days chasing it turned out to be
  three unrelated causes at once (RGB read as BGR in the measurement client, the camera
  inside world geometry, and Lumen GI explicitly disabled), which is why every
  single-cause hypothesis kept half-working. See
  `docs/worklog/2026-08-03-c11-washout-root-cause.md`.
- **Verify that the artifact you built is the artifact that ran.** Unreal de-duplicates
  plugins by name+version, so a backup copy left under `Plugins/` silently wins and a
  patched binary is never loaded. An md5 check that inspects the file *on disk* proves
  nothing about the one the engine *loaded*; a negative result obtained that way says
  nothing at all.
- **Sensor rates are capped in `perception.launch.py`** — imagery 20 Hz, LiDAR 10 Hz — and
  measured throughput sits at **94%** and **100%** of those ceilings
  (`scripts/measure_sensor_rates.sh`). A rate below the ceiling is a finding; the ceiling
  itself is a policy choice, not a limit of the stack.
- **The Unreal engine base image is credential-gated.**
  `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0` requires EpicGames org membership and a
  PAT with `read:packages`. Anonymous pulls return 403. This is the one step "reproducible
  from the repo alone" does not cover — **say so plainly in any doc that claims
  reproducibility**; do not paper over it.
- **The engine image is Ubuntu 22.04 (jammy) and ROS 2 Jazzy has no jammy packages**, so
  the renderer and the ROS 2 graph **cannot share a container**. The stack is multi-container
  by necessity, and the renderer↔ROS 2 boundary stays an RPC / MAVLink socket.
- **There is no compose file.** The simulator has never used `docker compose`; containers
  come up through `./scripts/sim_up.sh` with raw `docker run`. `scripts/check_image_refs.py`
  is the tier-1 CI check that keeps image names honest — every `drone-sim/...` reference in
  the tree must name an image declared under `images:` in `versions.lock`.

## Never command the real aircraft without asking first

This project's hardware target is a **real drone** — a Pixhawk 6C / X500 airframe, first in
**HITL** (real PX4 firmware in the loop) and then in **real flight** with a Jetson Orin NX.
Anything that can put the *real* aircraft in motion — arming the real Pixhawk, a HITL run
with motors live, a real offboard / trajectory / velocity setpoint stream to hardware, or a
real flight test — needs the operator's explicit go-ahead **for that specific run**, every
time.

Approval never carries over. Not to a retry after a run that failed or timed out, not to
the next scenario/seed in a list, and not because the operator approved a plan that
mentioned flying, agreed to a sequence, or said "do all". Each real run is its own
question, asked immediately before it.

When asking, say plainly what the aircraft will do: the mission/profile and its
parameters, how long it lasts, how high it goes, the geofence, and what stops it
(failsafe, kill switch, RC override). Then wait for an answer.

**SITL and pure-sim runs are exempt — they are the entire point of this repo, and safe to
run.** But say which you are doing, and **never let a "sim" command reach real hardware**:
the sim↔real boundary is the **transport swap** (SITL MAVLink/uXRCE-DDS vs a link to the
real Pixhawk, `use_sim_time`, HITL enabled in QGC). Be certain which side you are on before
you stream setpoints or arm — a misdirected offboard stream or an accidental HITL-enabled
arm is a real-motion event, not a sim one. The parity that makes this project valuable is
also what makes the mistake easy: the commands are identical by design.

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

- **Put the item in `docs/todo.md`** — the project's backlog — with a **`SIM-NN`** ID
  (mechanical, next free number; do not renumber existing ones). Docker and
  reproducibility work goes in `docs/docker/todo.md`. Retired items keep their old IDs and
  live under `docs/history/`; if an active doc still cites one, drop it or point at the
  archive instead.
- **The IDs are deliberately not `#N`.** A task reference must not be able to mis-link as
  a GitHub issue — see
  [Cross-references in PR and commit text](#cross-references-in-pr-and-commit-text).
- **State it clearly:** the change, the reason, and the acceptance/verification (which
  simulator run, seeded scenario, metric threshold, or unit test will prove it — see
  [Verifying changes](#verifying-changes)).
- **Keep the status current:** mark it in progress when you start and done when it lands,
  and reflect it in `docs/roadmap.html`. **A stale plan is a broken rule, not an untidy
  one** — a backlog that disagrees with the tree costs the next session more than it saved
  this one.
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
  packages, launch/bringup, the bring-up and scenario scripts, the perception glue, the
  Dockerfiles, or `versions.lock` — **especially large changes** — goes on a feature
  branch with a pull request, never a direct commit to the default branch. This keeps
  `main` reviewable and CI-gated.
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
- **Submodules / vendored trees.** This project pins third-party code (PX4, `px4_msgs`,
  Cosys-AirSim, the Micro-XRCE-DDS Agent) — prefer a `vcstool` `.repos` manifest over git
  submodules. If a submodule *is* used, remember a rebase replays commits as *new* SHAs:
  merge the submodule PR first, then update the superproject gitlink to the
  **post-rebase SHA now on the submodule's `main`** before merging the superproject PR.

### Cross-references in PR and commit text

On GitHub a bare `#N` in a PR description, issue, review comment, **or commit message**
auto-links to issue/PR **#N in the same repo** (the `drone-sim` repo). This project also
reuses `#N` as *internal* identifiers in its docs (task numbers, backlog items, bug
IDs). Pasted verbatim, an internal `#N` silently links to an unrelated PR/issue.
Classify every `#N` before you land PR/issue text or a commit message:

- **A real PR/issue in *this* repo** → leave the bare `#N` (the link is correct).
- **A PR/issue in *another* repo** → fully qualify it as `owner/repo#N` (e.g.
  `PX4/PX4-Autopilot#25089`, `Cosys-Lab/Cosys-AirSim#135`). A bare `repo#N` **without the
  owner** does not link at all — always include the owner. (Upstream trackers you'll cite
  often: `PX4/PX4-Autopilot`, `PX4/px4_msgs`, `Cosys-Lab/Cosys-AirSim`.)
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
never sufficient for anything that flies, perceives, or plans.**

**A correct component is not a working flight — verify end to end through the real
graph.** A node that behaves correctly in isolation, a passing unit test, and a bridge
that "did the right thing" are each necessary but not proof: the aircraft (sim or real) is
the only real client. **Bring the stack up with `./scripts/sim_up.sh`, exercise the *full*
ROS 2 graph** — renderer → XRCE agent → PX4 → perception → control → back to PX4 — and
confirm the actual flight/behaviour, not just the unit you touched. Bugs live in the seams
(a message-contract mismatch, a frame or timestamp error, a failsafe that never fires) that
no green component-level check will show.

- **Flight / control / perception behaviour → fly it and capture the evidence.**
  - **TEAR THE STACK DOWN AFTER EVERY FLIGHT — AND VERIFY IT, DO NOT ASSUME IT.**
    *(added 2026-08-14, strengthened 2026-08-15)* After **every flight**, not merely at the end
    of a session. **Never leave a stack up "in case another run is wanted"** — that is the
    specific habit this rule exists to stop, and it was corrected the day after the rule was
    first written, by leaving a City Sample stack running for exactly that reason.

    A bring-up is cheap and repeatable; someone else's machine is not. If another flight is
    wanted, bring it up again — `sim_up.sh` exists precisely so that costs a command rather than
    a negotiation. Every container, every renderer, every recorder. This machine is
    shared with the operator's other work: a stack left up holds ~8 GB of GPU, a CPU core or
    more, and hammers whichever disk the world lives on. Leaving one running is not untidy, it
    is taking someone else's machine.

    **The verification is the rule, not the teardown.** Teardown reporting success is not
    evidence that it worked:

    ```bash
    docker rm -f sim-px4 sim-ros2 sim-qgc sim-unreal
    docker ps -a --format '{{.Names}}\t{{.Status}}'          # -a, and read the AGES
    pgrep -a -f "Binaries/Linux/UnrealEditor"                 # the real binary path
    nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
    ```

    **Two ways this has already gone wrong, both on 2026-08-14:**

    - **A teardown was reported as successful, `docker ps` came back blank, and four containers
      were nonetheless found up TWO HOURS later** — recreated afterwards by a detached bring-up
      nobody re-checked. `docker ps` at one instant proves nothing; `docker ps -a` with the
      container ages does.
    - **The check itself was the bug.** `for p in UnrealEditor Xvfb ffmpeg ...; do pgrep -f "$p"`
      puts every one of those names into the asking shell's own argv, so `pgrep -f` matched
      itself and reported 2–4 of everything. Numbers that looked like evidence and were an
      artifact of how the question was asked. Match on a path the target has and the checker
      does not.

    A backgrounded or detached bring-up (`nohup`, `setsid`, `run_in_background`) **must** be
    confirmed dead by PID, not assumed dead because the foreground command returned.

  - **RECORD THE CHASE CAMERA ON EVERY FLIGHT TEST, AND HAND THE OPERATOR A COMMAND THEY
    CAN RUN THEMSELVES.** *(added 2026-08-13)* Bring the stack up with `--display` and set
    `SIM_CHASE_VIDEO=1` so the run writes `out/<scenario>-seed<N>-chase.mp4` alongside the
    MCAP, and **quote the exact command in the report** — not a description of it, not a
    paraphrase, the literal lines that reproduce the run:

    ```bash
    ./scripts/sim_up.sh --display                        # add --world PATH.uproject for your own map
    SIM_CHASE_VIDEO=1 python3 scripts/run_scenario.py \
        scenarios/square-10m.yaml --seed 1 --outdir out/<name> --no-restart
    ```

    **Why this is a rule and not a nicety.** Every vehicle camera is mounted *on* the
    aircraft, so the one object under test is the one object never in frame — and this
    project has twice reached a *confident wrong conclusion* that only the video overturned
    (`SIM-27`'s "it fell through the ground", withdrawn when the operator watched the
    landing). Numbers describe a flight; the chase view shows it. **A flight test reported
    without a watchable artifact is asking the reader to take the numbers on trust.**

    A run that cannot record it — a headless stack, a world without a display — must **say
    so explicitly** rather than quietly omit it. `run_scenario.py` already prints that
    warning; repeat it in the report.
  - Run a **seeded scenario** (`scripts/run_scenario.py`, which drives `sim_up.sh`
    directly) and assert the outcome — takeoff, waypoint square, collision-free traversal,
    land — as a **success rate over N seeded runs**, not a single lucky pass. Record a
    **rosbag2 → MCAP** artifact per run as evidence (`scripts/run_scenario.py`).
  - **Know what a seed currently controls: the spawn pose, and nothing else.** The retired
    Gazebo harness seeded wind and vehicle mass through a generated world overlay; **there
    is no equivalent here yet** — it needs Cosys-AirSim's wind API, which is still open
    work on `SIM-07`. So **do not describe gate runs as covering varied conditions**: ten
    seeds today are ten spawn poses in identical air. Saying otherwise overstates the
    evidence, and that overstatement would then be quoted as a result.
  - **`scripts/run_gate.py` is the flight gate.** It re-derives pass/fail from the numbers
    rather than trusting the controller's own `outcome` field, and rejects non-finite
    errors — a check missing from its first version that caught a real NaN-laundering bug.
    Its scoring semantics are load-bearing: **VOID is not FAIL.** A run whose EKF origin
    was stale never measured the flight code, so it is excluded from the success rate —
    but voids still **block** the criterion, so a gate cannot be passed by voiding
    everything inconvenient.
  - **Timing is free-running, so do not assert determinism.** Lockstep is dead code (see
    above). Measure and report rates and latencies as measurements; a run is not
    reproducible step-for-step and no test may assume it is.
  - **Perception — measure, don't assert.** IMU/camera **timestamp jitter and rate
    stability** before trusting sim VIO (this stack's IMU carries ~15% duplicate timestamps
    by upstream design), hover **drift over 60 s** for GPS-denied estimation, replan
    latency for a planner, and sensor throughput against the launch-file ceilings. With no
    second simulator to cross-check against, the control is a measurement of the renderer's
    own output — e.g. AirSim's capture against Unreal's native render of the same view.
  - **Applications evaluated on the simulator report application metrics.** A navigation
    or agent application reports **SR / SPL / NE / OSR / collision-rate** over a seeded
    episode set with the **success threshold recorded** (5 m and 20 m are both valid
    depending on the benchmark — parameterise it), and timestamps image-in → target-out for
    **p50/p95 decision latency**; the onboard budget is **≤1 s**.
  - **Metrics are ground truth; a returned tool call or a printed log line is
    supporting evidence, not a substitute.** Save the MCAP bag / metric table / latency
    numbers as the evidence in the worklog/PR.
- **Host-side logic, message contracts, parsers, pure functions, build-time
  invariants → a unit test** (or a host build that exercises the logic): message contracts
  and watchdogs, frame conversions, depth back-projection math, EKF2 param sets,
  scenario/eval parsing, spawn-pose injection, metric computation. Run these off-target
  where they're fast and deterministic. Tier-1 CI is exactly this set plus the parse and
  pin checks, and it must stay runnable on a hosted runner — **the simulator itself cannot
  run in CI**, and a self-hosted runner on a public repo would execute fork code on the
  workstation. `./scripts/run_local_ci.sh` is the accepted substitute; say when a result
  came from it rather than from CI.
- **HITL / real flight is a gate, not a step.** Before any real flight, PX4 **HITL on
  the Pixhawk 6C must pass the identical SITL suite** — no exceptions. HITL is
  community-supported in PX4; budget integration time.

**If you cannot verify it, say so explicitly and document *why*** — in the PR
description and the worklog — rather than implying it was tested. Name the concrete
blocker (e.g. "NVENC refuses on driver `610.43.03`, so the Pixel Streaming capture path is
unverified — `docs/nvenc-driver-blocker.md`"). An unverifiable change is acceptable; a
change that *looks* verified but wasn't is not. The same standard applies to a **negative**
result: before recording "the fix did not work", prove the fix was actually loaded.

**Leave the graph in a known-good state when a run ends.** After an experiment, restore
a known-good launch/scenario config and pinned versions rather than leaving a
half-broken setup, so the next session starts from a clean baseline. Note in the
worklog what's currently pinned and what's running.

## Adapting upstream code & version pinning

This stack is **assembled from pinned upstreams**, and the **dominant project risk is
version coupling**, not novel code. Treat both the pins and any port as reviewable
deliverables.

**Version-lock is the architecture.** Record every SHA/tag/digest in `versions.lock` before
writing code that depends on it; CI must assert the couplings hold:

- **One PX4 tree — v1.16.0** (SHA `6ea3539157ca358c70a515878b77077af7d4611d`), and it is
  **the same tree the real Pixhawk 6C is flashed from**. It speaks **uXRCE-DDS** to the ROS
  2 graph and the **MAVLink simulator API** (TCP 4560) to the renderer. *(The project used
  to carry a second tree, v1.14.3, because Pegasus was tested against it. That tree went
  away with Isaac Sim — there is now exactly one PX4, which removes the development plan's
  dominant architectural risk rather than working around it.)* **The PX4 image no longer
  installs Gazebo** — `Tools/setup/ubuntu.sh --no-sim-tools` plus an explicit reinstall of
  the build deps that are not Gazebo (`bc`, `libeigen3-dev`, `protobuf-compiler`,
  `pkg-config`, `libxml2-utils`); the image went **11.6 GB → 11.0 GB** and the build now
  asserts Gazebo is absent, so don't reintroduce it. **NuttX stays installed on purpose:**
  real Pixhawk 6C firmware is flashed from that tree.
- **`px4_msgs` MUST be branch-matched to the firmware** (`release/1.16`) — a mismatch does
  not error, it silently produces empty topics; CI asserts the topics actually populate.
- **One ROS 2 distro — Jazzy — everywhere** *(decided 2026-07-31)*. A second distro is not
  a small addition: it forks the base image, the `px4_msgs` branch match, the perception
  packages and CI. It is also the *safer* pin, measured rather than assumed — upstream
  Cosys-AirSim documents Jazzy on Ubuntu 24.04, its wrapper includes
  `<cv_bridge/cv_bridge.hpp>`, and Humble's `vision_opencv` ships only `cv_bridge.h`, so
  **Humble can no longer compile the wrapper at all**. *(There is no longer a second Python
  runtime either: the Isaac 3.11 / Jazzy 3.12 split went away with Isaac. One Python, 3.12,
  everywhere.)*
- **Cosys-AirSim ↔ UE5 — pin the exact SHA, never a branch.** Upstream has no `5.5` branch
  at all and `main` has already migrated 5.5 → 5.6dev → 5.7pdev → 5.8, so a branch pin
  evaporates under you. Current: **tag `5.8-v3.4.1`, SHA `a552dd6c`**, against **UE5.8**.
- **Pin the engine image by digest, and by the three-component tag.**
  `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0` @
  `sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46` — verified
  byte-identical between the registry query and the pull. `dev-slim-5.8` is a **moving
  alias** (the 5.5 alias tracked four patch releases), and Epic does not image every hotfix
  — `dev-slim-5.8.1` is a 404 even though UE 5.8.1 shipped. **Never derive a tag from a
  publication pattern**: the lag between an engine release and its image was 55 days, then
  13, then 0.
- **The engine image ships an installed engine, not a source tree** (`UnrealBuildTool.dll`
  present; `Setup.sh` / `GenerateProjectFiles.sh` absent), and it has **no system clang** —
  so `build.sh --ue-root` is mandatory, and any upstream instruction assuming a source
  checkout must be translated to a UBT plugin build. The proof that `--ue-root` took effect
  is the artifact, not the banner: `readelf -p .comment` on `libAirLib.a` reports
  `clang version 20.1.8`, the engine's bundled toolchain, not the image's gcc 11.
- **The engine image is jammy; Jazzy is noble.** Nothing Jazzy can be installed inside it,
  so the renderer and the ROS 2 graph are separate containers **by necessity, not by
  style**, and their boundary stays an RPC / MAVLink socket.
- **Cesium for Unreal** v2.28.0 supports UE5.5–5.8 and **v2.29.0 drops UE5.5**, so UE5.8 is
  the forward-supported path rather than merely the newest one. *(The old Cesium/PhysX
  mutual-exclusion warning was an Omniverse Fabric-Scene-Delegate coupling and retired with
  Isaac; it never bound Cesium for Unreal.)*
- **NVIDIA driver `610.43.03` is pinned by the host, and it costs two capabilities.** Isaac
  Sim SIGSEGVs on it (which retired that stack) and **NVENC refuses to initialise**, which
  blocks hardware-encoded capture — one driver, two independent blockers, one owner-only
  host rebase to fix. See `docs/bench.md` and `docs/nvenc-driver-blocker.md`.

**Least-destructive vendor edits.** When adapting a vendored/upstream tree (PX4, the
Cosys-AirSim wrapper or plugin, a driver), change as little as possible — keep the source
**byte-identical to upstream** wherever you can and push integration into the *build*,
*launch*, or *config* layer, not the files:

- **Exclude at the build layer** — leave a unit out of the compiled/launched set rather
  than deleting it.
- **Guard** target-specific behaviour behind a build flag / launch arg rather than
  ripping code out.
- **Patch a copy, not the checkout.** The working pattern here: `vendor/` stays
  byte-identical (`git status --porcelain vendor/` reports nothing), the deviations live as
  patch files in `patches/<component>/`, and the build script applies them to a
  container-local copy. A patch deliberately *not* applied lives outside the glob the build
  script uses, with a README saying why.
- Keep upstream's own files (README, license, tests, build scripts) in place unless
  they actively break the build.
- Every deviation — and every source edit — is recorded in the component's vendoring
  notes at **`docs/vendor/<component>.md`**, so upstream rebases stay clean and the
  divergence is auditable. **Not** inside the vendored tree: `vendor/*` holds nested git
  clones, so a file placed there is owned by that clone and can never be committed to this
  repo (and un-ignoring the directory makes git record a broken gitlink). Prefer a
  **`.repos` (vcstool) manifest** over submodules for third-party trees.
- **Generate every hunk from the real file.** Cosys-AirSim sources are CRLF; a hand-written
  LF hunk fails with `Hunk 1 FAILED (different line endings)` and costs a build cycle.
- **Report upstream defects upstream.** The applied patches here are upstream bugs, not
  local preferences — each is worth filing.

**A substantial port MUST ship a code-map doc.** Any close adaptation of an upstream
implementation — a planner port, a rewritten bridge, a subsystem lifted from another
project — ships a function-level, side-by-side **new-code ↔ upstream** mapping so a
reviewer can check it line by line:

- **Form.** A table, one row per ported function/structure: *new code (`file:line` +
  symbol)* ↔ *equivalent upstream code (`file:line` + symbol)*, grouped by sub-area,
  plus a final **"deliberate divergences"** section listing every intentional
  difference (API version, message types, threading/executor model, dropped feature)
  **with the reason** — divergences are flagged, not hidden.
- **Where it lives.** A large port gets its own file under the area's docs, linked from the
  area's status doc. A small one belongs in `docs/vendor/<component>.md`.
- **Verify every cited `file:line` — do NOT cite from memory.** Grep both trees (the
  new working tree + the pinned upstream checkout) to confirm each symbol is at the line
  you cite and that it's the *definition*, not a call site. Pin the upstream commit SHA
  at the top and add a "verified <date>" stamp. Lines drift — a code map written from
  memory is reliably wrong.
- Root-cause and follow the upstream implementation; don't paper over a symptom with a
  local hack that silently diverges from the reference. (The one-word fix that turned an
  unexplained `BadParamException: The string contains null characters` into a solved data
  race — a `Reentrant` callback group that should have been `MutuallyExclusive` — is what
  root-causing buys you over a retry loop.)

## Worklogs — write and update as you go

**For any non-trivial, multi-step investigation or implementation, keep a worklog
(`docs/worklog/YYYY-MM-DD-<slug>.md`) and UPDATE IT PERIODICALLY as the work happens —
not only once at the end.** The worklog is a running record, not a final report written
from memory.

- **Append at each meaningful checkpoint** — a confirmed finding, a measurement/number
  (success rate, replan latency, drift, decision p95, sensor rate), a decision and its
  reason, a dead-end (and why it was abandoned), a refuted hypothesis, a bench result,
  or a next-step. Write it while it's fresh, before moving on.
- **Why:** long agentic runs lose context (summarization, crashes, a new session). A
  worklog updated as you go means the thread survives — a resumed session (or a human)
  can pick up exactly where you were, with the evidence, instead of reconstructing it.
  It also stops the end-of-task write-up from quietly dropping the dead-ends and the
  *why*.
- **Standalone + honest:** each worklog is self-contained — never de-dup its findings
  into "see other doc" pointers — and records what was actually tried/measured,
  including what failed and what is still unverified, not a cleaned-up highlight reel.
- **Already-written worklogs are frozen.** They are the dated record of how the work
  actually happened, so **never edit, rename or move an existing one** — not to update
  terminology the project has since changed, not to tidy a filename, not to fold one into
  `docs/history/`. Link to them by their real current paths. Only the worklog for work
  currently in flight is live.
- **Keep the companion HTML render current** at meaningful checkpoints (see below).
- Trivial one-shot changes don't need a worklog (same bar as the "Plan first" TODO rule).

## Agent memory — keep it current, and keep it a pointer

Agents with a persistent memory (Claude Code: `~/.claude/projects/<project>/memory/`)
**must keep it current as work happens** — not only at the end, and not only when asked.
A fresh session starts with memory and nothing else; what is not there is re-derived, or
re-broken.

**But memory is a pointer, not a second copy of the repo.** Progress belongs in
[the worklog](#worklogs--write-and-update-as-you-go) and `docs/todo.md`, which
are reviewed, diffed and shared. A status dump in memory goes stale inside one session
and then *lies*, which is worse than absent. So:

- **Record in the repo:** what happened, what was measured, what failed, what is still
  unknown.
- **Record in memory:** *where to look* (start here, read the newest worklog), and facts
  that are **not derivable from the repo** — the GPU work-split and driver pin, the
  credential path to the engine image, owner preferences, tooling gotchas, the container's
  Docker/CDI workarounds.
- **Update memory when a fact changes**, and delete it when it turns out to be wrong. A
  confidently wrong memory is the most expensive artifact in this project.

**The rule that matters most — save the implication, not just the fact.** A memory that
records *"the 5060 Ti is Blackwell sm_120"* is trivia until it also says *"…therefore keep
it for inference and render on the 3080, and expect the newest driver branch to break
things."* When writing a memory, state what it means for the work — a fact nobody can act
on is not saved, it is stored.

**End a session so the next one is cheap:** worklog updated as you went (dead ends
included), gate status honest (`unknown` and `void` are valid), pinned versions and the
running config noted, and memory pointing at the newest worklog.

## Worklog HTML renders

**Every worklog (`docs/worklog/*.md`) must have a companion HTML render at
`docs/worklog/html/<same-name>.html`.** When you add a new worklog — or substantially
edit one that is still in flight — author/update its HTML in the same change and
add/refresh its card in `docs/worklog/html/index.html`. The existing renders and the
reference docs in `docs/history/reference/*.html` are the house style to match.

- **Hand-author it — do NOT run a Markdown→HTML converter.** Read the worklog and write
  the HTML directly. The goal is a thoughtfully laid-out, *visual* page, not a mechanical
  transform. (Scripting is fine for verification or metadata extraction — just not for
  generating the page content.)
- **Self-contained + shared design system.** Each page must render **standalone** — no
  external `.css`, JS, fonts, images, or other files; the CSS, diagram SVGs, and
  theme-toggle JS are all embedded inline. The existing pages are the **canonical design
  source**: a clean topbar, a `.content` column, a per-page table of contents, and a
  light/dark theme. Copy that `<style>` block verbatim into a new page so it doesn't
  drift; to change the design, edit the source page's `<style>`, then re-embed it.
- **Visuals + at least one diagram, built ONLY from the doc's real content.** Use
  callouts (ok/warn/bug), stat grids, before/after bars (widths **to scale** from the
  real numbers), and flow/topology diagrams (a small inline `<svg>`). Add a diagram
  wherever the doc has something structural or numeric to show — the ROS 2 data flow, a
  perception→planner→control loop, the container/GPU topology, a before/after metric.
  **Never fabricate** nodes, edges, or values, and don't force a diagram onto pure prose.
- **Faithful.** The HTML must carry all the worklog's information — findings, numbers,
  `file:line`, caveats — never a summary.

## Research & citations

**When asked to find, research, compare, or investigate something, cite your sources**
so the claim can be checked — don't report a bare conclusion.

- **Code / repo facts** → `file:line` (or commit SHA).
- **Sim / bench findings** → the command run and the relevant output, or the
  measurement and how it was taken (which world, which seed, how many runs, and whether
  the stack was cold or warm).
- **External facts (docs, papers, forums, issue trackers)** → the URL(s), ideally as a
  "Sources:" list. The historical design reports in `docs/history/reference/` already carry
  a large, verified source list — cite into it rather than re-deriving.
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
- **Two GPUs, and the split is deliberate: render on the 3080, infer on the 5060 Ti.**
  The 3080 (10 GB) drives the renderer; the 16 GB 5060 Ti is kept for inference. **Pin the
  GPU at the container boundary** (`--device nvidia.com/gpu=0`) — the engine image ships
  `NVIDIA_VISIBLE_DEVICES=all`, which does the opposite of pinning. Its
  `NVIDIA_DRIVER_CAPABILITIES` already includes `graphics`; don't override it with a
  narrower list. VRAM is a real constraint on what an application can co-host: a 30B-class
  VLM does not fit locally — serve a 2B/4B/8B, or serve it remotely.
- **Don't touch the container's load-bearing workarounds** unless they're already
  broken: the Docker `fuse-overlayfs` storage driver and the `/etc/cdi-local` CDI spec
  + `nvidia-cdi-local.service` that make GPU-in-Docker work. If `docker run --gpus all`
  fails on a missing `/usr/lib64/...` driver path, the spec is stale — regenerate per
  `docs/bench.md`.
- **Keep the ROS 2 graph identical across sim and real.** The names are frozen in
  `docs/conventions.md` (never rename or wrap a PX4 topic; multi-vehicle is the `px4_ns`
  parameter, not a refactor). Use `use_sim_time:=true` only in sim; swap only the
  transport. These names must reach the aircraft unchanged.
- **Refer to hardware by a stable label, not a volatile device path.** The Pixhawk
  6C's `/dev/ttyACM*` / `/dev/ttyUSB*` and the Orin NX's interfaces change across
  replugs; identify each by a documented label/role and record the mapping and the
  Orin↔Pixhawk UART wiring in a `docs/hardware/` doc.
- **Capture the command you actually ran** (world, settings file, spawn, seed, ports) as
  evidence in the worklog so a result can be reproduced.
- **Never use `~/` for tooling, caches, big data, or scratch without approval.** Archival
  artifacts — rosbags, recordings, datasets, model weights — go on the **7 TB external
  drive** (`/var/mnt/…`), **not** the ~279 GB internal NVMe. Project tooling stays inside
  the repo (`vendor/`); throwaway scratch goes to `/tmp`. RGB-D at 640×480@30 Hz is ~tens
  of GB/hour — budget storage before a sweep.
- **The simulator's live working set is the documented exception to that rule**
  *(decided 2026-07-31)*. The engine image, the plugin build and the working project stay
  on the **internal NVMe**, and Docker's data-root is not moved: the 7 TB volume is a
  ST10000NE0008, a 7200 RPM **spinning disk** (`rotational=1`), and UE5 shader
  compilation, asset streaming and tile paging are latency-sensitive random I/O. Budget
  the space — the engine image alone is 24.0 GB compressed and **57.4 GB extracted**. The
  external-drive rule was written for archival data, and archival data still goes there.
  Full reasoning: `docs/docker/todo.md`.
- **On any other drive, write only under `<drive-root>/Developments/projects/drone-sim/`.**
  Mirror the project path from the root of that drive — e.g.
  `/var/mnt/<uuid>/Developments/projects/drone-sim/`. **Never create a top-level directory
  on a drive you do not own**; these volumes are shared with the host, other containers,
  and unrelated data (the 7 TB drive also holds ~1.1 TB of Steam libraries), so project
  files must stay in one predictable, self-identifying place that is obvious to delete or
  back up.
- **Secrets stay off the tree and off history** — pass Wi-Fi creds, setup keys, and
  tokens (including the GitHub PAT that reaches the engine image) on the command line or
  via env/secret files, never committed.
