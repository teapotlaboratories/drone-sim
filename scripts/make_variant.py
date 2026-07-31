#!/usr/bin/env python3
"""Generate a per-seed world + model overlay so a seed changes the PHYSICS (P1-04a).

The original `P1-04a` — seed Gazebo's RNG with `gz sim --seed` — was measured and does not
work: at identical simulation timestamps, two runs with the same seed differ as much as two
runs with different seeds (0 of 716 samples identical). See
`docs/worklog/2026-07-31-gz-seed-negative-result.md`.

So this seeds the CONDITIONS instead. A success rate does not need determinism; it needs
diversity. Wind is the first knob because it is the disturbance a real multirotor actually
meets, and because it needs no reproducibility to be meaningful.

HOW THE OVERLAY REACHES GAZEBO
------------------------------
Nothing in the vendored PX4 tree is modified — it stays byte-identical to upstream. Instead
a copy of the world and of `x500_base` is written under the run's output directory, and
Gazebo is pointed at it:

  * `GZ_SIM_RESOURCE_PATH` — gz_env.sh **appends** to whatever it already holds
    (`GZ_SIM_RESOURCE_PATH=$GZ_SIM_RESOURCE_PATH:$PX4_GZ_MODELS:...`), so a value set from
    outside comes FIRST and the overlay wins.
  * The world is loaded by starting the Gazebo server ourselves. PX4 then attaches to the
    running world rather than starting its own (`px4-rc.gzsim:63`). Setting
    `PX4_GZ_WORLDS` from outside does NOT work — gz_env.sh overwrites it.

TWO THINGS WIND NEEDS, OR IT SILENTLY DOES NOTHING
--------------------------------------------------
  1. A `<wind>` element on the world AND the `WindEffects` system plugin. The plugin ships
     with Gazebo Harmonic (`libgz-sim8-wind-effects-system.so`) but is not in PX4's world.
  2. `<enable_wind>true</enable_wind>` on the vehicle's link. Upstream `x500_base` does not
     set it, so wind applies to nothing and the flight looks completely normal.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import shutil
import sys
from pathlib import Path

PX4_WORLDS = Path("/opt/px4/Tools/simulation/gz/worlds")
PX4_MODELS = Path("/opt/px4/Tools/simulation/gz/models")

# ONLY the <wind> element goes into the world.
#
# The WindEffects PLUGIN must NOT go here, even though that is the obvious place. A world
# SDF that declares any <plugin> makes Gazebo load ONLY those plugins, dropping the core
# systems from the server config — Physics, UserCommands, SceneBroadcaster. Observed
# exactly that: the world came up with 6 topics, no scene/info service, the vehicle never
# spawned, and PX4 sat in "Waiting for Gazebo world..." until it timed out. The world file
# looked completely healthy.
WIND_ELEMENT = """
    <!-- Injected per-seed by scripts/make_variant.py (P1-04a). -->
    <wind>
      <linear_velocity>{vx:.4f} {vy:.4f} 0</linear_velocity>
    </wind>
"""

# The plugin goes in a COPY of PX4's server config instead, so every core system PX4 needs
# still loads and WindEffects is simply added to them.
WIND_PLUGIN_ENTRY = """    <plugin entity_name="*" entity_type="world" \
filename="gz-sim-wind-effects-system" name="gz::sim::systems::WindEffects">
      <force_approximation_scaling_factor>1.0</force_approximation_scaling_factor>
      <horizontal>
        <magnitude>
          <time_for_rise>1</time_for_rise>
          <noise type="gaussian"><mean>0</mean><stddev>0.0002</stddev></noise>
        </magnitude>
        <direction>
          <time_for_rise>1</time_for_rise>
          <noise type="gaussian"><mean>0</mean><stddev>0.03</stddev></noise>
        </direction>
      </horizontal>
    </plugin>
"""

PX4_SERVER_CONFIG = Path("/opt/px4/src/modules/simulation/gz_bridge/server.config")


# NOTE: there is deliberately NO derive() here.
#
# An earlier version had one, duplicating run_scenario.derive_variant(). The two agreed
# only because wind happened to be drawn first in both; reordering either one's draws
# would have made "seed 3" mean two different things depending on which was asked, with
# nothing to catch it. `scripts/run_scenario.py` is the single source of seed derivation
# and passes the values in explicitly.


def build(scenario: dict, outdir: Path, variant: dict) -> dict:
    v = variant
    worlds = outdir / "worlds"
    models = outdir / "models"
    # Guarded: build() recursively deletes outdir, and through the CLI that path is
    # whatever --outdir says. Confine it to the run-output tree so a typo cannot become
    # `rmtree("/")`.
    allowed = Path(os.environ.get("VARIANT_ROOT", "/out"))
    if not str(outdir.resolve()).startswith(str(allowed.resolve()) + "/"):
        sys.exit(f"refusing to build in {outdir}: must be under {allowed}")
    if outdir.exists():
        shutil.rmtree(outdir)
    worlds.mkdir(parents=True)
    models.mkdir(parents=True)

    # --- world: inject wind + the WindEffects plugin ---------------------------------
    world_src = PX4_WORLDS / f"{scenario.get('world', 'default')}.sdf"
    text = world_src.read_text()
    marker = re.search(r"<world[^>]*>", text)
    if not marker:
        sys.exit(f"{world_src}: no <world> element")
    injected = WIND_ELEMENT.format(vx=v["wind_vx"], vy=v["wind_vy"])
    text = text[:marker.end()] + injected + text[marker.end():]
    (worlds / "seeded.sdf").write_text(text)

    # --- server config: PX4's, plus WindEffects ---------------------------------------
    cfg = PX4_SERVER_CONFIG.read_text()
    if "WindEffects" not in cfg:
        cfg = cfg.replace("</plugins>", WIND_PLUGIN_ENTRY + "  </plugins>", 1)
    (outdir / "server.config").write_text(cfg)

    # --- model: enable_wind on base_link, and scale the mass -------------------------
    # Copied, never edited in place: the vendored tree stays byte-identical to upstream.
    src = PX4_MODELS / "x500_base"
    dst = models / "x500_base"
    shutil.copytree(src, dst)
    m = (dst / "model.sdf").read_text()

    # enable_wind belongs INSIDE the link. Without it the wind plugin runs and applies
    # force to nothing, and the flight looks entirely normal.
    if "<enable_wind>" not in m:
        m = m.replace('<link name="base_link">',
                      '<link name="base_link">\n      <enable_wind>true</enable_wind>', 1)
    else:
        m = m.replace("<enable_wind>false</enable_wind>",
                      "<enable_wind>true</enable_wind>", 1)

    if abs(v["mass_scale"] - 1.0) > 1e-9:
        mm = re.search(r"<mass>([\d.eE+-]+)</mass>", m)
        if mm:
            scaled = float(mm.group(1)) * v["mass_scale"]
            m = m[:mm.start()] + f"<mass>{scaled:.4f}</mass>" + m[mm.end():]
    (dst / "model.sdf").write_text(m)

    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a seeded world/model overlay.")
    ap.add_argument("--wind-speed", type=float, default=0.0)
    ap.add_argument("--wind-heading", type=float, default=0.0)
    ap.add_argument("--mass-scale", type=float, default=1.0)
    ap.add_argument("--world", default="default")
    ap.add_argument("--outdir", type=Path, required=True)
    a = ap.parse_args()
    v = {
        "wind_speed_ms": a.wind_speed,
        "wind_heading_rad": a.wind_heading,
        "wind_vx": round(a.wind_speed * math.cos(a.wind_heading), 4),
        "wind_vy": round(a.wind_speed * math.sin(a.wind_heading), 4),
        "mass_scale": a.mass_scale,
    }
    build({"world": a.world}, a.outdir, v)
    print(f"variant written to {a.outdir}: wind {v['wind_speed_ms']} m/s "
          f"({v['wind_vx']}, {v['wind_vy']})  mass x{v['mass_scale']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
