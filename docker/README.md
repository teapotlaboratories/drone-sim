# `docker/` — the six images the simulator runs on

**There is no compose file, and never was one for this stack.** Bring-up is
[`../scripts/sim_up.sh`](../scripts/sim_up.sh) driving raw `docker run`. That is deliberate:
every service shares the renderer's network and IPC namespaces — which compose could
express — but the bring-up also has to *sequence* a settle-then-verify step around PX4's EKF
origin, and a second, half-correct path is worse than none.

```bash
./scripts/sim_up.sh                                  # the whole stack, ~80 s
./scripts/sim_up.sh --world /path/to/Your.uproject   # your own Unreal world
```

## The images

| Image | Dockerfile | Base | Role |
|---|---|---|---|
| `drone-sim/px4:v1.16.0` | `px4.Dockerfile` | `ubuntu:24.04` | PX4 v1.16.0 SITL + ROS 2 Jazzy + the uXRCE-DDS agent + branch-matched `px4_msgs`. **11.0 GB.** Base of the next three |
| `drone-sim/ros2:v1.16.0` | `ros2.Dockerfile` | `drone-sim/px4` | the companion-computer image — where every ROS 2 node and the Cosys-AirSim wrapper run |
| `drone-sim/qgc:v1.16.0` | `qgc.Dockerfile` (+ `qgc-entrypoint.sh`) | `drone-sim/px4` | QGroundControl headless — the **only** component that speaks MAVLink over IP |
| `drone-sim/video:v1.16.0` | `video.Dockerfile` | `drone-sim/px4` | a thin ffmpeg layer, used only to re-encode renders |
| `drone-sim/unreal:ue5.8` | `unreal.Dockerfile` | `ghcr.io/epicgames/unreal-engine` (by **digest**) | the renderer — Epic's UE5.8 image plus the three tools Cosys-AirSim's `build.sh` needs and `dev-slim` lacks (`cmake`, `rsync`, `wget`). The Cosys-AirSim tree itself is **mounted from `vendor/` at run time**, not baked in |
| `drone-sim/airsim-client:1` | `airsim-client.Dockerfile` | `python:3.11-slim` | 0.6 GB AirSim RPC client for measurement — capture harness, not flight |

`versions.lock` is the authority on that list: `scripts/check_image_refs.py` fails tier-1 CI
if any `drone-sim/...` reference in the repo names an image not declared there.

The only `COPY`'d scripts are `px4-entrypoint.sh` (sources ROS 2 + the workspace),
`qgc-entrypoint.sh`, and `ros-profile.sh` (baked to `/etc/profile.d/10-ros.sh`). Everything
else is bind-mounted at run time, so editing a script does not trigger an 11 GB rebuild.

## What each image no longer carries, and why that is load-bearing

- **`px4.Dockerfile` no longer installs Gazebo.** `Tools/setup/ubuntu.sh --no-sim-tools`,
  plus an explicit reinstall of the build deps that flag drops which are *not* Gazebo (`bc`,
  `libeigen3-dev`, `protobuf-compiler`, `pkg-config`, `libxml2-utils`). **MEASURED: 11.6 GB
  with Gazebo, 11.0 GB without**, and the build then asserts `gz-harmonic` is absent rather
  than trusting the flag. **NuttX is still installed on purpose** — real Pixhawk 6C firmware
  is flashed from that same tree, so `--no-nuttx` would slim the image while silently
  removing a capability.
- **`ros2.Dockerfile` no longer installs `ros-gz-bridge`.** It has no consumer: `/clock`
  comes from the simulator itself, remapped in
  `ros2_ws/src/bringup/launch/perception.launch.py` (`/airsim_node/clock` → `/clock`). The
  removal is worth more than the disk it frees — `ros-gz-bridge` pulls `gz_transport_vendor`,
  whose `libgz-transport13` lands on `LD_LIBRARY_PATH` for every process that sources ROS and
  silently breaks the `gz` CLI. In its place the image bakes in what the AirSim wrapper needs
  (`geographic_msgs`, `mavros_msgs`, `python3-msgpack`, `patch`), because those used to be
  apt-installed **inside a running container** on every bring-up: the dependency lived in a
  writable layer, vanished on teardown, and a network outage between two runs turned a
  working stack into a build failure.
- **`video.Dockerfile` no longer carries Xvfb, xterm, xdotool or openbox.** Those served the
  retired Gazebo demo, which rendered four GUI panes onto a virtual display and screen-recorded
  them. Frames now come from the simulator's own capture and from recorded MCAP bags, so the
  render path is offline and needs an encoder, not a window manager. It asserts `libx264` is
  present, not merely that `ffmpeg` installed.

## The containers `sim_up.sh` starts

**Four, and the rule is one container per machine that will exist when this flies for real.**

| Container | Image | Real counterpart | Role |
|---|---|---|---|
| `sim-unreal` | `drone-sim/unreal:ue5.8` | none — it *is* the world | the renderer, on **GPU 0**. Donates its network namespace, IPC namespace and `/dev/shm` to the rest |
| `sim-px4` | `drone-sim/px4:v1.16.0` | **Pixhawk 6C** | PX4 SITL, airframe 10016, talking the Simulator MAVLink API to the renderer |
| `sim-ros2` | `drone-sim/ros2:v1.16.0` | **Jetson Orin NX** | the uXRCE-DDS bridge, this repo's reference nodes (`interfaces`/`control`/`bringup`) and the AirSim wrapper. **User code does not go here** — it attaches from outside with `scripts/attach.sh` |
| `sim-qgc` | `drone-sim/qgc:v1.16.0` | **ground station** | the GCS datalink. **Load-bearing, not a viewer** |

**PX4 stays separate because that boundary is the sim-to-real claim.** On the aircraft PX4 runs
on its own flight-controller board and reaches the companion over a UART; sim and real differ
*only* by the transport across that line. Folding PX4 into `sim-ros2` would erase it in the
layout.

**The agent is not separate, because it is plumbing.** `MicroXRCEAgent` is what makes `/fmu/*`
appear; nothing connects to it directly, and on the Jetson it is a process beside the ROS 2
nodes rather than a machine. It had its own `sim-xrce` container until 2026-08-04 — a boundary
that exists nowhere in the real system.

It now runs inside `sim-ros2` under a **supervising subshell**, not `agent &` and not
`exec agent`. Both of those were measured and both are silently wrong:

| Shape | What a crash does |
|---|---|
| `agent & … exec sleep infinity` | `exec` replaces the parent, so nothing reaps: the dead agent becomes a **zombie** that `pgrep -f` still matches. Healthcheck green, bridge dead. `--init` does not fix it |
| `exec MicroXRCEAgent` as PID 1 | takes the container down with it — the ROS 2 workspace **and** any in-flight `docker exec`, destroying the MCAP that is a failing run's only evidence |
| **supervising loop** (what is used) | loop stays the parent, so it reaps and restarts. Verified: agent SIGKILLed, back in ~2 s with a new pid, container still `Running`, **0 zombies**, restart logged |

That is strictly more than the separate container had — this stack has never carried a restart
policy on anything.

> **Two reasons never to health-check the agent by its process.** `MicroXRCEAgent` **exits 0 on
> a bind failure**, and `pgrep -f MicroXRCEAgent` **also matches the supervising loop**, whose
> command line contains the string. Both report success over a dead bridge. Assert on the data:
> `wait_for_fmu` requires real `/fmu/out` topics with a **finite** EKF origin.

`sim-xrce` is still in both teardown lists on purpose — a stale one from an older checkout would
hold `udp/8888`, and the new agent exits 0 when it cannot bind.

Volume `sim-ddc` holds Unreal's derived-data cache (`/home/ue4/.config/Epic`), so a second
run does not recompile shaders.

**Four constraints encoded in that script — each cost a debugging session:**

- **`--ipc shareable` on `sim-unreal`, `--ipc container:sim-unreal` on every joiner.**
  Fast-DDS discovers over UDP but **delivers over shared memory**. Sharing the *network*
  namespace alone gets you topics in `ros2 topic list` and **nothing** from `ros2 topic echo`.
- **`--shm-size=2g` goes with it.** Docker defaults `/dev/shm` to 64 MB, and because that
  container donates its namespace, 64 MB would be the whole stack's shared-memory budget.
  The failure mode is silent starvation under load, not a clean error.
- **`bash -lc` for `docker exec`.** Without a login shell nothing sources ROS, and
  `docker exec sim-ros2 ros2 topic list` reports 0 topics on a perfectly healthy stack.
  `ros-profile.sh` exists so callers do not each repeat the source lines.
- **QGC is a functional dependency of flight.** PX4 refuses to arm without a GCS datalink
  (`NAV_DLL_ACT=2`, set by the airframe), and that check is left **enforced** because a real
  Pixhawk enforces it. Stop `sim-qgc` and arming is denied — verified in both directions.

**No ports are published.** Every service joins the renderer's network namespace, so there
is nothing bound on the host to reach; use `docker exec`. MAVLink is unauthenticated, and
publishing 14540 on a LAN would let anyone routable arm the vehicle.

## The one credential step

`drone-sim/unreal:ue5.8` builds `FROM ghcr.io/epicgames/unreal-engine@sha256:…`, which is
**credential-gated**: it needs EpicGames GitHub org membership plus a PAT with
`read:packages`. This is a real, documented gap against "a fresh machine reaches a working
stack from this repo alone", and it is stated rather than hidden. Log in first:

```bash
gh auth token | docker login ghcr.io -u <user> --password-stdin
```

The image is pinned **by digest, not by tag** — `dev-slim-5.8` is a moving alias, exactly as
a git branch is not a pin. Background: [`../docs/docker/todo.md`](../docs/docker/todo.md).

**This box runs Docker nested inside a rootless-podman distrobox.** The `fuse-overlayfs`
storage driver and the `/etc/cdi-local` CDI spec are load-bearing workarounds — do not
change them unless GPU-in-Docker is already broken (`docs/bench.md`).
