# SIM-39 — a required check that was red for ten days, and could never have been green

Noticed while merging PR 64: `gh pr checks 64` reported `off-target-tests  fail`. The reflex is
to look at the branch. The branch was not the problem.

## The finding

`off-target-tests` is the check **branch protection requires**. It had been failing on `main`
for **seven consecutive runs** — last green `c445863`, 2026-08-18 02:22Z. Every merge in that
ten-day window went past it with `--admin`.

So for ten days, **a real regression in that check would have looked exactly like the standing
failure.** The gate was still there, still required, still reported — and carrying no
information at all.

**`--admin` is for the approval requirement, not for a red check.** Branch protection here needs
a reviewer approval that cannot be supplied from this machine, which is why `--admin` is the
documented merge path. That is not a licence to merge past a failing check. If it is red, either
the branch broke it, or `main` is already broken — and then *that* is the work, first.

## Why it could never have passed

Two tests needed `vendor/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject` to exist, because
`resolve_world` ends in:

```python
if not p.is_file():
    sys.exit(f"world not found: {world}" + ...)
```

`vendor/` is gitignored. It arrives in quickstart step 0.2, long after CI has run. So the runner
reported:

```
world not found: vendor/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject
  (resolved to /home/runner/work/drone-sim/drone-sim/vendor/.../Blocks.uproject)
```

— naming a file that plainly exists on the workstation. **They could only ever pass on a machine
that had already built the vendored tree**, which is not a test a gate can use.

This is the same family as `SIM-37`'s `COPY vendor/`: work that assumes a tree CI does not have.
That one broke fresh-clone *builds*; this one broke the *gate*, which is quieter and worse.

## The fix

Root world resolution in a throwaway tree that has a `Blocks.uproject` in it. That keeps what the
tests are about — a relative `world:` anchors to the repo rather than to the caller's cwd, and
matches the same world spelled absolutely — and drops an accidental dependency on 2.4 GB of
vendored source. The third case was added explicitly, since it is the behaviour those two protect
and was only ever implied: running from outside the repo root used to report "world not found"
for a file that exists.

## Verified the way the failure happens

Running the suite in place proves nothing — in place, the file exists. So the tree was copied
**without `vendor/`** and the suite run against that copy: **206 passed**, against **2 failed,
203 passed** for `main`'s version.

The first attempt at that check was itself wrong, in a way worth keeping: `rsync --exclude=vendor`
is unanchored, so it also dropped `docs/vendor/`, and a different test failed for a reason that
had nothing to do with the change. Anchored to `--exclude=/vendor`. A check that cannot see what
it thinks it is checking looks exactly like a real failure — the same shape as `pgrep -x` silently
seeing nothing, and as the counters in `SIM-38`.

## From review

Review confirmed the diagnosis independently — reproduced the seven red runs, mutation-tested that
the rewritten tests still fail when the REPO anchoring is removed, and checked that patching
`rs.REPO` cannot leak between tests (`_rs_mod()` re-execs a fresh module and never registers it in
`sys.modules`). It also caught that the helper faked **only** `REPO`: `SIM_UP`, `RECORD_CHASE` and
`APPLY_PARAMS` are derived at import time and still point at the real repo, so a later test reusing
it as a general "fake repo" would silently run the real `record_chase.sh` on the workstation.
Renamed to say what it actually does. And it asked for this worklog, on the grounds that the
durable finding was sitting only in a commit message.
