# `tests/` — the off-target suite

Tests that decide whether something **works**, as opposed to whether it builds — and that
need **no simulator, no ROS, no containers and no GPU**. That constraint is the point: the
whole suite runs in **0.17 s** (97 tests as of the pivot to the simulator-only stack), which
is what lets GitHub Actions run it on every push against a stack that can never be brought
up on a hosted runner.

```bash
python3 -m pytest tests/ -q                     # the host-side suite (pytest + pyyaml, nothing else)
./scripts/run_local_ci.sh                       # tier 1 — the same checks CI runs, ~30 s
./scripts/run_local_ci.sh --gate                # + the 10-seed flight gate (needs the simulator)
```

> **The gate has never been timed against this stack.** The "~19 minutes for ten seeds"
> recorded in this repo is the **retired Gazebo gate's** wall time; the current gate restarts
> the full Unreal renderer per seed, so it starts slower, not faster. Measure it before
> quoting it.

| File | Pins | Because |
|---|---|---|
| `test_gate_checks.py` | the flight gate's verdict logic — PASS/FAIL/**VOID**, scenario-name validation, and the EKF-origin check that decides VOID | `check_run` shipped with a hole that let a **NaN** waypoint error count as a PASS |
| `test_apply_spawn.py` | operator-supplied spawn parsing and its refusals (`SIM-13`) | a bad spawn puts the camera inside terrain, and every image measurement taken afterwards looks plausible and is wrong — four investigations were lost that way |
| `test_inject_airsim.py` | the AirSim project-injection helpers, including the hand-rolled `ini_set` (`SIM-11`) | they rewrite files in the **user's own** Unreal project; the failure mode is destroying someone's settings while appearing to succeed |
| `test_park_tour.py` | the example mission's geometry and validation (`SIM-16`) | `park_tour.py` was the most defect-dense code in its PR — four bugs, every one found by flying; three reproducible with no simulator at all |

**They are regression pins, not coverage theatre.** Each exists because something silently
did nothing, or silently broke while looking healthy: a `NaN` that passed the gate, an
arrival test that scored a fly-through, a ramp-out that converged without ever arriving.

**`test_frames.py` is not here.** The single ENU↔NED conversion point is tested inside its
package, at `ros2_ws/src/control/test/test_frames.py`, because it imports `control.frames`:

```bash
colcon test --packages-select control --python-testing pytest
colcon test-result --verbose
```

> **`--python-testing pytest` is required, not optional.** Without it colcon falls back to
> `python3 -m unittest`, which cannot collect pytest-style test *functions* and reports
> **"NO TESTS RAN"** while exiting non-zero — zero coverage that looks like a broken build
> rather than a missing flag.

## Tier 1 — what CI runs on every push

`.github/workflows/checks.yml`, ~24 s: this suite, shell and Python parse checks,
`scripts/check_image_refs.py`, `check_repos_manifest.py`, `check_worklog_renders.py`,
`check_attribution.sh`, `check_versions_conflicts.py`. `main` requires it.

**`check_image_refs.py` replaced a `docker compose config` step.** That step caught a real
class of defect — a reference to an image that does not exist — for the compose stack that
has since been retired. The class did not go away with the file, so the check was rewritten
against the authority that survived: every `drone-sim/...` reference must name an image
declared under `images:` in `versions.lock`. Nothing else in tier 1 can see a renamed tag,
because tier 1 never builds or runs a container.

## Tier 2 — the flight gate, run by hand

`scripts/run_gate.py` needs the simulator: a 57 GB Unreal image, a GPU, and an 11 GB PX4
image. No hosted runner can bring that up, and a self-hosted runner on a **public** repo
would let any fork's pull request execute code on the workstation. So the gate is deferred
**by decision**, and `./scripts/run_local_ci.sh --gate` on the workstation is the accepted
substitute — one command, with a summary that can be pasted into a PR.

## What used to be here

A container smoke test lived here, asserting that the Gazebo image reproduced the numbers
measured natively — 24 `/fmu/out/*` topics, zero sensor TIMEOUTs, aggregate RTF ≥ 0.95. It
retired with that stack. Its successor is not a single script: the equivalent assurance comes from
`sim_up.sh` verifying the EKF origin before declaring a stack usable, and from the gate
scoring runs it could not trust as **VOID** rather than FAIL. The RTF assertion has no
successor at all and should not be reinvented — lockstep is dead code in Cosys-AirSim, so
there is no deterministic real-time factor to assert against.

## Not here yet

Host-side unit tests for metric computation once evaluation moves in-graph, and anything
covering the ROS 2 message contracts (`drone_interfaces`) — those need a ROS environment,
which is exactly what this directory is defined by not having.
