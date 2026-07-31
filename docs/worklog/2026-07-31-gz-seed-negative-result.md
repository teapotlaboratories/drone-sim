# 2026-07-31 — `gz sim --seed` does not control the sensor noise

**Task:** `P1-04a` (seed the simulator's RNG so "10 seeded runs" means something).
**Outcome:** **the premise is wrong.** The flag is accepted, the plumbing works, and it has
**no measurable effect**. `P1-04a` is redesigned rather than implemented.
**Lane:** A. SITL only; no hardware involved.

---

## Why this was worth doing

The Phase 1 gate passes SR 10/10, but the seed only drives the spawn pose — which in an
empty world changes almost nothing the controller can see. The plan was to reach the
genuinely stochastic part, sensor noise, via `gz sim --seed`. Until then, "10 seeded runs"
is closer to "10 repeats", and the gate's own reports say so.

## Getting the seed in — two traps first

PX4 normally starts Gazebo itself and offers no way to pass `--seed`. But
`px4-rc.gzsim:63` shows it **attaches to an already-running world** ("gazebo already running
world"), so starting the server ourselves is enough — `PX4_GZ_STANDALONE` is not required.
Confirmed in the log.

Two things bit on the way:

| Trap | Symptom |
|---|---|
| **Compose interpolates `$VAR` in a command block before the shell sees it — including inside comments.** | The world path resolved to an **empty string**, so Gazebo launched with no world argument and silently loaded something other than PX4's `default.sdf`. Needs `$$`. |
| **`gz_env.sh` must be sourced first.** | Without `GZ_SIM_RESOURCE_PATH` the server starts, publishes a clock, and then cannot find the vehicle: `Error finding file [/x500/model.sdf]`. The world looks healthy and no aircraft appears. |

With both fixed: seeded server up, world correct, `x500_0` present, PX4 attached.

## The measurement

### First attempt — too coarse to conclude anything

Waypoint errors from flights at seed 42 (twice) and seed 7:

| Comparison | Per-waypoint differences | Mean |
|---|---|---|
| same seed (42a vs 42b) | 0.035, 0.023, 0.005, 0.027 | 0.023 |
| different seeds (42a vs 07a) | 0.009, 0.051, 0.036, 0.005 | 0.025 |

Suggestive, but waypoint error is a heavily filtered end product. Not evidence.

### Second attempt — invalid, and worth recording as such

Sampling `/fmu/out/sensor_combined` with `--once` at arbitrary moments. **Those samples are
not time-aligned, so they would differ regardless of the seed.** Comparing them proves
nothing; it was a bad test, not a result.

Trying to align them properly failed too: PX4's `timestamp` arrives as **absolute
wall-clock microseconds** (the uXRCE bridge rewrites it), so no two runs share timestamps.

### Third attempt — the decisive one

Gazebo's **own** IMU topic is stamped in **simulation time**:

```
/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu
header { stamp { sec: 82  nsec: 320000000 } }
```

Three boots, ~1930 samples each, compared **at identical simulation timestamps**:

| Comparison | Aligned samples | Identical | Mean \|Δ accel_x\| |
|---|---|---|---|
| **Same seed** (42 vs 42) | 716 | **0 (0.0%)** | **0.00726** |
| **Different seed** (42 vs 7) | 1911 | 0 (0.0%) | **0.00718** |

**Two runs with the same seed differ by as much as two runs with different seeds.** Not one
sample matches. At the same instant of simulated time, the noise is unrelated.

## Conclusion

`gz sim --seed` is accepted by the binary and reaches the server, but **does not make
Gazebo's IMU noise reproducible** in this configuration. Whatever it seeds, it is not the
sensor noise stream that matters here.

The plumbing has been **reverted**: it added a branch to the PX4 service startup and bought
nothing measurable, and leaving it in would imply control the stack does not have. The
knowledge of *how* to start Gazebo ourselves is kept here, because `D-06` needs exactly that
to split Gazebo into its own service.

## What `P1-04a` should become

Stop trying to seed the RNG; **seed the conditions instead.** Determinism is not required
for a success rate to be meaningful — diversity is. Per-seed variation of things declared in
the scenario:

- **Wind** — speed and direction. The default world already has a `wind` element, and this
  is the most physically meaningful disturbance for a multirotor.
- **Vehicle mass / inertia** — within a declared band, so the controller meets a slightly
  different aircraft each run.
- **Sensor noise *parameters*** — the `stddev` in the model SDF, rather than the stream. A
  seed that widens or narrows the noise is a real robustness test even if the samples are
  not reproducible.

Each is a scenario-declared range that the runner samples with `random.Random(seed)` —
exactly the mechanism already in `scripts/run_scenario.py`, pointed at knobs that actually
change the physics.

**And the honest framing stands either way:** runs here are not reproducible, so a failing
seed cannot be replayed. That is why every run keeps its MCAP.
