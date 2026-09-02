"""Off-target tests for the collision witness's scoring and exit-code contract (SIM-22 / PR 40).

No simulator, no containers: `docker exec` is faked, so these are pure input-in, verdict-out.

WHY THIS FILE EXISTS. The rule these pin -- **an absent or unreadable witness is UNKNOWN, and
unknown must never be the value that looks clean** -- has drifted twice:

  1. It was implemented in `run_gate.py` (Python) and again in `run_park_tour.sh` (bash). The
     two disagreed: the gate failed a run whose witness wrote no file, the park tour scored it
     a clean PASS.
  2. The change that consolidated them reintroduced it in the shell half -- stdout was captured
     with `2>&1` and read with `cut -f1`, so any stderr line arriving first became the "count",
     and with the numeric guard also gone the run scored CLEAN again.

Both were caught by review, not by anything executable. `stop_and_score` is a pure function of
what the container returns, and the CLI mapping is pure arithmetic, so that is a gap worth
closing rather than a hard problem.

    python3 -m pytest tests/test_collision_witness.py -q
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "collision_witness", REPO / "scripts" / "collision_witness.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """`stop_and_score` waits a second for the observer to flush on the way out. That is right
    against a real container and pure waste here -- without this the file alone took 7 s, against
    0.13 s for the whole rest of the suite. A slow off-target test is one people stop running.
    """
    monkeypatch.setattr(cw.time, "sleep", lambda _s: None)


class FakeProc:
    """Stands in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def fake_dexec(stdout="", returncode=0):
    """Return a _dexec replacement: `cat` answers with stdout, everything else succeeds."""
    def _f(*args, **kw):
        if "cat" in args:
            return FakeProc(returncode, stdout)
        return FakeProc(0, "")
    return _f


def record(count, last=None, measured=True):
    """A witness file in its post-SIM-27 shape: the airborne phase BRACKETED by two samples,
    not watched. `measured` false means it could not bracket the flight at all."""
    return json.dumps({"airborne_contacts": count, "collision_count": count,
                       "last_object": last, "measured": measured,
                       "baseline_count": 0 if measured else None,
                       "final_count": count if measured else None})


# --- the rule itself ------------------------------------------------------------------

def test_a_clean_record_scores_zero(monkeypatch):
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(0)))
    n, detail = cw.stop_and_score()
    assert n == 0 and detail == ""


def test_collisions_are_counted_and_the_last_one_named(monkeypatch):
    """Two samples cannot produce an inventory, so the detail names the object in contact at
    the closing read and says so -- overclaiming a full list would be worse than a partial
    one."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(2, "Cube_7")))
    n, detail = cw.stop_and_score()
    assert n == 2
    assert "Cube_7" in detail and "last" in detail


def test_an_unreadable_witness_is_unknown_not_clean(monkeypatch):
    """The defect this module was extracted to prevent: absent must not read as zero."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec("", returncode=1))
    n, detail = cw.stop_and_score()
    assert n == -1, "an unreadable witness must not score 0"
    assert detail


def test_garbage_output_is_unknown_not_clean(monkeypatch):
    """A traceback or a docker warning where JSON was expected is not evidence of no impact."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec("Traceback (most recent call last):"))
    n, _ = cw.stop_and_score()
    assert n == -1


def test_a_missing_count_field_is_treated_as_zero_not_crash(monkeypatch):
    """Well-formed JSON from a bracketed flight that recorded nothing is a legitimately clean
    run. `measured` is what separates that from "we never looked"."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec(json.dumps(
        {"measured": True, "baseline_count": 4, "final_count": 4})))
    n, _ = cw.stop_and_score()
    assert n == 0


def test_a_flight_that_could_not_be_bracketed_is_unknown_not_clean(monkeypatch):
    """The witness now needs the vehicle to cross its altitude gate twice. A run that never
    did -- it never took off, or was stopped mid-air -- has not been shown to be clean, and
    scoring it 0 is exactly the absence-as-evidence failure this module exists to prevent."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(0, measured=False)))
    n, detail = cw.stop_and_score()
    assert n == -1, "an unbracketed flight must not score 0"
    assert "bracket" in detail


def test_the_full_record_is_persisted_not_just_the_count(monkeypatch, tmp_path):
    """The count reaches the gate report; the file keeps everything else the witness knew --
    the two bracketing samples, the altitude gate it used, and what it was blind to. Since
    SIM-27 that no longer includes impact points, and the artifact says so itself."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(1, "Cube_7")))
    out = tmp_path / "nested" / "collisions.json"
    cw.stop_and_score(out)
    assert out.exists(), "parent directory must be created"
    assert json.loads(out.read_text())["collision_count"] == 1


# --- the CLI contract the shell caller branches on ------------------------------------

def test_exit_codes_are_distinct():
    assert len({cw.EXIT_CLEAN, cw.EXIT_COLLIDED, cw.EXIT_UNKNOWN}) == 3


@pytest.mark.parametrize("count,expected", [
    (0,  cw.EXIT_CLEAN),
    (1,  cw.EXIT_COLLIDED),
    (7,  cw.EXIT_COLLIDED),
    (-1, cw.EXIT_UNKNOWN),
])
def test_stop_exit_code_matches_the_verdict(monkeypatch, capsys, count, expected):
    """run_park_tour.sh branches on this number and nothing else, because stdout can be
    garbled by a stray stderr line and an exit code cannot."""
    monkeypatch.setattr(cw, "stop_and_score", lambda save_to=None: (count, "detail"))
    monkeypatch.setattr("sys.argv", ["collision_witness.py", "stop"])
    assert cw.main() == expected
    capsys.readouterr()


def test_a_failed_start_exits_unknown_not_zero(monkeypatch, capsys):
    """`docker exec -d` returns 0 even for a command that cannot run, so a start that failed
    must be reported by this exit code or it is not reported at all."""
    monkeypatch.setattr(cw, "start", lambda: False)
    monkeypatch.setattr("sys.argv", ["collision_witness.py", "start"])
    assert cw.main() == cw.EXIT_UNKNOWN
    capsys.readouterr()


def test_a_successful_start_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cw, "start", lambda: True)
    monkeypatch.setattr("sys.argv", ["collision_witness.py", "start"])
    assert cw.main() == 0
    capsys.readouterr()


def test_start_deletes_the_previous_file_before_anything_else(monkeypatch):
    """Absence only means "unknown" if a stale file cannot survive into the next run. If the
    delete is not first, a clean previous run can be read back as this run's verdict."""
    calls = []
    monkeypatch.setattr(cw, "_dexec", lambda *a, **k: (calls.append(a), FakeProc(0, ""))[1])
    monkeypatch.setattr(cw.subprocess, "run", lambda *a, **k: FakeProc(0, ""))
    cw.start()
    assert calls, "start() made no docker exec call at all"
    assert calls[0][0] == "rm", f"first call was {calls[0]!r}, not the stale-file delete"

# --- the witness observes without consuming ------------------------------------ (SIM-27)

def _witness_src():
    return (REPO / "scripts" / "watch_collisions.py").read_text()



def test_the_witness_brackets_the_flight_instead_of_watching_it():
    """`simGetCollisionInfo` is read-and-reset and there is NO non-consuming way to ask -- the
    log route is closed too, because upstream commented out the UE_LOG calls in
    UAirBlueprintLib::LogMessage. So an observer cannot watch a flight without breaking it
    somewhere; it can only sample the monotonic counter at each end.

    Polling at 20 Hz broke landings: 10 pose splits in 12 CitySample runs, 0 in 7 without.
    Polling only above 2 m moved the damage to the cruise instead -- an actor frozen at 4.82 m
    while physics flew the whole mission 25 m away, and the gate scored it PASS. Two reads,
    neither of them during a contact, is what is left."""
    src = _witness_src()
    loop = src[src.index("while time.time() - t0 < a.max_seconds"):src.index("except KeyboardInterrupt")]
    assert loop.count('rpc.call("simGetCollisionInfo"') == 2, (
        "exactly two collision reads: crossing the gate upward, and crossing back down")
    assert 'rpc.call("simGetVehiclePose"' in loop, "the free pose read is what gates them"
    assert "was_airborne" in loop


def test_unmeasured_is_reported_as_unknown_not_as_zero():
    """A flight that never crossed the gate, or was stopped before descending through it, has
    NOT been shown to be clean. Reporting 0 there is the failure this repo has paid for
    repeatedly: an absence of evidence rendered as evidence of absence."""
    src = _witness_src()
    flush = src[src.index("def flush():"):src.index("was_airborne = False")]
    assert "None if (baseline is None or final_count is None)" in flush
    assert '"measured"' in flush
    assert '"blind_to"' in flush, "the artifact must state what it cannot see"

def test_the_altitude_gate_is_relative_to_the_resting_height():
    """An absolute z would be wrong on every world whose ground is not at zero -- which is every
    world we fly: CitySample rests at ~0.75 m NED, Blocks at ~0.6."""
    src = _witness_src()
    assert "rest_z" in src and "rest_z - z" in src


def test_no_vendor_patch_is_needed_for_the_collision_fix():
    """The first fix patched Cosys-AirSim so its RPC stopped consuming. That inverted this
    project's primary rule -- the simulator behaves correctly for its intended use, and WE
    introduced the 20 Hz poller. The fix belongs in the witness, and the patch was deleted
    rather than committed. If a 00NN patch touching collision ownership ever reappears at the
    top level of patches/cosys-airsim/, it will be applied to every world build: make that a
    deliberate decision, not a leftover."""
    top = sorted(p.name for p in (REPO / "patches" / "cosys-airsim").glob("*.patch"))
    assert not any("collision-flag" in n for n in top), (
        f"a collision-ownership patch is being auto-applied: {top}")
