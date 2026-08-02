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

    # A `why_not_<STATUS>_yet:` key inside a block whose status IS that status is a stale
    # leftover from an earlier state, and it says the opposite of the status line above it.
    # This is not hypothetical: lane_c.unreal_engine shipped `status: LOCKED` alongside
    # `why_not_LOCKED_yet: Nothing has been COMPILED against it` -- a direct self-
    # contradiction, in the file this project treats as authoritative, that survived review
    # until someone read the whole block. Mechanically detectable, so detect it.
    stale = []
    for m in re.finditer(r"^(\s*)why_not_([A-Za-z-]+)_yet\s*:", text, re.M):
        indent, claimed = m.group(1), m.group(2)
        line_no = text[:m.start()].count("\n") + 1
        # Walk backwards to the nearest `status:` at the same indent -- i.e. this block's.
        prefix = text[:m.start()]
        st = None
        for sm in re.finditer(rf"^{indent}status:\s*([A-Za-z-]+)", prefix, re.M):
            st = sm.group(1)
        if st is not None and st == claimed:
            stale.append((line_no, claimed))
    if stale:
        for line_no, claimed in stale:
            print(f"versions.lock:{line_no}: `why_not_{claimed}_yet` in a block whose status "
                  f"IS {claimed} - stale, and it contradicts the status line",
                  file=sys.stderr)
        return 1

    print(f"all {total} CONFLICT entries carry a summary; no stale why_not_*_yet keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
