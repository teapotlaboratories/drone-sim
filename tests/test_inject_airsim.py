"""Tests for the AirSim project-injection helpers (`C-11` A1).

These run off-target: no Unreal, no container, no GPU. The functions under test rewrite files
in the USER's own Unreal project, so the failure mode is destroying someone's settings while
appearing to succeed — which is exactly the kind of thing that is cheap to catch here and
expensive to discover in their project.

`ini_set` is hand-rolled rather than using `configparser`, because Unreal ini files use `+Key=`
repeat syntax and `[/Script/Foo.Bar]` section names that configparser mangles on write. That
choice is only safe if the hand-rolled version is pinned, hence this file.
"""
import importlib.util
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("inject_airsim", REPO / "scripts" / "inject_airsim.py")
inj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inj)

SEC = "[/Script/EngineSettings.GameMapsSettings]"
OTHER = "[/Script/Engine.RendererSettings]"


def write(tmp_path, text):
    p = tmp_path / "DefaultEngine.ini"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------------------
# ini_set


def test_key_absent_in_existing_section_is_inserted_there(tmp_path):
    p = write(tmp_path, f"{SEC}\nGameDefaultMap=/Game/A\n")
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    body = p.read_text()
    assert "GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode" in body
    # and it must land INSIDE the section, not after some later one
    assert body.index(SEC) < body.index("GlobalDefaultGameMode")


def test_existing_key_is_replaced_not_duplicated(tmp_path):
    p = write(tmp_path, f"{SEC}\nGlobalDefaultGameMode=/Script/Old.OldMode\n")
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    body = p.read_text()
    assert body.count("GlobalDefaultGameMode=") == 1
    assert "Old.OldMode" not in body


def test_a_key_present_twice_collapses_to_one(tmp_path):
    """Unreal takes the last value, so leaving two means the file states two different truths
    and the reader cannot tell which wins."""
    p = write(tmp_path, f"{SEC}\nGlobalDefaultGameMode=/Script/A.A\nGlobalDefaultGameMode=/Script/B.B\n")
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    assert p.read_text().count("GlobalDefaultGameMode=") == 1


def test_same_key_in_a_DIFFERENT_section_is_left_alone(tmp_path):
    """The subtle one. Rewriting a same-named key in an unrelated section would silently
    change behaviour the user never asked us to touch."""
    p = write(tmp_path, f"{OTHER}\nGlobalDefaultGameMode=/Script/NotOurs.Mode\n\n{SEC}\nGameDefaultMap=/Game/A\n")
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    body = p.read_text()
    assert "/Script/NotOurs.Mode" in body, "clobbered a key in someone else's section"
    assert body.count("GlobalDefaultGameMode=") == 2


def test_missing_section_is_created(tmp_path):
    p = write(tmp_path, f"{OTHER}\nr.Foo=1\n")
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    body = p.read_text()
    assert SEC in body and "GlobalDefaultGameMode=" in body
    assert "r.Foo=1" in body, "creating a section must not drop existing content"


def test_missing_file_is_created(tmp_path):
    p = tmp_path / "Config" / "DefaultEngine.ini"
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    assert p.is_file() and "GlobalDefaultGameMode=" in p.read_text()


def test_unrelated_settings_survive(tmp_path):
    """The whole reason for hand-rolling this instead of configparser."""
    original = (f"{SEC}\nGameDefaultMap=/Game/Theirs\n\n{OTHER}\n"
                "r.DefaultFeature.AutoExposure=False\n+CustomRepeat=(A=1)\n")
    p = write(tmp_path, original)
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    body = p.read_text()
    for keep in ["GameDefaultMap=/Game/Theirs", "r.DefaultFeature.AutoExposure=False",
                 "+CustomRepeat=(A=1)", OTHER]:
        assert keep in body, f"lost {keep!r}"


def test_is_idempotent(tmp_path):
    p = write(tmp_path, f"{SEC}\nGameDefaultMap=/Game/A\n")
    for _ in range(3):
        inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    assert p.read_text().count("GlobalDefaultGameMode=") == 1


def test_commented_out_key_is_not_treated_as_the_key(tmp_path):
    p = write(tmp_path, f"{SEC}\n;GlobalDefaultGameMode=/Script/Commented.Out\n")
    inj.ini_set(p, SEC, "GlobalDefaultGameMode", "/Script/AirSim.AirSimGameMode")
    body = p.read_text()
    assert ";GlobalDefaultGameMode=/Script/Commented.Out" in body, "ate a comment"
    assert "GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode" in body


# ---------------------------------------------------------------------------------------
# ini_add_once


def test_add_once_adds_then_does_not_duplicate(tmp_path):
    p = tmp_path / "DefaultGame.ini"
    line = '+DirectoriesToAlwaysCook(Path="/AirSim/Weather")'
    for _ in range(3):
        inj.ini_add_once(p, "[/Script/UnrealEd.ProjectPackagingSettings]", line)
    assert p.read_text().count(line) == 1


def test_add_once_appends_inside_the_named_section(tmp_path):
    p = tmp_path / "DefaultGame.ini"
    p.write_text("[/Script/UnrealEd.ProjectPackagingSettings]\n+Existing=1\n\n[Other]\nx=1\n")
    line = '+DirectoriesToAlwaysCook(Path="/AirSim/Weather")'
    inj.ini_add_once(p, "[/Script/UnrealEd.ProjectPackagingSettings]", line)
    body = p.read_text()
    assert body.index(line) < body.index("[Other]"), "leaked into the wrong section"
    assert "x=1" in body


# ---------------------------------------------------------------------------------------
# constants that other things depend on


def test_game_mode_names_a_PLUGIN_class_not_a_project_class():
    """This is the fact the whole bring-your-own-world path rests on. A project class such as
    /Script/Blocks.BlocksGameMode only exists in OUR project; naming one here would make the
    injection work on Blocks and fail on every user project, which is the worst failure shape:
    it passes the test we run and breaks the case we shipped it for."""
    assert inj.GAME_MODE == "/Script/AirSim.AirSimGameMode"
    assert inj.GAME_MODE.startswith("/Script/AirSim."), "must come from the plugin module"


def test_the_default_plugin_source_is_the_BUILT_copy():
    """The source-only copy has no Binaries/ and would silently force a UBT compile, turning
    A1 into A2 — the exact distinction this path exists to remove."""
    assert inj.BUILT_PLUGIN.name == "AirSim"
    assert "Environments/Blocks" in str(inj.BUILT_PLUGIN), (
        "default must be the built plugin inside Blocks, not Unreal/Plugins/AirSim"
    )
