# Copilot instructions

See [`AGENTS.md`](AGENTS.md) for the full, canonical agent rules; read
[`../docs/bench.md`](../docs/bench.md) (the machine + container),
[`../docs/quickstart.md`](../docs/quickstart.md) (how the simulator is launched and flown)
and [`../docs/conventions.md`](../docs/conventions.md) (the frozen ROS 2 graph spec) first.

`drone-sim` is a **photoreal drone simulator you can fly your own world in** — Unreal
Engine 5.8 · Cosys-AirSim · PX4 v1.16.0 SITL · ROS 2 Jazzy, brought up by
`./scripts/sim_up.sh`. The Gazebo baseline and the Isaac Sim stack are retired
(`docs/history/`). Key rules:

## Never command the real aircraft without asking first

The hardware target is a **real drone** (Pixhawk 6C / X500 — HITL, then real flight).
Anything that can put the *real* aircraft in motion — arming the real Pixhawk, a HITL run
with motors live, a real offboard/trajectory setpoint stream, or a real flight test —
needs the operator's explicit go-ahead **for that specific run**, every time. Approval
never carries over: not to a retry after a failed/timed-out run, not to the next
scenario/seed, and not because the operator approved a plan or said "do all". Say what
the aircraft will do — mission/profile, parameters, duration, altitude, geofence, what
stops it — then wait. **SITL and pure-sim runs are exempt and safe**, but say which you
are doing and never let a "sim" command reach real hardware (the sim↔real boundary is the
transport swap). Observing (telemetry, rosbags, QGC, logs, `nvidia-smi`) needs no
permission.

## Know the traps before debugging

The control interface is **ROS 2 only** (`px4_msgs` over uXRCE-DDS); AirSim RPC is for
simulator concerns and MAVLink is how PX4 and the renderer agree on physics. **Lockstep is
dead code** in Cosys-AirSim, so no timing is deterministic. A **stale PX4 EKF origin**
makes the vehicle report tens of metres of altitude while grounded and looks exactly like a
control bug; such a run is **VOID, not FAIL**. Frames are **NWU, not ENU**. A stale plugin
copy under `Plugins/` silently wins — verify the artifact that actually *ran*.

## Reproducible as Docker

*(project goal, added 2026-07-29)* The whole setup must be **easily reproducible as
Docker** — a fresh machine reaches a working stack from this repo alone, with no
undocumented manual steps. Pin the versions you actually built and smoke-tested, and
record deviations from the reference docs: a Dockerfile written from the docs rather than
from evidence reproduces a *broken* stack. One documented exception, stated plainly: the
Unreal engine base image is credential-gated (EpicGames org membership + a PAT with
`read:packages`). There is no compose file — `./scripts/sim_up.sh` runs the containers
directly. Backlog: `docs/docker/todo.md`.

## Reuse upstream, don't reinvent; version-lock is the architecture

Assembled from pinned upstreams (PX4, the Micro-XRCE-DDS Agent, `px4_msgs`, Cosys-AirSim,
QGroundControl) — the glue is the original work; don't hand-roll what a component
provides. The dominant risk is **version coupling**: **one** PX4 tree (v1.16.0, the same
tree the real Pixhawk 6C is flashed from), `px4_msgs` branch-matched to it (`release/1.16`),
ROS 2 Jazzy everywhere, Cosys-AirSim pinned by **SHA** (`5.8-v3.4.1`) against UE5.8 with the
engine image pinned by digest. Lock every version in `versions.lock` before writing code.

## Verify by running it, end to end

A green `colcon build` or a node correct in isolation is not a working flight — the
aircraft (sim or real) is the only real client. Bring the stack up with
`./scripts/sim_up.sh`, exercise the full ROS 2 graph on a seeded scenario, and record the
evidence (MCAP bag, metric table, measured rate/latency). A seed currently sets the
**spawn pose only**, so never describe a gate run as covering varied conditions. If you
cannot verify, say so and name the blocker.

## No AI / agent attribution

Do not add any AI- or coding-agent attribution, branding, or metadata to any artifact —
commit messages (no `Co-Authored-By:` an AI model/tool, no "Generated with …" or
session-link trailers), pull requests, code comments, docs, or any file. Write everything
as a normal human contributor would; keep authorship as the configured git user only.

## No committing or pushing during Pacific work hours

Never `git commit` or `git push` Mon–Fri 08:00–18:00 America/Los_Angeles (check with
`TZ=America/Los_Angeles date`). Keep changes in the working tree only (staging is fine);
commit and push off-hours, when commit dates are naturally correct — never back-date or
`--amend`/`--date` a timestamp to dodge the window. An explicit request now overrides it.

## Code changes go through a pull request

Feature/code changes branch off `main` and land through a PR; doc-only changes may go
straight to `main`. Run `/review <PR#>` (Claude Code) or an equivalent full-diff review
before any merge and resolve what it flags; default merge is `--rebase`. Never merge
unreviewed. Every feature starts as a documented TODO in `docs/todo.md` (`SIM-NN` IDs) and
is marked done when it lands; keep a worklog as you go, and never edit, rename or move a
worklog that is already written. Install into the container, never the host; keep tooling
and big data out of `~` (repo `vendor/` for tooling, the 7 TB external drive for archival
data, `/tmp` for scratch — but the simulator's live working set stays on the internal NVMe,
because the 7 TB volume is a spinning disk). **On any other drive, write only under
`<drive-root>/Developments/projects/drone-sim/`** — mirror the project path from that
drive's root; never create a top-level directory on a drive you don't own.
**Ask the operator first — every time — before any command that escapes the container**
(`distrobox-host-exec`, `flatpak-spawn --host`, `chroot`/`nsenter` into `/run/host`,
host-side `podman`/`distrobox`); approval is per command and never carries over.
