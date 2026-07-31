"""Off-target tests for the seeded world/model overlay (P1-04a).

No simulator, no containers: fixtures stand in for PX4's world, model and server config,
so these run anywhere in milliseconds.

WHY THESE EXIST
---------------
Every assertion here corresponds to a way the overlay silently did nothing, or silently
broke the simulator, while looking completely healthy:

  * A `<plugin>` in the world SDF made Gazebo load ONLY that plugin, dropping Physics,
    UserCommands and SceneBroadcaster. The world came up with 6 topics, no `scene/info`,
    the vehicle never spawned, PX4 timed out — and `gz sdf -k` called the file **Valid**.
    So parse-validity is NOT the property worth testing; absence of `<plugin>` is.
  * `enable_wind` outside the link means the wind plugin applies force to nothing and the
    flight looks entirely normal.
  * A mass knob that is declared but never reaches the model changes nothing, and says so
    nowhere.
"""

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mv = _load("make_variant", REPO / "scripts" / "make_variant.py")
rs = _load("run_scenario", REPO / "scripts" / "run_scenario.py")

WORLD = """<?xml version="1.0"?>
<sdf version="1.9">
  <world name="default">
    <gravity>0 0 -9.8</gravity>
  </world>
</sdf>
"""

MODEL = """<?xml version="1.0"?>
<sdf version="1.9">
  <model name="x500_base">
    <link name="base_link">
      <inertial><mass>2.0</mass></inertial>
    </link>
  </model>
</sdf>
"""

CONFIG = """<server_config>
  <plugins>
    <plugin entity_name="*" entity_type="world" filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
  </plugins>
</server_config>
"""


@pytest.fixture
def px4(tmp_path, monkeypatch):
    """Stand in for the PX4 tree, and confine the build to tmp_path."""
    worlds = tmp_path / "px4" / "worlds"; worlds.mkdir(parents=True)
    models = tmp_path / "px4" / "models" / "x500_base"; models.mkdir(parents=True)
    (worlds / "default.sdf").write_text(WORLD)
    (models / "model.sdf").write_text(MODEL)
    cfg = tmp_path / "px4" / "server.config"; cfg.write_text(CONFIG)
    monkeypatch.setattr(mv, "PX4_WORLDS", worlds)
    monkeypatch.setattr(mv, "PX4_MODELS", tmp_path / "px4" / "models")
    monkeypatch.setattr(mv, "PX4_SERVER_CONFIG", cfg)
    monkeypatch.setenv("VARIANT_ROOT", str(tmp_path))
    return tmp_path


def build(px4, **over):
    v = {"wind_vx": 3.0, "wind_vy": 0.0, "wind_speed_ms": 3.0,
         "wind_heading_rad": 0.0, "mass_scale": 1.0}
    v.update(over)
    out = px4 / "variants" / "t"
    mv.build({"world": "default"}, out, v)
    return out


def test_world_gets_the_wind_element(px4):
    w = (build(px4) / "worlds" / "seeded.sdf").read_text()
    assert "<wind>" in w
    assert "3.0000 0.0000 0" in w


def test_world_contains_NO_plugin(px4):
    """The regression this file exists for.

    A <plugin> here makes Gazebo load only that plugin and drop PX4's core systems. The
    resulting world parses fine and never spawns a vehicle."""
    w = (build(px4) / "worlds" / "seeded.sdf").read_text()
    assert "<plugin" not in w, "a plugin in the world SDF suppresses PX4's core systems"


def test_wind_plugin_goes_in_the_server_config_alongside_the_core_systems(px4):
    cfg = (build(px4) / "server.config").read_text()
    assert "WindEffects" in cfg
    assert "gz::sim::systems::Physics" in cfg, "core systems must survive"


def test_enable_wind_is_INSIDE_the_link(px4):
    """Present-in-the-file is not enough — outside the link it does nothing."""
    m = (build(px4) / "models" / "x500_base" / "model.sdf").read_text()
    link = re.search(r'<link name="base_link">(.*?)</link>', m, re.S)
    assert link, "base_link went missing"
    assert "<enable_wind>true</enable_wind>" in link.group(1)


def test_mass_is_scaled_when_asked(px4):
    m = (build(px4, mass_scale=1.10) / "models" / "x500_base" / "model.sdf").read_text()
    assert "<mass>2.2000</mass>" in m


def test_mass_untouched_at_scale_one(px4):
    m = (build(px4, mass_scale=1.0) / "models" / "x500_base" / "model.sdf").read_text()
    assert "<mass>2.0</mass>" in m


def test_refuses_to_build_outside_the_allowed_root(px4, tmp_path):
    """build() rmtree's its output directory; through the CLI that path is user input."""
    with pytest.raises(SystemExit):
        mv.build({"world": "default"}, Path("/tmp/elsewhere-xyz"),
                 {"wind_vx": 0.0, "wind_vy": 0.0, "mass_scale": 1.0})


def test_there_is_only_one_seed_derivation():
    """make_variant must NOT grow its own derive() again: two derivations from one seed
    agreed only by accident of draw order, and nothing would have caught divergence."""
    assert not hasattr(mv, "derive"), "seed derivation belongs to run_scenario alone"


def test_derivation_is_deterministic_and_within_declared_bounds():
    sc = {"seeded": {"wind_speed_max_ms": 3.0, "spawn_xy_jitter_m": 5.0,
                     "spawn_yaw_jitter_rad": 3.14159, "mass_jitter_pct": 10.0}}
    a, b = rs.derive_variant(sc, 7), rs.derive_variant(sc, 7)
    assert a == b
    assert 0.0 <= a["wind_speed_ms"] <= 3.0
    assert 0.9 <= a["mass_scale"] <= 1.1
    assert rs.derive_variant(sc, 7) != rs.derive_variant(sc, 8)


def test_mass_reaches_the_overlay_command():
    """A declared knob that never reaches the generator is a knob that does nothing."""
    src = (REPO / "scripts" / "run_scenario.py").read_text()
    assert "--mass-scale" in src


def test_overlay_is_decided_by_the_scenario_not_the_drawn_value():
    """Keying off the sampled wind let a ~zero draw run on the stock world with no drag,
    while other seeds ran on the overlay — two physics models in one success rate."""
    src = (REPO / "scripts" / "run_scenario.py").read_text()
    body = src.split("def build_variant_overlay")[1].split("\ndef ")[0]
    assert "wind_speed_max_ms" in body and "mass_jitter_pct" in body
