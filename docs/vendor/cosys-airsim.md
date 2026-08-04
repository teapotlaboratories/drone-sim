# Local patches — Cosys-AirSim

Upstream: https://github.com/Cosys-Lab/Cosys-AirSim
**Pinned: tag `5.8-v3.4.1` · SHA `a552dd6c`** (a SHA, never a branch — `main` has already
migrated 5.5 → 5.6dev → 5.7pdev → 5.8, and there is no `5.5` branch upstream at all).

**The vendored tree is byte-identical to upstream.** `git status --porcelain vendor/` reports
zero modifications. The three applied deviations below live in `patches/cosys-airsim/` and are
applied by `scripts/build_airsim_wrapper.sh` to a **container-local copy** at `/airsim_root`,
never to `vendor/`.

**All three are upstream defects, not local preferences, and all three are worth reporting to
Cosys-Lab.** Each was found by running the thing, not by reading it.

**All three patch the ROS 2 wrapper (`ros2/src/airsim_ros_pkgs/`), not the UE plugin.** The
plugin binary `libUnrealEditor-AirSim.so` is **stock upstream** (md5 `2122e037`), which is the
configuration every image-quality measurement on 2026-08-03 was taken against — so those
findings apply to vanilla Cosys-AirSim, not to a patched tree.

> **A fourth patch exists and is deliberately NOT applied.**
> `patches/cosys-airsim/experimental/0004-scene-capture-ldr.patch` changes the `Scene` capture
> source from `SCS_FinalToneCurveHDR` to `SCS_FinalColorLDR` (`PIPCamera.cpp:178`). It is the
> only patch that would touch the **UE plugin**, and it is excluded because the build script
> globs `patches/cosys-airsim/*.patch` — the `experimental/` subdirectory is outside that glob.
>
> **It has never actually run.** It was built and deployed on 2026-08-02 and recorded as a
> negative result, but on 2026-08-03 the plugin loader was found to have been resolving a
> *different* copy: `inject_airsim.py --force` had left backups inside `Plugins/`, Unreal
> de-duplicates plugins by name+version and kept the stale one, so the patched binary was
> ignored. The md5 check that "verified" it inspected the file on disk, not the one the engine
> loaded. **The negative result says nothing about the patch.**
>
> **It is not needed.** The washout it was written to fix was traced to three other causes
> entirely (RGB read as BGR in the measurement client, the camera being inside world geometry,
> and Lumen GI being explicitly disabled). With those addressed on the **stock** plugin,
> AirSim's capture matches Unreal's own render of the same view to 1.15 of 255.
>
> See `patches/cosys-airsim/experimental/README.md` and
> `docs/worklog/2026-08-03-c11-washout-root-cause.md`.

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

preceded by one MAVLink `hil` `TcpClientPort socket send failed with error: 32` (EPIPE).

**Observed once (n = 1).** The "~57 minutes" is a single data point, not a measured period. An
earlier version of the Lane C backlog claimed it "gets more likely with more actors" — that had
no measurement behind it and is **withdrawn**; the crash predates any actor work.

### Source-level analysis — 2026-08-03, a candidate mechanism (not yet proven)

This is characterisable from the vendored source, and **upstream half-documents it**:

```cpp
// RenderRequest.cpp, ExecuteTask() — render thread
//below is undocumented method that avoids flushing, but it seems to
//segfault every 2000 or so calls
RHICmdList.ReadSurfaceData(rhi_texture, FIntRect(0,0,size.X,size.Y), results_[i]->bmp, flags);
```

The ordering matters. `setupRenderResource()` writes `result->width`/`height` **before**
`ReadSurfaceData` populates `bmp` — so a read that fails to populate leaves the dimensions set
and the buffer empty. The consumer then checks the wrong thing:

```cpp
// RenderRequest.cpp — game thread, after the wait
if (results[i]->width != 0 && results[i]->height != 0) {   // <- DIMENSIONS only, never bmp.Num()
    ...
    UAirBlueprintLib::CompressImageArray(width, height, results[i]->bmp, ...);
}

// AirBlueprintLib.cpp:1067 — indexes by width*height
for (int32 Index = 0; Index < width * height; Index++)
    uint8 TempRed = MutableSrcData[Index].R;               // <- empty src => exactly our assert
```

That reproduces the observed message shape precisely: an index bounded by `width*height` into an
array of size 0.

**But our ROS 2 wrapper does not take that path**, which is why this is a candidate rather than
a conclusion. `airsim_ros_wrapper.cpp` requests Scene as `ImageRequest(name, type, false, false)`
(`compress = false`) and Depth as `ImageRequest(name, type, true)` (`pixels_as_float = true`).
Both land in `else` branches that **iterate** `bmp` / `bmp_float` rather than indexing it, and
iteration over an empty array is safe. So either the crash came from an **RPC client requesting
`compress = true`** (the AirSim default — `ImageCaptureBase.hpp:41`), or it is a different array
entirely. Unresolved.

**A second, quieter defect in the same block**, independent of the crash: those safe branches do
`image_data_uint8.SetNumUninitialized(width * height * 3)` and then fill only `bmp.Num()` entries.
On an empty buffer that yields a **fully uninitialised image** — garbage pixels, published as if
valid, no error. Silent corruption is worse than a crash, and it is on the path Lane C actually
uses.

**The EPIPE is plausibly a symptom, not the cause.** The caller blocks in
`while (!wait_signal_->waitFor(5))`, logging `Failed: timeout waiting for screenshot` and looping
forever. A stalled capture therefore stalls the calling thread in 5-second blocks, which is
enough to time out PX4's MAVLink link and produce the EPIPE *before* the crash surfaces.

### Soak result — 2026-08-03: NOT REPRODUCED, and both hypotheses refuted

`scripts/soak_capture.py` (A/B) and `scripts/soak_full_stack.sh` (arm C). GPU 0 only.

| arm | configuration | result |
|---|---|---|
| A | isolated sim, one camera, no PX4, RPC `compress=true` | **6,000 calls / 249 s — survived**, 0 anomalies |
| C | **full Lane C stack** — PX4 + MAVLink + wrapper polling Scene/Depth/GPU-LiDAR, **plus** concurrent RPC `compress=true` | **74,253 calls / 90 min — survived**, 0 anomalies |

**Both candidate mechanisms are refuted as stated:**

- **The count hypothesis is dead.** Upstream's own comment claims the read "seems to segfault
  every 2000 or so calls". Arm C made **74,253** `compress=true` calls without incident — wrong
  by more than an order of magnitude. That comment should not be treated as characterising the
  failure.
- **The time hypothesis is dead.** Arm C ran clean *through* the 57-minute window, under a
  heavier load than the session that crashed, and continued to the 90-minute cap. The
  "~57 minutes" was almost certainly incidental to that one run, not a property of the simulator.

**The silent-corruption path also never fired**: zero empty buffers, zero short buffers, zero
frame-statistic outliers across 80k+ captures over both arms.

**This is "not reproduced", NOT "fixed".** The original is `n = 1` with no stack trace beyond the
assertion line. A clean 90-minute run is weak evidence against a rare event, and nothing here
identifies what actually fired that day. Arm B (`compress=false`) was not run: it exists only to
discriminate *if* an arm crashes, and with nothing crashing it would answer no question.

**What survives regardless of the crash.** The source-level defects found while forming these
hypotheses are real and independent of whether they caused this event:

1. the consumer guard checks `width`/`height` but never `bmp.Num()`, while `CompressImageArray`
   indexes by `width * height`;
2. the iterate-not-index branches `SetNumUninitialized(w*h*3)` and then fill only `bmp.Num()`
   entries, so an empty buffer yields a **fully uninitialised image published as valid** — on the
   path Lane C actually uses.

Both are worth reporting to Cosys-Lab on their own merits. (2) is the more dangerous: silent
corruption beats a crash for cost-to-diagnose.

**Status: an uncharacterised stability ceiling that has resisted one deliberate attempt to
reproduce it.** If it recurs, capture the full simulator log and the wrapper's state at the time
— the assertion line alone was not enough to work from.

---

## Build-layer notes (not deviations, but required to build at all)

1. `drone-sim/ros2:v1.16.0` lacks `geographic_msgs` and `mavros_msgs`.
2. The wrapper **must be built in place** — `airsim_ros_pkgs/CMakeLists.txt` reaches
   `../../../cmake/{rpclib_wrapper,AirLib,MavLinkCom}` via `add_subdirectory`.
3. The build **writes into its own source tree** (`external/rpclib/.../version.h`,
   `config.h` via `configure_file`), so it cannot be built from a read-only mount.

All three are handled by `scripts/build_airsim_wrapper.sh`, which also asserts the artifact of
each patch rather than trusting `patch`'s exit code.
