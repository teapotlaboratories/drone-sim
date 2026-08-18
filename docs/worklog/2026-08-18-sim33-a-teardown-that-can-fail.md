# SIM-33 — a teardown command, and the four ways a checker lies

The rule says *tear down after every flight **and verify it***. The repo had no way to do one:
`teardown()` runs only at the **start** of a bring-up, and `scripts/sim_down.sh` does not exist
despite being reached for by name. Every teardown was hand-typed, and hand-typed lists have
consistently missed `sim-xrce` — whose stale copy holds udp/8888 while `MicroXRCEAgent` **exits 0
on a bind failure**, so the next bring-up breaks silently, nowhere near its cause.

`./scripts/sim_up.sh --down` stops the chase recorder, removes the canonical five containers, and
**prints every check**, exiting non-zero if anything survives.

## The design goal was that it can fail

After a branch whose worst defect was a guard that never executed while its test reported green, a
verifier that only ever prints "none" is worthless. Run against a live stack:

```
sim-unreal  Up 50 seconds
pgrep -x UnrealEditor            STILL RUNNING: 931470
pgrep -x px4                     STILL RUNNING: 931985
Xvfb on :77                      STILL RUNNING: 931530 Xvfb :77 -screen 0 1920x1080x24 …
GPU compute apps                 931470, 4569 MiB
exit 1
```

The GPU holder is the renderer's own PID — a cross-check that falls out for free. `--down` on that
same stack then returned all-clear, exit 0.

## Four ways this checker lied, and it lied to me during its own implementation

**`set -e` turned "no match" into an abort.** `pgrep` exits 1 when it finds nothing — the *normal*
answer here — so an unguarded capture aborted the function mid-report. It printed one line and
exited 1. Had it aborted a few lines later it would have printed an all-clear having checked
almost nothing.

**Off by one in the truncated pattern.** `comm` is truncated to 15 characters, so
`UnrealEditor-Cmd` (16) becomes `UnrealEditor-Cm`. I wrote `UnrealEditor-C` — 14. Verified with a
real binary: comm reads `UnrealEditor-Cm`, my pattern matches **0**, the correct one matches **1**.
A leaked `UnrealEditor-Cmd` holding GBs of VRAM would have printed `none`.

**And the test could not catch it.** `assert "UnrealEditor-C" in patterns` is a *substring* check,
true for the broken 14-char pattern and the correct 15-char one alike. It reported green over the
exact defect it was written to prevent. Now asserts `full[:15]` is present as an element.

**The GPU line never touched the verdict.** It printed "still holding" and returned success, so a
leaked renderer could keep 4.5 GiB under an exit-0 all-clear — while `CLAUDE.md` said the command
"exits non-zero if anything survives". It now cross-checks the PIDs against the leftovers it just
found, because `--query-compute-apps` gives PIDs and guessing was never necessary.

## Two more the review caught

**`--down` ran the bring-up's preparation first.** A missing settings file or a stale exported
`SPAWN` would kill a *teardown* with a message about spawns, stack still up — and write
`sim/ue5/.settings.run.json` as a side effect of a command meant to remove things. Now guarded;
tested with the settings file moved aside and `SPAWN=999,999,999` exported.

**The final claim overstated what was checked.** "Nothing of ours is left running" — but `pgrep -x`
is blind to scripts (`comm=bash`) and runners (`comm=python3`), which is *exactly* the two-hour
incident: the containers were re-created afterwards by a detached bring-up nobody re-checked. So
`--down` now walks `/proc` for one, excluding its own ancestors since this script's cmdline also
says `sim_up.sh`, and the claim narrowed to "containers, our processes, our display and the GPU are
clear".

## What it replaces

~50 lines of hand-verification caveats in `.ai/AGENTS.md`, kept because there was no command. They
are now code with tests — 171 total, ten covering this command, including that it can fail.

**The pattern worth keeping:** every defect here was in a checker, and none of them failed loudly.
An aborted report looks clean. An off-by-one pattern prints "none". A substring assertion passes.
A printed warning with no verdict change exits 0. A checker that cannot fail is not a check.

---

## Second review pass — four more ways, including the same dead-guard class again

**The GPU cross-check could never change the verdict.** I compared GPU-holding PIDs against
`ours` — but `ours` is only appended where `bad=1` already fires, so `ours` non-empty ⟺ the run
had already failed. And the case it was written for is precisely the one where `pgrep` *didn't*
see the process. Identical shape to the guard that never executed two branches ago. It now asks
the PID itself: `/proc/<pid>/cmdline` for `UnrealEditor`/`.uproject`/`AirSim`/`px4`, which is an
ownership test independent of whatever the name checks found. A bystander's GPU use is printed and
explicitly left alone.

**A failing `docker ps` read as "none running".** `|| true` swallowed daemon-unreachable,
docker-not-on-PATH, permission-denied and a wrong `DOCKER_HOST` alike, so the *primary* check
printed a clean line and exited 0 having seen nothing. Demonstrated by the reviewer with a stub.
Now distinguishes "docker answered, nothing matched" from "docker did not answer", and fails.

**The detached-bring-up scan matched mentions, not ownership.** `tail -f scripts/sim_up.sh` and
`vim scripts/sim_up.sh` both tripped it — demonstrated — so an operator with the file open in
another pane could never pass the teardown the rules mandate. Now the process must actually *be*
an interpreter (`/proc/<pid>/exe` is bash/sh/dash/python), which is the same "a name is not
ownership" test already applied to Xvfb: `tail`'s exe is `tail`.

**And my own fix for that had the same flaw one level down.** Piping `--down` through `grep`
flagged *itself*: a pipeline shares a process group, so a sibling shell whose cmdline contains
`sim_up.sh` is not an ancestor and the ancestor walk let it through. `--down | tee log` is
ordinary usage. Now excludes our whole process group.

Smaller: `ffmpeg` was matched host-wide although it only ever runs inside `$SIM` via `docker exec`,
so it could only ever catch the operator's own transcode — and then label their PID "one of OURS";
a missing `nvidia-smi` silently dropped its row while the success line claimed the GPU was clear;
`DOWN` was environment-readable, so a stray `export DOWN=1` would turn every bring-up into a
teardown; and every ordinary `--down` opened with a red `[chase] FATAL: no recording in progress`
because `record_chase.sh stop` dies when the state file is absent.

**Verified in all three directions**, because a checker that only prints "none" is worthless:
`tail -f` on the file → not flagged; the same command piped → not flagged; a real bash process
running it → flagged, exit 1.

**And I walked into the trap again while testing this.** `pkill -f "scripts/sim_up.sh"` killed my
own shell — the exact failure this command encodes, typed by hand, in the session that encoded it.
It also killed a bring-up mid-container-creation, which left docker throwing
`can't get final child's PID from pipe` and a `sim-ros2` container stuck in `Created`. Docker was
fine once cleaned up. The lesson is not "be careful": it is that the command exists so nobody has
to be.

177 tests.
