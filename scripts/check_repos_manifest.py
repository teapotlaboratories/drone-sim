#!/usr/bin/env python3
"""Assert `.repos` agrees with `versions.lock`.

`.repos` says so itself: "Versions here MUST agree with versions.lock. versions.lock is the
authority and carries the reasoning; this file is the mechanism."

On 2026-07-31 it did not, in two places, and one of them mattered:

  Micro-XRCE-DDS-Agent   .repos v2.4.2   versions.lock v2.4.3
  px4_ros_com            .repos main     versions.lock release/1.16

v2.4.2 is recorded in versions.lock as GENUINELY UNBUILDABLE - eProsima deleted the Fast-DDS
branch its superbuild pins, so it fails with `fatal: invalid reference: 2.12.x`. Anyone
running `vcs import vendor < .repos` on a fresh machine checked out the version that cannot
compile. That is the "reproduces a broken stack" failure the project has a rule against,
sitting in the file that IS the reconstruction mechanism.

A `.repos` entry passes if its `version` matches the lock entry's sha, tag, OR branch - so
either pinning style is accepted, while real drift is caught. Entries commented out in
`.repos` are not checked; entries with no mapping below are reported so the map cannot
silently rot.

Exits non-zero listing every disagreement. No arguments.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# .repos entry -> the path through versions.lock that owns its version
MAP: dict[str, tuple[str, ...]] = {
    "PX4-Autopilot-v1.16":  ("lane_a", "px4"),
    "Micro-XRCE-DDS-Agent": ("lane_a", "micro_xrce_dds_agent"),
    "px4_msgs":             ("lane_a", "px4_msgs"),
    "px4_ros_com":          ("lane_a", "px4_ros_com"),
    "Cosys-AirSim":         ("lane_c", "cosys_airsim"),
    # Lane B trees stay commented out in .repos while the lane is deferred:
    "PX4-Autopilot-v1.14.3": ("lane_b", "px4"),
    "PegasusSimulator":      ("lane_b", "pegasus"),
}


class _NoDuplicateKeys(yaml.SafeLoader):
    """PyYAML silently accepts a duplicate mapping key and keeps the LAST one.

    That is not academic here. On 2026-08-01 an edit to versions.lock replaced a coupling's
    `- id:` line instead of inserting before it, leaving the previous coupling's body
    orphaned under the new id. The result had two `assert:` keys in one mapping, one
    coupling silently vanished, and `yaml.safe_load` parsed it without complaint - so the
    existing parse check went green on a corrupted lock file.
    """


def _no_dupes(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"duplicate key {key!r} (line {key_node.start_mark.line + 1}) - "
                f"PyYAML would silently keep the last one", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateKeys.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dupes)


def dig(d: dict, path: tuple[str, ...]):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def main() -> int:
    problems: list[str] = []

    # Load both under the strict loader first: a duplicate key means the file does not say
    # what its author thinks it says, and every comparison below would be against the wrong
    # value.
    try:
        lock = yaml.load((REPO / "versions.lock").read_text(encoding="utf-8"), _NoDuplicateKeys)
    except yaml.YAMLError as exc:
        print(f"versions.lock has a duplicate key:\n  {exc}", file=sys.stderr)
        return 1
    try:
        repos_doc = yaml.load((REPO / ".repos").read_text(encoding="utf-8"), _NoDuplicateKeys)
    except yaml.YAMLError as exc:
        print(f".repos has a duplicate key:\n  {exc}", file=sys.stderr)
        return 1

    repos = (repos_doc or {}).get("repositories") or {}

    # FLOOR GUARD. Without this the whole check passes vacuously: an empty .repos, a
    # `repositories:` key with nothing under it, a mistyped top-level key, or a phase edit
    # that comments out the wrong block all yield {} - and the loop below then compares
    # nothing and prints a success banner. The sibling check
    # (check_worklog_renders.py) has had this guard since it was written; this one did not,
    # and a merge-gate review caught it.
    #
    # EXPECTED_ACTIVE is the set that must be uncommented at the current phase. Lane B is
    # deliberately commented out while deferred, so it is not listed.
    EXPECTED_ACTIVE = {
        "PX4-Autopilot-v1.16", "Micro-XRCE-DDS-Agent", "px4_msgs", "px4_ros_com",
        "Cosys-AirSim",
    }
    missing = EXPECTED_ACTIVE - set(repos)
    if missing:
        problems.append(
            f".repos is missing entries that must be active at this phase: "
            f"{sorted(missing)} - a commented-out or mis-indented block reconstructs a "
            f"tree without them"
        )

    # Couplings are a list of {id, assert, why, severity}; a clobbered entry shows up as a
    # duplicate id or a missing field rather than a parse error.
    seen_ids: set[str] = set()
    for c in lock.get("couplings") or []:
        cid = c.get("id")
        if cid in seen_ids:
            problems.append(f"versions.lock: duplicate coupling id {cid!r}")
        seen_ids.add(cid)
        for field in ("assert", "why", "severity"):
            if field not in c:
                problems.append(f"versions.lock: coupling {cid!r} is missing {field!r}")

    for name, entry in sorted(repos.items()):
        version = str((entry or {}).get("version", "")).strip()
        path = MAP.get(name)
        if path is None:
            problems.append(
                f"{name}: active in .repos but not mapped in {Path(__file__).name} "
                f"- add it to MAP so it cannot drift unchecked"
            )
            continue

        node = dig(lock, path)
        if not isinstance(node, dict):
            problems.append(f"{name}: versions.lock has no entry at {'.'.join(path)}")
            continue

        sha = str(node.get("sha") or "").strip()
        movable = {
            str(node.get(k)).strip()
            for k in ("tag", "branch", "version")
            if node.get(k) is not None
        } - {"None", ""}

        # A resolved SHA is a hard requirement, not one option among several. .repos's own
        # header: "When a `sha: TODO-verify` in versions.lock is resolved, pin the SHA here
        # too - a tag can move, a SHA cannot." The first version of this checker accepted
        # sha OR tag OR branch, which let px4_msgs sit on a live branch while versions.lock
        # had a SHA - the exact drift it was written to catch.
        if sha and sha != "TODO-verify":
            if version != sha:
                problems.append(
                    f"{name}: .repos version={version!r} but versions.lock "
                    f"{'.'.join(path)} has a RESOLVED sha={sha!r}. A resolved sha must be "
                    f"pinned here; {'a movable ref' if version in movable else 'this value'} "
                    f"is not reproducible."
                )
        elif not movable:
            problems.append(
                f"{name}: versions.lock {'.'.join(path)} pins no sha/tag/branch to check against"
            )
        elif version == "TODO-verify" or version in ("", "None"):
            problems.append(
                f"{name}: .repos version={version!r} is a sentinel, not a pin - "
                f"`vcs import` cannot check out TODO-verify"
            )
        elif version not in movable:
            problems.append(
                f"{name}: .repos version={version!r} but versions.lock "
                f"{'.'.join(path)} allows {sorted(movable)}"
            )

    if problems:
        print(".repos / versions.lock drift check FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nversions.lock is the authority; .repos is the mechanism. A disagreement means "
            "`vcs import vendor < .repos` reconstructs a tree that is not the one recorded as "
            "working - which has already happened once, with an agent version that cannot "
            "build at all.",
            file=sys.stderr,
        )
        return 1

    print(f"{len(repos)} active .repos entries, all agree with versions.lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
