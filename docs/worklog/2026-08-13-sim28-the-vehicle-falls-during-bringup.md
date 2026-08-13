# 2026-08-13 — The vehicle falls 80 m during every bring-up

**`SIM-28`.** Yesterday's worklog recorded that the EKF origin comes up stale on 40 of 40
bring-ups by a systematic 9.13 m, and concluded — in bold — that 57 mm of spread across 40
samples made it **"systematic, not a race"**.

**That conclusion was wrong, and this entry supersedes it.** Worklogs are frozen, so the
correction lives here rather than in yesterday's file.

**Headline: the vehicle genuinely falls ~80 m during bring-up and is then reset. PX4 boots into
the middle of that fall and latches its origin from whatever altitude the vehicle is passing.**

---

## How the wrong conclusion was reached

The reasoning was: a race scatters, and this doesn't — 9.111 to 9.168 m across 40 runs is 57 mm
of spread, so it must be a fixed geometric offset rather than a timing coincidence.

The step that does not follow: **a tight distribution can mean "deterministic timing" just as
easily as "constant offset".** A body falling at a repeatable rate from a repeatable height,
sampled at a repeatable moment, lands on a repeatable altitude. The two are only distinguishable
by watching the quantity *move*, which nothing had done yet.

That led to two more wrong steps before the mistake surfaced:

- **A geometric decomposition.** With AirSim's default `origin_geopoint` at 122 m AMSL, the 9.11 m
  split neatly into `ref_alt − spawn = −7.85 m` and `GPS − body = +1.87 m`, both stable across a
  10 m spawn change. Arithmetic on a moving target, presented as two constants.
- **"The −7.85 m term is the real defect."** It was not a term of anything.

---

## What actually happens

First, two measurements that narrowed it usefully:

**The Blocks floor is not at Z=0.** Body at rest NED `+0.582`, contact with `Ground` at `+0.900` —
the floor is 0.9 m *below* Unreal `0,0,0`, and `Z=0` releases the vehicle 0.9 m above it. That
mattered because it killed the tidy story: 0.9 m of drop cannot produce a 9.1 m origin error.

**PX4 latches from GPS, not the barometer.** Post-repair, `ref_alt` 123.314 matches `sensor_gps
altitude_msl_m` 123.314 exactly, while `vehicle_air_data baro_alt_meter` reads 123.249. Baro and
GPS agree to 65 mm, so the barometer cannot be the source of a 9 m error.

Then the one that settled it. A PX4 **restart** always latches correctly — GPS is right from its
first sample. So the fault is specific to the **first** boot. Sampling `sensor_gps` from that
first boot:

```
gps=118.593 -> 114.261 -> 108.967 -> 103.589 -> 98.372 -> 93.332 -> 88.472
    -> 84.152 -> 79.293 -> 73.717 -> 68.681 -> 63.286 -> ... -> 41.004
    -> 123.288      <- snaps back to the spawn altitude
ref_alt latched at 114.14   (the SECOND sample of that fall)
```

Roughly 5 m per sample, for about eighty metres, then a reset.

---

## What it reframes

| yesterday | today |
|---|---|
| a systematic 9.13 m offset | where the fall had reached at EKF init |
| 57 mm spread proves it is not a race | 57 mm spread proves the *timing* is reproducible |
| `−7.85 m` is the real defect | an artifact of a reproducible fall |
| `Z=−10` "passes" | still latches mid-fall; the error merely lands inside tolerance |

The existing repair works for a reason that is now obvious: restarting PX4 *after* the fall has
resolved latches a settled GPS.

**Same family as `SIM-21`**, where a vehicle with no collision geometry beneath it fell forever;
here the fall is arrested and reset. Whether it relates to `SIM-27` is open and should be held
loosely — one recovers, one did not — but a stack that drops the vehicle 80 m on every bring-up is
worth understanding before blaming a landing.

---

## What the check does and does not prove

`sim_up.sh` compares `ref_alt` against GPS, and GPS is only trustworthy once the fall has
resolved. It works today because the error is far outside the 1 m tolerance, not because the
comparison is sound. A configuration where the sampled altitude happened to land within tolerance
would report a clean origin while doing exactly the wrong thing — which is what `Z=−10` does.

---

## Next

- **Why does it fall at all?** It is spawned 0.9 m above the floor. Something releases it before
  the world's collision geometry is present — the `SIM-21` shape. Does the fall depth vary with
  world load time?
- **What resets it?** Whatever performs that snap-back knows when the world became ready, which is
  probably the fastest route to the cause.
- **The fix is ordering, not repair.** PX4 must not initialise its origin until the vehicle is
  settled; restarting it afterwards costs a restart on every bring-up and is two attempts from a
  VOID.

**The lesson, and it is the same one as the rest of this week.** Yesterday's error was not a bad
measurement — the 40 samples were real and the spread was real. It was a conclusion drawn from a
quantity nobody had watched move. "It is too consistent to be timing" felt like evidence and was
an assumption.
