"""Tests for the operator-supplied spawn position (`SIM-13`).

These run off-target: no simulator, no GPU. The value of testing here is that the failure this
code prevents is EXPENSIVE and SILENT — a bad spawn puts the camera inside terrain, and every
image measurement taken afterwards looks plausible and is wrong. Four investigations in this
investigation were already lost that way, so the tests below lean on the refusals rather than the
happy path.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("apply_spawn", REPO / "scripts" / "apply_spawn.py")
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def doc(*names):
    return {"SettingsVersion": 2.0, "Vehicles": {n: {"VehicleType": "PX4Multirotor"} for n in names}}


# ---------------------------------------------------------------------------------------
# parse_spawn


def test_three_components_parse():
    assert sp.parse_spawn("50,-30,-10") == {"X": 50.0, "Y": -30.0, "Z": -10.0}


def test_four_components_include_yaw():
    assert sp.parse_spawn("50,-30,-10,315") == {"X": 50.0, "Y": -30.0, "Z": -10.0, "Yaw": 315.0}


def test_whitespace_is_tolerated():
    assert sp.parse_spawn(" 1 , 2 , -3 ") == {"X": 1.0, "Y": 2.0, "Z": -3.0}


@pytest.mark.parametrize("bad", ["1,2", "1,2,3,4,5", ""])
def test_wrong_component_count_is_refused(bad):
    with pytest.raises(sp.SpawnError):
        sp.parse_spawn(bad)


def test_non_numeric_is_refused():
    with pytest.raises(sp.SpawnError, match="not a number"):
        sp.parse_spawn("a,b,c")


@pytest.mark.parametrize("bad", ["1,2,nan", "1,inf,-3", "-inf,2,-3"])
def test_nan_and_infinity_are_refused(bad):
    """float() accepts these happily; they would reach FVector as garbage. A NaN rotator is
    exactly what SIGSEGVs the simulator during BeginPlay."""
    with pytest.raises(sp.SpawnError, match="finite"):
        sp.parse_spawn(bad)


def test_none_is_refused():
    with pytest.raises(sp.SpawnError):
        sp.parse_spawn(None)


# ---------------------------------------------------------------------------------------
# check_altitude — the NED footgun


def test_positive_z_is_refused_by_default():
    """The whole point: Z is NED, so a positive Z is BELOW the origin. An operator typing 10
    for '10 m up' would get 10 m underground — reproducing the bug this feature fixes."""
    with pytest.raises(sp.SpawnError, match="NED"):
        sp.check_altitude({"X": 0.0, "Y": 0.0, "Z": 10.0})


def test_the_refusal_suggests_the_correct_sign():
    with pytest.raises(sp.SpawnError, match=r"Z=-10"):
        sp.check_altitude({"X": 0.0, "Y": 0.0, "Z": 10.0})


def test_positive_z_is_allowed_when_acknowledged():
    sp.check_altitude({"X": 0.0, "Y": 0.0, "Z": 10.0}, allow_below_origin=True)


def test_negative_z_is_fine():
    sp.check_altitude({"X": 0.0, "Y": 0.0, "Z": -10.0})


def test_zero_z_is_fine():
    """Exactly at origin is legal — some worlds really do put ground there."""
    sp.check_altitude({"X": 0.0, "Y": 0.0, "Z": 0.0})


# ---------------------------------------------------------------------------------------
# apply_spawn


def test_sole_vehicle_is_placed_without_naming_it():
    d = sp.apply_spawn(doc("PX4"), {"X": 1.0, "Y": 2.0, "Z": -3.0})
    assert d["Vehicles"]["PX4"]["X"] == 1.0
    assert d["Vehicles"]["PX4"]["Z"] == -3.0


def test_existing_vehicle_settings_survive():
    d = doc("PX4")
    d["Vehicles"]["PX4"]["UseTcp"] = True
    d["Vehicles"]["PX4"]["Sensors"] = {"imu": {"SensorType": 2}}
    out = sp.apply_spawn(d, {"X": 1.0, "Y": 2.0, "Z": -3.0})
    assert out["Vehicles"]["PX4"]["UseTcp"] is True
    assert out["Vehicles"]["PX4"]["Sensors"]["imu"]["SensorType"] == 2


def test_multiple_vehicles_refuse_to_guess():
    """Guessing wrong places the WRONG aircraft and fails at the far end of a long run."""
    with pytest.raises(sp.SpawnError, match="--vehicle"):
        sp.apply_spawn(doc("PX4", "Drone2"), {"X": 1.0, "Y": 2.0, "Z": -3.0})


def test_named_vehicle_among_several_is_placed():
    d = sp.apply_spawn(doc("PX4", "Drone2"), {"X": 1.0, "Y": 2.0, "Z": -3.0}, vehicle="Drone2")
    assert d["Vehicles"]["Drone2"]["X"] == 1.0
    assert "X" not in d["Vehicles"]["PX4"], "placed the wrong vehicle"


def test_unknown_vehicle_name_is_refused():
    with pytest.raises(sp.SpawnError, match="no vehicle named"):
        sp.apply_spawn(doc("PX4"), {"X": 1.0, "Y": 2.0, "Z": -3.0}, vehicle="Nope")


def test_missing_vehicles_block_is_refused():
    with pytest.raises(sp.SpawnError, match="no Vehicles"):
        sp.apply_spawn({"SettingsVersion": 2.0}, {"X": 1.0, "Y": 2.0, "Z": -3.0})


def test_yaw_is_written_when_supplied():
    d = sp.apply_spawn(doc("PX4"), sp.parse_spawn("0,0,-5,90"))
    assert d["Vehicles"]["PX4"]["Yaw"] == 90.0


def test_yaw_is_left_alone_when_not_supplied():
    """Three components must not silently zero an existing Yaw."""
    d = doc("PX4")
    d["Vehicles"]["PX4"]["Yaw"] = 42.0
    out = sp.apply_spawn(d, sp.parse_spawn("0,0,-5"))
    assert out["Vehicles"]["PX4"]["Yaw"] == 42.0


# ---------------------------------------------------------------------------------------
# the real repo settings file


def test_the_committed_settings_file_can_be_placed():
    """Guards against the repo file drifting into a shape this cannot handle."""
    src = REPO / "sim" / "ue5" / "settings.json"
    d = json.loads(sp.strip_jsonc(src.read_text(encoding="utf-8")))
    out = sp.apply_spawn(d, sp.parse_spawn("50,-30,-10,315"))
    px4 = out["Vehicles"]["PX4"]
    assert (px4["X"], px4["Y"], px4["Z"], px4["Yaw"]) == (50.0, -30.0, -10.0, 315.0)
    # and the things the simulator depends on are still there
    assert px4["UseTcp"] is True
    assert "gpulidar" in px4["Sensors"]
    assert px4["Cameras"]["front_center"]["CaptureSettings"][0]["ForceUpdate"] is True
