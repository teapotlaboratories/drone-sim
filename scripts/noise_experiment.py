#!/usr/bin/env python3
"""Test every STOCK Cosys-AirSim lever against the capture's foliage aliasing, and price each one.

The aliasing has a known mechanism: cameras start as `nodisplay`, so `bCaptureEveryFrame` is
false (PIPCamera.cpp:186-188, :455-459) and the scene capture renders one shot per request.
`bAlwaysPersistRenderingState` is true, so the temporal history BUFFER survives -- but with no
per-frame captures nothing accumulates into it, so TSR/TAA never converges. The main viewport
renders continuously and does converge, which is the whole of the difference.

Three interventions follow from that, none needing a plugin patch:

  forceupdate   `"ForceUpdate": true` -> setCameraTypeUpdate(Scene, false) -> bCaptureEveryFrame
                true (PIPCamera.cpp:730). Gives the temporal history something to accumulate.
                Upstream flags it "costly for performance!".
  fxaa          `r.AntiAliasingMethod 1` over simRunConsoleCommand. FXAA is a SPATIAL filter --
                it works on a single frame, so unlike TSR it does not need history the capture
                does not have. Attacks the symptom instead of the cause, and should be cheap.
  supersample   request 2x the pixels in settings.json and box-filter down in the client. The
                oldest and most reliable single-frame anti-aliasing; costs 4x the shaded pixels.

Each variant is scored the same way as the original finding -- high-frequency energy against
Unreal's own render of the identical view -- and each reports achievable simGetImages rate,
because a fix that halves the frame rate is not obviously a fix: the graph already claims 31 Hz.

Console cvars change the WHOLE renderer, so the native reference is re-captured per variant
rather than reused across them.
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
_vspec = importlib.util.spec_from_file_location("vsnative", REPO / "scripts" / "capture_vs_native.py")
vsn = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(vsn)

SCENES = [
    ("treeline_mid",   50.0, -30.0, -10.0, 315.0),
    ("treeline_close", 20.0, -55.0,  -6.0, 315.0),
]

# name -> (extra CaptureSettings keys, console commands, capture-scale, downsample)
VARIANTS = {
    "baseline":     ({}, [], 1, 1),
    "forceupdate":  ({"ForceUpdate": True}, [], 1, 1),
    "fxaa":         ({}, ["r.AntiAliasingMethod 1"], 1, 1),
    "supersample2": ({}, [], 2, 2),
    "force_fxaa":   ({"ForceUpdate": True}, ["r.AntiAliasingMethod 1"], 1, 1),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--gpu", default="0")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--settle", type=float, default=6.0)
    ap.add_argument("--fps-samples", type=int, default=20)
    ap.add_argument("--startup-timeout", type=float, default=900.0)
    ap.add_argument("--only", help="comma-separated variant names")
    ap.add_argument("--no-lumen", action="store_true",
                    help="disable Lumen GI/reflections, to test whether the residual\nnoise is stochastic GI sampling rather than geometric aliasing")
    args = ap.parse_args()

    wanted = set(args.only.split(",")) if args.only else None
    variants = {k: v for k, v in VARIANTS.items() if not wanted or k in wanted}

    results = []
    for vname, (extra, console, scale, down) in variants.items():
        outdir = REPO / "out/noise-exp" / (vname + ("_nolumen" if args.no_lumen else ""))
        outdir.mkdir(parents=True, exist_ok=True)
        cap.log(f"--- variant {vname}: settings={extra or 'none'} console={console or 'none'} "
                f"capture={scale}x downsample={down}x")

        tmp = Path(tempfile.mkdtemp(prefix=f"noiseexp-{vname}-"))
        settings = tmp / "settings.json"
        s = vsn.build_fpv_settings(args.width * scale, args.height * scale, args.fov,
                                   lumen=not args.no_lumen)
        s["Vehicles"]["Drone"]["Cameras"]["fpv"]["CaptureSettings"][0].update(extra)
        settings.write_text(json.dumps(s, indent=2))
        settings.chmod(0o644)

        try:
            cap.start_sim(settings, args.world, args.gpu)
            cap.wait_for_rpc(args.startup_timeout)

            for label, x, y, z, yaw in SCENES:
                before = vsn.existing_shots(args.world)
                cmd = [
                    "/scripts/_capture_noise.py", "--out", f"/out/{label}_airsim.png",
                    "--vehicle", "Drone", "--camera", "fpv",
                    "--x", str(x), "--y", str(y), "--z", str(z), "--yaw", str(yaw),
                    "--settle", str(args.settle), "--downsample", str(down),
                    "--fps-samples", str(args.fps_samples),
                    "--shot-res", f"{args.width}x{args.height}",
                ]
                for c in console:
                    cmd += ["--console", c]
                r = cap.client_run(cmd, timeout=600, outdir=outdir)
                if r.returncode != 0:
                    cap.log(f"    {label:<16} FAILED {r.stderr.strip()[-200:]}")
                    continue
                st = json.loads(r.stdout.strip().splitlines()[-1])

                shot = vsn.wait_for_new_shot(args.world, before)
                if shot is not None:
                    shutil.copy2(shot, outdir / f"{label}_native.png")
                    shot.unlink(missing_ok=True)
                cap.log(f"    {label:<16} {st['out_w']}x{st['out_h']} "
                        f"mean={st['mean']:>7.2f} fps={st['capture_fps']:>6.2f}")
                results.append({"variant": vname, "scene": label, **st})
        except Exception as e:
            cap.log(f"    variant FAILED: {e}")
        finally:
            cap.teardown()
            shutil.rmtree(tmp, ignore_errors=True)

    out = REPO / "out/noise-exp/results.json"
    out.write_text(json.dumps(results, indent=2))
    cap.log(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
