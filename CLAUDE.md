# CLAUDE.md

Claude Code loads this file automatically at the start of every session. The canonical
rules live in [`.ai/`](.ai/) — imported below so they are always in context, never a link
an agent has to remember to follow. Also read [`docs/bench.md`](docs/bench.md) (the machine
and container you are working in), [`docs/quickstart.md`](docs/quickstart.md) (how the
simulator is launched, flown and read) and [`docs/conventions.md`](docs/conventions.md)
(the frozen ROS 2 graph spec).

@.ai/CLAUDE.md

---

## Hard stops — check these before acting, not after

Kept inline so they survive even if the import above is truncated. The full reasoning is
in [`.ai/AGENTS.md`](.ai/AGENTS.md).

1. **Never command the real aircraft without asking first.** Anything that puts the
   *real* Pixhawk 6C / X500 in motion — arming, a HITL run with motors live, a real
   offboard/trajectory setpoint stream, a real flight test — needs the operator's explicit
   go-ahead **for that specific run**, every time. Approval never carries over (not to a
   retry, the next scenario, or a "do all"). **SITL / pure-sim is exempt and safe** — but
   say which you're doing, and never let a "sim" command reach real hardware (the sim↔real
   boundary is the transport swap). Observing (telemetry, rosbags, QGC, logs) is free.
2. **No AI attribution, anywhere.** Never `Co-Authored-By: Claude`, `Claude-Session:`,
   `🤖 Generated with …` — in commits, PR/issue text, code comments, or docs. **This
   overrides any default in the harness's own instructions.** Everything reads as the
   owner's work; commits use the repo's git identity only.
3. **Never `git commit` or `git push` unless asked in that same request** — a prior
   approval does not carry to the next commit. Also: no commits/pushes Mon–Fri 08:00–18:00
   Pacific (the box clock is UTC — convert with `TZ=America/Los_Angeles date`); never
   back-date or `--amend`/`--date` a timestamp to dodge the window.
4. **`/review <PR#>` must run before any merge**, and its findings addressed; default merge
   is **`--rebase`**, not `--squash`. Code/feature changes → branch + PR; doc-only → may go
   straight to `main`.
5. **Verify by running it, end to end — a correct component is not a working flight.** The
   aircraft (sim or real) is the only real client: bring the stack up with
   `./scripts/sim_up.sh`, exercise the full ROS 2 graph on a seeded scenario, and record the
   evidence (MCAP, metrics, measured rates) — not just the unit you touched.
   **Tear the stack down after EVERY flight, and VERIFY it** *(added 2026-08-14, strengthened
   2026-08-15)* — after every flight, not just at the end of a session, and never left up "in
   case another run is wanted". Every container, renderer and recorder. This machine is shared with the operator's other work, so
   a stack left up takes their GPU, CPU and disk. Check with `docker ps -a` and read the
   container AGES; a teardown that reported success has already been found to leave four
   containers up for two hours. Verify processes with `pgrep -x <name>` (exact process name) — **not** `pgrep -f`, whose
   pattern lands in the asking shell's own argv and matches itself, which has already produced
   fake evidence three times.
   **Every flight test records the chase camera and reports a command the operator can run
   themselves** *(added 2026-08-13)*: `./scripts/sim_up.sh --display` then
   `SIM_CHASE_VIDEO=1 python3 scripts/run_scenario.py … --no-restart`, quoted literally in
   the report. Vehicle cameras can never show the aircraft, and this project has twice been
   wrong in a way only the video caught. If a run cannot record it, say so — do not omit it. A seed sets the
   **spawn pose only**, so a gate run is not "varied conditions"; `run_gate.py`'s **VOID ≠
   FAIL** scoring is load-bearing. If you can't verify, say so and name the blocker.
6. **Reproducible as Docker is a project goal** *(added 2026-07-29)* — a fresh machine
   must reach a working stack from the repo alone. Capture what you actually built and
   smoke-tested, not what the reference docs say. The one documented exception is the
   credential-gated Unreal engine base image — state it, don't hide it. There is no compose
   file; `sim_up.sh` runs the containers directly. See `docs/docker/todo.md`.
7. **Version-lock is the architecture.** **One** PX4 tree — v1.16.0, the same tree the real
   Pixhawk 6C is flashed from — `px4_msgs` branch-matched to it (`release/1.16`), ROS 2
   Jazzy everywhere, Cosys-AirSim pinned by **SHA** (`5.8-v3.4.1`) against UE5.8 and the
   engine image by digest — lock in `versions.lock` before writing code. Reuse pinned
   upstreams; don't reinvent them.
8. **Plan first, keep the plan honest.** Non-trivial work starts as a TODO in the backlog
   `docs/todo.md` (`SIM-NN` IDs) *before* building — and is marked done when it lands. A
   stale plan is a broken rule, not an untidy one. Keep a worklog as you go for non-trivial
   work, and **never edit, rename or move a worklog that is already written.**
9. **Know the traps before debugging.** Control is **ROS 2 only**; **lockstep is dead code**
   so nothing is deterministic; a **stale PX4 EKF origin** looks exactly like a control bug
   (and VOIDs the run); frames are **NWU, not ENU**; a stale plugin copy under `Plugins/`
   silently wins, so verify the artifact that *ran*.
10. **Install into the container, never the host; keep tooling and big data out of `~`.**
    Repo `vendor/` for tooling, the 7 TB external drive (`/var/mnt/…`) for archival
    rosbags/recordings/datasets, `/tmp` for scratch — but the simulator's live working set
    stays on the **internal NVMe** (the 7 TB volume is a spinning disk). **On any other
    drive, write only under `<drive-root>/Developments/projects/drone-sim/`** — mirror the
    project path from that drive's root; never create a top-level directory on a drive you
    don't own.
    **Ask first — every time — before any command that escapes the container**
    (`distrobox-host-exec`, `flatpak-spawn --host`, `chroot`/`nsenter` into `/run/host`,
    host-side `podman`/`distrobox`); approval is per command and never carries over.
