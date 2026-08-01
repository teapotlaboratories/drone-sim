# 2026-07-31 — CI tier 1, branch protection, and making QGC self-contained

**Tasks:** `P1-07` (CI), `D-07` (filed), plus baking QGroundControl into its image.
**Lane:** A. SITL only; no hardware involved.

> **Written after the fact, which breaks the as-you-go rule.** The findings below are
> reconstructed from commands and commit messages rather than captured as they happened.
> Recorded as a deviation rather than presented as if the log had been kept live.

---

## CI — what is achievable, and what is not

The plan asked for "CI that builds the Lane A image and runs the gate, under 10 minutes".
Measured against a GitHub-hosted runner, that is not achievable:

| | Hosted runner | Lane A needs |
|---|---|---|
| Disk | ~14 GB free | image is **12.6 GB** |
| Build | — | **20–40 min** |
| CPU | 2 vCPU | gate asserts **aggregate RTF ≥ 0.95** |
| Gate | — | **~19 min** for 10 seeds |

The CPU line decides it even if the rest were solved. On two cores PX4 and Gazebo miss the
real-time floor **on the hardware, not the code**, and the only way to green it would be
lowering the floor — which removes the assertion that caught this box's nested-Docker
deficit in the first place.

So CI became two tiers: everything checkable without a simulator (built), and the flight
gate (deferred, `D-07`).

## Three checks that were wrong before they ran

Tier 1 shipped only after executing every step locally, which caught two of these:

| Check | Why it was wrong |
|---|---|
| No AI attribution | Failed on `.ai/AGENTS.md` and `CLAUDE.md` — the files that *define* the rule and quote the forbidden strings in order to forbid them |
| `versions.lock` has no CONFLICT | There **is** one, accepted: the NVIDIA driver against Isaac's validated version, which is why Lane B is deferred. A job that fails on a known deliberate state is one people learn to ignore. Now asserts every conflict is *documented* |
| No AI attribution, again | **Found by CI, not locally.** The workflow contains the pattern it greps for. It passed locally only because the file was still UNTRACKED — `git ls-files` lists tracked files only, so the check could not see itself until it was committed |

That third one is the useful lesson: running a workflow's steps by hand is worth doing, and
it does not reproduce what `git ls-files` returns after `git add`.

## Review of the CI found a real privilege problem

The job inherited the repository default `GITHUB_TOKEN`, which is **write**. A job running
`pytest` and `grep` had write access to the repo. Now `contents: read`, set explicitly —
this file is the template tier 2 and `D-05` will be copied from.

The actions were also pinned to `@v4` / `@v5`, moving refs. That contradicts this project's
own repeatedly-stated rule that a branch is not a pin — learned when the XRCE agent's branch
pin evaporated, and again when QGC's `latest` channel had to be repinned. Pinning binaries
while leaving the things that *execute* them on mutable tags was an inconsistency, not a
subtlety. Both are commit SHAs now.

## Branch protection

`main` now requires the `off-target-tests` check, enforces linear history, and blocks force
pushes and deletions. Two settings chosen deliberately:

- **`enforce_admins: false`** — the project's own rules allow doc-only changes straight to
  `main`. Enforcing on admins would contradict `.ai/AGENTS.md`.
- **no required approvals** — GitHub does not let you approve your own PR, so requiring one
  would lock a solo maintainer out of merging their own work. Revisit when a second
  contributor appears.

## QGC baked into its image

QGC was a 180 MB bind mount the user had to fetch first, on the reasoning that CI has no use
for it. That reasoning was wrong: PX4 will not arm without the datalink, so QGC is a
functional dependency of every flight, and a stack that cannot fly until someone runs a
download script is not "reproducible from the repo alone".

Now downloaded from the pinned URL, **SHA256-verified before extraction**, and unpacked once
at build. Proven by building with a deliberately wrong hash:

```
build exit: 1
QGroundControl 5.0.8 checksum MISMATCH
  expected deadbeef
  actual   06969c67ef58ea063def0a8271447a1cc385438c4a7df36813315b4475146737
```

**Cost:** 452 MB extracted against 180 MB compressed. Extraction at build rather than at
every start is deliberate — `--appimage-extract-and-run` unpacks on every launch, and the gate
restarts the stack ten times per run.

**Verified by deleting the host copy entirely** and flying: `gcs_connection_lost: false`,
4/4 waypoints.

### Two silent no-op edits

Two `str.replace` edits **matched nothing and did nothing** — the openbox block sits between
the copy and the exec, so patterns spanning both failed. The result was
`QGC_APPIMAGE: unbound variable` at startup and an `exec` still pointing at a deleted file.
Only running the container surfaced it. Edits now abort on a non-match; a replace that
quietly does nothing is the same failure mode as the inert `FOLLOW_*` knobs and the
`firstRunPromptIdsShown` written into the wrong section.

## Decision: local CI is accepted

`D-07` defers the automated flight gate. Two blockers, and the second decided it:

1. Hosted runners cannot do it (above).
2. **A self-hosted runner on a PUBLIC repo lets any fork's pull request execute code on the
   machine** — this one, holding SSH keys, the netbird tunnel and the 7 TB drive.

`./scripts/run_local_ci.sh --gate` is the accepted substitute: one command, the same tier-1
checks CI runs plus the flight gate, with a result line meant to be pasted into a PR. It
warns when the working tree is dirty, and distinguishes a fast-checks pass from a real gate
run so neither gets quoted as the other.

**What this costs, plainly:** a controller regression is caught when someone runs the gate,
not when it lands.

## Deviations from the project rules, recorded

- **This worklog was written after the fact**, not as the work happened.
- **`scripts/run_local_ci.sh` was built before being filed as a TODO.** It implements a
  decision that is now documented in `P1-07`/`D-07`, but the order was wrong.
- **Two worklogs went unrendered** (`2026-07-30-phase-1-offboard`, and the gz-seed negative
  result) until this audit; the index was stale with them.
