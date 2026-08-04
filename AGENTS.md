# AGENTS.md

These rules apply to **every** AI / coding agent working in this repository (Claude Code,
Codex, Cursor, Copilot, Gemini, Windsurf, Aider, etc.). The canonical, tool-agnostic
version is **[`.ai/AGENTS.md`](.ai/AGENTS.md)** — read it. The other agent-instruction
files mirror it: [`.ai/CLAUDE.md`](.ai/CLAUDE.md) (condensed Claude index),
[`.ai/GEMINI.md`](.ai/GEMINI.md), [`.ai/.cursorrules`](.ai/.cursorrules),
[`.ai/copilot-instructions.md`](.ai/copilot-instructions.md).

`drone-sim` is a **photoreal drone simulator you can fly your own world in** — Unreal
Engine 5.8 · Cosys-AirSim · PX4 v1.16.0 SITL · ROS 2 Jazzy, brought up by
`./scripts/sim_up.sh`. Before making changes, also read **[`docs/bench.md`](docs/bench.md)**
(the machine and container you are working in),
**[`docs/quickstart.md`](docs/quickstart.md)** (how the simulator is launched, flown and
read) and **[`docs/conventions.md`](docs/conventions.md)** (the frozen ROS 2 graph spec).
The retired Gazebo and Isaac Sim stacks, and the historical design reports, are in
[`docs/history/`](docs/history/).

---

The safety rule is repeated inline, because it is the one whose cost of being missed is
measured in aircraft rather than in commits — and a link an agent never follows must not
take it down with it:

## Never command the real aircraft without asking first

This project's hardware target is a **real drone** — a Pixhawk 6C / X500 airframe, first in
**HITL** (real PX4 firmware in the loop) and then in **real flight**. Anything that can put
the *real* aircraft in motion — arming the real Pixhawk, a HITL run with motors live, a
real offboard / trajectory / velocity setpoint stream to hardware, or a real flight test —
needs the operator's explicit go-ahead **for that specific run**, every time.

Approval never carries over. Not to a retry after a run that failed or timed out, not to
the next scenario/seed in a list, and not because the operator approved a plan that
mentioned flying, agreed to a sequence, or said "do all". Each real run is its own
question, asked immediately before it. When asking, say plainly what the aircraft will do:
the mission/profile and its parameters, how long it lasts, how high it goes, the geofence,
and what stops it (failsafe, kill switch, RC override). Then wait.

**SITL and pure-sim runs are exempt — they are the entire point of this repo, and safe to
run.** But say which you are doing, and **never let a "sim" command reach real hardware**:
the sim↔real boundary is the transport swap (SITL MAVLink/uXRCE-DDS vs a link to the real
Pixhawk, `use_sim_time`, HITL enabled in QGC). Be certain which side you are on before you
stream setpoints or arm — the parity that makes this simulator valuable is exactly what
makes the mistake easy, because the commands are identical by design. Observing needs no
permission — telemetry, rosbags, QGC, logs, `nvidia-smi`. Assume nothing about the
aircraft's state.
