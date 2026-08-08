# 2026-08-08 — The renderer was dying on a LiDAR frame that was never read

**`SIM-23`.** The owner asked to chase the renderer crash. It is one bug, it has been firing
since 2026-08-02, and the crash report states the cause outright in a single line that had not
been read closely enough:

```
Array index out of bounds: 42257 into an array of size 0
```

**Headline: the out-of-bounds index is not the bug.** Every index in every report is a *legal*
offset. The array is empty, and it is empty because the code asked the GPU for pixels, was told
the read failed, threw that answer away, and told the physics thread the frame was good.

---

## What was actually crashing

`vendor/Cosys-AirSim/Unreal/Environments/Blocks/Saved/Crashes/` held 18 reports. They are not
one population:

| count | signature | reading |
|---|---|---|
| 13 | `ProcessCapturedBuffers` → `Array.h:1339` | **the bug** — 2026-08-02 → 08-07, still firing |
| 3 | `IsRunningCommandlet` (pid 7) | a different, editor-commandlet assertion |
| 2 | `VulkanRHI::VerifyVulkanResult` | 2026-08-01 only, before the ICD symlink fix |

The 13 are identical, and always on AirSim's physics thread — never the game thread:

```
FDebug::CheckVerifyFailedImpl2
ALidarCamera::ProcessCapturedBuffers
ALidarCamera::UpdateAsync             LidarCamera.cpp:371
UnrealGPULidarSensor::getPointCloud   UnrealGPULidarSensor.cpp:49
GPULidarSimple::updateOutput -> World::worldUpdatorAsync
```

They are spread across six days, 1–5 a day, the last at **2026-08-07 23:52** — during the
previous evening's WAN imagery work. This was never a today problem; it had simply never been
opened.

---

## Reading the evidence, in the order it mattered

**First wrong instinct: a data race.** `UpdateAsync` runs on the physics thread and the buffer
it reads is filled by `ServiceAsyncCapture` on the game thread, with no mutex between them —
only two `std::atomic<bool>` flags, which order the *flags* and say nothing about the *buffer*.
A `TArray` reallocating under a live reader produces exactly this assertion, so the race
hypothesis fits the stack perfectly.

**The reported sizes killed it.** All eleven distinct reports say `into an array of size 0`. A
mid-loop reallocation would give varied nonzero sizes. Zero, every time, means the reader was
handed an array that had never been filled at all.

The indices confirm the rest. `2948, 3405, 8768, 18823, 32088, 42257, 52937, 57062, 58626,
69923, 147705` are all legal offsets into `resolution_²`, so the bounds check that *is* there —

```cpp
if (h_pixel >= 0 && h_pixel < resolution_ && v_pixel >= 0 && v_pixel < resolution_) {
    FColor value_depth = async_buffer_2D_depth_[h_pixel + (v_pixel * resolution_)];
```

— was doing its job. It validates the *coordinates* against `resolution_`, a **setting**. It
never validates the *buffer*. Nothing in `LidarCamera.cpp` calls `Num()` or `IsValidIndex` on
any of the three async buffers, so the size the reader assumes is inferred and never checked.

**Where the empty array comes from.** `ServiceAsyncCapture`:

```cpp
render_target_2D_depth->ReadPixels(async_buffer_2D_depth_);
...
async_capture_ready_ = true;
```

`ReadPixels` is not `void`:

```
ENGINE_API virtual bool ReadPixels(TArray<FColor>& OutImageData, ...)
  -- Engine/Source/Runtime/Engine/Public/UnrealClient.h:113   (checked in the UE5.8 image)
```

It returns `false` and leaves the destination **empty** when the readback does not complete.
Upstream discards that bool and publishes readiness unconditionally. The physics thread believes
it, indexes the empty array, and the process dies.

---

## Why this stack hits it

`async_capture_mode` is not a setting. `GPULidarSimpleParams.hpp:62` assigns it **before** the
JSON is parsed:

```cpp
async_capture_mode = (simmode_name == AirSimSettings::kSimModeTypeMultirotor);
```

There is no `"AsyncCaptureMode"` key anywhere in the parser. It is hardcoded on for every
multirotor and **cannot be turned off from `settings.json`** — so "disable the async path" was
never available as a workaround. Our `sim/ue5/settings.json` runs a multirotor with a GPU-LiDAR
(`SensorType 8`) enabled, which is precisely the configuration this code path exists for.

Disabling the sensor was also rejected: the settings file records GPU-LiDAR as *"a large part of
why this stack replaced Isaac Sim"*. Removing the feature to dodge its bug is not a fix.

Upstream does have a warm-up guard — `do_capture = waited_frames_ >= wait_frames_` — a fixed
frame count standing in for *"is the readback working yet"*. A fixed count is a guess, and this
box shares one 3080 between the renderer, the LiDAR capture and the video witness.

This is very likely the same root cause as the `rpc::timeout … getGPULidarData` failures seen
during `SIM-20`.

---

## The fix

`patches/cosys-airsim/0006-gpulidar-empty-readback.patch`, two changes:

1. **`ServiceAsyncCapture`** — `capture_ok` is the AND of every `ReadPixels` that ran and the
   resulting `Num()`. `async_capture_ready_` takes that value instead of `true`. A frame that
   could not be read is a frame to drop, not to publish.
2. **`ProcessCapturedBuffers`** — refuse to enter the loop unless each buffer the loop will
   actually index holds `resolution_²` pixels: depth always, intensity and segmentation behind
   their `generate_` flags, matching the three index sites exactly. `return false` means "no new
   pointcloud this frame", which the caller already handles.

(1) is the bug. (2) is defence in depth, so any future path that gets readiness wrong costs a
dropped scan rather than the process.

The drop logs at **Warning**, naming the actual size, because that size is the diagnosis.

**Deliberately not changed:** the `FReadSurfaceDataFlags` built for the segmentation readback is
configured with `SetLinearToGamma(false)` and then **never passed to `ReadPixels`**, so
segmentation is read with default flags. A real upstream bug, but fixing it changes segmentation
pixel values — a behaviour change, not this crash.

---

## Build route

`convert_world.sh` **refuses** Blocks, correctly:

```
[inject] FATAL: refusing to inject into a path inside this repo: .../Environments/Blocks
```

That script is for the user's project; injecting into `vendor/` would dirty the vendored tree.
So the patch was applied to the Blocks plugin copy and compiled with the same
`Build.sh BlocksEditor Linux Development` invocation `convert_world.sh` uses. Only 0006 was
applied, not 0005, to keep the variable under test single — Blocks is not a World Partition
level, so 0005 buys it nothing.

A false alarm along the way: the build "exited 0" while printing FATAL. That was the exit code
of `tail` in the compound command, not the script — `convert_world.sh` returns 1 properly.

---

## Verification

The acceptance bar was deliberately two-sided. "It did not crash" is weak evidence for a bug
that fires 1–5 times a day: the fix has to be shown to have **encountered** the condition and
survived it. It took three steps to get there.

### 1. The artifact is the one that ran

`libUnrealEditor-AirSim.so` went `2122e0377ec9` (Aug 1) → **`d00f61af6542`**, and the new log
string is inside it:

```
$ strings -el .../libUnrealEditor-AirSim.so | grep 'readback incomplete'
GPU-LiDAR readback incomplete (depth %d px, need %d), dropping frame
```

UTF-16, because that is how UE stores `TEXT()` literals. No shadowing `.so` anywhere under
Blocks — the plugin-shadowing trap was checked, not assumed.

### 2. A real flight, with the LiDAR provably live

`run_park_tour.sh --keep-up`, 5 legs at 20 m:

```
verdict: PASS   worst 1.343 m   mean 1.139 m   landed=True
no collisions (2 ground contacts, expected at takeoff and landing)
```

Zero assertions, zero new crash reports. **But zero readback warnings too** — the fault did not
occur naturally, so on its own this run proves only that the patch broke nothing.

It does prove the crash path executes. Queried while parked:

```
getGPULidarData -> 40960 floats = 8192 points
```

8192 = 512 × 16, exactly `MeasurementsPerCycle` × `NumberOfChannels`. `ProcessCapturedBuffers`
was running continuously and completing full pointclouds.

### 3. The controlled A/B that actually proves it

Waiting for an intermittent fault is not evidence. So the fault was injected — one
`async_buffer_2D_depth_.Empty()` on the 100th capture, emulating precisely what a failed
`ReadPixels` leaves behind — and built **both ways**, same injection, same world, same bring-up:

| | fault fired | response | renderer | new crash dir |
|---|---|---|---|---|
| **B** — upstream + fault | yes | `Array index out of bounds: 260098 into an array of size 0` | **dead in 20 ms** | +1 |
| **A** — 0006 + fault | yes | `GPU-LiDAR readback incomplete (depth 0 px, need 262144), dropping frame` | **alive at 59 s** | 0 |

Variant B reproduced the production signature exactly — same assertion, same file and line, same
`array of size 0`. Variant A logged the size and flew on. `need 262144` = 512², so the guard
caught the size-0 case on the buffer the reader was about to index.

Each build was checked for both markers before running (`FAULTINJECT` present/absent,
`readback incomplete` present/absent), because a build that silently didn't take the change would
look identical from outside.

The fault-injected crash report was **deleted** afterwards — `Saved/Crashes/` is evidence, and
leaving a synthetic crash in it would poison the next person's count. Back to 18.

The final clean rebuild produced `d00f61af6542` again — byte-identical, so the same source gives
the same binary.

### 4. …and then the artifact changed again, which invalidated step 2

Writing the build script (below) applied **every** Unreal-side patch to Blocks, and 0005 turned
out **not to be there** — so the artifact became `20f5430c1a61`, and the park tour in step 2 had
flown something the repo no longer produces. Evidence that does not match the shipping binary is
not evidence. Re-flown on `20f5430c1a61`:

```
verdict: PASS   worst 1.335 m   mean 1.141 m   landed=True
no collisions (4 ground contacts, expected at takeoff and landing)
crash dirs: 18 (baseline 18)   assertions: 0   getGPULidarData -> 8192 points
```

---

## The fix did not ship, and neither had 0005

Patching Blocks by hand fixes this box and nothing else. Quickstart 0.2 builds the plugin with
upstream's `build.sh` from **pristine** vendor source, and nothing in the repo then applies
`patches/cosys-airsim/*.patch` to it. `convert_world.sh` does that job for a *user's* world and
refuses, correctly, to touch anything inside this repo — so the world we actually fly, and the
one the flight gate scores, was the only world that never received an Unreal-side patch.

`scripts/build_blocks.sh` closes that: apply the Unreal-side patches to the Blocks plugin, then
compile `BlocksEditor` in the engine container. It reuses `convert_world.sh`'s routing rule
(match the diff header `^+++ b/Unreal/`, never the prose), its three-way apply / already-applied
/ refuse-to-guess logic, and its timestamp-marker artifact check — a `.so` count cannot work
here, because the plugin directory ships with one and the check could never fail.

**Running it proved the gap was not theoretical.** It reported:

```
applied  0005-worldpartition-streaming-source.patch
already  0006-gpulidar-empty-readback.patch
```

**0005 was not in the Blocks plugin** — five days after it landed. `docs/vendor/cosys-airsim.md`
had even flagged the risk ("not yet wired into the build … `inject_airsim.py` copies the **built**
plugin from Blocks"), and it had come true quietly: every world injected from Blocks in that
window carried an unpatched plugin. Looking for one bug's build route found another bug's
missing one.

---

## The 90-minute soak — and what it does not prove

Fault injection proves the fix *handles* the condition. It does not prove the condition stopped
being fatal in the wild, so the last step was time under load.

`soak_full_stack.sh` already existed for exactly this crash — and **it had been pointed at the
wrong path**. Its only load arm was `simGetImages` with `compress=true`, which never touches
`ProcessCapturedBuffers`. That is why its 74,253-call survival on 2026-08-03 was read as evidence
against the hypothesis: the arm could not have reproduced the fault it was built to reproduce.
Two things were added rather than writing a new harness:

- **`_soak_gpulidar.py`** — a GPU-LiDAR arm, so the path the crash is actually on is driven.
- **A drop counter in the sampling loop.** With 0006 an empty readback is survivable, so
  "the simulator is still up" no longer separates *the fault never happened* from *the fault
  happened and was handled*. A soak that caught one would otherwise look identical to one that
  caught none — the precise way the earlier arm was misread.

**And the vehicle was parked**, which was wrong. All 13 crashes happened during flights, and a
static scene is plausibly cheaper on the readback path. A continuous park-tour loop was added
against the live stack.

Result — 90 minutes, all three loads concurrent:

| | |
|---|---|
| simulator | **survived 5405 s** |
| flights | **45/45**, every leg ok, all landed |
| worst error | max 1.996 m · median 1.448 m · min 1.339 m — **0 over the 2.0 m tolerance** |
| GPU-LiDAR calls | 4,991,456 — 0 errors, 0 short clouds |
| image calls | ~82,000 |
| `readback incomplete` | **0** |
| assertions / new crash dirs | **0** / 18 against a baseline of 18 |

**Read this carefully, because it is absence evidence.** 45 consecutive flights and 90 minutes
past the historical ~57-minute failure point, on a stack that was producing 1–5 of these a day,
is real evidence the crash is gone. It is *not* evidence of the fix catching a spontaneous fault,
because none occurred. That remains proven only by injection.

One honest limitation in the arm: `stale: 4,937,495` of 4,991,456 samples. Polling
`getGPULidarData` returns the *cached* cloud rather than forcing a new readback, so the arm added
RPC and GPU contention but never raised the readback rate above the sensor's own 10 Hz. This was
realistic load over time, not accelerated load. A soak that genuinely accelerates the readback
path would need to drive the sensor's update rate, not the API in front of it.

---

## This closes an investigation that was open since 2026-08-03

`docs/vendor/cosys-airsim.md` carried a section titled **"Known upstream instability,
uncharacterised"**: a segfault after 57 minutes, `n = 1`, recorded as

```
Array index out of bounds: 18823 into an array of size 0
```

**`18823` is one of the thirteen.** The report holding it is
`crashinfo-Blocks-pid-1-019FC031…`, and it contains `ALidarCamera::ProcessCapturedBuffers` and
**no `RenderRequest` or `CompressImageArray` frame at all**.

That investigation reasoned from the message *shape* — "an index bounded by `width*height` into
an array of size 0" — to `RenderRequest.cpp` / `CompressImageArray`, a genuinely similar
dimensions-set-but-buffer-empty defect in the image path. The analysis of that code was correct
and the defect it describes is real. It was simply not the array that was crashing, and the
soak that refuted it (74,253 RPC calls, survived) could not have reproduced this one: it
exercised the **image** path, while the crash lives on the **LiDAR** path.

The lesson is narrow and worth keeping: *two call sites can produce the same assertion text.*
The stack in the report distinguished them the whole time.

