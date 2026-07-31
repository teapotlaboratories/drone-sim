"""Off-target tests for the gate's pass/fail logic (P1-06).

These need no simulator, no DDS and no containers — they are pure dict-in, verdict-out.
That matters: `check_run` is the function that decides whether the Phase 1 exit criterion
is met, and it shipped with a hole that let a NaN waypoint error count as a PASS. A
handful of cases here would have caught it before the gate was ever run for real.

    python3 -m pytest tests/test_gate_checks.py -q
"""

import importlib.util
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_gate", REPO / "scripts" / "run_gate.py")
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

SCENARIO = {"tolerances": {"accept_radius_m": 1.0}}


def result(errors, **over):
    base = {"outcome": "success", "waypoints_total": len(errors),
            "waypoints_reached": len(errors), "waypoint_errors_m": errors}
    base.update(over)
    return base


def test_clean_run_passes():
    ok, why = rg.check_run(result([0.20, 0.08, 0.15, 0.19]), SCENARIO)
    assert ok, why


def test_error_outside_accept_radius_fails():
    ok, why = rg.check_run(result([0.2, 99.0, 0.2, 0.2]), SCENARIO)
    assert not ok and "exceeds accept radius" in why


def test_nan_error_must_not_pass():
    """The regression this file exists for.

    Every comparison against NaN is False, so `worst > radius` silently passed it — and
    `max()` dropped it, so the reported worst error was wrong too. The case where the
    error is UNKNOWN must never be the case that looks clean."""
    ok, why = rg.check_run(result([0.2, float("nan"), 0.2, 0.2]), SCENARIO)
    assert not ok, "a NaN waypoint error passed the gate"
    assert "finite" in why


def test_none_error_must_not_pass():
    """The controller now writes JSON null rather than a bare NaN, so cover that shape."""
    ok, why = rg.check_run(result([0.2, None, 0.2, 0.2]), SCENARIO)
    assert not ok and "finite" in why


def test_infinite_error_must_not_pass():
    ok, why = rg.check_run(result([0.2, float("inf"), 0.2, 0.2]), SCENARIO)
    assert not ok and "finite" in why


def test_partial_mission_fails_even_if_outcome_says_success():
    """The gate re-derives the verdict rather than trusting `outcome`, so a controller bug
    that mislabels a partial flight cannot launder itself through the gate."""
    r = result([0.2, 0.2], waypoints_total=4, waypoints_reached=2)
    ok, why = rg.check_run(r, SCENARIO)
    assert not ok and "waypoints" in why


def test_error_count_must_match_waypoint_count():
    r = result([0.2, 0.2, 0.2], waypoints_total=4, waypoints_reached=4)
    ok, why = rg.check_run(r, SCENARIO)
    assert not ok and "error samples" in why


def test_explicit_failure_is_reported_with_its_reason():
    r = result([], outcome="failure", failure_reason="timeout in state arm",
               waypoints_total=4, waypoints_reached=0)
    ok, why = rg.check_run(r, SCENARIO)
    assert not ok and why == "timeout in state arm"


@pytest.mark.parametrize("bad", ["sq; touch /out/PWNED; echo", "../../opt/px4", "a b", ""])
def test_scenario_names_that_reach_the_shell_are_rejected(bad, tmp_path):
    """A scenario name lands in container paths and in an `rm -rf`. Phase 4 ingests
    external scenario sets, so this stops being hypothetical."""
    import importlib.util as iu
    spec = iu.spec_from_file_location("run_scenario", REPO / "scripts" / "run_scenario.py")
    rs = iu.module_from_spec(spec); spec.loader.exec_module(rs)
    f = tmp_path / "s.yaml"
    f.write_text(f'name: "{bad}"\n')
    with pytest.raises(SystemExit):
        rs.load_scenario(f)


def test_worst_error_ignores_unusable_values_without_hiding_them():
    """_worst is for REPORTING only — check_run has already failed the run by then."""
    assert rg._worst([0.2, float("nan"), 0.5]) == 0.5
    assert rg._worst([]) == 0.0
    assert rg._worst(None) == 0.0
