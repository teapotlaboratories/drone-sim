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


def record(count, names=()):
    return json.dumps({"collision_count": count,
                       "collisions": [{"object_name": n} for n in names]})


# --- the rule itself ------------------------------------------------------------------

def test_a_clean_record_scores_zero(monkeypatch):
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(0)))
    n, detail = cw.stop_and_score()
    assert n == 0 and detail == ""


def test_collisions_are_counted_and_named(monkeypatch):
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(2, ["Cube_7", "Cube_49"])))
    n, detail = cw.stop_and_score()
    assert n == 2
    assert "Cube_7" in detail and "Cube_49" in detail


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
    """Well-formed JSON that simply recorded nothing is a legitimately clean run."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec(json.dumps({"collisions": []})))
    n, _ = cw.stop_and_score()
    assert n == 0


def test_the_detail_is_truncated_but_says_how_many_were_hidden(monkeypatch):
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(9, ["a", "b", "c", "d", "e"])))
    _, detail = cw.stop_and_score()
    assert "+2 more" in detail


def test_the_full_record_is_persisted_not_just_the_count(monkeypatch, tmp_path):
    """The first question after "it hit something" is "how high was the something", and only
    the impact points answer it."""
    monkeypatch.setattr(cw, "_dexec", fake_dexec(record(1, ["Cube_7"])))
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
