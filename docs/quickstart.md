# Quickstart — run the simulator, read its sensors, fly the drone

**The simulator is Unreal Engine 5.8 + Cosys-AirSim + PX4 v1.16 SITL + ROS 2 Jazzy.** Bring
your own Unreal world, place the vehicle in it, choose which sensors it carries, and fly it
over ROS 2 — the same graph you would fly on real hardware.

**Everything here is SITL.** Nothing in this guide touches real hardware. The `sim ↔ real`
boundary in this project is the *transport swap*, not the commands — so the ROS 2 interface
below is the same one a real Pixhawk exposes, which is the point of the design.

**The drone is controlled over ROS 2 only.** There is an AirSim RPC API and a MAVLink link, and
neither is the control interface. RPC is used for *simulator* concerns (placing objects,
capturing frames for measurement); MAVLink is an internal detail of how PX4 and the renderer
agree on physics. Autonomy talks `px4_msgs` over uXRCE-DDS, exactly as it would on the aircraft.

Every number in this document was **measured on 2026-08-03**, not taken from upstream docs.

---

## 0. From a freshly installed machine

Nothing below assumes anything is already built. Expect **~65 GB of images** and, on a cold
machine, **an hour or two** — most of it the engine image and the PX4 build.

**On the host you need:** Docker with GPU access, an NVIDIA GPU and driver, `git`, `python3`,
`vcs` (`python3-vcstool`) and PyYAML (`pip install --user pyyaml`, needed by the scenario
runner). Everything else lives in containers — the project rule is to install into the
container, never the host.

> **One step cannot be automated away, and it is stated here rather than discovered later.** The
> Unreal engine base image `ghcr.io/epicgames/unreal-engine` is **credential-gated**: it needs
> EpicGames GitHub organisation membership **plus** a PAT with `read:packages`. Without that you
> cannot build the renderer. Everything else builds from a clone.

### 0.1 Authenticate and build the images

```bash
gh auth token | docker login ghcr.io -u <github-user> --password-stdin

docker build -f docker/px4.Dockerfile    -t drone-sim/px4:v1.16.0 .
docker build -f docker/qgc.Dockerfile    -t drone-sim/qgc:v1.16.0 .
docker build -f docker/ros2.Dockerfile   -t drone-sim/ros2:v1.16.0 .
docker build -f docker/unreal.Dockerfile -t drone-sim/unreal:ue5.8 .
```

**Order does not matter** — every image builds from `ubuntu:24.04`, or the Epic image for the
renderer, and none derives from another. Run them in parallel if you like.

| Image | Size | What it is |
|---|---|---|
| `drone-sim/unreal:ue5.8` | 57.5 GB | the renderer — credential-gated |
| `drone-sim/ros2:v1.16.0` | 4.39 GB | companion computer: ROS 2, uXRCE-DDS agent, `px4_msgs` |
| `drone-sim/qgc:v1.16.0` | 1.43 GB | ground station |
| `drone-sim/px4:v1.16.0` | 466 MB | the autopilot — SITL build output only |

The PX4 image verifies every pin against its recorded SHA and fails the build on a mismatch.
It also builds the NuttX/ARM toolchain and then **throws it away**: `docker build --target
firmware` still produces an image that can flash a real Pixhawk 6C, while the image that ships
copies only `build/px4_sitl_default` out of it.

### 0.2 Fetch the pinned upstreams and build the plugin once

```bash
vcs import vendor < .repos          # ~1.3 GB — Cosys-AirSim at a pinned SHA, not a branch

docker run --rm -v "$PWD/vendor/Cosys-AirSim:/src" drone-sim/unreal:ue5.8 \
  bash -lc './build.sh --ue-root /home/ue4/UnrealEngine'
```

> **`--ue-root` is mandatory, not advisory.** The engine image ships **no system clang** — the
> compiler is the engine's bundled toolchain, so a build without it has no compatible compiler
> at all rather than a graceful fallback.

Upstream's `build.sh` compiles **pristine** vendor source. The repo's own fixes to the Unreal
plugin live in `patches/cosys-airsim/` and are applied by a second step:

```bash
./scripts/build_blocks.sh          # apply the Unreal-side patches to Blocks, then rebuild
```

> **Do not skip this.** Without it the renderer segfaults mid-flight on an empty GPU-LiDAR
> readback (`SIM-23`), and a vehicle in a World Partition world falls through the level forever
> (`SIM-21`). The script is idempotent — re-run it after pulling any change under
> `patches/cosys-airsim/`, and it does nothing when everything is already applied.

### 0.3 Check it worked before flying anything

```bash
docker images | grep drone-sim          # four images
ls vendor/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject
```

If the renderer later fails on Vulkan rather than on anything above, read
[`gpu-in-docker.md`](gpu-in-docker.md) — GPU access here is CDI, and on a Fedora-family host
there is a Vulkan ICD path fix that is load-bearing.

---

## 1. Start the simulator

```bash
./scripts/sim_up.sh
```

That brings up four containers (renderer, PX4 SITL, ROS 2, QGC), waits for the
vehicle to settle, and then **verifies the EKF origin** before declaring the stack usable. It
prints `stack up and origin verified -- safe to fly` when it is ready — typically ~80 s.

> **Why it verifies rather than just waits.** PX4 sets its EKF local origin **once**. If it
> initialises before the simulated vehicle has settled, every altitude PX4 reports is silently
> offset for the whole session. That defect once presented as a control bug for a full day. If
> the origin is stale the script restarts PX4 and re-checks; a run that cannot be repaired is
> reported **VOID**, not failed — it never measured the flight code.

### Options

| flag | what it does |
|---|---|
| `--world PATH.uproject` | load **your own** Unreal world instead of the bundled Blocks environment |
| `--settings PATH.json` | supply your own `settings.json` — which sensors are active and how they are tuned |
| `--spawn X,Y,Z[,YAW]` | where to put the vehicle, in metres **NED** |
| `--vehicle NAME` | required only if your settings define several vehicles |
| `--allow-below-origin` | permit a positive `Z` (i.e. genuinely below the origin) |
> **Converting a world is a separate step.** Anything that ships `Source/` must be compiled
> against UE5.8, and World Partition levels need a vendor patch or the vehicle falls through the
> map forever. Run `./scripts/convert_world.sh <your.uproject> --map /Game/Maps/X` first —
> [`docs/worlds.md`](worlds.md) explains what it does and how to verify it worked.

Each has an environment equivalent: `WORLD`, `SETTINGS_FILE`, `SPAWN`, `SPAWN_VEHICLE`,
`SPAWN_ALLOW_BELOW`.

```bash
# your own world, drone placed deliberately, 10 m above the origin facing north-west
./scripts/sim_up.sh \
    --world /path/to/YourProject.uproject \
    --spawn 50,-30,-10,315
```

> **`Z` is NED — negative is UP.** For 10 m of altitude pass `Z=-10`. Passing `Z=10` puts the
> drone 10 m **underground**, so the script refuses a positive `Z` unless you add
> `--allow-below-origin`.
>
> **A spawn `Z` is a *release* height, not a resting height.** The vehicle falls to whatever is
> beneath it. If you do not know the ground height at your `(X, Y)`, release high and read the
> resting position back (§5) — that *is* a ground probe.

### Running your autonomy on another machine

By default the stack **publishes nothing** and is reachable only from the machine it runs on. That
is `NET_MODE=shared`: the renderer donates a private network and IPC namespace and every other
container joins it. If your code runs here too, use [`scripts/attach.sh`](../scripts/attach.sh) and
stop reading.

If your autonomy computer is a *different* machine — a Jetson on the bench, a box across the
LAN — you opt in. Which switch you need is decided by **one question: does the path between the two
machines carry UDP multicast?** DDS discovers over multicast (`239.255.0.1:7400`) by default, and a
VPN or a routed subnet almost never forwards it.

| Path between the machines | Switch | What the peer gets |
|---|---|---|
| Same machine | *(default)* `NET_MODE=shared` | private namespace, nothing published |
| LAN that forwards multicast | `NET_MODE=host` | the whole graph, no DDS config |
| VPN / routed subnet — **no multicast** | `NET_MODE=host` + `DISCOVERY_SERVER=<ip>:<port>` | the whole graph over plain unicast |

```bash
# LAN
NET_MODE=host ./scripts/sim_up.sh

# VPN: start a discovery server anywhere BOTH machines can reach, BEFORE the stack.
# `fastdds` ships in drone-sim/ros2, so the host needs no ROS 2 install:
docker run -d --name sim-ds --network host --entrypoint bash drone-sim/ros2:v1.16.0 \
    -lc 'fastdds discovery -i 0 -l 0.0.0.0 -p 11811'

NET_MODE=host DISCOVERY_SERVER=127.0.0.1:11811 ./scripts/sim_up.sh
```

The server must be up **before** the stack. It is not managed by `sim_up.sh` — teardown leaves
it alone, so it survives a re-run; stop it yourself with `docker rm -f sim-ds`.

`DISCOVERY_SERVER` changes only **how peers find each other**. It does not imply a network mode —
you still need `NET_MODE=host` for the stack to advertise a routable address instead of the
docker-bridge `172.17.0.2` that only this machine can reach.

On the **subscriber**, point at the same server and declare UDP as the only transport:

```bash
export ROS_DISCOVERY_SERVER=<server-ip>:11811
export ROS_SUPER_CLIENT=true
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/configs/dds/udp-only.xml
```

Measured from a **second host** on the overlay — no shared namespaces, no multicast on the path.
Every run is paired with the same probe **without** the server, which is what makes it evidence:

```
                     with discovery server      control: multicast only
topics visible       total=53  fmu=51  (×3)     total=2  fmu=0  (×3)
/fmu/out delivery    pos=1936  imu=1936         pos=0    imu=0
                     ref_alt 123.282 m — matches the stack's verified EKF origin
```

`fmu=51` is what the stack sees locally; the control's `total=2` is just the probe's own
`/parameter_events` and `/rosout`. The server is what carried the graph.

> Those are **discovery and delivery** figures, not throughput. The link was a relayed overlay
> path (~27 ms RTT, MTU 1280) — bandwidth measured across it describes the relay, not your network.

> **`ROS_SUPER_CLIENT=true` is not optional, and omitting it looks like total failure.** A plain
> discovery *client* is only told about participants it has already matched, but `ros2 topic echo`
> must resolve the message **type** from the graph *before* it can subscribe. As a plain client it
> fails with `Could not determine the type for the passed topic` while a healthy publisher sits
> right there. `sim_up.sh` sets it inside the stack; you must set it on your side.
>
> **`ros2 node list` returns 0 for `/fmu/*` — in every mode, multicast included.** The uXRCE-DDS
> agent creates raw DDS participants with no ROS 2 node metadata. Topics and data are fine; only
> the node listing is empty. It is not a broken link.
>
> **The server is a rendezvous, not a relay.** Data still flows peer-to-peer, so the two machines
> need direct routable reachability — it removes the multicast requirement, not the routing one.
>
> **Host mode exposes PX4's unauthenticated MAVLink ports** on every interface this machine has,
> including the VPN. Anyone routable can arm and command the vehicle. Use it on a trusted network
> only, and note the same switch later points at a **real** Pixhawk.

---

## 2. Configure which sensors are active, and how they are tuned

Copy the shipped settings file, edit it, and pass it with `--settings`. The committed
`sim/ue5/settings.json` is **never modified** by a run — your file is copied to a run-time
artifact, so your configuration and the reviewed default cannot drift into each other.

```bash
cp sim/ue5/settings.json my-settings.json
$EDITOR my-settings.json
./scripts/sim_up.sh --settings my-settings.json
```

A worked example ships at **`sim/ue5/examples/minimal-no-lidar.json`** — GPU-LiDAR disabled and
the cameras dropped to 320×240. Verified: the `gpulidar` topic disappears from the graph and
`image.width` reads `320`.

### Turning a sensor off

```jsonc
"Sensors": {
  "gpulidar": { "SensorType": 8, "Enabled": false }   // topic disappears entirely
}
```

> **The `Sensors` block REPLACES the defaults — it does not extend them.** AirSim only creates
> default sensors "when none specified in json". A settings file listing *only* a barometer
> leaves the vehicle with no IMU, GPS or magnetometer; PX4 then arms and immediately
> auto-disarms with `Preflight Fail: ekf2 missing data`, which looks exactly like a control bug.
> **List every sensor you want, every time.**

### Retuning a camera

```jsonc
"Cameras": {
  "front_center": {
    "X": 0.30, "Y": 0.0, "Z": 0.0, "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0,
    "CaptureSettings": [
      { "ImageType": 0, "Width": 1280, "Height": 720, "FOV_Degrees": 90,
        "LumenGIEnable": true, "LumenReflectionEnable": true, "ForceUpdate": true }
    ]
  }
}
```

The camera pose keys are **not optional**. A camera declared without `X/Y/Z/Pitch/Roll/Yaw`
keeps AirSim's NaN sentinels, reaches `FRotator::Quaternion` as `P=nan Y=nan R=nan`, and
**SIGSEGVs the simulator during `BeginPlay`** — a crash, not a validation error.

Those last three keys are why the imagery is photoreal — with them the capture matches Unreal's
own render to **1.15 of 255**; drop them and it renders with global illumination and reflections
**forced off**. They cost ~8.6% RGB and ~10% LiDAR throughput. See
`docs/worklog/2026-08-03-c11-washout-root-cause.md`.

### Useful sensor keys

| sensor | key | note |
|---|---|---|
| camera | `Width`, `Height`, `FOV_Degrees` | per `CaptureSettings` entry; `ImageType` 0 = Scene, 1 = DepthPlanar |
| camera | `LumenGIEnable`, `LumenReflectionEnable`, `ForceUpdate` | image quality; ImageType 0 only |
| GPU-LiDAR | `NumberOfChannels`, `RotationsPerSecond`, `MeasurementsPerCycle`, `Range` | shares the GPU with the renderer — raising these degrades frame rate |
| GPU-LiDAR | `DataFrame` | `SensorLocalFrame` or `VehicleInertialFrame` |
| barometer | `PressureFactorSigma` | lower = less drift = faster EKF convergence |
| any | `Enabled` | `false` removes the topic from the graph |

---

## 3. Sensors you receive

Published by `airsim_node`. Start it after the stack is up:

```bash
docker exec -d sim-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 launch bringup perception.launch.py'
```

> **The wrapper must be rebuilt after every `sim_up.sh`** — that script does
> `docker rm -f sim-ros2 …` on every bring-up, and the wrapper is built *inside* that
> container (`/airsim_root`), not baked into the image. Run `./scripts/build_airsim_wrapper.sh`
> (~2 min) if `ros2 launch` reports `package 'airsim_ros_pkgs' not found`. The order is always
> `sim_up.sh` → `build_airsim_wrapper.sh` → `ros2 launch`.

> **Launch it — do not `ros2 run` it.** A bare `ros2 run airsim_ros_pkgs airsim_node` starts,
> looks healthy, and its clock is silently dead: `publish_clock` defaults to false, and when
> enabled it publishes to `/airsim_node/clock`, never `/clock`. `perception.launch.py` flips
> that default, remaps the topic, and passes the five sensor-timer periods that the wrapper
> otherwise reads out of *uninitialised stack memory* — measured 1.1 Hz imagery and 1.6 Hz
> LiDAR when they are left unset. Add `use_sim_time:=true` to put your graph on simulator time;
> it is only safe because this launch file guarantees the `/clock` publisher.

| topic | type | rate | notes |
|---|---|---|---|
| `/airsim_node/PX4/front_center_Scene/image` | `sensor_msgs/msg/Image` | 17.1 Hz | RGB, `bgr8`/`rgb8` |
| `/airsim_node/PX4/front_center_Scene/camera_info` | `sensor_msgs/msg/CameraInfo` | 17.1 Hz | `frame_id` resolves in TF |
| `/airsim_node/PX4/front_center_DepthPlanar/image` | `sensor_msgs/msg/Image` | 16.6 Hz | `32FC1`, metres |
| `/airsim_node/PX4/front_center_DepthPlanar/camera_info` | `sensor_msgs/msg/CameraInfo` | 16.6 Hz | |
| `/airsim_node/PX4/gpulidar/points/gpulidar` | `sensor_msgs/msg/PointCloud2` | 9.0 Hz | 8192 points, `point_step` 32 |
| `/airsim_node/PX4/imu/imu` | `sensor_msgs/msg/Imu` | 333 Hz | see the caveat below |
| `/airsim_node/PX4/gps/gps` | `sensor_msgs/msg/NavSatFix` | 333 Hz | |
| `/airsim_node/PX4/global_gps` | `sensor_msgs/msg/NavSatFix` | 333 Hz | |
| `/airsim_node/PX4/magnetometer/magnetometer` | `sensor_msgs/msg/MagneticField` | 333 Hz | |
| `/airsim_node/PX4/altimeter/barometer` | `airsim_interfaces/msg/Altimeter` | | |
| `/airsim_node/PX4/odom_local` | `nav_msgs/msg/Odometry` | 333 Hz | frame `PX4/odom_local` |
| `/airsim_node/PX4/environment` | `airsim_interfaces/msg/Environment` | | |
| `/airsim_node/instance_segmentation_labels` | `airsim_interfaces/msg/InstanceSegmentationList` | | ground-truth labels |
| `/airsim_node/object_transforms` | `airsim_interfaces/msg/ObjectTransformsList` | | ground-truth poses |
| `/airsim_node/origin_geo_point` | `airsim_interfaces/msg/GPSYaw` | | the world's geo origin |
| `/clock` | `rosgraph_msgs/msg/Clock` | 333 Hz | remapped by the launch file |
| `/tf`, `/tf_static` | `tf2_msgs/msg/TFMessage` | | 4 static frames |

Vehicle state comes from PX4 itself — **24 `/fmu/out/*` topics**, byte-identical to what a real
Pixhawk publishes over the same `px4_msgs` definitions. That identity is the whole sim-to-real
argument: your node cannot tell which side of the transport swap it is on. The ones you will
actually use:

| topic | type |
|---|---|
| `/fmu/out/vehicle_local_position` | `px4_msgs/msg/VehicleLocalPosition` |
| `/fmu/out/vehicle_global_position` | `px4_msgs/msg/VehicleGlobalPosition` |
| `/fmu/out/vehicle_status_v1` | `px4_msgs/msg/VehicleStatus` |
| `/fmu/out/vehicle_odometry` | `px4_msgs/msg/VehicleOdometry` |

### Three caveats that cost real debugging time

**`/fmu/out/*` needs matching QoS.** Publishers are `BEST_EFFORT` + `TRANSIENT_LOCAL`. A default
`RELIABLE` subscription matches **nothing** and your node sees silence against a perfectly
healthy stack.

```python
QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
           durability=DurabilityPolicy.TRANSIENT_LOCAL,
           history=HistoryPolicy.KEEP_LAST, depth=5)
```

**It is `vehicle_status_v1`, not `vehicle_status`.** PX4 v1.16 renamed it. Subscribing to the
old name matches nothing while looking entirely correct.

**The IMU rate is a poll rate, not a data rate.** The wrapper polls AirSim, so a fraction of
messages carry duplicate timestamps. Treat ~333 Hz as the ceiling and check timestamps before
feeding a preintegrating VIO. `ros2 topic hz` will report a healthy-looking rate and tell you
none of this.

---

## 4. Commanding the drone — ROS 2 only

Three command kinds, all confirmed by measurement
(`scripts/verify_nav_interface.py`, 2026-08-03).

### The mode split you must respect

```
waypoint / velocity  ->  STREAM TrajectorySetpoint at >2 Hz, mode OFFBOARD
GPS waypoint         ->  ONE-SHOT VehicleCommand DO_REPOSITION, PX4's own nav mode
```

**There is no global (lat/lon) setpoint message.** `GotoSetpoint` is local NED;
`VehicleGlobalPosition` is an estimate *output*, not a command. So a GPS waypoint cannot be
streamed the way a local one is — it is a different control path with different failsafes.
**Do not mix the two casually**; switch modes deliberately.

Publish to `/fmu/in/*` with **`BEST_EFFORT` + `VOLATILE`** — PX4's subscribers use that, and a
`RELIABLE` publisher matches nothing and is silently dropped.

### Waypoint — measured error 0.98 m

```python
mode = OffboardControlMode(); mode.position = True
sp = TrajectorySetpoint()
sp.position = [x, y, z]                  # metres, NED, z negative is up
sp.velocity = [float('nan')] * 3
sp.yaw = yaw_radians
```

Stream **both** at >2 Hz. PX4 drops out of OFFBOARD if the stream stops for ~0.5 s, and it
rejects the mode change unless a stream is *already* present before you request it.

### Velocity — measured 2.00 m/s against 2.0 commanded

```python
mode = OffboardControlMode(); mode.velocity = True
sp = TrajectorySetpoint()
sp.position = [float('nan')] * 3         # MUST be NaN
sp.velocity = [vx, vy, vz]               # m/s, NED
```

**`position` must be NaN.** A finite position alongside a velocity makes PX4 prefer the position
and your velocity command silently does nothing.

Expect a **ramp**: the vehicle accelerates under PX4's jerk/accel limits. Commanding 2 m/s and
sampling over 5 s reads ~1.47 m/s and looks like a failure; over 14 s it reads exactly 2.00.

### GPS waypoint — measured 27.0 m of 30 m commanded, 2.99 m remaining

```python
cmd = VehicleCommand()
cmd.command = VehicleCommand.VEHICLE_CMD_DO_REPOSITION   # 192
cmd.param1 = -1.0        # ground speed, -1 = default
cmd.param2 = 1.0         # bitmask
cmd.param4 = float('nan')  # yaw, NaN = unchanged
cmd.param5 = latitude
cmd.param6 = longitude
cmd.param7 = altitude    # metres AMSL
cmd.from_external = True
```

Sent **once** — not streamed. PX4 flies it in its own navigation mode.

### Arming

```python
cmd.command = VehicleCommand.VEHICLE_CMD_DO_SET_MODE          # param1=1, param2=6 (OFFBOARD)
cmd.command = VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM # param1=1 arm, 0 disarm
cmd.command = VehicleCommand.VEHICLE_CMD_NAV_LAND
```

Stream setpoints **before** requesting OFFBOARD, or the mode change is rejected.

---

## 5. Verify it works

Both scripts live in the repo, not in the image, so copy them in first — and run them
through a **login** shell, because `docker exec` bypasses the entrypoint and a non-login
shell has no ROS environment at all (`import rclpy` fails before anything is checked).

```bash
docker cp scripts/verify_sensors.py       sim-ros2:/tmp/verify.py
docker cp scripts/verify_nav_interface.py sim-ros2:/tmp/verify_nav.py

# sensors — checks VALUES, not topic presence
docker exec sim-ros2 bash -lc 'python3 /tmp/verify.py'

# the command interface — arms and flies, SITL only
docker exec sim-ros2 bash -lc 'python3 /tmp/verify_nav.py'
```

`verify_sensors.py` deliberately asserts values: an all-black camera and a working one
both "publish an image", and only one has pixel variance. `verify_nav_interface.py` proves each
command kind by the **vehicle moving**, never by a publisher existing.

Read the vehicle's world position (useful as a ground probe when choosing a spawn):

```python
client.simGetObjectPose("PX4")     # WORLD frame
client.simGetVehiclePose("PX4")    # displacement since SPAWN — not the same thing
```

**`simGetVehiclePose` is spawn-relative.** AirSim anchors its NED frame at the spawn point, so
after a spawn it reads displacement, not world position. Reading the wrong one makes a working
spawn look like it was ignored.

---

## 6. Known limits

- **Lockstep is dead code** in Cosys-AirSim — `"LockStep": true` is silently ineffective, so
  **every timing number here is free-running**. Never quote an RTF as deterministic.
- **Frame rate is capped by the launch file, not by the hardware.**
  `perception.launch.py` pins imagery at 20 Hz and LiDAR at 10 Hz; measured throughput
  sits at 94% and 100% of those ceilings.
- **Frames are NWU, not ENU**, despite what the upstream docs say.
- **The capture carries ~2.5× the high-frequency speckle** of Unreal's own render. `ForceUpdate`
  removes the Lumen-attributable part; the residual is deferred as `SIM-12`, and the metric itself
  is not fully trustworthy.
- **A simulator segfault was seen once** after ~57 minutes of continuous running
  (`Array index out of bounds: 18823 into an array of size 0`, preceded by a MAVLink `hil`
  EPIPE). **n=1.**
  **A deliberate attempt to reproduce it failed (2026-08-03).** A 90-minute soak of the *full*
  stack — PX4, MAVLink, the wrapper polling Scene/Depth/GPU-LiDAR, plus a concurrent RPC client
  — ran **74,253 captures with zero anomalies** and did not crash. Two hypotheses died with it:
  upstream's own "segfaults every 2000 or so calls" comment (wrong by >30×), and the ~57-minute
  interval (ran clean through it under heavier load). **Not reproduced is not fixed** — treat it
  as a rare, uncharacterised event rather than a known ceiling. Detail in
  `docs/vendor/cosys-airsim.md`.

---

**Where to go next.** The backlog for the simulator is [`todo.md`](todo.md) — every `SIM-NN`
task, what is done, and what it cost. The wire conventions the graph is held to (frames, units,
topic names, QoS) are in [`conventions.md`](conventions.md).
