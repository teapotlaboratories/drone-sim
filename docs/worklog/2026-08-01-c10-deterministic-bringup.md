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
- **A deliberately mis-ordered start has not been fed to the gate** to confirm it is *failed*
  rather than scored. The check voids correctly in isolation; wiring it into `run_gate.py` is
  still open.
- **The settle heuristic is not characterised** — 5 reads at 0.05 m was chosen, not derived.

## Next

1. Wire `check_ekf_origin.py` into `run_gate.py` so a void run is excluded from the success
   rate rather than counted as a failure.
2. Run the cold start N times and record the distribution of the initial offset.
