# `bringup` — launch composition

Two launch files: one brings the simulator's sensors into the graph, the other flies the
vehicle. They are separate because **only one of them is allowed to know it is in a
simulator.**

| File | Starts | Sim-only? |
|---|---|---|
| `launch/perception.launch.py` | `airsim_node` — cameras, depth, LiDAR, IMU/GPS/mag/baro, TF, `/clock` | yes |
| `launch/control.launch.py` | the offboard controller from the `control` package | **no — this is the shared include** |

```bash
ros2 launch bringup perception.launch.py                 # after ./scripts/sim_up.sh
ros2 launch bringup control.launch.py use_sim_time:=true
```

`perception.launch.py` requires the Cosys-AirSim wrapper, which is **not** part of this
workspace — build it with `./scripts/build_airsim_wrapper.sh` and source
`/airsim_root/ros2/install/setup.bash` first.

**The invariant this package exists to protect:** the ROS 2 graph must be *identical* in
simulation and on the aircraft — same topic names, same node composition, same parameters.
Only the transport is swapped. `control.launch.py` is therefore the piece a future
`real.launch.py` wraps unchanged; if simulator-only machinery leaked into it, the
"only the transport is swapped" claim would quietly stop being true. Conventions:
[`../../../docs/conventions.md`](../../../docs/conventions.md).

## Why `perception.launch.py` is a file and not a command

`ros2 run airsim_ros_pkgs airsim_node` starts, looks healthy, and is wrong in two ways that
are invisible from outside. Both were measured on 2026-08-02 (`SIM-04`):

- **The clock is silently dead.** `publish_clock` defaults to *false* upstream, and even when
  enabled it publishes to `"~/clock"` → `/airsim_node/clock`, **not `/clock`**. So
  `use_sim_time:=true` against a bare node gives every node in the graph a clock frozen at
  zero: timers never fire, and it presents exactly as a deadlocked controller. The
  unconditional remap in this file is the load-bearing line.
- **Five sensor-timer periods are read from uninitialized stack memory.** The wrapper declares
  `double update_..._every_n_sec;` and calls `get_parameter`, which returns false for an
  undeclared parameter and **leaves the value unchanged**. Pass nothing and the timer period
  is undefined behaviour — observably arbitrary: **1.1 Hz imagery, 1.6 Hz LiDAR, and a
  1328 Hz state timer polling a 333 Hz IMU into 77% duplicate samples.** This file passes all
  five, with `value_type=float` forced, because a bare `LaunchConfiguration` arrives as a
  *string* and fails the same type check — the workaround would have looked applied and
  changed nothing.

Rates set here: state/IMU **333 Hz**, imagery **20 Hz** (the measured RPC ceiling for
Scene+Depth at 640×480 is ~21.7 Hz), LiDAR **10 Hz** to match `RotationsPerSecond` in
`sim/ue5/settings.json`. **The frame rate is capped by this file, not by the hardware**:
measured throughput on stock settings is 18.7 Hz imagery and 10.0 Hz LiDAR — **94%** and
**100%** of those ceilings — and the image-quality keys cost ~8.6% RGB and ~10% LiDAR on top.
They are bug workarounds, not tuning knobs.

`use_sim_time` still defaults to **false**: enabling it is only safe because this file
guarantees a `/clock` publisher, and leaving the default off means launching against a stack
that is not running fails visibly rather than hanging.

**Frames are NWU, not ENU**, despite upstream's docs — `convert_tf_msg_to_enu()` exists in the
wrapper and has zero call sites; measured yaw missed ENU by 97.3° and NWU by 7.3°. This launch
does **not** convert it. The conversion belongs in `control/frames.py`, the project's single
conversion point.
