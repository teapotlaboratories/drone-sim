#!/usr/bin/env python3
"""Sweep the camera through altitude in one simulator run and measure the image at each stop.

Why this exists: the 2x2 in capture_experiment.py showed Lumen is worth +16 dynamic range but
left both variants still badly washed -- and BOTH frames carry the out-of-focus concrete border
that means the camera is sitting inside world geometry. A near surface smeared across the frame
by depth-of-field is itself a milky veil over everything behind it, so "the capture is washed
out" and "the camera is buried" are confounded, and have been for this entire investigation.

Separating them needs a pose sweep, not another setting. Capture settings are parsed at startup
so the 2x2 had to pay a simulator restart per cell; POSE is free over RPC, so this walks one
running simulator through many positions and measures each.

If the wash is an artifact of embedding, the numbers improve monotonically as the camera clears
the geometry and then plateau. If it is a real rendering defect, they stay flat all the way up.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("capexp", REPO / "scripts" / "capture_experiment.py")
cap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--gpu", default="0")
    ap.add_argument("--x", type=float, default=50.0)
    ap.add_argument("--y", type=float, default=-30.0)
    ap.add_argument("--yaw", type=float, default=315.0)
    ap.add_argument("--z", default="-2,-5,-10,-20,-40,-80,-150,-300",
                    help="comma-separated NED z values (negative is UP)")
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--lumen", action="store_true", help="enable Lumen GI + reflections")
    ap.add_argument("--startup-timeout", type=float, default=900.0)
    ap.add_argument("--tag", default="sweep")
    args = ap.parse_args()

    outdir = REPO / "out/capture-exp"
    outdir.mkdir(parents=True, exist_ok=True)

    overrides = {"LumenGIEnable": True, "LumenReflectionEnable": True} if args.lumen else {}
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="capsweep-"))
    settings = tmp / "settings.json"
    settings.write_text(json.dumps(
        cap.build_settings(overrides, args.width, args.height, args.fov), indent=2))
    settings.chmod(0o644)

    zs = [float(v) for v in args.z.split(",")]
    cap.log(f"world={args.world}")
    cap.log(f"lumen={'on' if args.lumen else 'off'}; sweeping z over {zs} at "
            f"x={args.x} y={args.y} yaw={args.yaw}")

    results = []
    try:
        cap.start_sim(settings, args.world, args.gpu)
        cap.wait_for_rpc(args.startup_timeout)
        cap.log("RPC up; sweeping (no restarts -- pose is free over RPC)")

        for z in zs:
            name = f"{args.tag}_z{abs(int(z)):04d}"
            r = cap.client_run([
                "/scripts/_capture_client.py", "--out", f"/out/{name}.png",
                "--vehicle", "Drone", "--camera", "front_center",
                "--x", str(args.x), "--y", str(args.y), "--z", str(z),
                "--yaw", str(args.yaw), "--settle", str(args.settle),
            ], timeout=300)
            if r.returncode != 0:
                cap.log(f"  z={z:>7}: FAILED {r.stderr.strip()[-200:]}")
                results.append({"z": z, "ok": False})
                continue
            s = json.loads(r.stdout.strip().splitlines()[-1])
            cap.log(f"  z={z:>7}: mean={s['mean']:>7.2f} std={s['std']:>6.2f} "
                    f"range={s['range_p01_p99']:>6.2f} "
                    f"p01={s['p01']:>6.1f} p99={s['p99']:>6.1f}")
            results.append({"z": z, "ok": True, **s})
    finally:
        cap.teardown()

    (outdir / f"{args.tag}_results.json").write_text(json.dumps(results, indent=2))
    cap.log(f"wrote {outdir}/{args.tag}_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
