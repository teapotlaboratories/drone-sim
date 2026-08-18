# SIM-34 — the gate could not record its own evidence, and three review passes to fix it

Hard stop 5 requires the chase camera on every flight test, because a camera bolted to the
aircraft can never show the aircraft. The flight gate — the tool that exists to produce evidence —
could not satisfy it.

## What was broken

The first full 40-seed gate returned `chase_video: None` on **all 40 runs**, while the 40 videos it
*did* write were the vehicle camera: the exact view the rule was written to reject. Reported
alongside `video_written: True`. Green results, a truthy flag, 40 mp4 files, none of it the
required evidence.

**Nobody owned the coupling.** Chase recording needs an Xvfb screen, which only `sim_up.sh
--display` creates. Remembering that was each caller's job. `run_gate.py` forgot — silently,
because `chase_available()` simply returns false and the recorder never starts.

## The fix

`restart_stack` derives `--display` from the same `SIM_CHASE_VIDEO` that `run_flight` reads, so
"asked for chase but the renderer has no display" is unrepresentable rather than a per-caller
mistake. Chase is on by default; `--no-chase` opts out and *says so*.

Verified by running it: a 2-seed Blocks gate wrote two h264 1920×1080 recordings, 6041 and 6054
frames, ~100.7 s, `chase_video` populated on both, moov intact per `ffprobe` in-container.

## Three review passes, and what each caught

**Pass 1 — six findings.** The worst: `chase_mp4` was the one artifact never cleared before a
flight, while the bag, result, video and probe all are. `record_chase.sh` has two paths that leave
the destination untouched, and `chase_on` is not cleared by them, so a failed capture would report
the *previous* run's file as this run's evidence. Nearly unreachable while chase was opt-in; making
the gate its default producer made re-running the same gate routine.

Also: the report could not say whether the evidence was asked for or whether it arrived — a
`--no-chase` report and a chase-enabled report were byte-identical. And `run_local_ci.sh` would
have silently gained ~630 MB per invocation.

**A test I wrote failed on its first run, correctly.** `test_only_one_truthiness_literal_survives`
was meant to lock in the new `_env_true()` helper. It failed: `SIM_NO_VIDEO` carried two more
copies of the same literal. The drift the helper exists to prevent was already there.

**Pass 2 — my fix was worse than the bug.** The `unlink` was unconditional, so it ran with chase
*disabled* too: deleting the previous run's evidence and writing nothing back. And the same commit
added `--no-chase` to `run_local_ci.sh` over the same seed numbers — so `./scripts/run_local_ci.sh
--gate` would have deleted `square-10m-seed1..10-chase.mp4`, **including the two files this branch
cites as its own verification**, from `out/`, which is the symlink to the 7 TB archive.
Unrecoverable.

Fixed with `if chase_on:`, and verified the destructive way: ran a `--no-chase` gate over seed 1 and
confirmed the existing 42.9 MB video survived **byte-identical** (`md5 b3dc3625164a` before and
after). Not by re-reading the guard.

Pass 2 also found the `except Exception` too broad — it swallowed `ValueError` for a malformed
scenario, a fault that cannot succeed on any seed, and `TimeoutExpired` from a wedged docker
daemon, which would burn 20 minutes per seed (~13 h over 40) while each retry stacked a bring-up on
a half-dead one.

**Pass 3 — the abort re-created the loss it prevented.** The three-strikes abort I added in pass 2
called `sys.exit()` *before* the summary is written. A 40-seed run that flew 37 seeds over two
hours and then hit three bring-up failures would discard every completed flight — exactly the loss
the `try/except` cites as its reason for existing, three failures later. Now it records the
aborting seed, breaks, and writes the report.

Pass 3 also caught that a run failing *only* on missing chase evidence printed `success rate:
40/40 (100%)` and then `FAIL — criterion is SR = 100%`, naming a criterion that was met and
pointing the reader at flight control when the fault is in the recorder. And that the VOID text
hard-coded the EKF-origin explanation, sending an operator to the `SIM-10` trap for what is now
more likely a display fault.

And the ordering bug underneath the fix: `chase("stop")` **publishes** a leftover `.partial` to the
destination path, so clearing first and stopping second puts the old file straight back. Settle the
past, then clear.

## What this says

Every pass found something real, and two of the three worst findings were defects **I introduced
while fixing the previous one**. The pattern is specific: each fix touched the path that decides
what counts as evidence, and a mistake there does not fail loudly — it produces a confident report
about a file that is wrong, missing, or someone else's.

Which is the original bug restated. `SIM-34` was never "the flag wasn't set". It was a tool that
reported success while recording the wrong thing.

145 tests, up from 139.
