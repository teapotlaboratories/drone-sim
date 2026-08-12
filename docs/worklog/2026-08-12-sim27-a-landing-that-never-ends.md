# 2026-08-12 — A landing that never ends, and two diagnoses that were wrong

**`SIM-27`.** The 10-seed flight gate came back **9/10** and the failing seed reported
`timeout in state land`. Four days later the root cause is still open — but the failure is
characterised, two confident explanations have been withdrawn, a defect was found in the tooling
built to investigate it, and the gate now passes **40/40**.

**Headline: the most useful output of this investigation is the list of things it ruled out.**
Every cheap explanation was tried and none survived. That is worth more than the tidy story I
twice tried to tell.

---

## What failed

Seed 5 of 10. The mission itself was flawless — 4/4 waypoints at 0.770–0.777 m, the *best*
errors of all ten seeds. Then it entered `land` and never came out.

```
descent at a steady 0.694 m/s  == MPC_LAND_SPEED exactly
from 20 m, straight past the takeoff surface, to ~30 m below it
never touched down -> never disarmed -> 60 s state budget expired
```

Collisions 0. LiDAR drops 0. Both of those are *positive* statements rather than absences,
because `SIM-22` and `SIM-24` had just landed — the first time this harness could say "not an
impact, and not lost sensor data" rather than shrugging.

---

## Wrong diagnosis #1: it fell through the ground

That is what the telemetry says, and I wrote it up as established fact, citing the flight video
as confirmation. The owner looked at the video and said the drone appears to land perfectly.

They were right, and the claim collapsed on two counts:

**"Two independent sources agree" was the load-bearing error.** PX4's EKF is fed by AirSim's
*simulated sensors*, which are generated from AirSim's *physics integrator*. The bag and the
burned-in ground truth trace to **one** source. Their agreement proved far less than claimed.

**"The bottom camera shows blank void" was not evidence.** Seed 1 landed successfully and its
bottom camera shows the identical uniform cream.

Measured rather than eyeballed, second time around — PSNR between two front-camera frames,
higher meaning more identical:

| | span | PSNR |
|---|---|---|
| seed 5, across a physics-reported 23 m descent | 21 s | **26.11 dB** |
| seed 1, a genuine descent | 21 s | **13.17 dB** |

A real descent transforms the view. Seed 5's barely changed. Frame hashes 4 s apart all differ,
so the stream was live, not frozen.

---

## Wrong diagnosis #2: physics and render disagree, unknown which is right

Better, and still too strong. It became a mechanism read out of the vendored source:

```cpp
// PawnSimApi.cpp:662 — the per-tick pose update
SetActorLocationAndRotation(position, orientation, true);   // bSweep = true -> ground can BLOCK it
// FastPhysicsEngine.hpp:102 — the only way back
if (body.isGrounded() || (collision_info.has_collided && ts != last_ts)) { ... }
```

Two copies of "where the drone is": the physics integrator, and the Unreal actor the cameras are
bolted to. Physics → actor is a swept move the ground can block; actor → physics only happens on
a *new* collision event. Block the actor, miss the event, and the integrator free-runs while the
camera sits on the ground.

Coherent, sourced, consistent with everything. **And I could not make it happen.**

---

## What PX4 was doing, since "PX4 thinks it is falling" is itself alarming

It was doing its job:

| | |
|---|---|
| reported `vz` | +0.703 m/s |
| measured `dz/dt` | +0.694 m/s |
| EKF `z` / `vz` resets | **0** |
| `z_valid`, `v_z_valid` | `True` throughout |

Self-consistent, no resets. **Lied to, not broken.**

And it could not have known better. Every sensor AirSim synthesises derives from that one
integrator state — checked specifically for the rangefinder, since "add a distance sensor" is the
obvious fix:

```cpp
// UnrealDistanceSensor.cpp — getRayLength()
Vector3r start = pose.position;              // the PHYSICS pose
UAirBlueprintLib::GetObstacle(actor_, ...);  // actor_ only supplies the World
```

The ray starts wherever the integrator thinks the vehicle is. **No sensor you could add creates
an independent signal.** Exactly two things read the Unreal actor: the cameras, and
`simGetVehiclePose`. Which is precisely why the video was the only place this ever showed up.

**So the real finding is architectural**: the simulator holds one source of truth for state and
one unrelated source for imagery, with nothing reconciling them. A divergence is invisible to the
entire flight stack by construction.

---

## Four experiments, all negative

| experiment | runs | result |
|---|---|---|
| full missions, persistent stack | 12 | no failure |
| cold gate seeds | 8 | no failure, **probe captured nothing** (harness bug) |
| position hold 8 m *below* the floor | 45 s | no split; reached +0.702 m and stopped |
| real `AUTO.LAND` touchdowns | 40 | 40/40 clean, 47 collision events, no split |
| the same under 100 Hz flag-stealing | 36 | 36/36 clean |
| **cold gate seeds, exact failing config** | **40** | **40/40 clean, no split in 20,750 samples** |

The 88 middle runs were fairly criticised: persistent stack, 6 m hops, no video, no witness, no
waypoint mission. Volume in the wrong configuration. Only the 40-seed run and the earlier 8 were
true repeats.

**Rate revised:** 1 failure in 58 cold seeds ≈ **1.7%**, down from 5.6%. P(0 in those 40 | the old
estimate) = 0.10, so 5.6% is now unlikely.

---

## The defect the investigation found in itself

Hunting a *flag-stealing* theory turned up this:

```cpp
// RpcLibServerBase.cpp:435 -> PawnSimApi.cpp:507
getCollisionInfoAndReset() { ...; state_.collision_info.has_collided = false; }   // ON READ
```

`simGetCollisionInfo` is **read-and-reset**. It did not explain `SIM-27` — 36 landings at 100 Hz
polling stayed clean, because `isGrounded()` and Unreal's re-firing of hit events repair a stolen
flag. But it caught this ticket's own tooling: `probe_landing.py` was about to be wired into
every run *while also calling that RPC*, a second consumer racing `watch_collisions.py` (20 Hz),
which decides gate PASS/FAIL. It would have silently eaten impacts the witness needed —
reintroducing exactly the blindness `SIM-22` exists to prevent, from inside the tool built to
diagnose `SIM-27`. Call removed; the witness is now documented as sole owner.

Review then found a worse one in the shared RPC client:

```python
for msg in self.unp:
    if msg[0] == 1:        # returned the FIRST reply seen -- no msgid check
        return msg[3]
```

One lost reply desyncs the stream permanently, so `simGetVehiclePose` would return the
*kinematics* answer and `dz` would read 0.000 for the rest of the run — the divergence detector
reporting perfect agreement precisely when the RPC was sick. Fixed by matching `msgid`, proved by
feeding a stale reply ahead of the correct one.

---

## What the 40-seed run gave us instead

```
seeds 40   passed 40   SR 100.0%   voids 0   met=True
130 min    lidar drops 0   worst error 0.775-0.831 m
```

**The first success rate this project can quote.** The standing 10/10 predated four fixes and is
superseded.

And an unrelated finding, from watching the log rather than the report:

```
stale EKF origin, repaired: 40 of 40 bring-ups
offset: min 9.111  max 9.168  mean 9.126 m
```

57 mm of spread across 40 samples is **systematic, not a race**. It stayed invisible because
`sim_up.sh` restarts PX4 and the retry is sane to 0.000 m, so every report shows zero VOIDs. It
cost `SIM-10` a wrong "done" — closed on "ten sane origins, zero VOIDs", which measures the
*repair*, not the ordering. Reopened, and the offset is now `SIM-28`.

---

## Where it stands

| claim | status |
|---|---|
| fell through the ground | **refuted** by the imagery |
| PX4 estimator fault | **refuted** — self-consistent, zero resets |
| tight `state_timeout_s` | **ruled out** — 25 s needed of a 60 s budget |
| swept move blocks actor, physics runs on | **not reproducible** under deliberate forcing |
| a poller stealing the collision flag | **not reproducible** at 100 Hz over 36 landings |
| root cause | **open**, ~1.7% |

Forcing it is no longer a sensible use of hardware. `probe_landing.py` now runs on **every**
flight and `/fmu/out/vehicle_land_detected` is recorded, so the next occurrence arrives with the
actor-vs-integrator data the first one lacked, and the gate prints the split rather than filing
it.

**The recurring lesson, and it applies to me as much as the code.** Three times this week I built
a check that could not fail — a `git checkout` that ate my own uncommitted work, a test injection
that was a silent no-op, a probe reporting "armed" while capturing nothing — and then, fixing a
review finding about vacuous checks, placed the new check *before* the flight it was meant to
verify. Validating that something *started* rather than that it *produced data* is the failure
mode, and it is the same one behind `test -x` on an unlinkable binary and a soak aimed at the
wrong code path.
