# `tests/` — acceptance tests

Tests that decide whether something **works**, as opposed to whether it builds. These are
the gates CI runs; everything human-facing lives in [`../docker/demo/`](../docker/demo/).

| Test | Asserts |
|---|---|
| `lane-a-smoke.sh` | The Lane A container reproduces the native `P0-07` result |

## `lane-a-smoke.sh`

Runs **inside** the Lane A image and compares against the numbers measured natively, so a
regression shows up as a changed number rather than a vague failure.

```bash
docker run --rm --shm-size=2g -e DURATION=300 -e OUTDIR=/out \
  -v "$PWD/out:/out" \
  -v "$PWD/tests/lane-a-smoke.sh:/smoke.sh:ro" \
  drone-sim/lane-a:v1.16.0 bash /smoke.sh
```

**Assertions** (exits non-zero on any miss):

| Check | Bar | Native reference |
|---|---|---|
| `/fmu/out/*` topics | ≥ 24 | 24 |
| Sensor TIMEOUTs | 0 | 0 |
| `ERROR [...]` lines | 0 | 0 |
| Data actually moving | timestamps differ across a 20 s gap | yes |
| Real-time factor | **aggregate** ≥ 0.95 | 1.0000 native · 0.9967 host podman |

**Three details that are load-bearing — do not "simplify" them:**

1. **`--shm-size=2g` is required.** Docker defaults `/dev/shm` to 64 MB and Fast-DDS uses
   shared memory as its default transport.
2. **PX4 launches under `screen` with `stty min 1 time 0`.** Without it PX4's `pxh` shell
   busy-spins its prompt — ~1.45 M writes/s, 4.1 GB per 300 s run, one core consumed, and it
   fills the filesystem mid-run.
3. **The TIMEOUT grep must stay tolerant** (`fail(ed)?: *TIMEOUT`). PX4 prints
   `fail:  TIMEOUT!` with a *double* space and `failed:` for the magnetometer; a naive
   pattern matches neither and silently reports zero while TIMEOUTs are occurring.

**Assert on aggregate RTF, never the instantaneous field.** Gazebo's `real_time_factor`
swings 0.14–1.01 while the true ratio sits at 0.977; a healthy native run contains a lone
0.503 sample out of 2,931. The script derives the aggregate from `sim_time`/`real_time`,
both of which are in the same message.

Background: [`../docs/docker/todo.md`](../docs/docker/todo.md) and
[`../docs/worklog/2026-07-29-d01-container-parity.md`](../docs/worklog/2026-07-29-d01-container-parity.md).

## Not here yet

Phase 1 adds the seeded scenario regression (SR over N runs, MCAP artifacts) and host-side
unit tests for message contracts, metric computation and the `ttl` watchdog — those run
off-target where they are fast and deterministic.
