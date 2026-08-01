# 2026-08-01 — `C-03`: PX4 ↔ Cosys-AirSim, and `/fmu/*` parity proved

**Task:** `C-03` — run the simulator, link PX4 over the MAVLink SITL API, and diff `/fmu/*`
against Lane A.
**Lane:** C. **SITL only** — no real aircraft, nothing armed, nothing flown.

> Kept as the work happens.

---

## Result first

**The acceptance criterion is met.**

```
Lane A: 51 /fmu/ topics   (24 /fmu/out)
Lane C: 51 /fmu/ topics   (24 /fmu/out)
diff  : IDENTICAL
```

`C-03` asked for *"identical topic names, transport swapped only — verified by diffing, not
by inspection."* Evidence kept at `out/lane-c/fmu-topics-lane-{a,c}.txt`.

**This is the sim-to-real parity claim, proved rather than asserted.** The controller written
for Lane A subscribes to the same names against Unreal that it does against Gazebo — and the
same names the real Pixhawk 6C will produce.

The link itself:

```
INFO [simulator_mavlink] using TCP on remote host 127.0.0.1 port 4560
INFO [simulator_mavlink] Simulator connected on TCP port 4560.
INFO [lockstep_scheduler] setting initial absolute time to 1785599227656109 us
INFO [uxrce_dds_client] init UDP agent IP:127.0.0.1, port:8888
```

MAVLink drives the sim physics handshake; XRCE-DDS drives the autonomy code. Exactly the
division `04` describes and `versions.lock` records.

---

## Getting there: five failures, four of them already in this repo's notes

### 1. The renderer could not start at all — Vulkan ICD path mismatch

`UnrealEditor -RenderOffScreen` died with `exit 139` (SIGSEGV), preceded by:

```
LogVulkanRHI: Error: vpCreateInstance(...) failed, VkResult=-9    (INCOMPATIBLE_DRIVER)
```

Asked the loader directly instead of guessing, and it said so plainly:

```
ERROR: [Loader Message] /usr/lib64/libGLX_nvidia.so.0: cannot open shared object file
```

**The host is Bazzite (Fedora-family), so its CDI spec injects an ICD naming the Fedora
library path.** This container is Ubuntu, where the driver lives under multiarch at
`/lib/x86_64-linux-gnu/`. Two ICDs are present and they disagree:

| file | library_path | api |
|---|---|---|
| `nvidia_icd.json` | `libGLX_nvidia.so.0` (soname) | 1.3.204.1 |
| `nvidia_icd.x86_64.json` *(CDI-injected)* | `/usr/lib64/libGLX_nvidia.so.0` | 1.4.341 |

Fixed with a symlink in `docker/lane-c.Dockerfile`. Verified with `vulkaninfo`: **zero usable
devices before, `NVIDIA GeForce RTX 3080 / DISCRETE_GPU / 1.4.341 / driver 610.43.03` after.**

**This is a PATH mismatch, not the too-new-driver incompatibility that deferred Lane B.**
610.43.03 works fine once the loader can find it. Worth stating clearly, because the
superficial resemblance to `P0-09` invites the wrong conclusion.

*The link target is itself a symlink CDI creates at run time, so it dangles at build time and
resolves at run time. Do not "fix" that by pointing at the versioned `.so.610.43.03`, which
would break on a host driver update.*

### 2. `--settings /path` silently became a map name

Unreal took the space-separated argument as a level to load:

```
LogUObjectGlobals: Warning: LoadPackage can't find package /settings.json.
LogLoad: Error: Failed to enter /settings.json
```

`SimHUD.cpp:369` documents the real form: **`-settings=`** — single dash, `=`-joined. With it,
`listVehicles` returns `['PX4']` instead of the default `['SimpleFlight']`, which is how you
can tell the file was actually read.

### 3. "Port 41451 is bound" was not evidence of anything

Reported the host port as proof AirSim was up. It was not: **`docker -p` makes the host listen
via docker-proxy immediately, whether or not anything inside the container is.** The
connection reset gave it away. Real evidence is an RPC response:

```
getServerVersion -> 4      ping -> True      listVehicles -> ['PX4']
```

Note `getServerVersion` returning **4** against the client's minimum of 4 — the same handshake
that *failed* against CARLA, which reported version 1.

### 4 & 5. Silent `/fmu/out/*` — both causes already documented here

Topics listed, nothing published. Two distinct known failures, both live:

- **`D-02`: shared netns is not enough.** Fast-DDS discovers over UDP but *delivers* over
  shared memory, and each container has its own `/dev/shm`. Docker confirmed it with the exact
  error `D-02` records: `failed to join IPC namespace: non-shareable IPC (hint: use
  IpcMode:shareable for the donor container)`.
- **`P1-02`: `/fmu/out/*` publishers are BEST_EFFORT.** A default RELIABLE subscription matches
  nothing and sees zero messages **on a healthy stack** — so `ros2 topic hz` without a QoS
  override was never a valid probe.

With `--ipc shareable` on the sim, `--ipc container:lane-c-sim` on the joiners, and
`--qos-reliability best_effort`:

```
/fmu/out/sensor_combined         DATA
/fmu/out/timesync_status         DATA
/fmu/out/vehicle_local_position  DATA
sim clock: +5016 ms over ~4 s wall
```

**Four of the five failures were already written down in this repo.** Lane A's lessons
transferred to Lane C essentially unchanged — a concrete argument that keeping Lane A alive as
the reference was right.

---

## Two findings that outlive this task

### Lane A and Lane C collide on ports

Lane A publishes **4560, 8888, 14540, 14550, 18570** — exactly what Lane C needs. Discovered
by `Bind for 127.0.0.1:4560 failed: port is already allocated` when the gate had left Lane A
running.

**They cannot both run with published host ports on this box.** The parity diff above had to
be captured **sequentially** — Lane C, tear down, Lane A, compare.

That is a real constraint neither `D-06` nor `lane-c/todo.md` accounts for, and it will bite
`C-07`, whose whole purpose is comparing a Lane C run against a Lane A baseline. Options are
distinct `ROS_DOMAIN_ID`s plus non-overlapping published ports, or accepting that comparison
is always sequential and saying so.

### The derived-data cache does not survive container recreation

First launch spent **~3 minutes compiling shaders**; the DDC lives in the container's writable
layer, so `docker rm` threw it away and the next launch paid it again. Mounting a named volume
at `/home/ue4/.config/Epic` cut startup to **9 seconds**. Worth doing before any iteration
loop, and worth recording in `D-04`.

---

## Deliberate divergence from upstream, recorded in `sim/ue5/settings.json`

Upstream's PX4 example sets `NAV_RCL_ACT=0` and `NAV_DLL_ACT=0`, disabling the RC-loss and
datalink-loss failsafes. **Not done here.**

Lane A leaves the GCS-datalink check **enforced** (`NAV_DLL_ACT=2`, set by the x500 airframe)
and satisfies it with a real QGroundControl service, because PX4 refusing to arm without a
datalink is a property of the real aircraft too. Disabling it in Lane C would make the lanes
diverge exactly where sim-to-real parity matters, and hide an arming failure the real
Pixhawk 6C would still have.

**Consequence: arming in Lane C will need a datalink the same way Lane A does.** Intended, not
an oversight.

---

## What is NOT proved

- **Nothing has flown.** No arming, no takeoff, no waypoints. The vehicle has not moved.
- **`/fmu/out/vehicle_status` was silent** in the sample while three other topics carried
  data. Probably a slower publish rate than the 12 s window, but that is a guess — it has not
  been chased down.
- **Lockstep is engaged but not characterised.** PX4's `lockstep_scheduler` initialises and the
  sim clock advances, but the measured +5016 ms over ~4 s wall is too coarse to distinguish
  true lockstep from free-running, and the research warning still stands that
  `resetState()` may clear the flag. **Do not quote an RTF from this.** A proper aggregate
  measurement is still owed.
- **Sensor data is unverified.** `sensor_combined` publishes; nobody has checked the values
  are physically sensible or that IMU–camera timestamps align.

## Flying the Lane A controller against Lane C — arms, does not climb

Ran the **unmodified** `offboard_control` node from Lane A, same package built from the same
source, only the simulator underneath changed.

```
FCU alive; home ENU=(0.00, 0.00) waypoints=[(10,0,10), (10,10,10), (0,10,10), (0,0,10)]
wait_for_fcu -> stream_setpoints -> request_offboard -> armed        ✓
FAILED: timeout in state takeoff                                     ✗   0/4 waypoints
```

**Arming works, and that is a real result.** `NAV_DLL_ACT` was deliberately left enforced
rather than disabled as upstream's example does, so PX4 refused to arm without a GCS datalink
— QGC supplied it, exactly as on the real aircraft. The offboard handshake also succeeded:
AirSim logs `MavLinkVehicle: confirmed offboard mode`.

### The first failure was my config bug, and it was actively misleading

Initially the controller reported `armed` then `timeout in state takeoff`, which reads like a
control bug — wrong setpoint, bad handshake, a frame error. It was none of those:

```
PX4:    Preflight Fail: ekf2 missing data
PX4:    Preflight Fail: height estimate not stable
PX4:    Ready for takeoff!  ->  Disarmed by auto preflight disarming
```

**An AirSim `Sensors` block REPLACES the defaults, it does not extend them.**
`AirSimSettings.hpp:1874` — *"creates default sensor list when none specified in json"*. I had
listed only a barometer, to attach the `PressureFactorSigma` tweak, which left the vehicle
with **no IMU, no GPS and no magnetometer**. PX4 armed on a partly-satisfied preflight,
produced no thrust for want of a height estimate, and auto-disarmed via `COM_DISARM_PRFLT`
while the controller correctly waited out its own timeout.

Fixed by listing all four defaults explicitly. The whole failure signature is now written into
`sim/ue5/settings.json` so the next person seeing "armed then takeoff timeout" does not go
hunting in the controller.

### After the fix: better, still not flying

```
before:  Preflight Fail: ekf2 missing data / height estimate not stable
after:   Preflight: GPS Vertical Pos Drift too high
         Ready for takeoff!  ->  Disarmed by auto preflight disarming
```

The complaint moved from *missing data* to *drift* — the difference between having no GPS and
having one that will not settle. Confirms the sensor diagnosis. **But the vehicle still never
climbs**, PX4 still auto-disarms, and the controller still times out at 60 s. Reproduced twice.

### Where to look next, in order

1. **Lockstep.** The strongest hypothesis. If AirSim is free-running while PX4's
   `lockstep_scheduler` is active, sensor cadence and sim time diverge and GPS vertical drift
   is exactly what an EKF would report. This is also the `resetState()` warning from the
   research — `initialize()` sets `lock_step_enabled_`, `openAllConnections()` clears it — and
   it would explain the **intermittent bring-up deadlock** seen earlier just as well.
   **Settle this before anything else; several symptoms hang off it.**
2. **`OriginGeopoint`** is unset in `settings.json`, so AirSim's GPS origin and PX4's
   `LPE_LAT`/`LPE_LON` may disagree. `04` flags this as where AirSim+Cesium coordinate
   mismatches bite.
3. **Whether AirSim physics steps at all** — command the vehicle through AirSim's own RPC with
   PX4 out of the loop. If it does not move there either, the problem is upstream of PX4
   entirely. Requires publishing 41451.

### Two process notes

- **The result artifact went stale between runs.** `out/lane-c-flight.json` from the previous
  attempt was still on disk and read as if it were the new one. That is exactly the `P1-01`
  bug — `run_flight` scoring a seed from a twenty-minute-old file. Caught by checking mtime;
  the real fix is clearing the artifact before each run, as `run_flight` now does.
- Two bad probes of my own: `/fmu/out/actuator_motors` does not exist (it is `/fmu/in/`), and
  a `ConnectionRefused` on the RPC meant only that I had stopped publishing port 41451.

## Status

**`C-03`'s stated criterion is met** — topic parity, proved by diff. **The controller does not
yet fly in Lane C**, which the criterion did not ask for and which is now the next task rather
than a retroactive widening of this one.

## Next

- Settle the lockstep question — it plausibly explains both the GPS drift and the intermittent
  bring-up deadlock.
- Arm and fly the Lane A controller unchanged against Lane C — the real parity test, since
  identical *names* is necessary but not sufficient.
- Characterise lockstep properly and settle the `resetState()` question.
- Resolve the Lane A / Lane C port collision before `C-07`.
