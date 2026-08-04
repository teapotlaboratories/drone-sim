#!/usr/bin/env python3
"""Compare AirSim's simGetImages against Unreal's own render FROM THE SAME POSE.

This is the comparison the whole washout investigation rested on and never actually ran. The
original "AirSim renders worse than Unreal" side-by-side used two DIFFERENT viewpoints, which
is how a world-side property (aerial-perspective fog, which scales with camera altitude) got
attributed to the capture pipeline.

Making it apples-to-apples turns on one detail in the vendor source:

    SimModeBase.cpp:2120   CameraDirector->initializeForBeginPlay(..., getCamera("fpv"), ...)

The viewport's FPV camera is the PIPCamera literally NAMED "fpv" -- which is why earlier
attempts comparing against `front_center` were comparing two different cameras. Declare the
camera as "fpv" and both paths resolve to the SAME APIPCamera actor at the SAME transform:

    * Unreal's path : fpv_camera_->showToScreen() makes that actor's UCineCameraComponent the
                      view target, and `HighResShot` renders it through the normal pipeline.
    * AirSim's path : simGetImages() reads that same actor's USceneCaptureComponent2D.

Same actor, same transform, same FOV, same frame. Any difference is the capture path itself.

Runs several scenes at several distances, since the fog hypothesis predicts the gap should GROW
with distance to subject and nearly vanish up close.
"""
import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("capexp", REPO / "scripts" / "capture_experiment.py")
cap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cap)

# (label, x, y, z, yaw) -- z is NED, negative is UP.
SCENES = [
    ("treeline_close",  20.0, -55.0,  -6.0, 315.0),
    ("treeline_mid",    50.0, -30.0, -10.0, 315.0),
    ("treeline_far",    50.0, -30.0, -25.0, 315.0),
    ("park_high",       50.0, -30.0, -70.0, 315.0),
    ("lakeside",        50.0, -30.0,  -8.0, 270.0),
    ("path_north",      50.0, -30.0,  -8.0,   0.0),
]


def build_fpv_settings(width, height, fov, lumen):
    """Same shape as capture_experiment.build_settings, but the camera is named "fpv" and the
    view mode is Fpv, so the viewport and the capture component are the same actor."""
    capture = {"ImageType": 0, "Width": width, "Height": height, "FOV_Degrees": fov}
    if lumen:
        capture.update({"LumenGIEnable": True, "LumenReflectionEnable": True})
    return {
        "SettingsVersion": 2.0,
        "SimMode": "Multirotor",
        "ViewMode": "Fpv",
        "Vehicles": {
            "Drone": {
                "VehicleType": "SimpleFlight",
                "AutoCreate": True,
                "Cameras": {
                    # Explicit pose: omitting these leaves AirSim's NaN sentinels and SIGSEGVs
                    # the simulator in PawnSimApi::createCamerasFromSettings during BeginPlay.
                    "fpv": {
                        "X": 0.3, "Y": 0.0, "Z": 0.0,
                        "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0,
                        "CaptureSettings": [capture],
                    }
                },
            }
        },
    }


def shot_dirs(world_uproject):
    saved = Path(world_uproject).parent / "Saved" / "Screenshots"
    return [saved / "Linux", saved / "LinuxEditor", saved]


def existing_shots(world_uproject):
    seen = set()
    for d in shot_dirs(world_uproject):
        if d.is_dir():
            seen |= {p for p in d.glob("*.png")}
    return seen


def wait_for_new_shot(world_uproject, before, timeout=90):
    """HighResShot is asynchronous -- the RPC returns long before the file lands."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = existing_shots(world_uproject)
        new = now - before
        if new:
            p = max(new, key=lambda q: q.stat().st_mtime)
            # Wait for the write to finish rather than racing a partial file.
            last = -1
            while True:
                sz = p.stat().st_size
                if sz == last and sz > 0:
                    return p
                last = sz
                time.sleep(0.4)
        time.sleep(0.5)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--gpu", default="0")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--no-lumen", action="store_true",
                    help="capture with AirSim's stock Lumen defaults (GI/reflections OFF)")
    ap.add_argument("--startup-timeout", type=float, default=900.0)
    ap.add_argument("--only", help="comma-separated scene labels")
    ap.add_argument("--outdir", help="repo-relative output directory")
    args = ap.parse_args()

    outdir = REPO / (args.outdir or "out/vs-native")
    outdir.mkdir(parents=True, exist_ok=True)

    wanted = set(args.only.split(",")) if args.only else None
    scenes = [s for s in SCENES if not wanted or s[0] in wanted]

    tmp = Path(tempfile.mkdtemp(prefix="vsnative-"))
    settings = tmp / "settings.json"
    settings.write_text(json.dumps(
        build_fpv_settings(args.width, args.height, args.fov, not args.no_lumen), indent=2))
    settings.chmod(0o644)

    cap.log(f"lumen={'off (AirSim defaults)' if args.no_lumen else 'on'}; "
            f"{len(scenes)} scenes, paired AirSim/native captures")

    results = []
    try:
        cap.start_sim(settings, args.world, args.gpu)
        cap.wait_for_rpc(args.startup_timeout)
        cap.log("RPC up")

        for label, x, y, z, yaw in scenes:
            before = existing_shots(args.world)
            # One client invocation does both: it holds the pose, grabs the AirSim frame, and
            # fires HighResShot while STILL holding it. Splitting these into two RPC calls
            # would let the vehicle drift between them.
            r = cap.client_run([
                "/scripts/_capture_paired.py", "--out", f"/out/{label}_airsim.png",
                "--vehicle", "Drone", "--camera", "fpv",
                "--x", str(x), "--y", str(y), "--z", str(z),
                "--yaw", str(yaw), "--settle", str(args.settle),
                "--shot-res", f"{args.width}x{args.height}",
            ], timeout=300, outdir=outdir)
            if r.returncode != 0:
                cap.log(f"  {label:<16} FAILED {r.stderr.strip()[-200:]}")
                results.append({"scene": label, "ok": False})
                continue

            stats = json.loads(r.stdout.strip().splitlines()[-1])
            shot = wait_for_new_shot(args.world, before)
            if shot is None:
                cap.log(f"  {label:<16} AirSim ok but no HighResShot appeared")
                results.append({"scene": label, "ok": False, **stats})
                continue
            native = outdir / f"{label}_native.png"
            shutil.copy2(shot, native)
            shot.unlink(missing_ok=True)

            cap.log(f"  {label:<16} airsim mean={stats['mean']:>7.2f} "
                    f"range={stats['range_p01_p99']:>6.2f}   native -> {native.name}")
            results.append({"scene": label, "ok": True, "x": x, "y": y, "z": z, "yaw": yaw,
                            **stats})
    finally:
        cap.teardown()

    (outdir / "results.json").write_text(json.dumps(results, indent=2))
    cap.log(f"wrote {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
