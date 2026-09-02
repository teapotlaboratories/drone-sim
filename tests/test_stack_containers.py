"""The stack's container list must exist exactly once.                            (SIM-46)

`sim_up.sh` used to name its containers TWICE -- once in `teardown()`, which removes them, and
once in the verifier, which greps `docker ps` to prove they are gone. The two were kept in step
by hand.

Adding a container to one and not the other produces the worst outcome this script has: a
container left running under a teardown that printed "verified". The hard stops record that
exact failure ("a teardown that reported success has already been found to leave four
containers up for two hours"), and `SIM-46` added a sixth container, so the duplication is now
a single `STACK_CONTAINERS` array and these tests keep it that way.
"""
import re
from pathlib import Path

SIM_UP = Path(__file__).resolve().parent.parent / "scripts/sim_up.sh"
SRC = SIM_UP.read_text()

# Every container this stack is known to create. A new one must be added here AND to
# STACK_CONTAINERS -- which is the point: the test fails until both agree.
EXPECTED = {"sim-ros2", "sim-webui", "sim-qgc", "sim-px4", "sim-xrce", "sim-unreal"}


def _stack_containers():
    m = re.search(r"^STACK_CONTAINERS=\(([^)]*)\)", SRC, re.M)
    assert m, "sim_up.sh no longer defines STACK_CONTAINERS"
    names = [w.strip('"') for w in m.group(1).split()]
    # "$SIM" is the renderer, assigned at the top of the script.
    return {("sim-unreal" if n == "$SIM" else n) for n in names}


def test_stack_containers_is_the_full_set():
    assert _stack_containers() == EXPECTED


def test_teardown_removes_the_whole_list():
    """`docker rm -f` must take the array, not a hand-written list."""
    body = SRC[SRC.index("teardown() {"):]
    body = body[:body.index("\n}")]
    assert 'docker rm -f "${STACK_CONTAINERS[@]}"' in body, (
        "teardown() does not remove STACK_CONTAINERS -- it has a list of its own again")


def test_the_verifier_is_built_from_the_same_list():
    """The check that PROVES teardown worked must be derived from the same array. If it is
    hand-written it can silently verify a subset, which reads as success."""
    assert 'IFS=\'|\'; printf \'%s\' "${STACK_CONTAINERS[*]}"' in SRC, (
        "the teardown verifier no longer derives its names from STACK_CONTAINERS")


def test_no_hand_written_container_list_survives_anywhere():
    """A regex naming three or more sim-* containers in one place is the shape of the bug this
    file exists to prevent."""
    for line in SRC.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "STACK_CONTAINERS" in line:
            continue
        hits = re.findall(r"\bsim-(?:ros2|webui|qgc|px4|xrce|unreal)\b", line)
        assert len(set(hits)) < 3, (
            f"a second hand-written container list: {line.strip()!r}")


def test_every_created_container_is_in_the_list():
    """`docker run -d --name X` and `join X` are the two ways this script creates one. Anything
    it creates and does not list is a container teardown will leave running."""
    created = set(re.findall(r'docker run -d --name "?(\$?[\w-]+)"?', SRC))
    created |= set(re.findall(r"^join (\S+)", SRC, re.M))
    created = {("sim-unreal" if c in ("$SIM", "$name") else c) for c in created}
    created.discard("$name")            # the join() helper's own parameter
    listed = _stack_containers()
    missing = created - listed
    assert not missing, f"created but never torn down: {sorted(missing)}"
