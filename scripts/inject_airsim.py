#!/usr/bin/env python3
"""Inject Cosys-AirSim into a user's own Unreal project, so the simulator can load their world.

    scripts/inject_airsim.py /path/to/TheirProject.uproject [--map /Game/Maps/Their]

This is `SIM-11` A1: the bring-your-own-world path for CONTENT/BLUEPRINT-ONLY projects. It is
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
  Windows/macOS machine first; that is an open question recorded in SIM-11.
- It does not compile anything. If the project has its own Source/ (A2), this warns and
  continues — the injection is still correct, but a build step is then required.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
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


def shadowing_plugin_copies(root: Path) -> list:
    """Every AirSim.uplugin under Plugins/ other than the canonical Plugins/AirSim one.

    Unreal's plugin manager walks Plugins/ recursively and de-duplicates by plugin name and
    version, keeping one location and ignoring the others -- at Warning level, in a log nobody
    reads on a successful boot. Any second copy therefore makes "which code is actually
    running" unanswerable from the filesystem, which is precisely how a verified-by-md5 plugin
    ended up not being the one loaded.

    Returned paths are relative to `root` so the error message stays readable.
    """
    plugins = root / "Plugins"
    if not plugins.is_dir():
        return []
    canonical = (plugins / "AirSim" / "AirSim.uplugin").resolve()
    found = []
    for up in sorted(plugins.rglob("AirSim.uplugin")):
        if up.resolve() != canonical:
            found.append(up.relative_to(root))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject Cosys-AirSim into a user's UE project (SIM-11 A1).")
    ap.add_argument("uproject", type=Path, help="path to the user's .uproject")
    ap.add_argument("--map", default="", help="content path of the map to load, e.g. /Game/Maps/Mine")
    ap.add_argument("--plugin", type=Path, default=BUILT_PLUGIN,
                    help="AirSim plugin folder to copy (default: the BUILT one from Blocks)")
    ap.add_argument("--force", action="store_true",
                    help="if the project already has Plugins/AirSim, move it aside "
                         "to AirSim.bak.<timestamp> instead of refusing")
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
        # Do NOT silently delete this. Anyone who has integrated AirSim by hand already has
        # a Plugins/AirSim, possibly with local modifications, and this is the USER's project
        # -- the one directory this script has least business being cavalier with. The rest of
        # the script is careful about exactly this (it refuses paths inside our repo and
        # preserves their existing ini settings and plugin entries); an unannounced rmtree
        # here would undo that care in one line.
        if not a.force:
            die(f"{dest} already exists.\n"
                f"       Refusing to replace it — it may contain local modifications.\n"
                f"       Re-run with --force to move it aside to "
                f"AirSimBackups/AirSim.bak.<timestamp> and replace it.")
        # The backup must land OUTSIDE Plugins/. Unreal's plugin manager scans that directory
        # RECURSIVELY and keys plugins by name+version, so a backup left as a sibling is a
        # second copy of plugin "AirSim" v3 -- and the manager keeps exactly one, silently:
        #     LogPluginManager: Warning: The same version (v3) of plugin 'AirSim' exists at
        #     'AirSim.bak.<ts>/AirSim.uplugin' and 'AirSim/AirSim.uplugin'
        #       - second location will be ignored
        # The freshly injected plugin is the one ignored. This cost a full day: a rebuilt,
        # md5-verified plugin sat at Plugins/AirSim while the engine loaded a stale backup, and
        # the resulting "the patch changes nothing" was recorded as a negative result about the
        # patch rather than about the loader.
        backups = root / "AirSimBackups"
        backups.mkdir(parents=True, exist_ok=True)
        backup = backups / f"AirSim.bak.{int(time.time())}"
        dest.rename(backup)
        warn(f"existing plugin moved aside -> AirSimBackups/{backup.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(a.plugin, dest, symlinks=True)
    log(f"copied plugin -> Plugins/AirSim")

    shadows = shadowing_plugin_copies(root)
    if shadows:
        die("Plugins/ contains more than one copy of the AirSim plugin:\n"
            + "".join(f"         {s}\n" for s in shadows)
            + "       Unreal keeps ONE and silently ignores the rest, and the one it keeps is\n"
              "       not necessarily the copy just injected. Move the extras out of Plugins/\n"
              "       (anywhere else in the project is fine) and re-run.")

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

    # 4b. THE ONE THAT ACTUALLY DECIDES WHETHER IT LOOKS PHOTOREALISTIC ---------------
    # UE5's photorealism IS Lumen (global illumination + reflections) and Nanite, and BOTH
    # require Shader Model 6. On Linux/Vulkan the engine falls back to SM5 unless the project
    # explicitly asks for SM6 -- and on SM5 you get legacy lighting with no GI, no bounce
    # light and no virtual shadow maps. The result renders, runs, and looks flat and washed
    # out, which reads as "this asset is poor" rather than "the renderer is a tier down".
    #
    # Measured: Blocks sets +TargetedRHIs=SF_VULKAN_SM6 and runs rhifeaturelevel="SM6";
    # City Park had no such line and came up VULKAN_SM5 with
    # "Vulkan RayTracing disabled because SM6 shader platform is required".
    # Blocks does BOTH: it REMOVES SM5 and ADDS SM6. The `-` line matters -- leaving SM5 in
    # the targeted list lets the engine keep selecting it, so adding SM6 alone is not enough.
    lin = "[/Script/LinuxTargetPlatform.LinuxTargetSettings]"
    ini_add_once(eng, lin, "-TargetedRHIs=SF_VULKAN_SM5")
    ini_add_once(eng, lin, "+TargetedRHIs=SF_VULKAN_SM6")
    log("requested SF_VULKAN_SM6 (Lumen/Nanite need SM6; Linux Vulkan defaults to SM5)")

    # 4a. DefaultScalability.ini — upstream's step 7, easy to miss and visually obvious -
    # Cosys-AirSim's docs: "If using Unreal Engine 5.3 or higher check here for a fix to the
    # camera scene rendering bug in these engine versions." The fix is a DefaultScalability.ini
    # forcing r.DetailMode=2 at every EffectsQuality level. Blocks ships one; a user's project
    # will not, and without it captured frames come back washed out and low-detail — which
    # looks like a broken world rather than a missing config, and sends you hunting in the
    # wrong place. Copied verbatim from Blocks rather than re-typed.
    scal_src = BUILT_PLUGIN.parent.parent / "Config" / "DefaultScalability.ini"
    scal_dst = root / "Config" / "DefaultScalability.ini"
    if scal_src.is_file():
        if scal_dst.exists() and scal_dst.read_text() != scal_src.read_text():
            warn(f"{scal_dst.name} exists and differs from upstream's; leaving it alone. "
                 f"If captures look washed out, compare it against {scal_src}")
        elif not scal_dst.exists():
            scal_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(scal_src, scal_dst)
            log("copied DefaultScalability.ini (upstream step 7: UE5.3+ scene camera bug)")
    else:
        warn(f"no DefaultScalability.ini at {scal_src} — the UE5.3+ camera fix was NOT applied")

    # 4. DefaultGame.ini cook directives ----------------------------------------------
    game = root / "Config" / "DefaultGame.ini"
    ini_add_once(game, PACKAGING_SECTION, '+MapsToCook=(FilePath="/AirSim/AirSimAssets")')
    if a.map:
        # Blocks lists BOTH its own map and AirSim's. Latent while we run from the editor
        # binary, but the moment someone packages a user world, an uncooked map is a build
        # that silently ships without the level.
        ini_add_once(game, PACKAGING_SECTION, f'+MapsToCook=(FilePath="{a.map}")')
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
