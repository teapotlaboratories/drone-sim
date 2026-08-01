# drone-sim

A **triple-lane drone simulation framework** — PX4 · ROS 2 Jazzy · Gazebo · Unreal/AirSim ·
Isaac Sim — built toward **VLM-based sim-to-real drone navigation**, reproducing and
extending the SPF / Fly0 / OnFly line of work: a slow VLM target-generator paired with a
fast geometric planner, first in simulation, then onboard a Jetson Orin NX on a
Pixhawk 6C / X500 airframe.

---

## Project goals

### 1. Reproducible as Docker

> **The whole setup must be easily reproducible as Docker.**
>
> A fresh machine must reach a working stack from this repository alone — **no
> undocumented manual steps, no "it works on `carbonite`".**

This is a first-class goal, not packaging polish. The stack is an assembly of pinned
upstreams whose interactions are fragile, and the difference between "we got it working"
and "anyone can get it working" is entirely whether the recipe is captured.

Two rules follow from it, both learned the hard way here:

- **Pin what you actually built and smoke-tested — a SHA, never a branch.** A tagged
  upstream release in this stack became unbuildable retroactively because it pinned a
  dependency by *branch* and that branch was deleted. A branch is not a pin.
- **Write Dockerfiles from evidence, not from documentation.** Three of the steps needed
  to stand up Lane A deviate from the project's own reference docs. A Dockerfile written
  from those docs reproduces a **broken** stack.

Backlog: [`docs/docker/todo.md`](docs/docker/todo.md).

### 2. Reuse and integrate upstream — don't reinvent

PX4, the Micro-XRCE-DDS Agent, `px4_msgs`, Cosys-AirSim, Pegasus, Isaac ROS (cuVSLAM,
nvblox), EGO-Planner and vLLM are consumed as **pinned upstreams**. The original work is
the *glue*: one ROS 2 graph, launch composition, the scenario/eval harness, and the VLM
client — identical across sim and real, with only the transport swapped.

### 3. VLM-based sim-to-real navigation

Reproduce AerialVLN/OpenFly-style evaluation, then fly it — SITL → HITL → real flight,
with the same ROS 2 graph throughout.

---

## Quickstart

**Prerequisites:** Docker, ~15 GB free disk. **No GPU and no display required** — Lane A is
CPU-bound and runs fully headless.

### 1. Build the Lane A image

```bash
git clone <this repo> && cd drone-sim
docker build -f docker/lane-a.Dockerfile -t drone-sim/lane-a:v1.16.0 .
docker build -f docker/qgc.Dockerfile   -t drone-sim/qgc:v1.16.0 .      # thin, ~1 min
docker build -f docker/ros2.Dockerfile  -t drone-sim/ros2:v1.16.0 .     # thin, ~30 s
```

**All three images are required.** The second is not optional tooling: PX4 refuses to arm
without a ground-station datalink, and QGroundControl provides it — so without
`drone-sim/qgc` the stack comes up and nothing can fly.

QGC itself is **baked into that image**, pinned to 5.0.8 and SHA256-verified during the
build, so there is nothing to download by hand and a checksum mismatch fails the build
rather than surfacing later as a vehicle that will not arm.

Pulls PX4 v1.16.0 + submodules, Gazebo Harmonic, ROS 2 Jazzy, the uXRCE-DDS agent and
branch-matched `px4_msgs`, then **verifies every pin against its recorded SHA** and fails the
build on a mismatch. Expect 20–40 minutes and ~11.6 GB; the pins land in
`/etc/drone-sim-versions` inside the image.

### 2. Bring the stack up and prove it flies

```bash
docker compose -f docker/compose.yaml up -d                          # PX4 + Gazebo + XRCE agent + ROS 2
docker compose -f docker/compose.yaml --profile test run --rm verify # the acceptance gate
docker compose -f docker/compose.yaml down -v
```

Compose declares the constraints that are otherwise easy to forget — `shm_size: 2gb`, shared
network **and** IPC namespaces (Fast-DDS delivers over shared memory), loopback-pinned Gazebo
transport, and a `/dev/shm` sweep so a killed run cannot poison the next one.

Single-container equivalent, if you prefer no compose:

```bash
docker run --rm --shm-size=2g -e DURATION=300 \
  -v "$PWD/tests/lane-a-smoke.sh:/smoke.sh:ro" \
  drone-sim/lane-a:v1.16.0 bash /smoke.sh
```

Runs headless SITL for 5 minutes and asserts 24 populated `/fmu/out/*` topics, zero sensor
TIMEOUTs, moving telemetry, and aggregate real-time factor ≥ 0.95. Exits non-zero on any
miss. Reference: **RTF 1.0000 native · 0.9967 host podman · 0.9767 nested Docker**.

> **`--shm-size=2g` is required, not optional.** Docker defaults `/dev/shm` to 64 MB and
> Fast-DDS uses shared memory as its default transport; at the default it starves.

### 3. Poke at it interactively

With the stack up:

```bash
docker compose -f docker/compose.yaml exec px4-sitl screen -r px4sitl   # live PX4 shell
docker compose -f docker/compose.yaml exec ros2 bash -lc 'ros2 topic list | grep /fmu/out'
```

> **Use `bash -lc`.** `docker compose exec` bypasses the image entrypoint, so a plain
> `exec ros2 ros2 topic list` runs without a ROS environment and reports **0 topics on a
> perfectly healthy stack**. The login shell picks up `/etc/profile.d/10-ros.sh`.

> **`stty min 1` is required.** PX4's `pxh` shell clears `ICANON` without setting `VMIN`, so
> with a pipe or a `screen` pty it busy-spins its prompt — ~1.45 M writes/s, **4.1 GB of
> escape codes per 300 s run** and a fully consumed CPU core.

### 4. Watch it fly — record a video

Lane A runs headless by design: the smoke test starts the Gazebo *server* only, and the
interactive path gives you the PX4 shell but no windows. The `recording` profile renders
all four interfaces onto a virtual display and captures them.

**This is fully repeatable — it is a compose profile, not a one-off.**

```bash
# one-time: the demo image adds ffmpeg, xterm, xdotool and openbox (~1 min, layers cached)
docker build -f docker/demo/lane-a-video.Dockerfile -t drone-sim/lane-a-video:v1.16.0 .

# with the stack already up (step 2), record a flight
docker compose -f docker/compose.yaml --profile record run --rm recording
```

You get **`out/quad-flight.mp4`** — 1920x1080, ~37 s, four panes:

| Pane | Shows |
|---|---|
| top-left | **Gazebo**, camera locked onto the drone for the whole flight |
| top-right | **QGroundControl** — *Flying*, altitude, battery, live telemetry |
| bottom-left | the real **PX4 console** |
| bottom-right | the **ROS 2 controller** stepping arm → takeoff → waypoints → land |

Knobs, all optional:

```bash
RES=1280x720 ALT=5 FOLLOW_X=-2 FOLLOW_Y=-2 FOLLOW_Z=1 \
  docker compose -f docker/compose.yaml --profile record run --rm recording
```

`RES` capture size · `ALT` takeoff altitude · `FOLLOW_*` camera offset from the drone in
metres.

**It attaches to the running stack** rather than starting its own PX4 — the Gazebo GUI
client talks to the live server, the PX4 pane tails the real console, and the flight is the
same ROS 2 node the acceptance gate runs. So the video is evidence about the deployment you
are testing, not about a private copy of it.

**It exits non-zero if no successful flight happened**, and clears the previous run's
artifacts first. That matters: an early version cheerfully produced a convincing 1080p
video of a simulator that never armed, and reported success. Check the exit status, or read
`out/mission-result.json`.

> **Recordings, not live windows.** Everything renders to Xvfb inside the container, so you
> get an `.mp4`, not something you can click. Attaching a browser-reachable viewer
> (x11vnc + noVNC on the same display) is `D-02b` in
> [`docs/docker/todo.md`](docs/docker/todo.md).

Details and the gotchas that cost time: [`docker/demo/README.md`](docker/demo/README.md).

### Ports

All four are published on **`127.0.0.1` only**.

| Port | Use |
|---|---|
| 18570/udp | **GCS datalink — PX4's actual GCS MAVLink port** (`px4-rc.mavlink:11`) |
| 14550/udp | the port a ground station conventionally listens on |
| 14540/udp | offboard + programmatic control |
| 8888/udp | uXRCE-DDS agent (→ ROS 2 topics) |
| 4560/tcp | Gazebo ↔ PX4 |

> **18570, not 14550.** PX4 SITL's GCS MAVLink instance binds `18570+instance`. Nothing is
> ever sent to 14550 — verified by binding it and receiving nothing while PX4 already held
> 18570. A heartbeat aimed at 14550 is discarded silently, with no error and no log line,
> and the datalink simply never comes up.

> **MAVLink has no authentication.** Published on `0.0.0.0`, port 14540 lets anyone routable
> arm and command the vehicle. To reach the stack from another machine, opt in explicitly:
> ```bash
> BIND_ADDR=0.0.0.0 docker compose -f docker/compose.yaml up -d
> ```
> Do that on a trusted network only — and note that this compose file is the template for
> Phase 4 hardware bring-up, where the same setting points at a real Pixhawk.

---

## Status

**Phase 0 — Environment & Version Lock.** 4 of 5 exit criteria met.

| Lane | Stack | Role | Status |
|---|---|---|---|
| **A** | PX4 v1.16.0 + Gazebo Harmonic + ROS 2 Jazzy | CI/iteration backbone | ✅ **working, smoke-tested** |
| **B** | Isaac Sim 5.1 + Pegasus | photoreal perception | ⛔ **deferred** — [why](docs/lane-b/isaac-driver-decision.md) |
| **C** | UE5.5 + Cosys-AirSim | photoreal perception + benchmark reproduction | **promoted**, next up |

Lane A verified end to end: headless SITL for 300 s with **0 sensor TIMEOUTs**, real-time
factor **1.000**, **24 `/fmu/out/*` topics at 100 Hz**, and QGroundControl connected.

---

## Layout

```
versions.lock          pinned SHAs + the couplings CI must assert — the authority
.repos                 vcstool manifest for third-party trees (phase-gated)
docker/                compose.yaml + the Lane A Dockerfile + entrypoint
docker/demo/           video/flight demos (not needed by CI)
tests/                 lane-a-smoke.sh — the acceptance gate
ros2_ws/src/           the original glue: interfaces, bringup, perception,
                       state_estimation, planning, control, vlm_client, evaluation
sim/{gazebo,isaac,ue5} per-lane worlds and scenes
scenarios/             seeded worlds + instruction sets
configs/               per-lane YAML overrides
vendor/                pinned upstream checkouts (git-ignored; see .repos)
docs/                  bench briefing, reference designs, backlogs, worklogs
```

## Where to start

| Doc | What it is |
|---|---|
| [`docs/roadmap.html`](docs/roadmap.html) | **Phases and timeline** — what is done, what is next, what is deferred and why |
| [`docs/drone-sim-todo.md`](docs/drone-sim-todo.md) | Master backlog index — start here |
| [`versions.lock`](versions.lock) | Every pin, its status, and how it was verified |
| [`docs/lane-a/architecture.html`](docs/lane-a/architecture.html) | **What runs where and how it is wired** — container topology, ports, the traps |
| [`docs/bench.md`](docs/bench.md) | The machine and container this runs on |
| [`docs/reference/`](docs/reference/) | Simulator landscape, development plan, hardware assessment |
| [`docs/worklog/`](docs/worklog/) | Running record of each investigation, with evidence |

---

## Local CI

```bash
./scripts/run_local_ci.sh          # fast checks, ~30 s
./scripts/run_local_ci.sh --gate   # + the 10-seed flight gate, ~19 min
```

The fast checks are the same ones GitHub Actions runs on every push, so a local pass means
the same thing. **The flight gate is not automated** — it cannot run on a hosted runner
(12.6 GB image, 2 vCPU against an RTF floor of 0.95), and a self-hosted runner on a public
repo would let fork pull requests execute on the machine. Running it here is accepted as
having run it; see `P1-07` and `D-07`.

Skipping `--gate` is fine for docs or tooling. It is **not** fine for the controller, the
scenario runner, the overlay or the compose stack — nothing else in this repo would catch a
regression there.

## Verifying changes

A clean build proves nothing about flight. Behaviour is verified by **running it in the
target lane** — headless SITL on a seeded scenario, with the evidence recorded (MCAP bag,
metric table, measured latency). A success rate over N seeded runs, never a single pass.
