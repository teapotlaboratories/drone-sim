# drone-sim

**A photoreal drone simulator you fly over ROS 2** — Unreal Engine 5.8 + Cosys-AirSim renders
the world and the sensors, PX4 v1.16 SITL flies the aircraft, and ROS 2 Jazzy is the only
control interface. Bring your own Unreal world, place the vehicle in it, choose which sensors
exist and how they are tuned, and fly **the same ROS 2 graph you would fly on real hardware**.

Everything here is SITL. The `sim ↔ real` boundary in this project is the *transport swap*,
not the commands — which is why the controller that flies here is the controller that flies on
a Pixhawk 6C.

---

## Goals, in order

### 1. A photoreal simulator that flies, on your world

Not a demo world with a drone bolted on. `scripts/sim_up.sh --world YourProject.uproject
--spawn 50,-30,-10` loads a project you authored, puts the vehicle where you asked, and hands
back a stack whose EKF origin has been **verified rather than assumed**. Imagery is
photorealistic on a **stock upstream plugin** — `simGetImages` matches Unreal's own render of
the same camera actor at the same transform to **1.15 of 255**, across six scenes from close-up
to 70 m.

### 2. Reproducible as Docker

> A fresh machine must reach a working stack from this repository alone — **no undocumented
> manual steps, no "it works on `carbonite`".**

This is a first-class goal, not packaging polish. The stack is an assembly of pinned upstreams
whose interactions are fragile, and the difference between "we got it working" and "anyone can
get it working" is entirely whether the recipe is captured.

Two rules follow from it, both learned the hard way here:

- **Pin what you actually built and smoke-tested — a SHA, never a branch.** A tagged upstream
  release in this stack became unbuildable retroactively because its superbuild pinned a
  dependency by *branch* and that branch was deleted (`fatal: invalid reference: 2.12.x`).
  A branch is not a pin.
- **Write Dockerfiles from evidence, not from documentation.** Getting to a running
  `airsim_node` took **four undocumented discoveries**, every one of them now a comment in
  `scripts/build_airsim_wrapper.sh`; three of the steps that stood up the original Gazebo
  baseline likewise deviated from this project's own reference docs. A Dockerfile written from
  the docs reproduces a **broken** stack.

**One credential step cannot be removed from this side, and is stated up front rather than
buried:** the Unreal engine base image `ghcr.io/epicgames/unreal-engine` is credential-gated —
it needs EpicGames GitHub org membership **plus** a PAT with `read:packages`. Everything else
builds from a clone. Backlog: [`docs/docker/todo.md`](docs/docker/todo.md).

### 3. Reuse and integrate upstream — don't reinvent

PX4, the Micro-XRCE-DDS Agent, `px4_msgs`, Cosys-AirSim and QGroundControl are consumed as
**pinned upstreams**. The original work is the *glue*: the ROS 2 graph and its launch
composition, the bring-up ordering, the scenario/eval harness, the measurement scripts, and the
bring-your-own-world path. Vendored trees stay **byte-identical to upstream** — the three
patches this stack needs live in [`patches/cosys-airsim/`](patches/cosys-airsim/) and are
applied to a container-local copy, never to `vendor/`.

### 4. Sim-to-real parity — one ROS 2 graph, swap only the transport

Demonstrated, not argued: an **unmodified** offboard controller written against the Gazebo
baseline reached **4/4 waypoints, max error 0.79 m**, reproduced three times including once
from a cold start. The controller was never patched; only the transport was swapped. PX4's
topic surface is identical either way — 51 `/fmu/` topics, 24 `/fmu/out`, verified by diffing
rather than by inspection.

**What people build *on* this** — vision-based navigation, VLM agents, planners, perception
stacks, benchmark reproduction — are applications, not the repo's purpose. The deliverable is
the simulator.

---

## Quickstart

**Prerequisites.** Docker with GPU access, an NVIDIA GPU for the renderer, and disk: the engine
image is **24.0 GB compressed / ~57 GB on disk**, the PX4 image **11.0 GB**, the vendored
Cosys-AirSim tree ~1.3 GB. The renderer is pinned to **GPU 0** at the container boundary
(`--gpus '"device=nvidia.com/gpu=0"'`, in `sim_up.sh`) because `-RenderOffScreen` has
historically ignored application-level GPU hints; see [`docs/bench.md`](docs/bench.md) for the
render/infer split.

### 1. Authenticate, then build the images

```bash
gh auth token | docker login ghcr.io -u <github-user> --password-stdin   # EpicGames org + read:packages

docker build -f docker/px4.Dockerfile    -t drone-sim/px4:v1.16.0 .
docker build -f docker/qgc.Dockerfile    -t drone-sim/qgc:v1.16.0 .
docker build -f docker/ros2.Dockerfile   -t drone-sim/ros2:v1.16.0 .
docker build -f docker/unreal.Dockerfile -t drone-sim/unreal:ue5.8 .
```

The PX4 image pulls PX4 v1.16.0 plus submodules, the uXRCE-DDS agent and branch-matched
`px4_msgs`, then **verifies every pin against its recorded SHA** and fails the build on a
mismatch; the pins land in `/etc/drone-sim-versions` inside the image. Expect 20–40 minutes.

> **It no longer installs Gazebo.** `Tools/setup/ubuntu.sh --no-sim-tools` plus an explicit
> reinstall of the build dependencies that are *not* Gazebo (`bc`, `libeigen3-dev`,
> `protobuf-compiler`, `pkg-config`, `libxml2-utils`) — measured **11.6 GB → 11.0 GB**, and the
> build asserts Gazebo is absent rather than trusting the flag. **NuttX is still installed on
> purpose**: real Pixhawk 6C firmware is flashed from that tree.

**QGroundControl is required, not optional tooling.** PX4 refuses to arm without a
ground-station datalink and `NAV_DLL_ACT` is left enforced deliberately — a real Pixhawk
refuses too, so relaxing it in simulation would hide a real-flight failure. QGC is **baked into
the image**, pinned and SHA256-verified at build time, so a checksum mismatch fails the build
instead of surfacing later as a vehicle that will not arm.

The ROS 2 image is the companion-computer side: ROS 2 Jazzy plus the wrapper's build
dependencies (`ros-jazzy-geographic-msgs`, `ros-jazzy-mavros-msgs`, `python3-msgpack`, `patch`)
and `docker/ros-profile.sh` at `/etc/profile.d/10-ros.sh`. It deliberately carries **no
`ros-gz-bridge`** — `/clock` now comes from the simulator, remapped in `perception.launch.py`,
so the bridge has no consumer.

### 2. Fetch the pinned upstream trees and build the plugin once

```bash
vcs import vendor < .repos          # ~1.3 GB — Cosys-AirSim at the pinned SHA, not a branch

docker run --rm -v "$PWD/vendor/Cosys-AirSim:/src" drone-sim/unreal:ue5.8 \
  bash -lc './build.sh --ue-root /home/ue4/UnrealEngine'
```

> **`--ue-root` is mandatory, not advisory.** The engine image ships **no system clang** — the
> compiler is the engine's bundled `v26_clang-20.1.8-rockylinux8`, and a build without
> `--ue-root` has no compatible compiler at all rather than a graceful fallback. Verified on the
> artifact rather than on the build script's own banner: `readelf -p .comment libAirLib.a`
> reads `clang version 20.1.8`. Detail:
> [`docs/worklog/2026-08-01-c02-ue58-engine-image.md`](docs/worklog/2026-08-01-c02-ue58-engine-image.md).

To fly a world of your own, inject the plugin into it once with
`scripts/inject_airsim.py /path/to/YourProject.uproject` — pure text edits plus a folder copy,
no editor, no GUI, no display.

### 3. Bring the stack up

```bash
./scripts/sim_up.sh
```

**Four containers**, brought up **in the only order that works**. It waits for the vehicle to
settle, then **verifies the EKF origin** before declaring the stack usable, printing
`stack up and origin verified -- safe to fly` in roughly 80 s.

#### Why four, and why these four

**Each container is one machine that will exist when this flies for real.** That is the whole
rule, and it is worth stating because the obvious alternatives are both wrong: one container
is not enough, and a container per process is too many.

| Container | Image | Real counterpart |
|---|---|---|
| `sim-unreal` | `drone-sim/unreal:ue5.8` | **none** — it *is* the world. Also the namespace donor (see below) |
| `sim-px4` | `drone-sim/px4:v1.16.0` | the **Pixhawk 6C** — PX4 firmware, on its own board |
| `sim-ros2` | `drone-sim/ros2:v1.16.0` | the **Jetson Orin NX** — the uXRCE-DDS bridge and this repo's reference nodes. **Not where your code goes** — see below |
| `sim-qgc` | `drone-sim/qgc:v1.16.0` | the **ground station** — the only MAVLink-over-IP client |

**Why PX4 is separate.** On the aircraft PX4 runs on dedicated flight-controller hardware and
reaches the companion computer over a **UART**. That boundary is the entire sim-to-real claim:
sim and real differ *only* by the transport across it. Folding PX4 into the companion container
would erase in the layout the one line the design rests on.

**Why the agent is *not* separate.** The uXRCE-DDS agent is **plumbing, not a service** — it is
what makes `/fmu/*` appear, and nothing connects to it directly; your code talks ROS 2. On the
Jetson it is simply a process beside your nodes. It had its own container until 2026-08-04, and
that modelled a boundary which does not exist anywhere in the real system. It now runs inside
`sim-ros2` under a supervising loop that restarts it and prefixes its output `[xrce]`, which is
**more** than it had before: this stack has never carried a restart policy on anything.

> **Never health-check the agent by looking for its process.** Two independent traps, both
> measured here:
> - `MicroXRCEAgent` **exits 0 when it fails to bind**, so "it started and returned success"
>   is compatible with no bridge at all.
> - `pgrep -f MicroXRCEAgent` **matches the supervising loop as well as the agent** — the
>   loop's own command line contains the string. It reports a hit whether or not the agent is
>   alive.
>
> What proves the bridge is up is the bring-up's `wait_for_fmu`: real `/fmu/out` topics
> carrying a **finite** EKF origin. **Assert on the data, never on the process.**

#### Your code does not live in any of them

**The simulator does not host your application.** `sim-ros2` runs the uXRCE-DDS bridge and this
repo's reference nodes — `interfaces`, `control`, `bringup` — which exist to prove the graph
works, not to be where you build. Your autonomy code stays yours: your image, your workspace,
your branch. It attaches to the running graph from outside:

```bash
./scripts/attach.sh --image my/autonomy:latest ros2 run my_pkg my_node
./scripts/attach.sh                 # or just an interactive shell, ROS 2 already sourced
```

**A native `ros2` install needs no configuration at all.** A process in its own namespaces
already receives the whole graph — Fast-DDS falls back to UDP on its own. No DDS profile, no
published ports, no flags. All it needs is `px4_msgs` (below).

> **The one combination that does NOT work is sharing the network namespace alone** — and it
> is worse than sharing nothing. Measured against a live stack, same subscriber, three ways:
>
> | attach | messages received |
> |---|---|
> | no namespaces shared (fully separate) | **3** ✓ |
> | `--network container:sim-unreal` | **0** ✗ |
> | `--network …` **and** `--ipc container:sim-unreal` | **3** ✓ |
>
> Sharing the netns makes Fast-DDS see the peer as same-host, so it picks the shared-memory
> transport — but `/dev/shm` is still your own, and nothing is delivered. `ros2 topic list`
> shows all 51 topics throughout. **Share both, or share neither.** `attach.sh` shares both.

Use `attach.sh` when you want the shared-memory path (large image topics) or a shell with the
stack's environment ready; run natively when you would rather not containerise.

Two things your image needs: **`px4_msgs` built from the same branch as the firmware**
(`release/1.16` — base on `drone-sim/ros2:v1.16.0` and you get it), and **`BEST_EFFORT` +
`TRANSIENT_LOCAL` QoS** on `/fmu/out/*`, because a default `RELIABLE` subscription matches
nothing and reads as silence. Both are in [`docs/conventions.md`](docs/conventions.md).

**Why QGroundControl is a container and not an afterthought.** PX4 refuses to arm without a GCS
datalink (`NAV_DLL_ACT=2`), and that check is deliberately left **enforced** because a real
Pixhawk enforces it. Stop `sim-qgc` and arming is denied — verified in both directions. It is a
functional dependency of flight, not a viewer.

**The one boundary that is admittedly wrong.** `sim-unreal` donates the network and IPC
namespaces every other container joins, so the renderer — the one component with *no* hardware
analogue — is what the whole stack structurally depends on. That inverts reality, and it means
the split does not buy isolation: it is one machine wearing four hats. Tracked as `D-06` in
[`docs/docker/todo.md`](docs/docker/todo.md); the namespace sharing is load-bearing today
because Fast-DDS delivers over shared memory (see *Network and ports* below).

> **Why it verifies rather than just waits.** PX4 sets its EKF local origin **once**. If it
> initialises before the simulated vehicle has settled onto geometry, `ref_alt` freezes at the
> wrong height and every altitude PX4 reports is silently offset for the rest of the session —
> measured once at **35.167 m**, i.e. the vehicle "was" 35 m up while sitting on the ground, the
> controller commanded a descent, nothing moved, and every symptom pointed at flight code that
> was fine. That cost a day. If the origin is stale the script restarts PX4 and re-checks; a
> stack it cannot repair is refused, and `run_gate.py` scores such runs **VOID**, never FAIL —
> they never measured the flight code.

| flag | what it does |
|---|---|
| `--world PATH.uproject` | load **your own** Unreal world instead of the bundled Blocks environment |
| `--settings PATH.json` | your own `settings.json` — which sensors are active and how they are tuned |
| `--spawn X,Y,Z[,YAW]` | where to put the vehicle, in metres **NED** |
| `--vehicle NAME` | required only if your settings define several vehicles |
| `--allow-below-origin` | permit a positive `Z` (i.e. genuinely below the origin) |

Each has an environment equivalent: `WORLD`, `SETTINGS_FILE`, `SPAWN`, `SPAWN_VEHICLE`,
`SPAWN_ALLOW_BELOW`. **`Z` is NED — negative is UP**; `Z=10` puts the drone 10 m *underground*,
which is why the script refuses a positive `Z` without the opt-in. The committed
`sim/ue5/settings.json` is never modified: a run-time copy is written beside it.

### 4. Build the wrapper and start the perception graph

```bash
./scripts/build_airsim_wrapper.sh      # ~2 min

docker exec -d sim-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 launch bringup perception.launch.py'
```

> **The wrapper must be rebuilt after every `sim_up.sh`** — that script recreates the ROS 2
> container, which is where the wrapper lives. If `ros2 launch` reports
> `package 'airsim_ros_pkgs' not found`, this is why.
>
> **Use `bash -lc`.** `docker exec` bypasses the image entrypoint, so a plain
> `docker exec sim-ros2 ros2 topic list` runs without a ROS environment and reports **0 topics
> on a perfectly healthy stack**. The login shell picks up `/etc/profile.d/10-ros.sh`.

### 5. Fly it, and keep the evidence

```bash
./scripts/run_park_tour.sh                                    # Blocks — the known-good control
./scripts/run_park_tour.sh --world /path/CityPark.uproject \
    --spawn 50,-30,-10 --mode circle --radius 25 --altitude 8
```

This is the end-to-end example: it brings the stack up itself (steps 3 and 4), starts the bag
**before** the mission node and stops it after, flies a closed circuit using **only** the ROS 2
interface — no RPC, no MAVLink — then lands and reports a verdict.

```
out/park-tour-<UTC>/
  park-tour_0.mcap   every /fmu/out/*, /airsim_node/*, /tf and /clock for the whole run
  metadata.yaml      ros2 bag's own
  summary.json       waypoints, per-leg error, verdict
  mission.log        the node's stdout
  stack.log          bring-up, for when a run dies before it flies
```

`--mode circle` streams a continuously moving setpoint with velocity feed-forward, so PX4 tracks
a smooth arc instead of braking at every corner. `scripts/render_run_video.py` and
`scripts/plot_run_path.py` derive an mp4 and a ground-track plot **from the bag**, so the picture
and the verdict come from the same evidence and cannot drift apart.

### 6. Verify by value, not by topic list

```bash
docker cp scripts/verify_sensors.py sim-ros2:/tmp/verify.py
docker exec sim-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  python3 /tmp/verify.py'
```

Every failure this project has hit here **looked healthy from the outside**: topics listed while
publishing nothing because the subscriber QoS did not match, an IMU at 1501 Hz of which 78% were
the same sample republished, a `camera_info` whose `frame_id` no TF-aware node could resolve, a
stale origin reporting 35 m of rock-steady altitude with `z_valid: true`. So the check asserts
**values** — an all-black camera and a working one both publish an image, and only one has pixel
variance.

### Network and ports

**By default `sim_up.sh` publishes nothing to the host.** The renderer owns the network namespace
and every other container joins it with `--network container:` **and** `--ipc container:` — sharing
the network namespace alone gets you topic names in `ros2 topic list` and **silence** from
`ros2 topic echo`, because Fast-DDS discovers over UDP but *delivers* over shared memory.

Inside that namespace: **4560/tcp** simulator ↔ PX4 MAVLink, **8888/udp** uXRCE-DDS agent,
**18570/udp** the GCS datalink, **14540/udp** offboard.

> **18570, not 14550.** PX4 SITL's GCS MAVLink instance binds `18570+instance`. A heartbeat
> aimed at 14550 is discarded silently, with no error and no log line, and the datalink simply
> never comes up — verified by binding 14550 and receiving nothing while PX4 already held 18570.

> **MAVLink has no authentication.** Nothing above is reachable from outside the namespace
> today, and that is the safe default. If you publish any of these ports onto a host interface,
> anyone routable can arm and command the vehicle — do that on a trusted network only, and note
> that the same setting later points at a **real** Pixhawk.

### Reaching the graph from another machine

Your autonomy computer is usually not this machine — it is a Jetson on the bench or a box across
the LAN. Two switches cover that, and **both are off by default**: the stack publishes nothing and
is reachable only from the host it runs on, because host mode exposes unauthenticated MAVLink and
that is a decision to make per network, not a default to inherit.

Which switch you need is decided by **one question: does the path between the two machines carry
UDP multicast?** DDS discovers over multicast (`239.255.0.1:7400`) by default, and a VPN or a routed
subnet almost never forwards it.

| Path between the machines | Switch | What the peer gets |
|---|---|---|
| Same host | *(default)* `NET_MODE=shared` | private namespace, nothing published |
| LAN that forwards multicast | `NET_MODE=host` | the whole graph, no DDS config |
| VPN / routed subnet — **no multicast** | `NET_MODE=host` + `DISCOVERY_SERVER=<ip>:<port>` | the whole graph over plain unicast |

```bash
# LAN
NET_MODE=host ./scripts/sim_up.sh

# VPN: run a discovery server anywhere both machines can reach, then point the stack at it
fastdds discovery -i 0 -l 0.0.0.0 -p 11811 &
NET_MODE=host DISCOVERY_SERVER=127.0.0.1:11811 ./scripts/sim_up.sh
```

`DISCOVERY_SERVER` changes only **how peers find each other** — it implies no network mode. You
still need `NET_MODE=host` for the stack to advertise a routable address rather than the
docker-bridge `172.17.0.2` that only this machine can reach.

On the **subscriber**, point at the same server and use the UDP-only profile:

```bash
export ROS_DISCOVERY_SERVER=<server-ip>:11811
export ROS_SUPER_CLIENT=true
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/configs/dds/udp-only.xml
```

Measured against a live stack, from a container sharing **no** namespaces with it and using **no**
multicast — the graph the peer sees is identical to the graph the stack sees (53 topics, 51
`/fmu/`), and both directions carry traffic:

```
stack → peer    1904 vehicle_local_position + 1904 sensor_combined   in 20 s
                ref_alt 123.285 m — matches the verified EKF origin
peer  → stack   5/5 commands published externally arrived inside the stack
```

> **`ROS_SUPER_CLIENT=true` is not optional, and omitting it looks like a total failure.** A plain
> discovery *client* is only told about participants it has already matched. `ros2 topic echo` has
> to resolve the message **type** from the graph *before* it can subscribe, so as a plain client it
> fails with `Could not determine the type for the passed topic` — with a healthy publisher sitting
> right there. `sim_up.sh` sets it for you; set it on your side too.

> **`ros2 node list` returns 0 for `/fmu/*` — in every mode, including plain multicast.** The
> uXRCE-DDS agent creates raw DDS participants without ROS 2 node metadata. Topics and data are
> fine; only the node listing is empty. Do not read it as a broken link.

> **The server is a rendezvous, not a relay.** Data still flows peer-to-peer, so the two machines
> need direct routable reachability — it removes the multicast requirement, not the routing one.

> Host mode puts PX4's **unauthenticated** MAVLink ports on every interface this machine has,
> including the VPN. Use it on a network you trust.

---

## Status

| Capability | State | Evidence |
|---|---|---|
| Flies over ROS 2 | ✅ | 4/4 waypoints, max error **0.79 m**, reproduced 3× including a cold start |
| `/fmu/*` parity with real hardware | ✅ | 51 `/fmu/` topics, 24 `/fmu/out` — diffed **identical** against the retired Gazebo baseline the controller came from |
| Sensors in the ROS 2 graph | ✅ | RGB, depth, GPU-LiDAR, IMU, GPS, magnetometer, odometry — all pass **value-based** checks |
| Photorealistic imagery | ✅ | matches Unreal's own render to **1.15 of 255** across six scenes, on a **stock** plugin |
| Bring your own world + deliberate spawn | ✅ | `--world` / `--spawn`, with a ground probe for unknown terrain |
| Deterministic bring-up | 🟡 | origin verified and repaired, or the run is refused — but verified **once**, not N cold starts in a row |
| Recorded example mission | ✅ | `scripts/run_park_tour.sh` — MCAP, `summary.json`, video, ground track |
| Flight gate | 🟡 | `scripts/run_gate.py` — success rate over N seeded runs, VOID excluded from the rate but blocking the criterion |
| Dynamic actors in the world | 📋 | the RPC surface (`simSpawnObject`, `simSetObjectPose`) is known live in this build; nothing spawned yet — and it needs **no** project C++ and no plugin change |
| Wind / environment control | 📋 | needs Cosys-AirSim's own wind API |

**Measured sensor throughput.** Rates are capped by `perception.launch.py`, not by the
hardware — imagery at **20 Hz**, LiDAR at **10 Hz** — and measured throughput sits at **94% and
100%** of those ceilings. The per-topic table, with types and measured rates, is in
[`docs/quickstart.md`](docs/quickstart.md).

### Known limits — measured, not guessed

- **Lockstep is dead code** in Cosys-AirSim: `initialize()` sets the flag and
  `openAllConnections()` clears it twice, so `"LockStep": true` is silently ineffective.
  **Every timing number here is free-running** — never quote an RTF from this stack as
  deterministic.
- **A seed controls the spawn pose and nothing else.** The retired Gazebo harness varied wind
  and vehicle mass through a generated world overlay; there is no equivalent yet. Ten seeded
  runs are closer to ten repeats — still worth running, since flaky failures surface under
  repetition, but **do not describe a gate run as covering varied conditions**.
- **Runs are not bit-reproducible.** Two back-to-back runs with identical config gave waypoint
  errors `[0.225, 0.104, 0.154, 0.204]` and `[0.118, 0.076, 0.158, 0.187]`. A failing seed
  cannot be replayed — which is exactly why every run keeps its MCAP.
- **Frames are NWU, not ENU**, despite the upstream documentation.
- **Video capture is latency-bound, not bandwidth-bound.** Every route funnels through a
  blocking GPU→CPU readback: **14.1 Hz at 960×540, 10.3 Hz at 1920×1080** — ~71 ms fixed plus
  ~5 ms/MB. GPU encode would sidestep it, but **NVENC cannot open a session on driver
  610.43.03**, and Isaac Sim is deferred on the same driver: two capabilities, one host-side
  decision. See [`docs/nvenc-driver-blocker.md`](docs/nvenc-driver-blocker.md).
- **One simulator segfault, n=1**, after ~57 minutes of continuous running. A deliberate
  90-minute soak of the *full* stack ran **74,253 captures with zero anomalies** and refuted
  both standing hypotheses. **Not reproduced is not fixed** — treat it as a rare,
  uncharacterised event, not a known ceiling.

---

## Layout

```
versions.lock            every pin, its status, and how it was verified — the authority
.repos                   vcstool manifest for the vendored upstream trees
docker/                  one Dockerfile per image — unreal, px4, ros2, qgc, video,
                         airsim-client — plus the two entrypoints and the ROS profile
scripts/sim_up.sh        the stack, in the only order that works
scripts/                 wrapper build, flight gate, scenario runner, the example mission,
                         and the measurement harnesses that produced the numbers above
ros2_ws/src/             the glue: interfaces (mission contracts), bringup (launch
                         composition), control (offboard + the park tour); perception,
                         state_estimation, planning and evaluation are placeholders
sim/ue5/                 settings.json — which sensors exist and how they are tuned,
                         plus worked examples
scenarios/               seeded mission definitions the gate runs
patches/cosys-airsim/    three upstream defects, applied to a container-local copy only
tests/                   off-target tests — the tier-1 CI suite
vendor/                  pinned upstream checkouts (git-ignored; see .repos)
out/                     run artifacts — MCAP, summary.json, video (git-ignored)
docs/                    quickstart, the backlog, graph conventions, bench briefing,
                         worklogs, and the retired stacks under history/
```

## Where to start

| Doc | What it is |
|---|---|
| [`docs/quickstart.md`](docs/quickstart.md) ([HTML](docs/quickstart.html)) | **Run it** — launch, world selection, sensor selection and tuning, the topic/type/rate table, and the ROS 2 command interface |
| [`docs/todo.md`](docs/todo.md) | **The backlog** — every `SIM-NN` with its acceptance criterion and its evidence. The one cross-cutting area keeps its own file: [`docs/docker/todo.md`](docs/docker/todo.md) |
| [`docs/roadmap.html`](docs/roadmap.html) | Where the simulator is and which capability comes next |
| [`docs/conventions.md`](docs/conventions.md) | The **frozen** ROS 2 graph — these names reach the aircraft unchanged |
| [`versions.lock`](versions.lock) | Every pin, its status, and how it was verified |
| [`docs/bench.md`](docs/bench.md) | The machine and container this runs on, and the GPU split |
| [`docs/nvenc-driver-blocker.md`](docs/nvenc-driver-blocker.md) | Why GPU video encode is unreachable on this driver |
| [`docs/worklog/`](docs/worklog/) | Dated record of each investigation, with the evidence and the dead ends |
| [`docs/history/`](docs/history/) | Retired backlogs and design docs — the Gazebo baseline, Isaac Sim, and the original research plan |

---

## Local CI

```bash
./scripts/run_local_ci.sh          # fast checks, ~30 s
./scripts/run_local_ci.sh --gate   # + the seeded flight gate
```

The fast checks are the same ones GitHub Actions runs on every push, so a local pass means the
same thing: off-target tests, shell and Python parse checks, **every `drone-sim/…` image
reference names an image declared in `versions.lock`** (`scripts/check_image_refs.py`), `.repos`
agrees with the lock, every worklog has an HTML render and an index card, the attribution sweep
over every tracked file (`scripts/check_attribution.sh`), and every `versions.lock` CONFLICT is
documented.

**The flight gate is not automated.** It cannot run on a hosted runner — it needs a GPU and tens
of gigabytes of images, and the retired Gazebo gate already missed that budget without one
(12.6 GB image, 2 vCPU against an RTF floor of 0.95) — and a self-hosted runner on a public repo
would let fork pull requests execute on the workstation. Running it here is accepted as having
run it.

Skipping `--gate` is fine for docs or tooling. It is **not** fine for the controller, the
scenario runner, the bring-up ordering or the gate itself — nothing else in this repo would
catch a regression there.

## Verifying changes

A clean build proves nothing about flight. Behaviour is verified by **running it in the
simulator** — headless, on a seeded scenario, with the evidence recorded (MCAP bag, metric
table, measured latency) — and a success *rate* over N runs, never a single green pass. If a
change cannot be verified that way, say so and name the blocker.
