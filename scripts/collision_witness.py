#!/usr/bin/env python3
"""Start, stop and SCORE the collision witness. One implementation, two callers.

SITL only. This is the plumbing; `watch_collisions.py` is the observer it runs.

WHY THIS MODULE EXISTS
----------------------
The witness was wired up twice -- once in bash inside `run_park_tour.sh`, once in Python inside
`run_gate.py` -- and both copies encoded the same rule:

    an absent or unreadable witness is UNKNOWN, and unknown must never be the value that
    looks clean.

Two implementations of one rule is how a rule drifts, and it drifted immediately: the Python
copy failed a run whose witness never wrote a file, while the bash copy scored it a clean PASS.
That was caught in review, one commit after the rule was written down. The second copy would
have gone on quietly disagreeing.

So the rule lives here now, once. Python callers import it; shell callers invoke it as a CLI:

    python3 scripts/collision_witness.py start
    python3 scripts/collision_witness.py stop --save out/run-collisions.json
      -> prints "<count>\\t<detail>", exits 0 clean / 1 collided / 2 unknown

THE TWO TRAPS IT ENCODES, both measured
---------------------------------------
`docker exec -d` reports success whenever the CONTAINER exists, even when the command cannot
run at all -- verified: it returns 0 for `python3 /nonexistent.py`. So starting the witness
proves nothing, and the previous run's file must be deleted first or a stale result is read as
this run's verdict.

`has_collided` alone is useless: it reports CURRENT contact, and a parked drone is in contact
with the floor. Ground is separated by `object_name` inside `watch_collisions.py`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SVC = "sim-ros2"
REMOTE_JSON = "/tmp/collision_witness.json"

# Exit codes for the CLI form, so a shell caller can branch without parsing.
EXIT_CLEAN, EXIT_COLLIDED, EXIT_UNKNOWN = 0, 1, 2


def _dexec(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", SVC, *args],
                          capture_output=True, text=True, timeout=timeout)


def start() -> bool:
    """Begin witnessing. False if it could not be started.

    Deletes the previous run's file FIRST. `docker exec -d` cannot tell us the process actually
    started, so absence of a file is the only reliable signal that nothing observed this run --
    and that signal only works if a stale file is not sitting there.
    """
    try:
        _dexec("rm", "-f", REMOTE_JSON).check_returncode()
        for f in ("watch_collisions.py", "airsim_rpc_client.py"):
            subprocess.run(["docker", "cp", str(REPO / "scripts" / f), f"{SVC}:/tmp/{f}"],
                           check=True, capture_output=True, timeout=30)
        subprocess.run(["docker", "exec", "-d", SVC, "bash", "-lc",
                        f"cd /tmp && python3 /tmp/watch_collisions.py --out {REMOTE_JSON} "
                        f"> /tmp/collision_witness.log 2>&1"],
                       check=True, capture_output=True, timeout=30)
        return True
    except Exception:
        return False


def stop_and_score(save_to: Path | None = None) -> tuple[int, str]:
    """Stop it and return (count, detail). (-1, reason) when the state is UNKNOWN.

    -1 is not a failure to report a number; it is the number. A caller must treat it as at
    least as bad as a collision, because an unobserved run and a clean run are indistinguishable
    from the outside and only one of them is safe to call clean.
    """
    try:
        _dexec("bash", "-lc", "pkill -INT -f watch_collisions.py || true")
        # SIGINT, then a beat: the observer flushes on the way out. It also flushes continuously,
        # so this sleep is belt-and-braces rather than the only thing keeping the file current.
        time.sleep(1.0)
        p = _dexec("cat", REMOTE_JSON)
        if p.returncode != 0:
            return -1, "collision witness wrote no readable file"
        d = json.loads(p.stdout)
        if save_to is not None:
            # The DETAIL, not just the count: the first question after "it hit something" is
            # "how high was the something", and only the impact points answer it. That is what
            # chose 20 m for square-10m.
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_text(json.dumps(d, indent=2))
        n = int(d.get("collision_count", 0))
        if not n:
            return 0, ""
        names = sorted({e.get("object_name", "?") for e in d.get("collisions", [])})
        shown = ", ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "")
        return n, f"{n} collision(s) with {shown}"
    except Exception as exc:
        return -1, f"collision witness unreadable: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Start/stop the collision witness (SITL only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    st = sub.add_parser("stop")
    st.add_argument("--save", type=Path, default=None,
                    help="write the full collision record here")
    a = ap.parse_args()

    if a.cmd == "start":
        ok = start()
        print("started" if ok else "FAILED to start")
        return 0 if ok else EXIT_UNKNOWN

    n, detail = stop_and_score(a.save)
    print(f"{n}\t{detail}")
    return EXIT_UNKNOWN if n < 0 else (EXIT_COLLIDED if n > 0 else EXIT_CLEAN)


if __name__ == "__main__":
    sys.exit(main())
