# Two gates in one day: Blocks passes 40/40, City Sample fails 10/10

The 40-seed gate had never been run. Everything the backlog claimed about `SIM-28`, `SIM-10` and
`SIM-27` rested on 3–5 runs. This is what happened when it finally ran — twice, in two worlds.

## Blocks, 40 cold seeds — PASS

`scripts/run_gate.py scenarios/square-10m.yaml --seeds 40 --outdir out/gate-40`

| | |
|---|---|
| verdict | **PASS**, 40/40, 0 VOID |
| worst-error | min 0.778 · median 0.786 · mean 0.792 · max 0.829 m |
| spread | sd **0.013 m** |
| per run | ~155 s, cold bring-up each |
| origin verified | **40/40**, `0.000 m` from GPS |
| ground repairs | **0** |

The gate's own criterion — `SR == 1.0 over independent seeded runs, with zero VOID runs` — met on a
40-sample basis for the first time.

**And it exercises almost nothing.** The `simPause` repair fired zero times, because Blocks has no
World Partition: the vehicle never falls, so `ensure_grounded` finds it already resting at
`z ≈ +0.620` and releases. The 40/40 is a real baseline for the flight path and says nothing about
`SIM-30`'s repair, `SIM-10`'s stale origin, or `SIM-27`. Those are World Partition faults.

## City Sample, 10 cold seeds — FAIL 10/10

| seed | worst | coll | max split | vz@max | actors |
|---|---|---|---|---|---|
| 1 | 0.782 | 172 | **28.553** | 0.693 | surrogate |
| 2 | 0.799 | 513 | **28.682** | 0.697 | surrogate |
| 3 | 0.814 | 265 | 9.825 | 0.684 | crowd |
| 4 | 0.784 | 200 | **27.767** | 0.695 | surrogate |
| 5 | 0.775 | 198 | **28.103** | 0.697 | surrogate |
| 6 | 0.792 | 197 | **28.298** | 0.699 | surrogate |
| 7 | 0.809 | 18 | −0.640 | −1.814 | **car** + surrogate |
| 8 | 0.805 | 170 | **28.123** | 0.694 | surrogate |
| 9 | 0.814 | 181 | **28.005** | 0.694 | surrogate |
| 10 | 0.783 | 217 | **28.017** | 0.698 | surrogate |

**Tracking is identical to Blocks** — 0.775–0.814 m against 0.778–0.829 m. The flight controller does
the same job in both worlds. Every failure is in landing, ground contact and origin init; none in
flying.

### `SIM-27` is not a 1.7% flake

Nine of ten. Eight between 27.767 and 28.682 m, every one at `vz ≈ 0.694–0.699` against
`MPC_LAND_SPEED = 0.7`, six splitting for *exactly* 29% of their trace. The trace shows the shape
directly:

```
t=136.26  phys_z=28.896  pose_z=0.763  dz=28.13  vz=0.696
t=136.86  phys_z=29.315  pose_z=0.763  dz=28.55  vz=0.693
```

`pose_z` frozen — the actor is on the ground, which is what the cameras see and why the video always
looked like a normal landing. `phys_z` still growing at the last sample. **~28 m is 40 s at 0.7 m/s:
the state timeout, not a physical floor.**

The ~1.7% came from Blocks. **The sample-size problem was never the obstacle — the world was.**

### The surrogate ties `SIM-27` to `SIM-32`

`FastGeoSurrogateActor_0` is the actor in 9 of 10 seeds, and it is the same far-field surrogate that
made `SIM-32`'s streaming gate release 0.4 s early. Leading hypothesis, **not proven**: it carries
collision the integrator does not honour as ground, so the actor jams while physics keeps descending.
One defect, two faces. Confirming it needs per-contact timestamps clustering inside the descent
window — not yet checked.

**Seed 7 is the lever.** The only seed with no split was the only one struck by a traffic car
(`vz = -1.814`, ascending). An external impulse appears to break the stuck state.

**Collision count is not the driver.** Pearson r = +0.416 (n=10); seed 2 logged 513 collisions and
seed 8 logged 170 for the same 28 m. What separates cleanly is *which actor*: surrogate ~28 m,
crowd-only 9.8 m, car none.

### `SIM-10`, twice

Origins 875 m and 975 m out, both repaired by a PX4 restart, both correctly scored **VOID, not
FAIL** — the distinction the backlog calls load-bearing, working. Against 0 in 40 on Blocks.
Seed 2 showed `on ground at z=+0.000` and *still* had an origin 875 m out: PX4 had already built its
EKF during the descent before `ensure_grounded` ever looked.

### `simPause` did not fail

Worth stating plainly, because "the gate failed on City Sample" invites the opposite reading. Only
**1 of 10** seeds needed a ground repair — it caught the vehicle at **z = +1159 m** and put it back
at +0.745 in 125 s. Meanwhile seeds 4, 5, 6, 8 split by ~28 m with **no** repair and no origin
restart. So the split happens without `simPause` ever intervening, which rules out both "the repair
causes it" and "the repair failed to prevent it". `SIM-30` fixes the bring-up fall; it never claimed
to fix landing, and City Sample is the first place we could see that landing is broken at all.

## Three defects the runs exposed in the harness itself

- **`SIM-34` — the gate cannot record the chase camera.** `chase_video: None` on all 40 Blocks runs.
  `run_gate.py` writes the field but never sets `SIM_CHASE_VIDEO` and never brings the stack up with
  `--display`, so `chase_available()` is false every time. The 40 videos it *does* write are the
  **vehicle** camera — precisely the view hard stop 5 exists to reject, recorded 40 times, reported
  as success alongside `video_written: True`.
- **`SIM-35` — the collision criterion cannot tell a crash from being walked into.** Crowds (seed 3)
  and a traffic car (seed 7) hit a landed aircraft. As written the gate can never pass on a populated
  world, which collides with the project's stated purpose of flying your own world.
- **`SIM-36` — a scenario's declared world can be silently overridden.** And this one qualifies the
  run above.

## The correction that matters

`scenarios/square-10m.yaml` declares `world: …/Blocks.uproject` and describes itself as *"an empty
world. No obstacles by design"*. I flew it in City Sample via `--world`, and **nothing warned**:
`resolve_world()` is `cli_world or scenario["world"]`, with no comparison. Its own docstring says the
scenario's *"results mean nothing if it is flown somewhere else"* — and the code permits exactly that.

So a 10 m square at 20 m altitude, *relative to HOME*, landing back on a city street: a mission
designed for an empty box, flown into traffic and crowds. Waypoints being HOME-relative is why it is
silent — the square is geometrically valid anywhere, so nothing fails loudly.

**What survives:** the `SIM-27` split and the stale origins. A landing-physics defect and an EKF init
defect do not care which square was flown.

**What is now suspect:** the collision failures I first reported as a gate scoring problem. They are
at least as much a wrong-pairing artefact I created.

## No chase video, again

Neither run recorded one — `SIM-34` for Blocks, and City Sample was run `--no-video` deliberately,
since the only view that runner can produce is the vehicle camera the rule rejects, at ~290 MB/seed.
Saying so rather than omitting it, per the rule. What reproduces the runs:

```bash
python3 scripts/run_gate.py scenarios/square-10m.yaml --seeds 40 --outdir out/gate-40
python3 scripts/run_gate.py scenarios/square-10m.yaml --seeds 10 --outdir out/gate-city-10 \
    --world /var/mnt/…/worlds/CitySample/CitySample.uproject --no-video
```

Both torn down and verified afterwards (0 containers, nothing by exact process name, `Xvfb` checked
scoped to `:77`, no GPU compute apps holding memory). The `pgrep -f 'run_gate.py'` check matched its
own shell **again** — resolved by reading `/proc/<pid>/comm` and discarding shells, which is now what
`.ai/AGENTS.md` prescribes.

Housekeeping: `out/` is at **74 GB** on the internal NVMe, against the rule that archival recordings
belong on the 7 TB drive.
