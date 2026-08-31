# SIM-27 — the instrument was causing the fault it was measuring

**Date:** 2026-08-31 · **Ticket:** `SIM-27` · **19 flights over two days, CitySample.**

## The finding

`watch_collisions.py` — the collision witness the gate uses to score PASS/FAIL — polls
`simGetCollisionInfo` at 20 Hz during every gate run. **That RPC is read-and-reset.** The flag is
one-shot, and the first reader clears it. The witness was winning that race against
`FastPhysicsEngine`, which needs the same flag at `:102` to run collision response:

```cpp
if (body.isGrounded() || (collision_info.has_collided && collision_response.collision_time_stamp != collision_info.time_stamp))
```

No flag → no collision response → no ground lock → the integrator descends through the world
while the Unreal actor stays on the surface. Every PX4 sensor is synthesised from that
integrator, so the aircraft is told it is still falling, never disarms, and the landing state
times out.

| configuration | runs | pose splits | takeoff timeouts |
|---|---|---|---|
| witness ON | 12 | **10** | 0 |
| witness OFF | 7 | **0** | **2** |

## The evidence, in one pair of lines

With the witness running, during the descent:

```
gate=F-HIT  n=100 object=FastGeoSurrogateActor_0 impact_z=0.959 actor_z=0.759 normal=(0.000,0.000,-1.000)
gate=G-TICK grounded=0 z=2.081 vz=0.695 collided=0
```

Unreal is firing hit events continuously, with a **textbook ground normal**, against an actor
resting at 0.759 — and the engine sees `collided=0` while the body sinks past 2 m below the
surface. Same fixture with nothing polling the RPC:

```
gate=C-GROUND-LOCK ENGAGED body=MultiRotorPhysicsBody at z=0.759
gate=G-TICK grounded=1 z=0.759 vz=0.000 collided=1
```

`collided=1` — the flag survives to the physics tick, the lock engages, the landing terminates.

**It also explains the rate nobody could explain.** 1.7% on Blocks, 70–90% on CitySample: Blocks
re-fires contacts densely enough that physics usually catches one between polls; the CitySample
surrogate does not.

## And the naive fix is wrong

Removing the witness produced **2 takeoff timeouts in 7 runs**, against 0 in 12 with it. Seed 7,
on the stock binary:

```
gate=G-TICK grounded=0 z=0.803 vz=-2.641 collided=1     <- commanding 2.6 m/s of climb, not moving
```

The flag is *meant* to be consumed. With no reader at all it stays set, the engine keeps taking
the collision path against a contact that is still being re-reported, and the vehicle is pinned.
**So the theft was masking a second defect**, and "stop polling" trades a landing bug for a
takeoff one. What is needed is a read that observes without consuming, leaving the engine the
sole owner of the reset.

## Three wrong answers before this one, and why

This ticket produced three confident conclusions that did not survive. They are recorded because
the pattern is the useful part.

1. **"Gate B rejects the surface — the normal reads as a wall."** True on exactly one seed of six.
   Generalised from a single measurement, and it sent me off to write a C++ patch (`0009`) against
   a cause that was not the cause. The patch built, flew, and changed nothing.
2. **"The ground lock works, the body is static."** An artifact of comparing **seed 1's probe
   trace against seed 2's engine log**. Two different flights, lined up on wall-clock by
   assumption rather than by checking.
3. **"The engine is never told about the contact."** Right that it was not being told, wrong about
   why — the events were arriving and being consumed by our own tool.

The instrumentation itself was wrong twice as well: one shared throttle timer starved the rare
gate so gate C could never print, and the first release probe logged no body identity, so 171 s
of zero-wrench lines could not be attributed to anything.

**What consistently worked** was refusing to accept silence as evidence. Every wrong answer came
from reading an absence — no gate C lines, no engine activity, no contact — as if it meant
something. The gate G probe, which logs unconditionally every tick, is what finally made the
absence impossible.

## What the split detector should say now

`SIM-27` step 2's fault message is *"landing surface rejected — actor frozen, integrator
descending"*. The second half is right; the first half names a cause that is now known to be
wrong — nothing rejected the surface, the notification never arrived. The detector itself is
untouched: it caught 10 of 10 affected runs and voided them, which is exactly what it exists for.
Only the sentence needs rewording.

## Machine state

19 flights, every one torn down and verified. `patches/cosys-airsim/0009` reverted from the
deployed plugin; probes `0008`, `0010`, `0011` remain (logging only).
