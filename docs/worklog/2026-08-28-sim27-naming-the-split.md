# SIM-27 step 2 — make the landing split a named fault

**Date:** 2026-08-28 · **Ticket:** `SIM-27`, step 2 of the START HERE plan · **No flying.**

## What this is, and what it deliberately is not

The failing mode: AirSim's physics integrator and the Unreal actor hold two independent
positions for the vehicle, and nothing reconciles them. On `citysample-sensors-seed1`
(2026-08-27) the actor stopped on geometry at `pose_z = 0.757` while the integrator kept
descending to `phys_z = 108.706` — a **107.949 m** split. PX4 is fed entirely from the
integrator, so it never sees a touchdown, never disarms, and the run ends 240 s later as
`timeout in state land`: a **control failure**, in a run whose mission flew perfectly.

This step does **not** prevent the split — that is step 3, and it is a C++ change to the
ground-lock decision inside `FastPhysicsEngine.hpp`. This step stops the harness from
misreporting it: trip on the split live, end the run in seconds, and name the fault.

Verdict chosen with the owner (2026-08-28): **VOID, not FAIL.** Same call the stale-EKF-origin
check makes. The aircraft's ability to fly is not what broke, so the seed must not sit next to
real control failures — and a BYO world hitting this must not read "success rate 0%", which is
the misdiagnosis the ticket calls the worst possible outcome. Void is the stricter verdict: it
is excluded from the rate *and* blocks the criterion outright.

## Log

### Reading the ground before touching it

- `probe_landing.py` already computes `dz = phys_z - pose_z` per sample at 5 Hz and is on for
  every run. Nothing reads it **during** the flight: `run_scenario.py:1286` reads the max
  *after* the run is over and prints a line to stdout, long after the verdict was decided.
- The verdict itself comes from `offboard_control.py:295`, `timeout in state {state}`, on the
  generic per-state timeout.
- `/out` is bind-mounted to `<repo>/out`, so the host can tail the probe's jsonl directly —
  no second RPC consumer (the `simGetCollisionInfo` read-and-reset hazard stays untouched),
  no `docker exec` per poll.
- On SIGINT the controller raises `KeyboardInterrupt` past `_write_result` (`main()`, bottom of
  the file), so `pkill -INT` would cost the run its `waypoints_reached`, per-waypoint errors and
  terminal `MissionStatus`. Abort therefore goes through the controller, not around it.
- CI installs **pytest and pyyaml only** (`.github/workflows/checks.yml:52`). `probe_landing.py`
  imports `airsim_rpc_client`, which imports `msgpack` at module scope — so the detector cannot
  be importable by a test until that import is made lazy.

### The detector, and the numbers behind its thresholds

`SplitWatch` in `probe_landing.py`: trip when `dz >= 1.0 m` for `>= 1.0 s` **and** `>= 3` samples
**and** `vz >= +0.1 m/s` (NED, positive down). Latches — it fires once.

`dz > 0`, not `|dz| > 0`, and that turned out to matter. The failing CitySample trace reaches
`dz = -0.279` during its 3 m/s climb: pure skew between the two RPC calls, and precisely what an
absolute-value test would eventually trip on. The signature is the integrator sinking *below* a
stopped actor, which is one-sided.

The RPC import moved into `main()` so the detector is importable without msgpack.

### Replayed over every landing trace on disk — 91 traces, 49,732 samples, 0.9 s

`out/` holds 91 `*-landing.jsonl` traces, each with the result JSON of the run that produced it.
That is a labelled corpus, so the detector could be scored against verdicts rather than argued
about:

```
runs recorded as `timeout in state land` : 9   of which tripped: 9
runs recorded as success                 : 82   of which tripped: 0
other verdicts / no result file          : 0
```

Nine for nine, zero for eighty-two. The nine are `citysample-sensors-seed1`,
`citysample-updown-30m-seed1` and seven of the ten `square-10m` seeds — every one of them
`failure / timeout in state land` with **4/4 waypoints reached**, which is the whole point: the
mission was flown perfectly and the verdict blamed the flight controller.

How much earlier it ends the run:

| trace | fault at | trace ran to | saved | final \|dz\| |
|---|---|---|---|---|
| `citysample-sensors-seed1` | 85.1 s | 237.7 s | **152.7 s** | 107.9 m |
| `citysample-updown-30m-seed1` | 85.0 s | 237.1 s | **152.1 s** | 107.6 m |
| `square-10m-seed{3,4,5,6,8,9,10}` | 98.9–100.1 s | ~137 s | **37.4–38.0 s** | 9.8–28.3 m |

**Headroom.** The worst `dz` on a non-tripping trace, counting only samples that pass the descent
condition, is **0.255 m** (`square-10m-seed7`). The threshold sits ~4× above it. Seed 7's raw
max `|dz|` is 0.640 m — but that sample is *ascending*, and it is the seed the ticket notes was
struck by a traffic car. The sign condition is what keeps it out; an `abs()` detector would have
0.36 m of headroom instead of 0.745 m.

### The abort path, and why it goes through the controller

The runner cannot end the flight by signal without losing the evidence that matters. `pkill -INT`
raises `KeyboardInterrupt` past `_write_result` (`offboard_control.py`, `main()`), so the run
would forfeit `waypoints_reached`, the per-waypoint errors and the terminal `MissionStatus` — on
runs that flew **4/4 waypoints**, which is the whole proof that the aircraft was not at fault.

So: `SplitAbort` (a thread in `run_scenario.py`) tails the probe's trace on the host, and on the
fault record writes `/out/<tag>-abort.json` atomically. `offboard_control` polls that path each
tick and calls `_fail(reason)` — **before** the state-timeout check, because both end the flight
and the order decides which sentence the report carries. A file, not a topic: `conventions.md`
freezes the ROS 2 graph, and this is harness plumbing, not part of the interface a real aircraft
flies.

The trace is read from the host, not over RPC: `sim_up.sh:1076` bind-mounts `<repo>/out` to
`/out`. A second RPC consumer is exactly the hazard that already caught this ticket's own
tooling once — `simGetCollisionInfo` is read-and-reset, and the collision witness owns it.

### Two bugs, both found by running it rather than reading it

Replaying the real CitySample trace through the actual classes (watcher → abort file → the parse
the controller performs) rather than trusting the code:

1. **`self._stop = threading.Event()` shadowed `threading.Thread._stop`**, which is the internal
   method `join()` calls. Every flight would have ended with
   `TypeError: 'Event' object is not callable` out of the `finally` block. Renamed `_done`.
2. **The diagnostic sentence was clobbered by the fault record's own short `reason`.**
   `{"reason": reason, **rec}` — `rec` carries the bare fault name, spread last, so the
   controller would have failed with four words and no measurement. Now `{**rec, "reason": ...}`.

Both are pinned by tests. Neither was visible by reading; the second is exactly the class of
defect the "verify by running it" rule exists for, in miniature.

Handshake, end to end, against the recorded trace:

```
flight-time of the fault : 85.06 s   (the trace runs to 237.73 s)
abort file written       : True
controller would _fail() : landing surface rejected -- actor frozen, integrator descending:
    the physics integrator was 1.71 m below the Unreal actor and still descending at
    0.696 m/s, sustained 1.0 s, 85.06 s into the flight. The mission is not what failed --
    AirSim never accepted the surface as ground. SIM-27.
```

### The verdict, and where it surfaces

`_with_split_fault` in `run_scenario.py` voids the run on all **three** return paths — including
"no result produced", which is precisely where an unexplained abort would otherwise land. It
overwrites `failure_reason` (unlike the sensor check, which must not) because here the split is
*causal*: the abort file the watcher wrote is what the controller read. Any earlier controller
reason is kept as `controller_failure_reason` rather than dropped.

`run_gate.py` prefers `split_void_reason` over `sensor_void_reason`: a run stopped mid-mission
easily records nothing on a requested sensor, and "recorded no messages on /camera/depth"
describes a consequence where the split describes the cause. The summary gains
`runs_ended_by_pose_split`, distinct from the existing `max_pose_split_m` magnitude.

A successful run is voided too, deliberately. A landing that terminated while the two poses
disagreed by more than a metre is not a pass with a footnote — every camera frame in that bag
shows a world the vehicle's own state was not in.

### Tests

`tests/test_landing_split.py`, 16 cases, self-contained (no `out/`, no msgpack, no rclpy):
trip behaviour, latching, the four things that must **not** trip it (single-sample skew, climbing,
negative split, `vz: null`), the reconnect-gap case, and the verdict. The controller's half is
checked as source text, the way `test_gate_checks.py` already does — it imports rclpy and
px4_msgs, which exist only in the container.

Full off-target suite: **226 passed**. One existing test needed updating —
`test_sensor_evidence_is_attached_to_every_return_path` pins the exact decorator spelling on
each return path, and now pins the split decorator's *order* too (outside-in matters: the sensor
check refuses to overwrite an existing `failure_reason`).

### What this does NOT do

It does not prevent the split. That is step 3 — the ground lock in `FastPhysicsEngine.hpp:228`
is reachable only through a collision normal test (`:156`, `kAxisTolerance = 0.25`), and which
of the two gates actually aborts is still **unmeasured**. Step 1 measures it and needs a flight.

### Flown — Blocks, seed 1, 2026-08-28 (owner-approved, SITL)

```
./scripts/sim_up.sh --display
SIM_CHASE_VIDEO=1 python3 scripts/run_scenario.py scenarios/square-10m.yaml --seed 1 \
    --outdir out --no-restart
```

Cold stack, Blocks, EKF origin sane on the first attempt (`ref_alt 123.284` vs GPS `123.284`,
0.000 m apart — no PX4 restart needed, unlike `SIM-28`'s 40/40). Spawn pose NOT applied, since
`--no-restart` is what the chase-camera rule prescribes.

| | |
|---|---|
| outcome | **success**, 4/4 waypoints, 112.8 s |
| waypoint errors | 0.798 / 0.763 / 0.781 / 0.777 m |
| `max_pose_split_m` | **0.1667 m** — no fault, no abort file written |
| `pose_split_fault` | `None` |
| probe / video / chase | 529 samples · `square-10m-seed1.mp4` · `square-10m-seed1-chase.mp4` (64.6 MB) |
| LiDAR readback drops | 0 |

That is the case that mattered: **a healthy landing must not trip the detector**, because a false
positive voids good runs. It did not, with 0.83 m of headroom. Blocks cannot produce the fault
itself — flat plane, no HLOD proxy — so a true positive was never on offer here.

**The abort handshake was then exercised directly on the live stack**, without flying: an abort
file written by hand, then `ros2 run control offboard_control --ros-args -p abort_file:=...`. The
node failed on its FIRST tick, in `wait_for_fcu`, long before any arm command:

```
[ERROR] FAILED: landing surface rejected -- actor frozen, integrator descending: SMOKE TEST...
[INFO]  state: wait_for_fcu -> failed
[INFO]  result: {"outcome": "failure", "failure_reason": "landing surface rejected -- ...",
                 "waypoints_reached": 0, ...}
```

38 ms from start to verdict, terminal state published, result file written. So the one path the
Blocks flight could not reach is verified too — the controller reads the file, fails through
`_fail()`, and the result JSON carries the sentence verbatim.

**A third defect, found by the flight and fixed.** The probe's summary line — the ONLY place the
split verdict is reported — never printed. `pkill -INT` lands in the `time.sleep` at the bottom
of the loop, not in the RPC call that had the handler, so a traceback replaced it:

```
File "/tmp/probe_landing.py", line 222, in main
    time.sleep(max(0.0, period - (time.time() - loop)))
KeyboardInterrupt
```

Pre-existing — and the file's own docstring claimed the line was "printed on the way out however
we leave, including SIGINT". It was not. The whole loop is now inside the handler. This is the
third bug in this change that only running it could find.

**Stack torn down and verified**: `./scripts/sim_up.sh --down` reported containers, processes,
display and GPU all clear, confirmed independently with `docker ps -a` (empty).

### Follow-up filed

The probe's SIGINT hole is written up as **`SIM-40`** rather than left as a footnote here. The
instance is fixed on this branch; what is open is the audit it implies — every helper
`run_scenario.py` stops with `pkill -INT` reports its exit the same way, and none of them has been
proven to survive a real signal. Same shape as `SIM-22`, `SIM-38` and `SIM-33`: a component that
says "nothing to see" through the same path it uses for "I could not look."

### From review (`/review high`, 2026-08-28) — four findings, all fixed

**1. A non-dict abort file took the node down.** `_abort_requested` caught `FileNotFoundError`,
`OSError` and `ValueError`, but valid JSON that is not an object — `["stop"]`, `"stop"`, `null` —
reaches `.get()` and raises `AttributeError` straight out of a timer callback, skipping
`_write_result` and destroying the evidence. Exactly the outcome the docstring three lines above
promised could not happen. Not hypothetical: the live handshake was exercised with a *hand-written*
abort file, and `"stop"` is what a hand writes. Now `isinstance(doc, dict)` first.

**2. And a malformed abort file was silently ignored.** The same `except` returned `""`, so the
flight ran out its 240 s `timeout in state land` — the misdiagnosis this whole mechanism exists to
remove. Split the handling: a failed *read* (`OSError`) is transient and retries next tick; bad
*content* (`ValueError`, non-dict) still stops the flight, with
`UNPARSEABLE_ABORT`. An abort whose reason cannot be read is still an abort.

**3. The sampling skew is systematic, and the hold could not filter it.** `dz` is the difference
between two *sequential* RPCs, so it carries `vz × gap` of pure sampling error — and that error
persists for as long as the descent does, so neither the 1 s hold nor the 3-sample count touches
it. Measured gap is ~0.08 s (0.21 m at −2.5 m/s), but a render hitch stretching it to half a
second during a 2 m/s leg would manufacture a metre of "split" and **void a healthy run** — the
failure mode that matters most here. The probe now times both calls and records `gap` per sample,
and `SplitWatch` trips only on what survives after the worst-case skew that gap could explain is
subtracted. A missing `gap` means no correction, so the 91-trace replay still scores identically:
**9 tripped, 82 clean**, re-run after the change.

**4. Two readers of the same file disagreed.** `_probe_summary` adopted the first line containing
the substring `"fault"` without checking `rec.get("fault") == "pose_split"`, which `SplitAbort`
does — so a probe error record whose exception text contained the word would void a run with *"the
physics integrator was None m below the Unreal actor"*. Guard mirrored.

**And one claim corrected rather than defended.** The gate comment, this worklog and a test all
described a precedence between `split_void_reason` and `sensor_void_reason` that the pipeline
cannot produce: `_with_split_fault` voids the run before `_with_sensor_evidence` looks at it, and
that helper only writes `sensor_void_reason` on a run still marked success. The ordering is
*defensive*, and now says so. The real consequence was elsewhere and is fixed: a split-voided run
reached the report with `sensors_empty` populated and no reason anywhere in the JSON, the
explanation living only on a stderr line. It is recorded as `sensor_evidence_note` and carried
into the gate report.

**Known limitation, deliberately not fixed here.** `SplitWatch` is phase-blind — it has no notion
of `LAND` — so a split on a descending waypoint leg would still be reported as *landing surface
rejected*. Making the sentence phase-aware means plumbing controller state into the probe, which
is more coupling than the wording is worth today. Recorded rather than hidden.

Suite: **231 pass**.

