#!/usr/bin/env python3
"""Assert every worklog has an HTML render, and every render has an index card.

`.ai/AGENTS.md` requires one companion render per `docs/worklog/*.md`, plus a card in
`docs/worklog/html/index.html`. That rule has now been missed twice: once caught during a
CI audit, and again on 2026-07-31 when two same-day worklogs shipped unrendered. Both times
the gap was invisible until someone went looking, which is the definition of a check worth
automating.

Deliberately filesystem-based rather than `git ls-files`: a render missing from the working
tree should fail here BEFORE it is committed. In CI the checkout contains only tracked
files, so the two views coincide there anyway.

Exits non-zero with a list of what is missing. No arguments.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKLOGS = REPO / "docs" / "worklog"
RENDERS = WORKLOGS / "html"
INDEX = RENDERS / "index.html"


def main() -> int:
    problems: list[str] = []

    logs = sorted(p for p in WORKLOGS.glob("*.md"))
    if not logs:
        print(f"no worklogs found under {WORKLOGS} - is the path right?", file=sys.stderr)
        return 1

    renders = {p.name for p in RENDERS.glob("*.html")} - {"index.html"}
    index_html = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
    if not index_html:
        problems.append(f"{INDEX.relative_to(REPO)} is missing or empty")

    # 1. every worklog has a render
    for log in logs:
        expected = log.stem + ".html"
        if expected not in renders:
            problems.append(
                f"worklog has no HTML render: docs/worklog/{log.name} "
                f"-> expected docs/worklog/html/{expected}"
            )

    # 2. every render is linked from the index
    for render in sorted(renders):
        if not re.search(rf'href="{re.escape(render)}"', index_html):
            problems.append(f"render has no card in index.html: {render}")

    # 3. a render with no worklog behind it is also a drift signal
    stems = {log.stem for log in logs}
    for render in sorted(renders):
        if Path(render).stem not in stems:
            problems.append(
                f"render has no matching worklog: docs/worklog/html/{render} "
                f"-> expected docs/worklog/{Path(render).stem}.md"
            )

    if problems:
        print("worklog render check FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\n.ai/AGENTS.md: every worklog gets a hand-authored, self-contained HTML "
            "render plus a card in the index. Write the render in the same change as the "
            "worklog - do not batch them up for a later audit.",
            file=sys.stderr,
        )
        return 1

    print(f"{len(logs)} worklogs, {len(renders)} renders, all carded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
