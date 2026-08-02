# Local patches — Cosys-AirSim

Upstream: https://github.com/Cosys-Lab/Cosys-AirSim
**Pinned: tag `5.8-v3.4.1` · SHA `a552dd6c`** (a SHA, never a branch — `main` has already
migrated 5.5 → 5.6dev → 5.7pdev → 5.8, and there is no `5.5` branch upstream at all).

**The vendored tree is byte-identical to upstream.** `git status --porcelain vendor/` reports
zero modifications. All three deviations below live in `patches/cosys-airsim/` and are applied
by `scripts/build_airsim_wrapper.sh` to a **container-local copy** at `/airsim_root`, never to
`vendor/`.

**All three are upstream defects, not local preferences, and all three are worth reporting to
Cosys-Lab.** Each was found by running the thing, not by reading it.

> **Line endings.** Cosys-AirSim sources are **CRLF**. A hand-written LF hunk fails with
> `Hunk 1 FAILED (different line endings)` — this cost a build cycle. Every hunk here was
> **generated from the real file**; regenerate the same way if one ever drifts.

---

## 1. `0001-mutually-exclusive-callback-group.patch`

**Date:** 2026-08-01 · **File:** `ros2/src/airsim_ros_pkgs/src/airsim_node.cpp:22`
**One word:** `CallbackGroupType::Reentrant` → `MutuallyExclusive`

**Symptom.** `airsim_node` connects, logs `AirsimROSWrapper Initialized!`, then aborts before
publishing anything:

```
eprosima::fastcdr::exception::BadParamException: The string contains null characters
```

**It is a data race, not a bad string.** A `Reentrant` group on a `MultiThreadedExecutor` lets
`drone_state_timer_cb` re-enter concurrently, so threads race on the shared per-vehicle
`curr_odom_`. Copying a `std::string` while another thread reassigns it is a torn read, which
Fast-CDR reports as embedded NULs.

**How it was proved, after three wrong guesses.** Segmentation object names (255, clean), the
RPC settings string (4827 chars, clean) and the frame-id constants (plain literals) were all
checked and all innocent. A gdb backtrace put the abort in `publish_odom_tf`, and hex-dumping
both frame ids there produced **862 prints containing zero `00` bytes while it still aborted** —
refuting the whole class of "which string holds a NUL" and forcing the race explanation. The
tell was that **30% of consecutive log timestamps were out of order** for a single vehicle.

---

## 2. `0002-camera-info-frame-id.patch`

**Date:** 2026-08-02 · **Files:** `airsim_ros_wrapper.cpp` (`:1916` + 2 call sites),
`airsim_ros_wrapper.h` (`:305`)

`generate_cam_info()` set `header.frame_id = camera_name + "_optical"`, while the static TF
(`:1709`) and the image both use `vehicle_name + "/" + camera_name + "_optical"`:

```
camera_info frame_id : front_center_optical         <- no vehicle prefix
image       frame_id : PX4/front_center_optical
tf_static child      : PX4/front_center_optical
```

**`camera_info` was the lone outlier**, and both Scene and DepthPlanar were affected — so
`image_proc`, `depth_image_proc` and any TF-aware perception node could not resolve the camera
frame.

**Deliberate shape.** `generate_cam_info()` used `camera_name` *only* for this frame_id, so the
two-line fix would have been to pass a pre-prefixed string at the call sites. The patch threads
`vehicle_name` through as an explicit parameter instead — five lines across two files — because
passing `"PX4/front_center"` under an argument named `camera_name` makes the signature a lie for
whoever reads it next. The call sites already had `curr_vehicle_name` in scope (`:165`).

---

## 3. `0003-per-timer-callback-groups.patch`

**Date:** 2026-08-02 · **Files:** `airsim_ros_wrapper.cpp` (5 timer sites),
`airsim_ros_wrapper.h` (`:413`)

**This one fixes a cost that patch `0001` introduced**, and is recorded that way rather than as
a standalone improvement.

`0001` made the *single shared* callback group `MutuallyExclusive`, which is correct but too
coarse: one such group serialises **every** callback in it, so the state timer queued behind
~50 ms image fetches. IMU, GPS, magnetometer and odometry all ride the state timer, so all were
capped at the image rate.

```
requested state timer        333 Hz  (update_airsim_control_every_n_sec = 0.003)
achieved                     ~35 Hz
RPC cost of one state cycle  0.26 ms   ->  a 3854 Hz ceiling
```

So the limit was neither the RPC nor the simulator. The race `0001` fixed was
`drone_state_timer_cb` re-entering **itself**; a **per-timer** `MutuallyExclusive` group still
prevents that while letting different timers run concurrently on the `MultiThreadedExecutor`.

Groups are stored as **members** because rclcpp's node holds only *weak* references to callback
groups — a locally-scoped `shared_ptr` would be freed and the timer silently orphaned.

**Result:** RGB 1.1 → 31.2 Hz, depth 1.1 → 29.6 Hz, LiDAR 1.6 → 17.4 Hz, IMU 35 → 366 Hz
published / 311 Hz distinct.

---

## Deviations that are NOT source patches

Recorded here because they are just as load-bearing, and are deliberately kept in the launch and
config layers per the least-destructive rule.

### The five uninitialized timer periods — fixed in the launch layer

`airsim_ros_wrapper.cpp` reads all five sensor-timer periods like this:

```cpp
double update_airsim_img_response_every_n_sec;              // UNINITIALIZED
nh_->get_parameter("update_airsim_img_response_every_n_sec", ...);   // returns FALSE
create_wall_timer(std::chrono::duration<double>(that_variable), ...);
```

`get_parameter` returns false for an undeclared parameter and **leaves the value unchanged**,
and `airsim_node` only auto-declares parameters passed as overrides. Pass nothing and the timer
period is **uninitialized stack memory** — observably arbitrary: 1.1 Hz imagery, 1.6 Hz LiDAR,
and a 1328 Hz state timer polling a 333 Hz IMU into 77% duplicate samples.

Affects `control`, `img_response`, `lidar`, `gpulidar`, `echo`.

**No patch: `ros2_ws/src/bringup/launch/lane_c_perception.launch.py` passes all five**, with
`value_type=float` forced — a bare `LaunchConfiguration` arrives as a *string*, and
`get_parameter(name, double&)` fails a type mismatch exactly as it fails an undeclared name,
leaving the same uninitialized value. The workaround would have looked applied and changed
nothing.

### `publish_clock` and the `/clock` topic — also launch layer

`publish_clock` defaults false (`:52`) **and** is never declared, and when enabled publishes to
`"~/clock"` (`:412`) → `/airsim_node/clock`, not `/clock`. Handled by the same launch file with
an unconditional remap. `use_sim_time:=true` against a bare node freezes every timer at zero —
the `P1-03a` failure shape.

### Frames are NWU, not ENU

`convert_tf_msg_to_enu()` exists at `:1600` and has **zero call sites**; all four conversions
use `convert_tf_msg_to_ros()`, which negates only y and z. Measured yaw missed ENU by 97.3° and
NWU by 7.3°. **Not patched** — the conversion belongs in this project's single conversion point
(`control/frames.py`, per `conventions.md` §3), not in the vendored tree.

### The IMU is a polled snapshot

`publish_vehicle_state()` fetches `getImuData()` once per state-timer tick and publishes only
the latest sample. Even at a correct timer period this yields ~15% duplicate timestamps and
non-uniform spacing. **Not patched** — it is an upstream design property, and any fix belongs
upstream. It is a live design constraint for `C-05` (cuVSLAM wants a dense, evenly-spaced
stream). IMU messages also ship with zero covariances (`// todo covariances` upstream).

---

## Known upstream instability, uncharacterised

The simulator segfaulted after **57 minutes** of continuous running:

```
Assertion failed: (Index >= 0) & (Index < ArrayNum) [Array.h:1339]
Array index out of bounds: 18823 into an array of size 0
```

preceded by one MAVLink `hil` `TcpClientPort socket send failed with error: 32` (EPIPE). Same
*shape* as `0001` — a read against something concurrently emptied — but **not diagnosed**, and
not addressed by any patch here. It is a stability ceiling that matters for long missions.

---

## Build-layer notes (not deviations, but required to build at all)

1. `drone-sim/ros2:v1.16.0` lacks `geographic_msgs` and `mavros_msgs`.
2. The wrapper **must be built in place** — `airsim_ros_pkgs/CMakeLists.txt` reaches
   `../../../cmake/{rpclib_wrapper,AirLib,MavLinkCom}` via `add_subdirectory`.
3. The build **writes into its own source tree** (`external/rpclib/.../version.h`,
   `config.h` via `configure_file`), so it cannot be built from a read-only mount.

All three are handled by `scripts/build_airsim_wrapper.sh`, which also asserts the artifact of
each patch rather than trusting `patch`'s exit code.
