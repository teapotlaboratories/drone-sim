#!/usr/bin/env python3
"""How many GPU-LiDAR scans the renderer dropped. One owner, three callers.   (SIM-24)

SITL only, and read-only: it reads a container log and commands nothing.

WHY THIS MODULE EXISTS
----------------------
`patches/cosys-airsim/0006` makes a failed GPU-LiDAR readback survivable: instead of crashing,
the renderer logs a Warning and drops the scan. Everything that wants to know how often that
happened has to agree on two facts — **what the line says** and **which container says it** —
and for a while three places each carried their own copy:

    run_scenario.py     READBACK_DROP = "readback incomplete"
    run_park_tour.sh    grep -c 'readback incomplete'
    soak_full_stack.sh  grep -c 'readback incomplete'   (twice)

That is a rule with four spellings. Change the wording in 0006 and three of them silently
return **0** — which is exactly the value that looks clean, and the reason this counter was
written in the first place. This repo has already paid for that shape four times in a week
(`collision_witness.py`, the patch-routing rule in `SIM-25`, artifact ownership in `SIM-26`,
and this), so the fix here is the one that worked before: one implementation, Python callers
import it, shell callers invoke it as a CLI.

    python3 scripts/lidar_drops.py            -> prints the count, exits 0
                                                 prints -1 and exits 2 if the log is unreadable

UNKNOWN IS NOT ZERO. An unreadable log reports -1, never 0, for the same reason the collision
witness does: a run nobody could measure and a clean run are indistinguishable from the
outside, and only one of them is safe to report as clean.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# The renderer container, and the line 0006 emits. THE source of truth for both — if either
# changes, it changes here and every caller follows.
UNREAL = "sim-unreal"
READBACK_DROP = "readback incomplete"

EXIT_OK, EXIT_UNKNOWN = 0, 2


def readback_drops(container: str = UNREAL) -> int:
    """Drops in the container's log SO FAR. -1 if the log cannot be read.

    Cumulative on purpose: callers difference it around a flight (see drops_during). Reading a
    single total at the end is only correct when the renderer is always fresh, and `--reuse`
    keeps one renderer for every seed of a gate — a lifetime total would then charge each seed
    with all the earlier seeds' drops and climb monotonically.
    """
    try:
        r = subprocess.run(["docker", "logs", container],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return -1
    if r.returncode != 0:
        return -1
    return (r.stdout or "").count(READBACK_DROP) + (r.stderr or "").count(READBACK_DROP)


def drops_during(before: int, after: int) -> int:
    """Drops attributable to one flight. -1 if either endpoint was unknown.

    Clamped at zero: a truncated or rotated log can make `after` smaller than `before`, and a
    negative number of lost scans is not a thing this should ever report.
    """
    if before < 0 or after < 0:
        return -1
    return max(0, after - before)


def main() -> int:
    ap = argparse.ArgumentParser(description="Count GPU-LiDAR readback drops (SITL only).")
    ap.add_argument("--container", default=UNREAL)
    a = ap.parse_args()
    n = readback_drops(a.container)
    print(n)
    return EXIT_UNKNOWN if n < 0 else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
