#!/usr/bin/env python3
"""Every `drone-sim/...` image reference names an image this repo actually builds.

    python3 scripts/check_image_refs.py

WHY THIS EXISTS
---------------
A Docker image tag is a string repeated across Dockerfiles (`FROM`), bring-up scripts
(`docker run`), tooling and docs. Rename it in one place and miss another, and there is no
error until someone tries to fly: `docker run` reports "Unable to find image ... locally"
and then a registry 404, which reads as a network problem rather than a typo. Nothing else
in tier-1 CI can see it, because tier-1 CI never builds or runs a container.

This replaced a `docker compose config` step. That step caught the same CLASS of defect —
a reference to something that does not exist — for the compose stack that has since been
retired. The class did not go away with the file.

WHAT IS THE AUTHORITY
---------------------
`versions.lock`, per its own contract: the `images:` section lists every image the repo
builds, each with a `tag:`. A reference to anything not in that list is the error. So
adding an image means recording it in the lock first, which is the ordering the project
wants anyway.

Deliberately NOT checked: whether the image exists in a local Docker daemon. This runs on
a hosted runner with no images and no daemon, and a check that can only pass on one
workstation is not a check.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "versions.lock"

# Any `drone-sim/<name>` optionally followed by `:<tag>`.
#
# Two exclusions, both learned from false positives on the first run:
#   (?<![\w/.-])  the reference must not be the tail of a longer path. Without it,
#                 `/…/projects/drone-sim/venvs/vllm` reads as an image called
#                 `drone-sim/venvs`, and the check fails on a filesystem path.
#   [A-Za-z0-9]$  the name and tag may not END in punctuation, so `drone-sim/airsim-client.`
#                 at the end of a sentence does not swallow the full stop.
REF = re.compile(
    r"(?<![\w/.-])drone-sim/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?::[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)?")

# Frozen historical records. They describe images as they were named on the date they were
# written, and rewriting them to match today's tags would falsify the record rather than
# fix anything. Excluded from the check for exactly that reason.
#
# docs/worklog/ is frozen by the same rule: it is the project's dated record, kept verbatim.
EXCLUDE_PREFIXES = ("docs/history/", "docs/worklog/")
EXCLUDE_FILES = {"scripts/check_image_refs.py"}   # this file quotes tags to explain them

BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".zip", ".mp4", ".pdf")


def built_images() -> set[str]:
    """Tags declared under `images:` in versions.lock.

    Parsed with a line scanner rather than a YAML load on purpose: versions.lock is read by
    several small checkers, and none of them should make PyYAML a hard requirement of CI
    for a job whose whole point is to need nothing.
    """
    tags: set[str] = set()
    in_images = False
    for line in LOCK.read_text().splitlines():
        if re.match(r"^images:\s*$", line):
            in_images = True
            continue
        # Any other top-level key ends the section.
        if in_images and re.match(r"^[A-Za-z_]", line):
            break
        if in_images:
            m = re.match(r"\s+tag:\s*(\S+)", line)
            if m:
                tags.add(m.group(1))
    return tags


def main() -> int:
    declared = built_images()
    if not declared:
        print("FAIL: no images: section found in versions.lock — is the path right?")
        return 1

    # Names without a tag are checked against the name half, so `drone-sim/px4` in prose
    # still catches a rename while never demanding that prose carry a version.
    declared_names = {t.split(":", 1)[0] for t in declared}

    # -z, and split on NUL. Splitting `git ls-files` on whitespace fragments any path with a
    # space in it into nonexistent paths, which then fail to open and are silently skipped by
    # the except below — so the file is never scanned and the check still exits 0. A CI check
    # that fails OPEN on an unusual filename is worse than no check.
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                           capture_output=True, text=True).stdout.split("\0")
    problems: list[str] = []
    seen: set[str] = set()

    for rel in files:
        if not rel:
            continue
        if rel.startswith(EXCLUDE_PREFIXES) or rel in EXCLUDE_FILES:
            continue
        if rel.endswith(BINARY_SUFFIXES):
            continue
        try:
            text = (REPO / rel).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for ref in REF.findall(line):
                seen.add(ref)
                name = ref.split(":", 1)[0]
                if ":" in ref:
                    if ref not in declared:
                        problems.append(
                            f"{rel}:{lineno}: {ref} is not declared under images: in "
                            f"versions.lock")
                elif name not in declared_names:
                    problems.append(
                        f"{rel}:{lineno}: {ref} is not declared under images: in "
                        f"versions.lock")

    if problems:
        print(f"FAIL: {len(problems)} reference(s) to an image this repo does not build:\n")
        for p in problems:
            print(f"  {p}")
        print(f"\n  declared in versions.lock: {', '.join(sorted(declared))}")
        print("\n  Either fix the reference, or add the image to versions.lock images: "
              "with its dockerfile and role.")
        return 1

    print(f"OK: {len(seen)} distinct image reference(s), all declared in versions.lock")
    for t in sorted(declared):
        print(f"  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
