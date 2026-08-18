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

_ospec = importlib.util.spec_from_file_location(
    "check_ekf_origin", REPO / "scripts" / "check_ekf_origin.py")
ekf = importlib.util.module_from_spec(_ospec)
_ospec.loader.exec_module(ekf)

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


def test_runner_clears_the_result_before_a_run():
    """Pinned after a run whose controller never started reported `success 4/4`.

    `run_flight` reads /out/<tag>.json after the flight. Without deleting it first, a
    flight that fails to start is scored from whatever a previous run left behind — and the
    gate calls run_flight for every seed, so that laundered a failure into a pass.
    """
    src = (REPO / "scripts" / "run_scenario.py").read_text()
    body = src.split("def run_flight")[1].split("\ndef ")[0]
    assert "result_in_container" in body.split("recorder = subprocess.Popen")[0], \
        "the result file must be removed BEFORE the flight, not after"
    assert "unlink(missing_ok=True)" in body, \
        "the host-side copy must be cleared too"


# ---------------------------------------------------------------------------------------
# offboard_control's result-fallback path, guarded statically.
#
# The allow_nan=False fallback replaces `result` with a two-key dict, so every field the
# MissionResult population reads must use .get() with a default. Indexing directly raised
# KeyError inside the timer callback, which killed rclpy.shutdown() and destroyed the very
# artifact the fallback exists to produce.
#
# Then the FIX introduced the same class of bug one line over: a default referencing
# `self.takeoff_alt`, an attribute that does not exist (it is `self.alt`), which would have
# raised AttributeError on exactly the same path. Static, because these tests run
# off-target with no ROS.
# ---------------------------------------------------------------------------------------
import ast
import pathlib

_CONTROL = (pathlib.Path(__file__).resolve().parent.parent
            / "ros2_ws/src/control/control/offboard_control.py")


def _write_result_fn():
    tree = ast.parse(_CONTROL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_write_result":
            return tree, node
    raise AssertionError("_write_result not found in offboard_control.py")


def test_mission_result_population_never_indexes_result_directly():
    """Every `result[...]` read after the fallback must be `.get()` with a default."""
    _, fn = _write_result_fn()
    offenders = []
    for node in ast.walk(fn):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "result"):
            offenders.append(getattr(node, "lineno", "?"))
    assert not offenders, (
        f"offboard_control.py: result[...] indexed directly at line(s) {offenders}. "
        "The allow_nan=False fallback leaves only {outcome, failure_reason}, so this "
        "raises KeyError inside the timer callback and no artifact is written."
    )


def test_self_attributes_used_as_defaults_actually_exist():
    """A default like `self.takeoff_alt` must name a real attribute.

    Scoped to the SECOND argument of `result.get(key, default)` calls - that is the only
    place a wrong name silently waits for the fallback path to be taken. Checking every
    `self.x` load would flag inherited rclpy Node methods (get_logger, get_clock).
    """
    tree, fn = _write_result_fn()
    assigned = {
        t.attr for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AugAssign))
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
        and t.value.id == "self"
    }
    used = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2):
            for sub in ast.walk(node.args[1]):
                if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"):
                    used.add(sub.attr)
    assert used, "no `result.get(key, default)` defaults found - has the code moved?"
    unknown = sorted(used - assigned)
    assert not unknown, (
        f"offboard_control.py _write_result uses self.{{{', '.join(unknown)}}} as a "
        ".get() default, but it is never assigned - AttributeError on the fallback path."
    )


# ---------------------------------------------------------------------------------------
# SIM-10: a stale EKF origin must VOID a run, not fail it.
#
# The numbers below are the ones actually observed in SIM-09, not invented. PX4 froze its
# local origin at 88.113 m while GPS read 123.280 m throughout, so every reported altitude
# was 35.167 m high and the vehicle "was" 35 m up while sitting on the ground. The failure
# is silent, order-dependent, and looks exactly like a control bug.


def test_the_real_c09_offset_is_caught():
    ok, why = ekf.origin_is_sane(88.113, 123.280)
    assert not ok
    assert "STALE" in why and "35.167" in why


def test_the_real_c09_fixed_state_passes():
    # After restarting PX4 so the origin re-initialised.
    ok, _ = ekf.origin_is_sane(123.280, 123.28)
    assert ok


def test_live_sensor_noise_does_not_trip_it():
    # Measured on a healthy stack: ref_alt 123.280 vs GPS 123.195. A tolerance that flagged
    # this would make the check flaky, which is worse than not having it.
    ok, _ = ekf.origin_is_sane(123.280, 123.195)
    assert ok


@pytest.mark.parametrize("ref_alt,gps_alt", [
    (None, 123.28),
    (123.28, None),
    (None, None),
])
def test_missing_telemetry_is_never_a_pass(ref_alt, gps_alt):
    """Absence of evidence must not read as evidence of sanity -- silent /fmu/out topics
    are a documented failure here (P1-02 BEST_EFFORT, D-02 shared /dev/shm), so an
    unreadable origin has to be VOID rather than OK."""
    ok, why = ekf.origin_is_sane(ref_alt, gps_alt)
    assert not ok
    assert "could not read" in why


def test_tolerance_boundary_is_exclusive_not_inclusive():
    tol = 1.0
    assert ekf.origin_is_sane(100.0, 101.0, tol)[0], "exactly at tolerance should pass"
    assert not ekf.origin_is_sane(100.0, 101.001, tol)[0], "just beyond should void"


def test_sign_of_the_offset_does_not_matter():
    # An origin set too HIGH is just as broken as one set too low.
    assert not ekf.origin_is_sane(123.280, 88.113)[0]


def test_void_exit_codes_are_distinct_from_success():
    """Callers must be able to tell 'void' from 'ran and failed'. If these ever collide
    with 0, a mis-ordered stack starts silently counting as a scored run."""
    assert ekf.VOID_STALE != 0 and ekf.VOID_UNKNOWN != 0
    assert ekf.VOID_STALE != ekf.VOID_UNKNOWN


@pytest.mark.parametrize("ref_alt,gps_alt", [
    (float("nan"), 123.28),
    (123.28, float("nan")),
    (float("nan"), float("nan")),
    (float("inf"), 123.28),
    (123.28, float("-inf")),
])
def test_non_finite_origin_is_never_a_pass(ref_alt, gps_alt):
    """PX4 publishes ref_alt as NaN before the EKF has an origin at all. Because
    abs(nan - x) is nan and `nan > tol` is False, a naive comparison reports SANE for the
    single most dangerous state. The first version of this check did exactly that on a real
    cold start -- "OK: ref_alt nan m ... = nan m apart" -- and would have green-lit flying
    against a vehicle with no origin."""
    ok, why = ekf.origin_is_sane(ref_alt, gps_alt)
    assert not ok, "a non-finite origin must never read as sane"
    assert "not finite" in why


# ---------------------------------------------------------------------------------------
# SIM-10: VOID runs are excluded from the rate AND block the criterion.


def _runs(*specs):
    """specs: (passed, void) tuples."""
    return [{"seed": i, "passed": p, "void": v} for i, (p, v) in enumerate(specs, 1)]


def test_void_runs_are_not_counted_as_failures():
    """A void run did not measure the flight code, so it must not drag the rate down.
    Counting it would blame a controller byte-identical to the one passing 10/10."""
    v = rg.score(_runs((True, False), (True, False), (False, True)), reuse=False)
    assert v["success_rate"] == 1.0, "the void run must be excluded from the denominator"
    assert v["valid_total"] == 2 and v["voids"] == 1


def test_voids_still_block_the_criterion():
    """Excluding voids without blocking would let a gate that was almost entirely void
    report a perfect rate and claim a pass."""
    v = rg.score(_runs((True, False), (True, False), (False, True)), reuse=False)
    assert v["sr_perfect"] is True
    assert v["met"] is False, "any void must make the gate inconclusive, not passed"


def test_a_mostly_void_gate_cannot_claim_success():
    v = rg.score(_runs(*([(True, False)] + [(False, True)] * 9)), reuse=False)
    assert v["success_rate"] == 1.0 and v["valid_total"] == 1
    assert v["met"] is False


def test_all_void_is_never_a_pass():
    """Zero valid runs must not divide-by-zero into a pass, nor report 100%."""
    v = rg.score(_runs((False, True), (False, True)), reuse=False)
    assert v["valid_total"] == 0
    assert v["success_rate"] == 0.0
    assert v["sr_perfect"] is False
    assert v["met"] is False


def test_empty_run_list_is_not_a_pass():
    v = rg.score([], reuse=False)
    assert v["met"] is False and v["sr_perfect"] is False


def test_clean_sweep_with_no_voids_passes():
    v = rg.score(_runs((True, False), (True, False), (True, False)), reuse=False)
    assert v["met"] is True and v["voids"] == 0


def test_reuse_still_blocks_the_criterion_even_with_no_voids():
    """The pre-existing --reuse caveat must survive the void rework."""
    v = rg.score(_runs((True, False), (True, False)), reuse=True)
    assert v["sr_perfect"] is True and v["met"] is False


def test_a_real_failure_is_still_a_failure_not_a_void():
    v = rg.score(_runs((True, False), (False, False)), reuse=False)
    assert v["voids"] == 0 and v["valid_total"] == 2
    assert v["success_rate"] == 0.5 and v["met"] is False


def test_origin_check_runs_inside_the_ros2_service_not_on_the_host():
    """`ros2` does not exist on the host that runs the gate. An earlier version invoked the
    checker with sys.executable locally, which would have made EVERY run VOID and left the
    gate permanently INCONCLUSIVE -- a check that fails closed on its own plumbing disables
    the gate just as surely as one that fails open.

    Asserted MODULE-WIDE rather than against one function name. The first version of this
    test named `_origin_void_reason` directly, and a later refactor that moved the exec into
    a `_run_origin_check` helper broke it -- a false positive on a change that preserved the
    invariant perfectly. Pin the property, not the call site's current shape.

    It was pinned a SECOND time by the string `rs.COMPOSE`, and the pivot away from the
    Gazebo compose stack broke it the same way: run_gate.py now reaches the container
    through `rs.dexec(...)` and the invariant was never violated. The lesson repeated
    itself, so the assertion below is written against the PROPERTY -- the checker's argv is
    built by run_scenario's container helper, and nothing runs it locally -- rather than
    against whichever helper currently spells that."""
    src = (REPO / "scripts" / "run_gate.py").read_text()
    assert "rs.dexec(" in src, (
        "the checker must be exec'd into the ROS 2 container via run_scenario's helper, "
        "so the container name has exactly one definition"
    )
    assert "sys.executable" not in src, (
        "the origin checker must NOT run on the gate host - there is no ros2 there"
    )
    # And the helper must genuinely enter a container, not shell out on the host.
    rs_src = (REPO / "scripts" / "run_scenario.py").read_text()
    assert '"docker", "exec"' in rs_src, "dexec must be a real `docker exec`"


# ---------------------------------------------------------------------------------------
# The gate must WAIT for an origin before judging (the barrier), and must treat the two void
# codes differently. STALE cannot be waited out; UNKNOWN is transient by definition.


def _origin_probe(sequence):
    """Feed _origin_void_reason a scripted series of checker exit codes."""
    calls = {"n": 0}

    def fake():
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[i]
    return fake, calls


def test_gate_waits_out_a_transient_missing_origin(monkeypatch):
    """restart_stack() returns on CONTAINER HEALTH, which is not the same event as the EKF
    establishing an origin -- PX4 publishes ref_alt as NaN until it does. Checking
    immediately races the estimator, and because ANY void blocks the criterion, one slow
    start would turn the whole gate INCONCLUSIVE."""
    fake, calls = _origin_probe([rg.ORIGIN_UNKNOWN, rg.ORIGIN_UNKNOWN, 0])
    monkeypatch.setattr(rg, "_run_origin_check", fake)
    monkeypatch.setattr(rg, "ORIGIN_POLL_S", 0)
    assert rg._origin_void_reason() == "", "a transient UNKNOWN must be waited out, not voided"
    assert calls["n"] == 3


def test_gate_does_not_wait_out_a_stale_origin(monkeypatch):
    """An EKF origin is set ONCE, so a stale one never re-settles. Retrying would just burn
    the timeout and then void anyway -- and would hide the actionable message."""
    fake, calls = _origin_probe([rg.ORIGIN_STALE])
    monkeypatch.setattr(rg, "_run_origin_check", fake)
    monkeypatch.setattr(rg, "ORIGIN_POLL_S", 0)
    why = rg._origin_void_reason()
    assert why and "STALE" in why
    assert calls["n"] == 1, "STALE must void immediately, not after the full wait"


def test_gate_gives_up_and_voids_if_no_origin_ever_appears(monkeypatch):
    fake, _ = _origin_probe([rg.ORIGIN_UNKNOWN])
    monkeypatch.setattr(rg, "_run_origin_check", fake)
    monkeypatch.setattr(rg, "ORIGIN_POLL_S", 0)
    monkeypatch.setattr(rg, "ORIGIN_WAIT_S", 0)
    why = rg._origin_void_reason()
    assert why and "no EKF origin appeared" in why


def test_an_unrunnable_checker_is_void_not_pass(monkeypatch):
    """An unverifiable stack must never read as verified."""
    fake, _ = _origin_probe([-1])
    monkeypatch.setattr(rg, "_run_origin_check", fake)
    monkeypatch.setattr(rg, "ORIGIN_POLL_S", 0)
    monkeypatch.setattr(rg, "ORIGIN_WAIT_S", 0)
    why = rg._origin_void_reason()
    assert why and "could not run" in why


def test_void_exit_codes_are_imported_not_redeclared():
    """Two copies of "which number means stale" is exactly the drift that makes a void look
    like a pass."""
    assert rg.ORIGIN_STALE == ekf.VOID_STALE
    assert rg.ORIGIN_UNKNOWN == ekf.VOID_UNKNOWN


# --------------------------------------------------------------------------------------
# Collision scoring (SIM-22 / PR 40).
#
# This rule has drifted TWICE. First between two copies of it -- run_gate.py failed an
# unreadable witness while run_park_tour.sh passed it -- and then again inside the branch
# that consolidated them, where stdout parsing let a stray stderr line become the "count"
# and the run scored CLEAN. Both were caught by review rather than by anything executable.
#
# check_run is a pure function of its arguments, so there is no excuse for that.
# --------------------------------------------------------------------------------------

def test_a_collision_fails_a_run_that_is_otherwise_perfect():
    """Every other number says success; the vehicle hit a building."""
    ok, why = rg.check_run(result([0.10, 0.12, 0.09]), SCENARIO, collisions=2,
                           collision_detail="2 collision(s) with TemplateCube_Rounded_7")
    assert not ok
    assert "TemplateCube_Rounded_7" in why


def test_unknown_collision_state_is_not_clean():
    """-1 is not "no collisions". An unobserved run and a clean run look identical from the
    outside, and only one of them is safe to call clean."""
    ok, why = rg.check_run(result([0.10, 0.12, 0.09]), SCENARIO, collisions=-1)
    assert not ok
    assert "unknown" in why.lower()


def test_zero_collisions_does_not_block_a_pass():
    ok, why = rg.check_run(result([0.10, 0.12, 0.09]), SCENARIO, collisions=0)
    assert ok, why


def test_collisions_are_judged_before_the_waypoint_numbers():
    """Ordering is load-bearing: after an impact the waypoint errors describe a crash that
    happened to land near the target, so they must not be allowed to speak first."""
    ok, why = rg.check_run(result([0.05, 0.05]), SCENARIO, collisions=1)
    assert not ok
    # the reason must be the collision, not anything about waypoints
    assert "waypoint" not in why.lower()


def test_a_collision_outranks_an_explicit_failure_reason():
    """Both are failures; the collision is the more actionable one to report."""
    ok, why = rg.check_run(result([0.05], outcome="failure", failure_reason="timeout"),
                           SCENARIO, collisions=3, collision_detail="3 collision(s) with Wall")
    assert not ok
    assert "Wall" in why


@pytest.mark.parametrize("count", [-99, -2, -1])
def test_every_negative_count_is_unknown_not_clean(count):
    ok, _ = rg.check_run(result([0.1]), SCENARIO, collisions=count)
    assert not ok


# --------------------------------------------------------------------------------------
# GPU-LiDAR readback drop accounting (SIM-24).
#
# SIM-23 turned a renderer crash into a dropped scan, which moved the failure from loud to
# silent. These pin the two places that silence could creep back in: a drop count that is
# UNKNOWN must never be summed as zero, and a per-flight delta must not be negative or
# inherit a previous seed's drops under --reuse.
# --------------------------------------------------------------------------------------

def _run(drops):
    return {"seed": 1, "lidar_readback_drops": drops}


def test_drops_total_sums_real_drops():
    assert rg._drops_total([_run(2), _run(0), _run(3)]) == 5


def test_unknown_drop_count_is_not_summed_as_zero():
    """-1 means the renderer log was unreadable. Adding it would UNDERCOUNT the total, which is
    the same 'unknown looks clean' failure the collision witness already guards against."""
    assert rg._drops_total([_run(4), _run(-1)]) == 4


def test_a_run_that_never_reported_is_not_counted():
    """None means the run never got far enough to ask — a VOID bring-up, say."""
    assert rg._drops_total([_run(4), _run(None), {}]) == 4


def test_no_drops_totals_zero():
    assert rg._drops_total([_run(0), _run(0)]) == 0


def test_unknown_runs_are_counted_separately_so_they_are_visible():
    """The total alone cannot distinguish 'clean' from 'never measured'; the companion count is
    what makes an unmeasured gate say so."""
    runs = [_run(0), _run(-1), _run(-1), _run(None)]
    unknown = sum(1 for r in runs
                  if r.get("lidar_readback_drops") is not None
                  and r["lidar_readback_drops"] < 0)
    assert rg._drops_total(runs) == 0 and unknown == 2


@pytest.mark.parametrize("before,after,expected", [
    (0, 0, 0),          # clean flight on a fresh stack
    (0, 3, 3),          # three scans lost
    (10, 13, 3),        # --reuse: only THIS flight's drops, not the container's total
    (-1, 5, -1),        # baseline unreadable -> unknown, never a number
    (5, -1, -1),        # end unreadable   -> unknown
    (-1, -1, -1),
    (7, 5, 0),          # log rotated/truncated: clamp, never report negative drops
])
def test_drops_during_is_a_delta_and_propagates_unknown(before, after, expected):
    assert rg.rs.drops_during(before, after) == expected


# --- SIM-34: the display decision and the recording decision must never disagree -------------
#
# restart_stack derives `--display` from SIM_CHASE_VIDEO, and run_flight derives `chase_on` from
# the same variable. That guarantee is only as good as the two tests being IDENTICAL -- the
# literal used to be copied to three places, so the next edit to one copy would silently
# re-create SIM-34: a gate that asks for chase, gets no display, and reports chase_video: None
# beside video_written: True.                                                (review, PR 58)

def _rs():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "rs_env", Path(__file__).resolve().parents[1] / "scripts" / "run_scenario.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_env_true_accepts_only_the_documented_truthy_values(monkeypatch):
    rs = _rs()
    for v in ("1", "true", "yes"):
        monkeypatch.setenv("SIM_CHASE_VIDEO", v)
        assert rs._env_true("SIM_CHASE_VIDEO") is True, f"{v!r} should be truthy"
    for v in ("", "0", "no", "false", "off", "TRUE", "Yes"):
        monkeypatch.setenv("SIM_CHASE_VIDEO", v)
        assert rs._env_true("SIM_CHASE_VIDEO") is False, f"{v!r} should be falsey"
    monkeypatch.delenv("SIM_CHASE_VIDEO", raising=False)
    assert rs._env_true("SIM_CHASE_VIDEO") is False, "unset should be falsey"


def test_only_one_truthiness_literal_survives_in_run_scenario():
    """Grep, deliberately: the point is that no SECOND copy of the tuple exists to drift."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts" / "run_scenario.py").read_text()
    assert src.count('("1", "true", "yes")') == 1, (
        "the truthiness literal must live in _env_true() alone -- a second copy is how SIM-34 "
        "comes back")


def test_chase_video_is_cleared_before_the_flight():
    """A stale chase mp4 must never be reported as this run's evidence."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts" / "run_scenario.py").read_text()
    assert "chase_mp4.unlink(missing_ok=True)" in src, (
        "chase_mp4 must be cleared up-front like the bag, result, video and probe -- "
        "record_chase.sh has paths that leave the destination untouched")
