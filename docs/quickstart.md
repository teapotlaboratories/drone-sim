# Lane C quickstart — run the simulator, read its sensors, fly the drone

**Everything here is SITL.** Nothing in this guide touches real hardware. The `sim ↔ real`
boundary in this project is the *transport swap*, not the commands — so the ROS 2 interface
below is the same one a real Pixhawk exposes, which is the point of the design.

**The drone is controlled over ROS 2 only.** There is an AirSim RPC API and a MAVLink link, and
neither is the control interface. RPC is used for *simulator* concerns (placing objects,
capturing frames for measurement); MAVLink is an internal detail of how PX4 and the renderer
agree on physics. Autonomy talks `px4_msgs` over uXRCE-DDS, exactly as it would on the aircraft.

Every number in this document was **measured on 2026-08-03**, not taken from upstream docs.

---

## 1. Start the simulator

```bash
./scripts/lane_c_up.sh
```

That brings up five containers (renderer, XRCE agent, PX4 SITL, QGC, ROS 2), waits for the
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

Each has an environment equivalent: `WORLD`, `SETTINGS_FILE`, `SPAWN`, `SPAWN_VEHICLE`,
`SPAWN_ALLOW_BELOW`.

```bash
# your own world, drone placed deliberately, 10 m above the origin facing north-west
./scripts/lane_c_up.sh \
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

---

## 2. Configure which sensors are active, and how they are tuned

Copy the shipped settings file, edit it, and pass it with `--settings`. The committed
`sim/ue5/settings.json` is **never modified** by a run — your file is copied to a run-time
artifact, so your configuration and the reviewed default cannot drift into each other.

```bash
cp sim/ue5/settings.json my-settings.json
$EDITOR my-settings.json
./scripts/lane_c_up.sh --settings my-settings.json
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

Those last three keys are why Lane C imagery is photorealistic; drop them and the capture
renders with global illumination and reflections **forced off**. They cost ~8.6% RGB and ~10%
LiDAR throughput. See `docs/worklog/2026-08-03-c11-washout-root-cause.md`.

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
docker exec -d lane-c-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash
  ros2 launch bringup lane_c_perception.launch.py'
```

> **The wrapper must be rebuilt after every `lane_c_up.sh`** — that script deletes the ROS 2
> container, which is where the wrapper lives. Run `./scripts/build_airsim_wrapper.sh` (~2 min)
> if `ros2 launch` reports `package 'airsim_ros_pkgs' not found`.

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

Vehicle state comes from PX4 itself — **24 `/fmu/out/*` topics**, byte-identical to what Lane A
and real hardware publish. The ones you will actually use:

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

```bash
# sensors — checks VALUES, not topic presence
docker exec lane-c-ros2 python3 /tmp/verify.py          # scripts/verify_lane_c_sensors.py

# the command interface — arms and flies, SITL only
docker exec lane-c-ros2 python3 /tmp/verify_nav.py     # scripts/verify_nav_interface.py
```

`verify_lane_c_sensors.py` deliberately asserts values: an all-black camera and a working one
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
  **every Lane C timing number is free-running**. Never quote a Lane C RTF as deterministic.
- **Frame rate is capped by the launch file, not by the hardware.**
  `lane_c_perception.launch.py` pins imagery at 20 Hz and LiDAR at 10 Hz; measured throughput
  sits at 94% and 100% of those ceilings.
- **Frames are NWU, not ENU**, despite what the upstream docs say.
- **The capture carries ~2.5× the high-frequency speckle** of Unreal's own render. `ForceUpdate`
  removes the Lumen-attributable part; the residual is deferred as `C-12`, and the metric itself
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
