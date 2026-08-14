#!/usr/bin/env python3
"""Write an operator-supplied spawn position into a run-time copy of settings.json.   (SIM-13)

WHY THIS EXISTS
---------------
AirSim places the vehicle at a level's `PlayerStart`, and falls back to world origin when there
isn't one. An arbitrary user world has no obligation to put usable ground at the origin — City
Park's terrain sits above it — so the drone spawns INSIDE the terrain. That single defect
confounded four separate investigations here: three because the camera was buried in
geometry, and a fourth because of the workaround adopted to stop the vehicle falling
(`simPause`, which freezes the renderer and makes `simGetImages` return stale frames).

AirSim reads the spawn from vehicle-level keys — `AirSimSettings.hpp:1061-1062`:

    vehicle_setting->position = createVectorSetting(settings_json, ...)   // "X" "Y" "Z"
    vehicle_setting->rotation = createRotationSetting(settings_json, ...) // "Yaw" "Pitch" "Roll"

Deriving a good spawn automatically is `SIM-14`; this is the operator saying where it is.

TWO FOOTGUNS, HANDLED LOUDLY
----------------------------
1. **Z is NED — negative is UP.** An operator typing `10` for "10 metres up" gets 10 metres
   UNDERGROUND, which is precisely the failure this is meant to fix. A positive Z is therefore
   refused unless explicitly acknowledged with --allow-below-origin.
2. **A malformed spawn must abort, never silently fall through to origin** — falling through
   would reproduce the original bug while appearing to have fixed it.

The committed `sim/ue5/settings.json` is never modified: spawn is per-world and per-run, and
baking it into a reviewed repo artifact would be wrong.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


class SpawnError(ValueError):
    """Bad operator input — the caller must abort, not fall back to a default."""


def parse_spawn(text: str) -> dict:
    """Parse "X,Y,Z" or "X,Y,Z,YAW" into settings keys. Raises SpawnError on anything else.

    Returns a dict of AirSim vehicle-level keys, so the caller never has to know the spelling.
    """
    if text is None or not str(text).strip():
        raise SpawnError("spawn is empty; expected X,Y,Z or X,Y,Z,YAW (metres, NED)")

    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) not in (3, 4, 6):
        raise SpawnError(
            f"spawn needs 3, 4 or 6 comma-separated numbers, got {len(parts)}: {text!r}\n"
            f"       expected X,Y,Z | X,Y,Z,YAW | X,Y,Z,YAW,PITCH,ROLL\n"
            f"       (metres NED, Z negative is UP; angles in DEGREES)"
        )

    # Pitch and Roll are AirSim settings keys in their own right -- the header above quotes
    # createRotationSetting reading "Yaw" "Pitch" "Roll" -- they were simply never plumbed.
    # A scenario that wants the vehicle to start tilted (a sloped rooftop, a failed-landing
    # attitude) could not say so.                                                    (SIM-31)
    names = ("X", "Y", "Z", "Yaw", "Pitch", "Roll")
    vals = {}
    for name, raw in zip(names, parts):
        try:
            v = float(raw)
        except ValueError:
            raise SpawnError(f"spawn component {name} is not a number: {raw!r}") from None
        # NaN and infinity parse happily as floats and would reach FRotator/FVector as garbage.
        if v != v or v in (float("inf"), float("-inf")):
            raise SpawnError(f"spawn component {name} must be finite, got {raw!r}")
        vals[name] = v
    return vals


def parse_origin(text: str | None) -> dict | None:
    """LAT,LON,ALT -> AirSim's OriginGeopoint, or None if not requested.          (SIM-31)

    This is where the world sits on Earth. AirSim synthesises the GPS sensor from it, and PX4's
    EKF then latches its origin from that GPS -- so this value reaches the flight stack, it just
    reaches it INDIRECTLY. Anything converting lat/lon to a local setpoint must use PX4's own
    reference (`ref_lat`/`ref_lon` on vehicle_local_position), never this, because the two differ
    by whatever the EKF latched. SIM-28 measured that gap at 9.13 m on 40 of 40 bring-ups.
    """
    if not text:
        return None
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 3:
        raise SpawnError(f"origin needs LAT,LON,ALT, got {len(parts)}: {text!r}")
    out = {}
    for name, raw, lo, hi in (("Latitude", parts[0], -90.0, 90.0),
                              ("Longitude", parts[1], -180.0, 180.0),
                              ("Altitude", parts[2], -500.0, 9000.0)):
        try:
            v = float(raw)
        except ValueError:
            raise SpawnError(f"origin component {name} is not a number: {raw!r}") from None
        if v != v or v in (float("inf"), float("-inf")):
            raise SpawnError(f"origin component {name} must be finite, got {raw!r}")
        # Bounds, because a swapped lat/lon is silent otherwise: the world simply sits somewhere
        # else on Earth and every GPS number in the run is quietly wrong.
        if not (lo <= v <= hi):
            raise SpawnError(f"origin {name} out of range [{lo}, {hi}]: {v}")
        out[name] = v
    return out


def check_altitude(vals: dict, allow_below_origin: bool = False) -> None:
    """Refuse a positive Z unless acknowledged. Z is NED: positive is DOWN."""
    z = vals["Z"]
    if z > 0 and not allow_below_origin:
        raise SpawnError(
            f"Z={z:g} is BELOW the origin — AirSim uses NED, where negative Z is UP.\n"
            f"       For {abs(z):g} m of altitude you want Z={-abs(z):g}.\n"
            f"       If you really do mean below origin, pass --allow-below-origin."
        )


def strip_jsonc(text: str) -> str:
    """settings.json in this repo carries // comments, which json.loads rejects."""
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def apply_spawn(doc: dict, vals: dict, vehicle: str | None = None) -> dict:
    """Return `doc` with the spawn keys set on the chosen vehicle. Mutates and returns doc.

    With no `vehicle`, applies to the sole vehicle — and refuses to guess when there are
    several, because picking the wrong one fails silently at the far end of a long run.
    """
    vehicles = doc.get("Vehicles")
    if not isinstance(vehicles, dict) or not vehicles:
        raise SpawnError("settings.json has no Vehicles block to place")

    if vehicle is None:
        if len(vehicles) > 1:
            raise SpawnError(
                f"settings.json defines {len(vehicles)} vehicles "
                f"({', '.join(sorted(vehicles))}); name one with --vehicle"
            )
        vehicle = next(iter(vehicles))
    elif vehicle not in vehicles:
        raise SpawnError(
            f"no vehicle named {vehicle!r}; settings.json has: {', '.join(sorted(vehicles))}"
        )

    vehicles[vehicle].update(vals)
    return doc


def apply_origin(doc: dict, origin: dict | None) -> dict:
    """Set OriginGeopoint at the top level, where AirSim reads it."""
    if origin is not None:
        doc["OriginGeopoint"] = origin
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--settings", required=True, help="source settings.json (never modified)")
    ap.add_argument("--out", required=True, help="where to write the run-time copy")
    ap.add_argument("--spawn", required=True, help="X,Y,Z or X,Y,Z,YAW (metres, NED)")
    ap.add_argument("--vehicle", help="vehicle name; required only if several are defined")
    ap.add_argument("--allow-below-origin", action="store_true",
                    help="permit a positive Z (NED: below the origin)")
    ap.add_argument("--origin", default="",
                    help="LAT,LON,ALT for OriginGeopoint -- where the world sits on Earth")
    a = ap.parse_args()

    try:
        vals = parse_spawn(a.spawn)
        check_altitude(vals, a.allow_below_origin)
        src = Path(a.settings)
        doc = json.loads(strip_jsonc(src.read_text(encoding="utf-8")))
        doc = apply_spawn(doc, vals, a.vehicle)
        doc = apply_origin(doc, parse_origin(a.origin))
    except SpawnError as e:
        print(f"\033[31m[spawn] FATAL:\033[0m {e}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as e:
        print(f"\033[31m[spawn] FATAL:\033[0m cannot read {a.settings}: {e}", file=sys.stderr)
        return 2

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    out.chmod(0o644)  # mounted read-only into the simulator container

    where = ", ".join(f"{k}={v:g}" for k, v in vals.items())
    print(f"\033[36m[spawn]\033[0m {where}  ->  {out}")
    if a.origin:
        o = doc["OriginGeopoint"]
        print(f"\033[36m[spawn]\033[0m origin {o['Latitude']:.6f}, {o['Longitude']:.6f}, "
              f"{o['Altitude']:g} m AMSL")
    if vals["Z"] < 0:
        print(f"\033[36m[spawn]\033[0m that is {abs(vals['Z']):g} m above the origin (NED)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
