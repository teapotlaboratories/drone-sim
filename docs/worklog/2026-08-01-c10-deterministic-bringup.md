# 2026-08-01 — `C-10`: a deterministic bring-up, and a check that nearly lied

**Task:** `C-10` — make the EKF-origin ordering deterministic so Lane C does not fly by luck.
**Lane:** C. **SITL only** — no real aircraft, nothing real armed or flown.

> Kept as the work happens.

---

## Result

**Cold start, `docker rm -f` to flying, unattended: 83 s to a verified stack, then 4/4
waypoints.**

```
[lane-c] removing any previous stack
[lane-c] starting simulator (ipc shareable: it is the netns + /dev/shm donor)
[lane-c] waiting for the vehicle to settle (5 reads within 0.05 m)
[lane-c] waiting for /fmu/out telemetry and a FINITE EKF origin
VOID: EKF origin is STALE: ref_alt 114.210 m vs GPS 123.279 m = 9.069 m apart
[lane-c] origin stale; restarting PX4 (attempt 1/2)
OK:   EKF origin sane: ref_alt 123.279 m vs GPS 123.279 m = 0.000 m apart
[lane-c] stack up and origin verified -- safe to fly
```

Then, on that stack: `success`, 4/4, errors 0.772 / 0.786 / 0.773 / 0.773 m. **Third
consecutive successful mission**, and the first from a genuinely cold start.

Two artifacts: `scripts/lane_c_up.sh` (ordering) and `scripts/check_ekf_origin.py` (the
assertion, with the decision logic pure so it is testable without a simulator).

---

## The check caught a real one, on its first honest cold start

The stale origin above is **not** a replay of `C-09`'s 35.167 m — it is 9.069 m, a fresh value
from a fresh race. That matters: it is evidence the failure is genuinely order-dependent and
recurs with a different magnitude each time, which is exactly why a fixed workaround would not
have held and why the check has to be a *measurement* rather than a known-bad constant.

**The retry path executed for real**, which is the part of error handling that usually ships
untested.

## My settle-wait was necessary but not sufficient — say so plainly

The script waits for AirSim's ground-truth z to hold still (5 reads within 0.05 m) before
starting PX4. **That was not enough on its own** — the origin still came up 9 m stale. What
actually saves the run is the verify-then-restart-PX4 loop after the fact.

So the honest description of the fix is *"verify and repair"*, not *"order it correctly"*.
Recording that because the tempting write-up — "we now start things in the right order" — would
be a claim the evidence does not support, and would make the next person delete the retry loop
as redundant.

## The check reported OK on a NaN, and I only found it by running it

First cold start printed:

```
OK: EKF origin sane: ref_alt nan m vs GPS 123.280 m = nan m apart (tolerance 1 m)
```

`abs(nan - 123.28)` is `nan`, and `nan > 1.0` is **False**, so the comparison fell straight
through to "sane". **PX4 publishes `ref_alt` as NaN until the EKF has established an origin at
all** — so the single most dangerous state, no origin whatsoever, was the one state the check
green-lit.

This repo already had `test_nan_error_must_not_pass` in the gate tests, for the same class of
bug in the same kind of numeric guard. I did not apply the lesson to new code. Now guarded
explicitly, with a regression test carrying the actual observed string, and the exit code
distinguishes **UNKNOWN** (no origin yet — a caller may wait and retry) from **STALE** (ordering
already wrong — needs a restart).

`wait_for_fmu` had the same defect independently: it treated *any* published `ref_alt` as
"telemetry ready", NaN included, and handed back a stack with no origin. It now requires a
finite value.

**A checker that passes on garbage is worse than no checker**, because it converts "unknown"
into "verified". Same class as the Dockerfile layer that recorded empty versions while the
build succeeded, and the capture overlay that silently printed `...` for a whole video.

## Why VOID and not FAIL

A run against a mis-initialised origin must be **void, not failed**. Scoring it would blame the
flight code for a bring-up defect — and the flight code is byte-identical to the one that scores
10/10 in Lane A. Poisoning a success-rate gate with void runs would make the gate actively
misleading. This is the same distinction `P1-08` draws for Lane A, now built for Lane C.

## Configuration recovered from evidence, not docs

The container invocations existed nowhere in the repo — only in worklog prose. They were
recovered with `docker inspect` **from the stack that had just flown 4/4**, so the script
reproduces something observed working rather than something the reference docs describe. That
is the project's own rule about Dockerfiles written from docs reproducing broken stacks, applied
to a bring-up script.

Load-bearing details that are easy to mistake for style:

- **`--ipc shareable` on the sim, `--ipc container:lane-c-sim` on the joiners.** Fast-DDS
  discovers over UDP but *delivers* over shared memory; a joiner with its own `/dev/shm` sees
  silence on a healthy stack (`D-02`).
- **`--qos-reliability best_effort` on every probe.** `/fmu/out/*` publishers are BEST_EFFORT, so
  a default RELIABLE subscription matches nothing and also reads as silence (`P1-02`).
- **QGC is not a convenience.** It supplies the GCS datalink, and Lane C deliberately leaves
  `NAV_DLL_ACT` enforced because a real Pixhawk refuses to arm without one.

## Not proved

- **"N times in a row" is not met.** One cold start, verified. The done-when asks for repeated
  unattended cold starts, and that has not been run.
- **A deliberately mis-ordered start has not been fed to the gate end to end.** The wiring is
  now done (see below) and the healthy path is verified over 10 seeds, but no run has been
  *observed* flowing through `run_gate.py` as VOID. The check itself has caught a real stale
  origin (9.069 m, above) and the scoring is unit-tested, so the two halves are proved
  separately — just not joined.
- **The settle heuristic is not characterised** — 5 reads at 0.05 m was chosen, not derived.

## Gate integration, and the Lane A gate re-run

`run_gate.py` now asserts the origin before every run. Voids are **excluded from the rate** and
**separately block the criterion** — excluding without blocking would let a gate where 9 of 10
runs were void report 100%. Scoring moved into a pure `score()`, unit-tested for all-void, empty,
reuse, and real-failure cases.

**One bug caught before shipping:** the first version ran the checker with `sys.executable` on
the gate host, where `ros2` does not exist. Every run would have been VOID and the gate could
never have passed again. **A check that fails closed on its own plumbing disables a gate as
surely as one that fails open.** It now execs into the `ros2` service the way `run_scenario.py`
does, piped over stdin rather than assuming a mount path, with an AST test pinning the call site.

**I also called this "blocked" when it was not.** I recorded the Lane A gate as unverifiable
because Lane A and Lane C collide on ports. They collide only when run *simultaneously* —
sequential is fine, and is exactly what `C-03` already did for the parity diff. Tearing Lane C
down took one command.

```
[ 1/10] seed 1  PASS worst 0.364 m      [ 6/10] seed 6  PASS worst 0.502 m
[ 2/10] seed 2  PASS worst 0.555 m      [ 7/10] seed 7  PASS worst 0.422 m
[ 3/10] seed 3  PASS worst 0.414 m      [ 8/10] seed 8  PASS worst 0.382 m
[ 4/10] seed 4  PASS worst 0.377 m      [ 9/10] seed 9  PASS worst 0.374 m
[ 5/10] seed 5  PASS worst 0.503 m      [10/10] seed 10 PASS worst 0.432 m

success rate: 10/10 (100%)   voids: 0   met: true   wall 1350 s
```

**The Phase 1 gate still holds under the new code** — the assertion adds a guard without
disturbing the result.

*Process note: the gate's log stayed 0 bytes for 15 minutes because Python block-buffers stdout
when redirected to a file. Third time buffering has masked live output in this session, after
`ros2 topic echo` twice. The process was healthy throughout; only the observation was broken.*

## Next

1. ~~Wire `check_ekf_origin.py` into `run_gate.py`~~ — done, and the Lane A gate re-verified.
2. Run the cold start N times and record the distribution of the initial offset.
