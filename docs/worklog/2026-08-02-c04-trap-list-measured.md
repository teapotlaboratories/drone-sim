# 2026-08-02 — `C-04`: the trap list, measured against a running node

**Task:** `C-04` — Cosys-AirSim sensors into the ROS 2 graph. This entry checks the four
documented traps against a *running* `airsim_node` instead of against the docs.
**Lane:** C. **SITL only** — no real aircraft, nothing real armed or flown.

> Kept as the work happens.

---

## Result: all four traps confirmed; two of them fixed

| # | Trap | Verdict |
|---|---|---|
| 1 | Frames are NWU, not ENU — docs say otherwise | **CONFIRMED** — measured, not read |
| 2 | `/clock` published on the wrong topic | **CONFIRMED**, and worse than filed |
| 3 | IMU is a polled snapshot, not a stream | **CONFIRMED and quantified — 77.9% duplicates** |
| 4 | `camera_info.frame_id` mismatches the TF tree | **CONFIRMED and FIXED** — patch `0002` |

---

## The build is no longer ephemeral

The wrapper previously existed only inside a container that `lane_c_up.sh` deletes on every
run. It is now reproducible:

- **`patches/cosys-airsim/0001-mutually-exclusive-callback-group.patch`** — the data-race fix
  from `C-04`, as a patch. `vendor/` stays pristine.
- **`scripts/build_airsim_wrapper.sh`** — installs the two missing ROS deps, lays out a build
  root, applies the patch, builds, and **asserts the artifact** rather than trusting colcon's
  exit code. End-to-end in 1 m 43 s from nothing.

**The patch failed on first use, for a reason worth recording:** `Hunk #1 FAILED (different
line endings)`. Cosys-AirSim sources are **CRLF**; my hand-written hunk was LF. The fix was to
*generate* the hunk from the real file rather than write it, and the patch header now says so —
otherwise the next person hand-edits it and hits the same wall.

---

## Trap 1 — NWU, confirmed twice

**Code:** `convert_tf_msg_to_enu()` is defined at `airsim_ros_wrapper.cpp:1600` and has
**zero call sites**. All four conversions call `convert_tf_msg_to_ros()` instead
(`:1636`, `:1653`, `:1670`, `:1687`), which negates only y and z — that is NED→**NWU**.

**Measured**, comparing the published odom against AirSim's ground truth:

```
AirSim GT (NED)  yaw   +0.000 deg
wrapper odom     yaw   -7.342 deg
NWU predicts     yaw   -0.000 deg    -> |err|  7.342 deg
ENU predicts     yaw  +90.000 deg    -> |err| 97.342 deg
==> NWU
```

Unambiguous: 7° against 97°. **Anything written against REP-103 or `px4_ros_com`'s ENU
assumption will be yaw-rotated 90°.**

**Honest residual:** the 7.342° is not explained. Ground truth says yaw is exactly 0, so a pure
NWU conversion should emit 0. The wrapper's odom comes from `kinematics_estimated` rather than
`simGetGroundTruthKinematics`, so a small estimator difference is plausible — but that is a
guess, and it is recorded as an open question rather than waved through. It does not affect the
NWU-vs-ENU conclusion, which turns on 90°, not 7°.

## Trap 2 — confirmed, and it is a *double* defect

Filed as "publishes to `~/clock`, defaults False". Both true, and there is a third layer:

```
airsim_ros_wrapper.cpp:52    publish_clock_(false)                     <- default off
airsim_ros_wrapper.cpp:131   nh_->get_parameter("publish_clock", ...)  <- NEVER declared
airsim_ros_wrapper.cpp:412   create_publisher<Clock>("~/clock", 1)     <- wrong topic
```

`ros2 param list` on the running node shows `publish_clock` is **not among the declared
parameters** — only `host_ip`, `vehicle_name`, `use_sim_time` and the QoS overrides.

Measured, in three steps:

```
default                                    ->  no clock topic at all
-p publish_clock:=true                     ->  /airsim_node/clock     (NOT /clock)
-p publish_clock:=true -r /airsim_node/clock:=/clock  ->  /clock ticking
```

**The remap is the fix**, and it must be in the launch, not left to whoever runs the node.
`use_sim_time:=true` with nothing on `/clock` freezes every timer at zero and looks exactly
like a deadlocked controller — the failure `P1-03a` already cost this project once.

## Trap 3 — confirmed, and much worse than "polled"

The filed version said intermediate samples are dropped. Measured, over 20 s with a subscriber
(not `ros2 topic hz`, which only sees arrival times, not content):

```
messages received : 30022  ->  1501 Hz published
DISTINCT stamps   :  6630
duplicate rate    :  77.9%
distinct-sample dt: median 3.000 ms  ->  ~333 Hz of REAL sensor data
                    min 3.000 ms   max 9.000 ms
```

**Nearly four out of five IMU messages are the same sample republished.** The 1501 Hz is the
RPC poll rate, not a sensor rate. The real sensor runs at ~333 Hz, and `max dt 9 ms` = 3× the
3 ms base, so **samples are also genuinely dropped**, not merely duplicated.

For cuVSLAM or any preintegrating VIO this is the bad case on both counts: repeated timestamps
*and* non-uniform spacing. `ros2 topic hz` alone would have reported a healthy-looking 1501 Hz
with 0.17 ms jitter and told us nothing — **the duplicate check is what makes this a
measurement rather than a reassurance.**

IMU messages also ship with zero covariances (`// todo covariances` upstream), unchanged.

## Trap 4 — confirmed, then fixed

Initially untestable: no `camera_info` existed because `sim/ue5/settings.json` declared no
`Cameras` block. **The UE pawn does carry default cameras** — `simGetImages` over RPC works on
`front_center` — but the wrapper enumerates `vehicle_setting->cameras`, and
`loadCameraSettings` begins with `cameras.clear()`. No settings entry, no topics.

### Adding the sensors, without repeating the earlier mistake

Added a `Cameras` block (RGB + `DepthPlanar`, 640×480) and a GPU-LiDAR (`SensorType: 8`).
**Checked the parsing semantics first**, because a `Sensors` block replacing the defaults is
exactly what cost a session on `C-03`:

- `loadCameraSettings` clears first, but its default is an **empty** map — so adding `Cameras`
  is purely additive and destroys nothing.
- Per-vehicle `Sensors` is iterated by key, so the GPU-LiDAR had to be *added alongside* the
  four existing sensors, and an assertion in the edit confirms all five survive.

**A bug I introduced and caught in the same step:** I first placed `_comment_gpulidar` *inside*
the `Sensors` block. `loadSensorSettings` iterates the keys of that block and reads `SensorType`
off each one, so the comment would have been parsed as a sensor with `SensorType 0` — not a
valid enum. Only per-sensor `_comment` fields nested *inside* a sensor object are safe. Moved to
vehicle level, with the reason written next to it.

Result — 19 topics, up from 14, carrying real data:

```
/airsim_node/PX4/front_center_Scene/{image,camera_info}          640x480 rgb8
/airsim_node/PX4/front_center_DepthPlanar/{image,camera_info}
/airsim_node/PX4/gpulidar/points/gpulidar        width 8192, point_step 32, is_dense
```

### The mismatch, measured

```
camera_info frame_id : front_center_optical         <- no vehicle prefix
image       frame_id : PX4/front_center_optical
tf_static child      : PX4/front_center_optical
```

Source: `airsim_ros_wrapper.cpp:1916` sets `camera_name + "_optical"`, while the static TF at
`:1709` uses `vehicle_name_ + "/" + camera_name + "_optical"`. **`camera_info` is the lone
outlier**, and both Scene and DepthPlanar are affected — so `image_proc`, `depth_image_proc` and
any TF-aware node cannot resolve the camera frame.

**Fixed** as `patches/cosys-airsim/0002-camera-info-frame-id.patch`. `generate_cam_info()` used
`camera_name` *only* for this frame_id, so the vehicle name is threaded through explicitly
rather than passing a pre-prefixed string under a `camera_name` argument — the call sites
already have `curr_vehicle_name` in scope (`:165`). Verified:

```
front_center_Scene         camera_info -> PX4/front_center_optical
front_center_DepthPlanar   camera_info -> PX4/front_center_optical
```

`scripts/build_airsim_wrapper.sh` now applies every patch in `patches/cosys-airsim/` in
numbered order and asserts the artifact of each, rather than trusting `patch`'s exit code.

---

## Measurement hygiene notes

- **`pgrep -c airsim_node` reported 3 nodes when 1 was running.** Two were `<defunct>` zombies
  from earlier `docker exec -d` launches; zombies still match `pgrep`. Nearly led me to
  discard the cadence numbers as contaminated by duplicate publishers. `ps -eo pid,comm,args`
  showed the truth immediately.
- Cold start #3 (first after a machine reboot) again came up with a stale EKF origin and again
  self-repaired — `scripts/lane_c_up.sh` is now three for three.

## Both remaining fixes landed

### The `/clock` remap is in a launch file now

`ros2_ws/src/bringup/launch/lane_c_perception.launch.py` starts `airsim_node` with
`publish_clock:=true` (upstream defaults it false) and an **unconditional** remap of
`/airsim_node/clock` → `/clock`. Verified: `ros2 launch bringup lane_c_perception.launch.py`
with **no flags at all** yields a ticking `/clock` and all 19 topics.

`use_sim_time` still defaults to **false**, matching `sim.launch.py`'s reasoning: enabling it
is only safe *because* this file guarantees a publisher, and a false default means launching
against a dead stack fails visibly instead of hanging. The docstring also warns that the topics
are NWU, so nobody wires up a consumer assuming otherwise.

### NWU→ENU went into the existing conversion point, not a new one

`conventions.md` §3 freezes conversion to **one place**, and that place already exists —
`control/frames.py`, with `test_frames.py` beside it. Lane C does not get to invent a second
convention, so `nwu_to_enu` / `enu_to_nwu` / `yaw_nwu_to_enu` / `yaw_enu_to_nwu` were added
there rather than in a Lane C node.

**One structural difference is worth more than the code:** `enu_to_ned` is its own inverse, and
the module's docstring leans on that — *"applying it twice returns the input"* — as the reason a
stray double call is so hard to spot. **NWU↔ENU is not an involution.** It is a 90° rotation, so
applying it twice is a 180° rotation with x and y both negated. Anyone carrying the
"twice is harmless" intuition across from the other pair will corrupt data.

That is pinned by `test_this_pair_is_NOT_an_involution_unlike_enu_ned`, which asserts both
properties in one place. Seven new tests, 15 total in that file. **Verified by breaking it:**
replacing `return (-y, x, z)` with the plausible-but-wrong `return (y, x, z)` fails 3 tests.

Nothing consumes these yet — the conversion exists and is tested, but no Lane C node calls it.
That is deliberate: `C-05` is where perception topics get consumed, and wiring a converter into
a node with no consumer would be speculative.

## Navigation-readiness sweep — and the bug it exposed

Asked directly: *is the simulated drone capable of RGB, depth, LiDAR, GPS and the basics for
navigation over the ROS 2 link?* Answer: **yes now — but it was not when the question was
asked, and topic-existence would have said yes either way.**

`scripts/verify_lane_c_sensors.py` checks **values**, not presence, because every Lane C
failure so far looked healthy from outside. It asserts things that can fail: RGB pixel
variance (a blank buffer and a real frame both "publish"), depth finite and metric, LiDAR
returns not all at the origin, GPS lat/lon plausible and non-zero, **IMU |accel| ≈ 9.81 at
rest** (a zero-filled Imu deserialises perfectly), `camera_info.frame_id` resolvable in
`tf_static`, and `/clock` actually advancing.

First run: every required check passed — **at 1.1 Hz imagery.** Valid data, useless rate.

### Root cause: five uninitialized doubles

```cpp
double update_airsim_img_response_every_n_sec;              // UNINITIALIZED
nh_->get_parameter("update_airsim_img_response_every_n_sec", ...);   // returns FALSE, leaves it
create_wall_timer(std::chrono::duration<double>(that_variable), ...);
```

`get_parameter` returns false for an undeclared parameter and **leaves the value unchanged**,
and `airsim_node` only auto-declares parameters that are *passed as overrides*. Pass nothing,
and the timer period is uninitialized stack memory. **All five sensor timers do this** —
`control`, `img_response`, `lidar`, `gpulidar`, `echo`.

Same class as `publish_clock`, but worse: that one at least had a sane initializer.

**This also re-explains trap 3.** The 77.9% IMU duplicate rate was never inherent to the
polled-snapshot design — the state timer's garbage period made it poll a 333 Hz sensor at
1328 Hz. The low image rate and the IMU duplicates were *one bug*.

Fixed in the launch, with `value_type=float` forced: a bare `LaunchConfiguration` arrives as a
**string**, and `get_parameter(name, double&)` fails a type mismatch exactly as it fails an
undeclared name — leaving the same uninitialized value. The workaround would have looked
applied and changed nothing.

### Then my own patch turned out to be the next bottleneck

With the periods set, the state timer still ran at **35 Hz against a requested 333 Hz**. Not
the RPC: a full state cycle measured **0.26 ms**, a 3854 Hz ceiling. The cause was `0001` —
making the *single shared* callback group `MutuallyExclusive` serialised **every** callback, so
the state timer queued behind ~50 ms image fetches. IMU, GPS, magnetometer and odometry all
ride that timer, so all were capped at the image rate.

`0003` gives each timer its **own** `MutuallyExclusive` group. The race `0001` fixed was
`drone_state_timer_cb` re-entering *itself*; a per-timer group still prevents that while
letting different timers run concurrently. Groups are stored as members because rclcpp's node
holds only weak references — a locally-scoped `shared_ptr` would be freed and the timer
silently orphaned.

### Result

| | before | after |
|---|---|---|
| RGB | 1.1 Hz | **31.2 Hz** |
| Depth | 1.1 Hz | **29.6 Hz** |
| GPU-LiDAR | 1.6 Hz | **17.4 Hz** |
| IMU | 1328 Hz, 77.3% duplicate | **366 Hz, 311 Hz distinct, 14.6% duplicate** |
| GPS / magnetometer / odometry | 1330 Hz (mostly duplicate) | **365 Hz** |

All required checks pass at navigation-grade rates. Depth reads 0.39 m to a 16312 m
no-return sentinel; IMU reads 9.807 m/s² at rest; LiDAR 1500/1500 sampled points non-origin.

**Not fixed, and worth stating:** the sim segfaulted after **57 minutes** of continuous run —
`Array index out of bounds: 18823 into an array of size 0`, preceded by a MAVLink `hil` EPIPE.
That is a stability ceiling nobody has characterised, and it is not the same bug as any of the
above.

> **Followed up 2026-08-03 — not reproduced.** A 90-minute soak of the full stack (PX4, MAVLink,
> the wrapper polling Scene/Depth/GPU-LiDAR, plus a concurrent RPC client) ran **74,253 captures
> with zero anomalies** and did not crash. Both candidate mechanisms are refuted: upstream's
> "segfaults every 2000 or so calls" (wrong by >30×) and the ~57-minute interval. This paragraph
> is left as written because it was accurate at the time; the current state is in
> [`docs/vendor/cosys-airsim.md`](../vendor/cosys-airsim.md).

## Next

1. ~~Put the `/clock` remap into the launch~~ — **done.**
2. ~~Reach the frozen frame convention~~ — **done.** See below.
3. Decide how the NWU→ENU conversion is done *once, in a tested place*, per
   `docs/lane-a/conventions.md` — Lane C must reach the frozen convention, not invent a second.
4. Chase the unexplained 7.342° yaw residual.
