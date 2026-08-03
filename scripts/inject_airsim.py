#!/usr/bin/env python3
"""Inject Cosys-AirSim into a user's own Unreal project, so the simulator can load their world.

    scripts/inject_airsim.py /path/to/TheirProject.uproject [--map /Game/Maps/Their]

This is `C-11` A1: the bring-your-own-world path for CONTENT/BLUEPRINT-ONLY projects. It is
pure text edits plus a folder copy — no compile, no editor, no GUI, no display.

WHY A SCRIPT AND NOT THE UPSTREAM INSTRUCTIONS
----------------------------------------------
Upstream's docs (`docs/unreal_custenv.md`) describe this as an interactive editor workflow, and
its step 9 is "in Window/World Settings set GameMode Override to AirSimGameMode" — a GUI action
that cannot happen under `-RenderOffScreen -unattended`.

That step is avoidable. `AAirSimGameMode` is a PLUGIN class (AIRSIM_API, declared in
Plugins/AirSim/Source/AirSimGameMode.h), so it can be named directly in config and applied
globally, exactly as the Blocks project does with its own class:

    [/Script/EngineSettings.GameMapsSettings]
    GlobalDefaultGameMode=/Script/Blocks.BlocksGameMode   <- Blocks
    GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode   <- what we write

That single fact is what makes the whole bring-your-own-world path scriptable.

WHICH PLUGIN COPY GETS INJECTED, AND WHY IT MATTERS
---------------------------------------------------
The vendored tree has TWO copies and only one is usable here:

    Unreal/Plugins/AirSim                    330 MB  SOURCE ONLY — no Binaries/
    Unreal/Environments/Blocks/Plugins/AirSim 506 MB  BUILT — has Binaries/Linux/*.so

We copy the BUILT one. Injecting the source-only copy would silently turn A1 into A2 by
forcing UnrealBuildTool to compile, which is the entire distinction this path exists to avoid.

WHAT THIS DOES NOT DO
---------------------
- It does not convert engine versions. A project authored for 5.2 may need an editor pass on a
  Windows/macOS machine first; that is an open question recorded in C-11.
- It does not compile anything. If the project has its own Source/ (A2), this warns and
  continues — the injection is still correct, but a build step is then required.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILT_PLUGIN = REPO / "vendor/Cosys-AirSim/Unreal/Environments/Blocks/Plugins/AirSim"

REQUIRED_PLUGINS = ["AirSim", "ChaosVehiclesPlugin"]
GAME_MODE = "/Script/AirSim.AirSimGameMode"
MAPS_SECTION = "[/Script/EngineSettings.GameMapsSettings]"
PACKAGING_SECTION = "[/Script/UnrealEd.ProjectPackagingSettings]"

# Forces packaged builds to include AirSim's own content. Straight from upstream's doc.
COOK_DIRS = ["HUDAssets", "Beacons", "Blueprints", "Models", "Sensors", "StarterContent",
             "VehicleAdv", "Weather"]

C, R, G, Y, X = "\033[36m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def log(m): print(f"{C}[inject]{X} {m}")
def warn(m): print(f"{Y}[inject] WARNING:{X} {m}")
def die(m): print(f"{R}[inject] FATAL:{X} {m}", file=sys.stderr); sys.exit(1)


def ini_set(path: Path, section: str, key: str, value: str) -> None:
    """Set key=value inside section, creating either if absent. Idempotent.

    Hand-rolled rather than configparser: Unreal .ini files use `+Key=` repeat syntax and
    section names containing '/', '.' and '[]', which configparser mangles on write. Losing a
    project's existing settings while "adding" ours would be a bad trade.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    out, in_sec, done, sec_seen = [], False, False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_sec and not done:
                # Insert BEFORE any trailing blank lines of the section, not after them.
                # Appending after the blank still lands inside the section (a blank line does
                # not close one) so it is correct either way -- but these files get read by
                # people, and a key floating below a gap looks like it belongs to nothing.
                tail = []
                while out and not out[-1].strip():
                    tail.append(out.pop())
                out.append(f"{key}={value}")
                out.extend(tail)
                done = True
            in_sec = (stripped == section)
            sec_seen = sec_seen or in_sec
        elif in_sec and re.match(rf"^\s*{re.escape(key)}\s*=", line):
            if done:
                continue          # drop duplicates rather than leaving two truths in the file
            out.append(f"{key}={value}")
            done = True
            continue
        out.append(line)
    if in_sec and not done:
        out.append(f"{key}={value}")
        done = True
    if not sec_seen:
        if out and out[-1].strip():
            out.append("")
        out += [section, f"{key}={value}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def ini_add_once(path: Path, section: str, line: str) -> None:
    """Append a `+Key=(...)` repeat-syntax line to a section if not already present."""
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if line in text:
        return
    lines = text.splitlines()
    if section not in [l.strip() for l in lines]:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [section, line]
    else:
        idx = next(i for i, l in enumerate(lines) if l.strip() == section)
        j = idx + 1
        while j < len(lines) and not lines[j].strip().startswith("["):
            j += 1
        lines.insert(j, line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject Cosys-AirSim into a user's UE project (C-11 A1).")
    ap.add_argument("uproject", type=Path, help="path to the user's .uproject")
    ap.add_argument("--map", default="", help="content path of the map to load, e.g. /Game/Maps/Mine")
    ap.add_argument("--plugin", type=Path, default=BUILT_PLUGIN,
                    help="AirSim plugin folder to copy (default: the BUILT one from Blocks)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    proj = a.uproject.resolve()
    if not proj.is_file() or proj.suffix != ".uproject":
        die(f"not a .uproject: {proj}")
    root = proj.parent
    if REPO in root.parents or root == REPO:
        die(f"refusing to inject into a path inside this repo: {root}\n"
            f"       This is for the USER's project. Injecting into vendor/ would dirty the "
            f"vendored tree, which must stay byte-identical to upstream.")

    if not (a.plugin / "AirSim.uplugin").is_file():
        die(f"no AirSim.uplugin under {a.plugin}")
    has_binaries = any((a.plugin / "Binaries").rglob("*.so")) if (a.plugin / "Binaries").exists() else False
    if not has_binaries:
        die(f"{a.plugin} has no Binaries/**/*.so — that is the SOURCE-ONLY copy.\n"
            f"       Injecting it would force a UnrealBuildTool compile, turning this A1 "
            f"(no-compile) path into A2. Use the built copy:\n       {BUILT_PLUGIN}")

    log(f"project : {proj.name}  ({root})")
    tier = "A2" if (root / "Source").is_dir() else "A1"
    if tier == "A2":
        warn("this project has its own Source/ — it is A2, not A1. The injection below is "
             "still correct, but the project's C++ must then be compiled against UE5.8.")
    else:
        log("tier    : A1 (content/Blueprint-only) — no compile required")

    data = json.loads(proj.read_text(encoding="utf-8"))
    ea = str(data.get("EngineAssociation", "")).strip()
    if ea and not ea.startswith("5.8"):
        warn(f"EngineAssociation is '{ea}', not 5.8. This script does NOT convert engine "
             f"versions — if it fails to load, convert it in the editor on a "
             f"Windows/macOS machine first, then re-copy.")

    if a.dry_run:
        log("dry run — no changes written")
        return 0

    # 1. the plugin ------------------------------------------------------------------
    dest = root / "Plugins" / "AirSim"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(a.plugin, dest, symlinks=True)
    log(f"copied plugin -> Plugins/AirSim")

    # 2. the .uproject ---------------------------------------------------------------
    plugins = data.setdefault("Plugins", [])
    names = {p.get("Name") for p in plugins}
    for want in REQUIRED_PLUGINS:
        if want not in names:
            plugins.append({"Name": want, "Enabled": True})
        else:
            for p in plugins:
                if p.get("Name") == want:
                    p["Enabled"] = True
    proj.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    log(f"enabled plugins in .uproject: {', '.join(REQUIRED_PLUGINS)}")

    # 3. DefaultEngine.ini — the step upstream says needs a GUI -----------------------
    eng = root / "Config" / "DefaultEngine.ini"
    ini_set(eng, MAPS_SECTION, "GlobalDefaultGameMode", GAME_MODE)
    log(f"set GlobalDefaultGameMode={GAME_MODE}")
    if a.map:
        ini_set(eng, MAPS_SECTION, "GameDefaultMap", a.map)
        ini_set(eng, MAPS_SECTION, "EditorStartupMap", a.map)
        log(f"set GameDefaultMap={a.map}")
    else:
        warn("no --map given; the project's existing GameDefaultMap is left alone. If it has "
             "none, the sim will load an empty level.")

    # 4. DefaultGame.ini cook directives ----------------------------------------------
    game = root / "Config" / "DefaultGame.ini"
    ini_add_once(game, PACKAGING_SECTION, '+MapsToCook=(FilePath="/AirSim/AirSimAssets")')
    for d in COOK_DIRS:
        ini_add_once(game, PACKAGING_SECTION, f'+DirectoriesToAlwaysCook=(Path="/AirSim/{d}")')
    log(f"added {len(COOK_DIRS) + 1} cook directives")

    # 5. assert the ARTIFACTS, not that we reached the end ----------------------------
    # A script's own success banner has lied in this repo before.
    errs = []
    if not (dest / "AirSim.uplugin").is_file():
        errs.append("plugin descriptor missing after copy")
    if not any((dest / "Binaries").rglob("*.so")):
        errs.append("plugin binaries missing after copy — this would force a rebuild")
    check = json.loads(proj.read_text(encoding="utf-8"))
    got = {p.get("Name") for p in check.get("Plugins", []) if p.get("Enabled")}
    for want in REQUIRED_PLUGINS:
        if want not in got:
            errs.append(f"{want} not enabled in .uproject after edit")
    etext = eng.read_text(encoding="utf-8")
    if f"GlobalDefaultGameMode={GAME_MODE}" not in etext:
        errs.append("GlobalDefaultGameMode not set in DefaultEngine.ini")
    if etext.count("GlobalDefaultGameMode=") != 1:
        errs.append(f"GlobalDefaultGameMode appears {etext.count('GlobalDefaultGameMode=')} "
                    f"times — ambiguous, a later one would win silently")
    if errs:
        for e in errs:
            print(f"{R}  - {e}{X}", file=sys.stderr)
        die("injection did not produce the expected artifacts")

    print(f"\n{G}  injected OK{X}  ({tier})   run it with:")
    print(f"    UnrealEditor {proj.name} -game -RenderOffScreen -nosound -unattended "
          f"-stdout -settings=/settings.json\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
