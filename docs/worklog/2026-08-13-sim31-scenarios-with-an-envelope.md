# 2026-08-13 — Scenarios that state their own flight envelope

**`SIM-31`, slices one and two.** A scenario could say *where* to fly and how tightly to score it.
It could not say how fast the aircraft may fly getting there, where on Earth the world sits, or
where the vehicle starts. This is the first two of those; GPS waypoints — the only piece needing
controller changes — is still open.

**Headline: capping horizontal speed cut waypoint error by an order of magnitude, and the review
found that the same change had silently broken the flight gate.**

---

## What the envelope is worth

Same seed, same mission, on CitySample:

| | waypoint errors (m) | wall |
|---|---|---|
| unlimited (12 m/s) | 0.762 / 0.756 / 0.763 / 0.761 | 112.0 s |
| `velocity_xy_max_mps: 0.5` | **0.060 / 0.062 / 0.066 / 0.061** | 137.4 s |

That is worth stating plainly: **the baseline's ~0.76 m error is largely approach speed, not
controller quality.** Every gate number this project has quoted was measured at 12 m/s.

## The envelope and the time budget are one decision

At the baseline's `state_timeout_s: 60` the slow run failed **3 of 4** with `timeout in state
waypoints` — while tracking beautifully. The budget covers the *whole* sequence, and 40 m at
0.5 m/s needs ~88 s of flying. Nothing was wrong with the aircraft; the scenario simply had not
been given time to be slow in.

`scenarios/square-10m-slow.yaml` therefore carries both, and the baseline is untouched so its
numbers stay comparable.

## Limits belong in PX4, not in our controller

`offboard_control` sends **position** setpoints; PX4's position controller turns those into
velocity and acceleration. A limit enforced on our side would be one the autopilot does not know
about — it would keep planning as if unbounded, and the number in the scenario would describe the
harness rather than the aircraft.

## Then the review, which found ten more things

**The critical one: the flight gate could not run at all.** Adding `scenario` as the second
*positional* parameter of `restart_stack` silently rebound every argument at `run_gate.py`'s two
call sites — `scenario` received the world path, and the gate died on seed 1 with
`'str' object has no attribute 'get'`.

It was invisible because **every verification in the PR went through `run_scenario.py`.** The gate
is the thing that scores this project, and it had been broken by a change that never touched it.
The parameter is now **keyword-only**, so the same mistake cannot recur positionally.

### Five more that were real

- **A harness fault was being scored as a flight failure.** `apply_limits` raised `RuntimeError`,
  and `run_gate` catches everything as `outcome: failure` — so a misspelled limit key would have
  produced N consecutive FAILs and a report reading *"SR 0%, control failure"* for runs where the
  aircraft never left the ground. That is the VOID-is-not-FAIL distinction `SIM-10` exists to
  protect. It now has its own exception type and **aborts the gate** instead of scoring.
- **Limits leaked across runs on a reused stack.** Nothing reset PX4 between scenarios, so flying
  the slow scenario and then the baseline under `--no-restart` would have flown the baseline at
  0.5 m/s while recording `applied_limits: null` — and its errors would then have been compared
  against numbers gathered at 12 m/s. The applier now **restores the build's declared defaults**
  when a scenario declares none. Verified: baseline after slow run reads `MPC_XY_VEL_MAX = 12.0`
  and errors return to 0.78.
- **A stopped PX4 read as a missing parameter.** `docker exec` failing gave empty stdout, so the
  operator was told `MPC_XY_VEL_MAX: not a parameter in this PX4 build` and sent to hunt a naming
  problem that did not exist.
- **An origin-only run rewrote a declared vehicle pose.** Passing a "no-op" `0,0,0` spawn is not a
  no-op against a settings file that deliberately placed the vehicle at `Z: -50` — `apply_spawn`
  does `vehicles[name].update(vals)`, so it would have been dropped back to the PlayerStart. That
  is the terrain-burial failure `apply_spawn.py`'s own header exists to prevent.
- **Omitting `altitude_m` would have moved the world 122 m.** AirSim's default `OriginGeopoint` is
  `(47.641468, -122.140165, 122)` and it overrides only the keys present. Substituting `0.0`
  re-references the world's GPS altitude without anyone asking. All three keys are now required.

### And one that undermined the central claim

`apply_px4_params.py` was built on *"read it back, because `px4-param set` on an unknown name is
not an error"*. True — but a read-back proves the value is **stored**, not that PX4 considers it
**legal**. `param set` neither clamps nor rejects.

`MPC_XY_CRUISE` has a declared range of `[3.0, 20.0]`, and `velocity_xy_max_mps: 0.5` was writing
`0.5` into it. The read-back dutifully "confirmed" a number PX4 itself treats as out of range.

Two changes: the applier now validates against `parameters.json` — which ships inside the image and
is the authority — and **`MPC_XY_CRUISE` was dropped from the mapping entirely**, because
`MPC_XY_VEL_MAX` is the cap that acts on offboard position setpoints. Re-flown afterwards: errors
0.063 / 0.063 / 0.058 / 0.056, unchanged. It had never been the operative limit.

## The measurement that decides the remaining slice

A scenario declaring `origin_geopoint: 37.4123, -121.995, 50.0` produced a PX4 EKF reference of
`37.4123278, -121.9948484, 51.28` — **13.8 m horizontally and 1.28 m vertically from the declared
origin.**

So a GPS waypoint converted against `OriginGeopoint` instead of `ref_lat`/`ref_lon` would land
~14 m off, an error shaped exactly like a control bug. The design asserted this yesterday; it is a
measurement now.

**What the 13.4 m east is made of — level offset from the world origin, GPS init bias, or both —
is not explained.** It is written down as unexplained rather than rounded away, because slice
three depends on knowing.

## Verification

- Flight gate, 2 seeds, Blocks: **2/2, 100%** — the critical break, gone.
- CitySample `square-10m-slow`: 4/4 with the envelope printed, read back, and recorded.
- Blocks with a declared origin and 45° yaw: 4/4, origin confirmed in the run-time settings **and**
  in PX4's own `ref_lat`/`ref_lon`.
- Negative tests: unknown limit key refused; `accel_horizontal_mps2: 1.0` refused against
  `MPC_ACC_HOR`'s declared minimum of 2.0; origin-only run leaves a declared pose at `10/20/-50`.

## Open

- **GPS waypoints.** The remaining slice, and the one that touches the controller.
- **Five of the six limit keys have still never been flown** — only set and read back.
- The 13.4 m east.
