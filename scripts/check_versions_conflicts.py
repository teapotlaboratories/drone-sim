#!/usr/bin/env python3
"""Assert every `status: CONFLICT` in versions.lock carries a summary.

NOT "there are no conflicts". There is a real, accepted one - the NVIDIA driver against
Isaac Sim's validated version, which is why Lane B is deferred. Failing on a known,
deliberate state makes a job people learn to ignore. What is worth enforcing is that a
CONFLICT never sits there unexplained.

Shared by tier-1 CI and scripts/run_local_ci.sh so a local pass and a CI pass mean the same
thing.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    text = (REPO / "versions.lock").read_text(encoding="utf-8")
    bad = []
    total = 0
    for m in re.finditer(r"^(\s*)status:\s*CONFLICT\s*$", text, re.M):
        total += 1
        tail = text[m.end():m.end() + 800]
        if "summary:" not in tail.split("\n\n")[0]:
            bad.append(text[:m.start()].count("\n") + 1)
    if bad:
        print(f"versions.lock: CONFLICT without a summary at line(s) {bad}", file=sys.stderr)
        return 1
    print(f"all {total} CONFLICT entries carry a summary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
