# 2026-08-01 — `C-09`: lockstep is dead code, a stale EKF origin, and Lane C flies

**Task:** `C-09` — find out why the Lane A controller arms against Lane C but never climbs.
**Lane:** C. **SITL only** — no real aircraft, nothing real armed or flown.

> Kept as the work happens.

---

## Two findings, and only one of them is the cause

Both were on `C-09`'s list. Both turned out to be real defects. **They are not the same bug,
and the loud one is not the culprit** — worth stating plainly, because I went in expecting
lockstep to explain everything and it does not.

| Finding | Real? | Causes the takeoff failure? |
|---|---|---|
| `LockStep: true` is silently ineffective | **yes, confirmed twice over** | **no** |
| Vehicle reports 35.17 m altitude while sitting on the ground | **yes** | **yes — this is it** |

---

## Finding 1 — lockstep is dead code (confirmed, but a red herring here)

Filed in `versions.lock` as *"LOCKSTEP MAY BE DEAD CODE"* from reading. It is not a maybe.

`MavLinkMultirotorApi.hpp` at the vendored SHA has exactly **four** mentions of the flag:

```
:66    initialize()       lock_step_enabled_ = connection_info.lock_step;   SET
:1613  handleLockStep()   if (!lock_step_active_ && lock_step_enabled_)     READ
:1916  resetState()       lock_step_enabled_ = false;                       CLEAR
:1993                     bool lock_step_enabled_ = false;                  DECL
```

`initialize()` sets it at `:66`, then calls `openAllConnections()` at `:68` — which clears it
**twice** before returning, once via `close()` → `disconnect()` → `resetState()` (`:957`) and
once directly (`:992`). Nothing sets it again. So the `:1613` guard can never pass,
`lock_step_active_` never becomes true, and `"Enabling lockstep mode"` can never be emitted.

**Runtime agrees, and this is the part that makes it evidence rather than inference:**

```
"Enabling lockstep mode"     0 occurrences across a full session
"Waiting for mavlink vehicle"  present   <- same addStatusMessage path, so the channel works
sim clock 10.960 s / 11.922 s wall  ->  RTF 0.9193
```

The control matters: another message from the *same* logging path does appear, so the silence
is real and not a swallowed log. And an RTF that smoothly tracks wall time is what
free-running looks like, not stepping.

**Consequence: every timing number Lane C produces is a free-running number.** `"LockStep":
true` in `settings.json` is left in place deliberately — it states the intent and starts
working the moment the patch lands — but nothing may quote a Lane C RTF or latency as
deterministic until then. Recorded in `versions.lock`, `settings.json`, and this file.

**But it does not explain the takeoff failure**, and I nearly let it. Free-running SITL flies
fine; it degrades under load, which is a Phase 2 problem, not an arming one.

---

## Finding 2 — the vehicle thinks it is already at 35 m

Instrumented a run instead of inferring from the controller's timeout. The altitude channel
settled it in one line:

```
z (NED, negative = up):  min=-35.168  max=-35.166  samples=9415
```

**Constant to within 2 mm across 9,415 samples.** The vehicle believes it is at 35.17 m
altitude, and it never moves. One query pinned the arithmetic exactly:

```
ref_alt            88.113 m     <- EKF local-origin altitude
altitude_msl_m    123.280 m     <- GPS
                  -------
difference         35.167 m     <- exactly the stuck z
dist_bottom          0.0999 m   <- and it is sitting on the ground
z_valid true   xy_valid true   fix_type 3   satellites_used 15
```

So the EKF's local origin sits 35.17 m below where GPS says the vehicle is. Nothing is drifting
and nothing is broken in the estimator's own terms — `z_valid` is true and the value is rock
steady. The **origin is simply in the wrong place**, and `GPS Vertical Pos Drift too high` is
that same 35 m disagreement wearing a misleading name.

### Why that stops the flight, exactly

```
offboard_control.py:344   self.home_enu = (cur[0], cur[1])          <- x, y only; z DROPPED
offboard_control.py:395   self.target_enu = (home[0], home[1], self.alt)   <- z = 10.0 ABSOLUTE
offboard_control.py:425   _reached()  ->  proximity, accept_radius = 1.0 m
```

The takeoff target is **absolute ENU z = 10 m**, not *current + 10*. In Lane A the vehicle
rests at z ≈ 0, so 10 m absolute is 10 m above ground and correct. In Lane C it reports
+35.17 m, so **the controller is commanding a 25 m descent into the ground.** The proximity
test then measures a 25.17 m error against a 1 m radius, never passes, and times out — while
PX4, having armed and not taken off, auto-disarms via `COM_DISARM_PRFLT`.

Every symptom is now accounted for, including the two that looked like control bugs.

### Ground truth settles it: the vehicle is on the ground, PX4 is wrong

`dist_bottom` read 0.0999 m but with `dist_bottom_valid: false`, which left one thing genuinely
open: is the vehicle *actually* parked 35 m in the air, or on the ground with a bad origin?
AirSim's ground truth answers it independently of PX4's estimator:

```
simGetGroundTruthKinematics -> position z = +0.620 m   (NED: on the ground at spawn)
getHomeGeoPoint             -> altitude   = 123.279 m
PX4 /fmu/out/vehicle_gps_position altitude_msl_m = 123.28 m    <- AGREES with AirSim
PX4 /fmu/out/vehicle_local_position ref_alt      =  88.113 m   <- 35.17 m TOO LOW
```

**The vehicle is not spawned high — it is on the ground, and PX4's GPS input is correct.**
AirSim's home altitude and PX4's GPS agree to 1 mm. The error is isolated to PX4's local-origin
altitude, which sits 35.17 m below its own GPS.

### I inferred the wrong cause from that, and measuring refuted it

From the above I concluded the EKF had anchored height to the **barometer**, and that baro and
GPS disagreed by 35 m. That was an inference, not a measurement, and it was **wrong**. Querying
the sensors directly:

```
getBarometerData -> altitude 122.883 m,  pressure 99856.85,  qnh 1013.25
getGpsData       -> altitude 123.280 m
                    the two AGREE, within 0.4 m
```

**Both AirSim sensors are correct and consistent.** There is no sensor disagreement. The 35 m
lives entirely in PX4's `ref_alt` — an origin established at 88.113 m and then never revised,
while both sensors reported ~123 m the whole time.

That reframes it: not a *sensor* problem but a **stale EKF origin**, which makes it a **startup
ordering** problem. PX4 set its local origin before the vehicle had settled at its final
altitude, and an EKF origin is set once.

### Confirmed by restarting PX4 alone

The sim kept running untouched; only `lane-c-px4` restarted:

```
before:   ref_alt  88.113 m    z = -35.167 m
after:    ref_alt 123.280 m    z =  -0.0002 m
```

`ref_alt` now matches GPS and AirSim exactly, and z is zero because the vehicle is on the
ground — which it always was. **One container restart, no config change, offset gone.**

`OriginGeopoint` was the wrong fix and setting it would have changed nothing. Worth recording
plainly: the earlier entry in this file reasoned from `ref_alt` to a sensor cause without
querying the sensors, and one RPC call would have caught it. **Measure the thing, do not infer
it from the thing downstream of it** — the same lesson as the bound port that proved nothing.

### This also explains the intermittent bring-up

An order-dependent origin is exactly the shape of a defect that works some runs and not others,
which is what `C-03` recorded and could not pin down. Same root, two symptoms.

### The controller is not at fault, and should not be patched

It is byte-identical to the one that scores 10/10 in Lane A. It is correct for any simulator
whose local origin is at ground level. **The fix belongs in the sim**, and keeping it there is
the whole point of the parity claim — a controller that needs per-lane altitude fudging is not
the same controller.

**Latent coupling worth naming:** dropping z at `:344` while treating the z target as absolute
is an unstated assumption that ground ≈ z 0. It holds in Lane A by convention, not by
construction, and Lane C is what surfaced it. Capturing `cur[2]` as a home reference would make
the controller origin-agnostic — but that is a **deliberate open question**, not a silent fix:
it changes what `takeoff_altitude` means (AGL vs local-frame absolute), so it needs to be
decided rather than patched in mid-diagnosis.

---

## It flies

With the origin correct, the **unmodified** Lane A controller flew the whole mission in Lane C:

```
reached takeoff altitude 10.0 m
waypoint 1/4 reached (error 0.78 m)
waypoint 2/4 reached (error 0.79 m)
waypoint 3/4 reached (error 0.78 m)
waypoint 4/4 reached (error 0.78 m)
landed and disarmed
outcome: success   waypoints_reached 4/4   errors [0.78, 0.787, 0.775, 0.775]
```

Peak altitude −10.233 m NED against a 10 m target; AirSim ground truth and PX4's EKF tracked
each other to within 0.8 m through the flight. **`C-09`'s done-when is met: the Lane A
controller, byte-unchanged, reaches all four waypoints in Lane C.** Not having patched the
controller is what makes that mean anything.

**Reproduced.** A second cold run of the same mission also returned `success` 4/4, errors
0.785 / 0.771 / 0.782 / 0.774 m — so the first was not a fluke of one origin initialisation.

Recorded: `out/lane-c/lane-c-flight-SUCCESS-2026-08-01.mp4` (88.3 s) and a before/after cut,
`out/lane-c/c09-before-after.mp4` (81.2 s), composed by
`scripts/compose_c09_beforeafter.py`.

**A false caption nearly shipped.** The overlay hardcoded `(on the ground)`, which was true in
the failure run and stayed on screen through a 10 m hover in the success run — the video would
have asserted the drone was grounded while visibly flying. Caught on a still before sending,
fixed to derive the label from the measured altitude, and the success run was **re-recorded**
rather than shipped with a caveat. Cheap to fix, and a caption that contradicts its own footage
discredits the evidence it is supposed to carry.

## Next

1. **Make the ordering deterministic in the bring-up**, so this cannot regress: PX4 must
   initialise its EKF origin only after the vehicle has settled. A restart is the *diagnosis*,
   not the fix — the launch layer should enforce it, and a gate should assert `ref_alt` matches
   GPS before a run counts.
2. **Patch lockstep separately** — restore `lock_step_enabled_` from `connection_info_` rather
   than forcing false in `resetState()`, which also survives reconnects. Vendored C++, so it
   needs a recorded patch plus a plugin rebuild; two copies of the header exist
   (`AirLib/...` and `Unreal/Plugins/AirSim/Source/AirLib/...`) and **both** must be patched.
3. **Decide the AGL-vs-absolute question** above before touching the controller.

## Video evidence

Recorded the run: `out/lane-c/lane-c-flight-2026-08-01.mp4` (30.8 s, SITL only). Two camera
feeds — `front_center` and `bottom_center` — with a telemetry band burning in the comparison
that matters:

```
AirSim TRUTH  z =  +0.620 m   (on the ground)
PX4 EKF       z = -35.167 m   -> believes it is 35.2 m UP   (off by 35.8 m)
```

`bottom_center` shows the vehicle's own arms against ground that is plainly a fraction of a
metre away, for the whole 30 s, while the band reports a 35 m altitude. The bug is legible
without reading a single log line.

Tooling kept as `scripts/record_lane_c_flight.py` and `scripts/airsim_rpc_client.py`.
The RPC client is hand-written rather than `pip install airsim`: **`msgpack-rpc-python` pins
tornado 4.x, which does not import on Python 3.12**. Two details were read out of the vendored
source rather than copied from upstream docs, and both would have broken a guessed client —
`ImageRequest` carries a fifth field (`annotation_name`, a Cosys-AirSim addition), and
`simGetImages` binds `(requests, vehicle_name)`, not the three-arg form with `external`.

**Two bugs in my own capture, both silent:** `ros2 topic echo` redirected to a file is
block-buffered, so the overlay read an empty file (fixed with `stdbuf -oL`); and the echo
interleaves `---` separators, which pass a naive "starts with `-` so it is a number" test and
then raise inside a `try` that wrapped the whole loop — printing `...` for an entire recording
rather than failing. The first video was produced, looked fine, and showed no EKF value at all.
A capture that silently degrades to blank is the same class as the version-recording layer that
wrote empty values while the build passed.

## Process note

Cleared `res.json` before this run rather than trusting mtime — the `P1-01` stale-artifact
trap that already bit this investigation once.
