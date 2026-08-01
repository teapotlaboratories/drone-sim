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

        accepted = {
            str(node.get(k)).strip()
            for k in ("sha", "tag", "branch", "version")
            if node.get(k) is not None
        }
        accepted.discard("None")

        if not accepted:
            problems.append(
                f"{name}: versions.lock {'.'.join(path)} pins no sha/tag/branch to check against"
            )
        elif version not in accepted:
            problems.append(
                f"{name}: .repos version={version!r} but versions.lock "
                f"{'.'.join(path)} allows {sorted(accepted)}"
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
