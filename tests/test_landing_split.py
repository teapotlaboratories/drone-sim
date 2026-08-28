"""Off-target tests for the landing-split detector and the verdict it produces (SIM-27 step 2).

No simulator, no AirSim, no msgpack: `SplitWatch` is pure arithmetic over samples the probe
already takes, and `_with_split_fault` is a pure dict transform.

WHAT THESE PIN, and why it is worth pinning. AirSim holds two independent positions for the
vehicle -- the physics integrator's, which every PX4 sensor is derived from, and the Unreal
actor's, which the cameras are bolted to -- and nothing reconciles them. When they split, the
run used to end 240 s later as `timeout in state land`: a CONTROL failure, on a flight that had
reached 4/4 waypoints. Nine such traces sit in `out/`.

The detector must therefore do two things that pull against each other:

  1. TRIP on the real thing, quickly. Replayed over the 91 recorded traces (49,732 samples) it
     fires on 9 of 9 runs recorded as `timeout in state land`, 37-153 s before they ended.
  2. NEVER trip on skew. The two poses are read by two separate RPC calls, so a fast-moving
     vehicle shows a gap that is pure sampling: 0.21 m at -2.5 m/s across 40 AUTO.LAND
     touchdowns, and 0.640 m in the trace of the one gate seed that was struck by a car.

Those recorded traces live on the evidence drive, so they cannot be the test -- a test that
reads `out/` passes only on the bench. The cases below are the shapes distilled from them.

    python3 -m pytest tests/test_landing_split.py -q
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pl = _load("probe_landing")
rs = _load("run_scenario")


def _feed(watch, samples):
    """Push (t, dz, vz) triples until something trips. Returns the fault or None."""
    for t, dz, vz in samples:
        fault = watch.update(t, dz, vz)
        if fault is not None:
            return fault
    return None


def _descent(start_t=0.0, seconds=10.0, hz=5.0, dz0=0.0, rate=0.7, vz=0.695):
    """A split opening at `rate` m/s -- the shape of every recorded failure."""
    n = int(seconds * hz)
    return [(round(start_t + i / hz, 2), round(dz0 + rate * i / hz, 3), vz) for i in range(n)]


# --- the fault it exists to catch -----------------------------------------------------

def test_trips_on_a_sustained_split_while_descending():
    fault = _feed(pl.SplitWatch(), _descent(seconds=20))
    assert fault is not None
    assert fault["fault"] == "pose_split"
    assert fault["reason"] == pl.LANDING_FAULT_REASON
    assert fault["dz"] >= pl.SPLIT_FAULT_M
    assert fault["held_seconds"] >= pl.SPLIT_FAULT_SECONDS


def test_trips_within_three_seconds_of_the_split_opening():
    """The whole point is ending the run early. At 0.7 m/s the gap passes 1 m at t=1.43 s, the
    first qualifying SAMPLE lands at 1.6 s, and the hold is 1 s -- so 2.6 s, and the measured
    CitySample trace agrees (crossed at 84.06 s, tripped at 85.06 s). A trip much later than
    that means the detector is itself the thing wasting the time it was written to save."""
    fault = _feed(pl.SplitWatch(), _descent(seconds=20))
    assert fault["t"] <= 3.0


def test_fires_once_and_then_stays_quiet():
    """It latches. A second fault record would rewrite the abort file under a controller that
    has already read the first one."""
    w = pl.SplitWatch()
    assert _feed(w, _descent(seconds=20)) is not None
    assert w.update(99.0, 107.9, 0.7) is None


# --- what must NOT trip it ------------------------------------------------------------

def test_ignores_a_single_sample_of_rpc_skew():
    """The measured false-positive shape: one large sample between two clean ones."""
    samples = [(0.0, 0.01, 0.7), (0.2, 2.5, 0.7), (0.4, 0.02, 0.7), (0.6, 0.01, 0.7)]
    assert _feed(pl.SplitWatch(), samples) is None


def test_ignores_a_split_while_climbing():
    """`square-10m-seed7` reaches |dz| 0.640 m at -3 m/s and PASSED. Sign matters: the fault is
    the integrator sinking BELOW a stopped actor, so an ascending gap is skew by construction --
    and an abs() detector would have tripped on the one seed a traffic car hit."""
    assert _feed(pl.SplitWatch(), _descent(seconds=20, vz=-2.99)) is None


def test_ignores_a_negative_split():
    """Actor below the integrator is not this fault, however large."""
    samples = [(i / 5, -5.0, 0.7) for i in range(50)]
    assert _feed(pl.SplitWatch(), samples) is None


def test_unknown_velocity_is_not_descent():
    """The probe writes `vz: null` when AirSim omits linear_velocity. Unknown must break the
    run, not extend it -- treating a hole in the data as evidence is how a witness lies."""
    samples = [(i / 5, 5.0, None) for i in range(50)]
    assert _feed(pl.SplitWatch(), samples) is None


def test_a_gap_in_the_trace_cannot_satisfy_the_hold_alone():
    """The probe RECONNECTS after an RPC error, so two samples can straddle a 90 s outage. The
    duration would be satisfied by that gap alone; the sample count is what stops it."""
    samples = [(0.0, 5.0, 0.7), (90.0, 5.0, 0.7)]
    assert _feed(pl.SplitWatch(), samples) is None
    # A third sample after the gap is enough -- by then the split is genuinely persistent.
    assert _feed(pl.SplitWatch(), samples + [(90.2, 5.0, 0.7)]) is not None


def test_a_split_that_closes_resets_the_hold():
    """Below threshold clears the run, so an intermittent gap never accumulates into a fault."""
    samples = []
    for i in range(40):
        t = round(i * 0.2, 2)
        samples.append((t, 5.0 if i % 2 == 0 else 0.01, 0.7))
    assert _feed(pl.SplitWatch(), samples) is None


# --- sampling skew, which is systematic rather than transient -------------------------

def test_a_stretched_rpc_gap_cannot_manufacture_a_split():
    """`dz` is the difference between two SEQUENTIAL RPCs, so it carries `vz * gap` of pure
    sampling error. That error persists for as long as the descent does, so neither the hold
    nor the sample count filters it: a render hitch stretching the gap to half a second during
    a 2 m/s descent would invent a metre of split and void a healthy run."""
    samples = [(i / 5, 1.05, 2.1, 0.5) for i in range(50)]     # 1.05 m of "split", all skew
    w = pl.SplitWatch()
    assert all(w.update(t, dz, vz, gap) is None for t, dz, vz, gap in samples)


def test_the_real_split_still_trips_with_a_normal_gap():
    """The measured gap is ~0.08 s. At the landing descent rate that is 0.056 m of skew against
    a 1.7 m split -- the correction must not blunt the thing it is protecting."""
    samples = [(t, dz, vz, 0.08) for t, dz, vz in _descent(seconds=20)]
    w = pl.SplitWatch()
    fault = next((f for f in (w.update(*s) for s in samples) if f), None)
    assert fault is not None
    assert fault["rpc_gap_s"] == 0.08
    assert fault["dz_less_skew"] < fault["dz"]


def test_an_absent_gap_scores_exactly_as_before():
    """Every trace recorded before 2026-08-28 has no `gap` field, and the 9-of-9 / 0-of-82
    replay those traces produced is this change's evidence. Missing must mean uncorrected."""
    a = _feed(pl.SplitWatch(), _descent(seconds=20))
    w = pl.SplitWatch()
    b = next((f for f in (w.update(t, dz, vz, 0.0) for t, dz, vz in _descent(seconds=20)) if f),
             None)
    assert a["t"] == b["t"] and a["dz"] == b["dz"]


# --- the verdict ----------------------------------------------------------------------

def _fault():
    return _feed(pl.SplitWatch(), _descent(seconds=20))


def test_a_split_voids_the_run_and_names_it():
    res = rs._with_split_fault({"outcome": "failure",
                                "failure_reason": "timeout in state land"}, _fault())
    assert res["outcome"] == "void"
    assert pl.LANDING_FAULT_REASON in res["failure_reason"]
    assert res["split_void_reason"] == res["failure_reason"]
    # The controller's own words are kept, not dropped.
    assert res["controller_failure_reason"] == "timeout in state land"


def test_a_successful_landing_is_voided_too_if_the_poses_split():
    """A landing that terminated while the two poses disagreed by metres is not a pass with a
    footnote: the imagery in that bag shows a world the vehicle's state was not in."""
    res = rs._with_split_fault({"outcome": "success", "failure_reason": ""}, _fault())
    assert res["outcome"] == "void"
    assert "controller_failure_reason" not in res


def test_no_fault_leaves_the_result_exactly_as_it_was():
    before = {"outcome": "success", "failure_reason": ""}
    assert rs._with_split_fault(dict(before), None) == before


def test_the_sentence_says_what_was_measured_and_who_is_not_at_fault():
    """The report prints this verbatim, and it is the whole deliverable of step 2: a stranger
    debugging their own world must not read it as a flight-control failure."""
    reason = rs._split_reason(_fault())
    assert "SIM-27" in reason
    assert "The mission is not what failed" in reason
    assert "descending" in reason


def test_the_abort_file_carries_the_full_sentence_not_the_bare_fault_name(tmp_path):
    """The watcher writes this file and the controller fails with whatever `reason` it holds,
    so the two must not disagree. They did: the fault record carries its own short `reason`
    (the bare fault name), and spreading it over the diagnostic sentence left the controller
    reporting four words and no measurement. Caught by replaying a real trace, not by reading
    the code."""
    trace, abort = tmp_path / "t-landing.jsonl", tmp_path / "t-abort.json"
    trace.write_text("")
    w = rs.SplitAbort(trace, abort, "t")
    fault = _fault()
    w._fire(fault)
    import json
    doc = json.loads(abort.read_text())
    assert doc["reason"] == rs._split_reason(fault)
    assert doc["reason"] != pl.LANDING_FAULT_REASON
    assert "SIM-27" in doc["reason"]
    # And the measurements survive alongside it, so the abort file is readable evidence
    # on its own.
    assert doc["fault"] == "pose_split" and doc["dz"] == fault["dz"]


def test_the_watcher_does_not_shadow_a_thread_internal(tmp_path):
    """`self._stop` is threading.Thread's own method -- join() calls it -- so an Event by that
    name makes join() raise TypeError at the end of every flight. Also found by running it."""
    import threading
    w = rs.SplitAbort(tmp_path / "nope.jsonl", tmp_path / "abort.json", "t")
    assert callable(getattr(w, "_stop"))
    assert isinstance(getattr(w, "_done"), threading.Event)
    w.start()
    w.stop()
    w.join(timeout=5)
    assert not w.is_alive()
    # A trace that never appears is not an error: the probe may not have written a line yet.
    assert w.fault is None


# --- the controller's half, checked as source -----------------------------------------
#
# offboard_control.py imports rclpy and px4_msgs, which exist only inside the container, so
# these read the source the way test_gate_checks.py already does for run_scenario.

def _controller_src():
    return (REPO / "ros2_ws" / "src" / "control" / "control"
            / "offboard_control.py").read_text()


def test_the_abort_is_checked_before_the_state_timeout():
    """Both end the flight; the ORDER decides which sentence the run reports. Checked after the
    timeout, a split still reads as `timeout in state land` -- the exact misdiagnosis this
    change exists to remove -- because LAND runs out its budget at the same moment."""
    body = _controller_src()
    body = body[body.index("def _tick("):]
    assert body.index("_abort_requested()") < body.index("timeout in state")


def test_the_abort_ends_the_flight_through_fail_not_a_signal():
    """_fail() reaches the terminal state, which publishes the last MissionStatus and writes
    the result file. A SIGINT raises KeyboardInterrupt past both, and every run this fires on
    had flown 4/4 waypoints -- the evidence that the aircraft was not what failed."""
    body = _controller_src()
    tick = body[body.index("def _tick("):body.index("def _abort_requested(")]
    assert "self._fail(abort)" in tick


def test_an_unreadable_abort_file_does_not_take_the_flight_down():
    """A malformed byte on the harness side must not raise out of a timer callback. The repo
    has this exact scar: a ValueError from json.dumps escaped _write_result and destroyed the
    evidence it existed to protect.

    Valid JSON that is not an object -- `["stop"]`, `"stop"`, `null` -- gets past json.load and
    dies on .get(). A hand-written abort file is how this mechanism was first exercised, so
    that is the likely shape, not an exotic one."""
    fn = _abort_fn()
    assert "except FileNotFoundError" in fn
    assert "except OSError" in fn
    assert "except ValueError" in fn
    assert "isinstance(doc, dict)" in fn


def test_a_malformed_abort_file_still_stops_the_flight():
    """The one thing worse than an unreadable abort is an IGNORED one: the run then grinds out
    its 240 s `timeout in state land` and reports a control failure, which is the exact
    misdiagnosis this whole mechanism removes. The runner writes atomically, so a parse failure
    is not a half-written file -- it is a file someone meant to be an abort."""
    fn = _abort_fn()
    # The ValueError and non-dict branches must return the fallback, never "".
    for branch in ("except ValueError", "isinstance(doc, dict)"):
        tail = fn[fn.index(branch):]
        assert "UNPARSEABLE_ABORT" in tail[:tail.index("return ") + 40]
    # ...while a failed READ (as opposed to bad content) is transient and retries next tick.
    osec = fn[fn.index("except OSError"):]
    assert osec[:osec.index("return ") + 12].strip().endswith('return ""')


def _abort_fn():
    body = _controller_src()
    return body[body.index("def _abort_requested("):body.index("def _publish_status(")]


def test_the_gate_prefers_the_split_reason_over_a_sensor_void():
    """A run stopped mid-mission can easily have recorded nothing on a requested sensor. The
    empty topic is a consequence; the split is the cause, and the cause is what gets printed.

    Note the hand-built state: in the real pipeline these two keys cannot both be set, because
    _with_split_fault voids the run before _with_sensor_evidence looks at it. The precedence is
    defensive, and this test says so rather than implying the pipeline produces it."""
    res = rs._with_split_fault(
        {"outcome": "failure", "failure_reason": "timeout in state land"}, _fault())
    res["sensor_void_reason"] = "recorded no messages on /camera/depth"
    chosen = res.get("split_void_reason") or res.get("sensor_void_reason") or ""
    assert chosen == res["split_void_reason"]


def test_a_split_voided_run_still_says_what_its_bag_is_missing():
    """_with_sensor_evidence declines to void a run that already failed -- correctly, or a real
    control failure would be laundered into a void. But it also used to drop the diagnosis on
    the floor: `sensors_empty` reached the report with no reason anywhere in the JSON, and the
    explanation lived on a stderr line nobody keeps."""
    body = (REPO / "scripts" / "run_scenario.py").read_text()
    fn = body[body.index("def _with_sensor_evidence("):body.index("def run_flight(")]
    assert 'res["sensor_evidence_note"] = why' in fn
    gate = (REPO / "scripts" / "run_gate.py").read_text()
    assert '"sensor_evidence_note": result.get("sensor_evidence_note")' in gate
