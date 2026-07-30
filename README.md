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
```

Pulls PX4 v1.16.0 + submodules, Gazebo Harmonic, ROS 2 Jazzy, the uXRCE-DDS agent and
branch-matched `px4_msgs`, then **verifies every pin against its recorded SHA** and fails the
build on a mismatch. Expect 20–40 minutes and ~11.6 GB; the pins land in
`/etc/drone-sim-versions` inside the image.

### 2. Prove it flies (the acceptance test)

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

```bash
docker run --rm -it --shm-size=2g drone-sim/lane-a:v1.16.0
# inside:
screen -dmS px4sitl bash -c "stty min 1 time 0; cd /opt/px4 && HEADLESS=1 GZ_IP=127.0.0.1 make px4_sitl gz_x500"
screen -r px4sitl        # live PX4 shell:  commander status, param show, ...
ros2 topic list | grep /fmu/out    # the 24 topics the ROS 2 graph attaches to
```

> **`stty min 1` is required.** PX4's `pxh` shell clears `ICANON` without setting `VMIN`, so
> with a pipe or a `screen` pty it busy-spins its prompt — ~1.45 M writes/s, **4.1 GB of
> escape codes per 300 s run** and a fully consumed CPU core.

### 4. Watch it fly (optional)

**What steps 2–3 do and do not show you.** Lane A runs headless by design: the smoke test
starts the Gazebo *server* only, and the interactive path gives you the **PX4 CLI** but no
Gazebo window and no QGroundControl. To see the simulator, use the demo image — it renders
the GUIs onto a **virtual display** and records them to video:

```bash
# one-time, ~1 min (base layers are cached)
docker build -f docker/demo/lane-a-video.Dockerfile -t drone-sim/lane-a-video:v1.16.0 .

# arm → take off to 10 m → hover → land, recording four panes side by side
mkdir -p out
docker run --rm --shm-size=2g -e OUTDIR=/out -e RES=1920x1080 -e ALT=10 -e HOVER_S=45 \
  -v "$PWD/out:/out" \
  -v "$PWD/docker/demo/lane-a-record-quad.sh:/record.sh:ro" \
  -v "$PWD/docker/demo/lane-a-fly.py:/fly.py:ro" \
  -v "$PWD/vendor/tools/QGroundControl.AppImage:/qgc.AppImage:ro" \
  drone-sim/lane-a-video:v1.16.0 bash /record.sh
# -> out/quad-flight.mp4   (Gazebo GUI · QGroundControl · PX4 CLI · MAVLink script)
```

Single-pane variant: swap `lane-a-record-quad.sh` for `lane-a-record-flight.sh` and drop the
QGC mount.

**QGroundControl is not in the image** (172 MB, and CI has no use for it) — fetch it once to
`vendor/tools/QGroundControl.AppImage` and bind-mount it as above.

> **These are recordings, not live windows.** Everything renders to Xvfb inside the
> container, so you get an `.mp4` rather than an interactive GUI. Attaching a live viewer
> (x11vnc + noVNC on the same virtual display) is not wired up yet — see `D-02` in
> [`docs/docker/todo.md`](docs/docker/todo.md).

Details and the seven gotchas that cost time: [`docker/demo/README.md`](docker/demo/README.md).

### Ports

| Port | Use |
|---|---|
| 14550/udp | QGroundControl / GCS |
| 14540/udp | offboard + programmatic control |
| 8888/udp | uXRCE-DDS agent (→ ROS 2 topics) |
| 4560/tcp | Gazebo ↔ PX4 |

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
docker/                per-service Dockerfiles + compose
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
| [`docs/drone-sim-todo.md`](docs/drone-sim-todo.md) | Master backlog index — start here |
| [`versions.lock`](versions.lock) | Every pin, its status, and how it was verified |
| [`docs/bench.md`](docs/bench.md) | The machine and container this runs on |
| [`docs/reference/`](docs/reference/) | Simulator landscape, development plan, hardware assessment |
| [`docs/worklog/`](docs/worklog/) | Running record of each investigation, with evidence |

---

## Verifying changes

A clean build proves nothing about flight. Behaviour is verified by **running it in the
target lane** — headless SITL on a seeded scenario, with the evidence recorded (MCAP bag,
metric table, measured latency). A success rate over N seeded runs, never a single pass.
