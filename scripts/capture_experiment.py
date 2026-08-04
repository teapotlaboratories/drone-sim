#!/usr/bin/env python3
"""Measure what actually causes the AirSim Scene washout, one setting at a time.

Background: AirSim's `simGetImages` Scene capture renders visibly flatter and hazier than
Unreal's own screenshot of the same world. Six world-side interventions (scalability ini,
SM5->SM6, the UE4->UE5 conversion, AtmosphericFog->SkyAtmosphere) and one plugin patch
(FinalToneCurveHDR -> FinalColorLDR) all failed to change it, which places the fault in how
the capture COMPONENT is configured rather than in the world or the capture source.

Reading the vendor source turns up two settings that are applied to the Scene capture and to
nothing else, both of which default to values that would produce exactly this look:

  * TargetGamma defaults to 1.4 for ImageType 0 ONLY (AirSimSettings.hpp:1526, applied at
    PIPCamera.cpp:747). A 1.4 gamma applied on top of an already tone-curved image lifts the
    midtones -- a milky wash.
  * LumenGIEnable / LumenReflectionEnable default to FALSE, and PIPCamera.cpp:701-715 does not
    merely skip Lumen when they are false, it explicitly forces DynamicGlobalIlluminationMethod
    ::None and ReflectionMethod::None. So the capture runs with global illumination and
    reflections OFF in a world authored for Lumen, while Unreal's own render uses both.

Those are independent, so this runs them as a 2x2 factorial rather than changing both at once
and learning nothing about which mattered.

Every variant is captured from the SAME pose in the SAME world, because the single biggest
source of error in the earlier attempts was comparing two different views and attributing the
difference to a setting. Each variant needs its own simulator run: these are parsed from
settings.json at startup, so they cannot be swapped over RPC.

The vehicle is SimpleFlight, not PX4. The capture path is identical -- PIPCamera does not know
what firmware is driving the vehicle -- and it removes the autopilot, its uXRCE-DDS link and
the EKF origin from a measurement that has nothing to do with any of them.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIM_IMAGE = "drone-sim/unreal:ue5.8"
CLIENT_IMAGE = "drone-sim/airsim-client:1"
SIM_NAME = "capture-exp-sim"
DDC_VOLUME = "sim-ddc"          # shared shader/DDC cache: without it every run recompiles
ENGINE = "/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"

# The Scene camera settings under test. `None` means "leave the key out", i.e. take AirSim's
# default -- which is the whole point for the baseline.
VARIANTS = [
    ("baseline",      {}),
    ("gamma1.0",      {"TargetGamma": 1.0}),
    ("lumen",         {"LumenGIEnable": True, "LumenReflectionEnable": True}),
    ("gamma1.0+lumen", {"TargetGamma": 1.0, "LumenGIEnable": True, "LumenReflectionEnable": True}),
]


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def log(msg):
    print(f"\033[36m[capture-exp]\033[0m {msg}", flush=True)


def strip_jsonc(text):
    """settings.json in this repo carries // comments, which json.loads rejects."""
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def build_settings(overrides, width, height, fov):
    """A minimal settings.json: one SimpleFlight vehicle, one Scene camera, nothing else.

    Deliberately NOT derived from sim/ue5/settings.json -- that file carries PX4, the LiDAR
    and the depth camera, all of which cost startup time and none of which affect the Scene
    capture being measured.
    """
    capture = {"ImageType": 0, "Width": width, "Height": height, "FOV_Degrees": fov}
    capture.update(overrides)
    return {
        "SettingsVersion": 2.0,
        "SimMode": "Multirotor",
        "ViewMode": "NoDisplay",
        "Vehicles": {
            "Drone": {
                "VehicleType": "SimpleFlight",
                "AutoCreate": True,
                "Cameras": {
                    # The pose keys are NOT optional, despite reading like defaults. AirSim
                    # initialises them to its NaN "unspecified" sentinel, and a camera declared
                    # without them reaches FRotator::Quaternion as P=nan Y=nan R=nan, which
                    # takes the whole simulator down in PawnSimApi::createCamerasFromSettings
                    # during BeginPlay -- a SIGSEGV at startup, not a validation error.
                    "front_center": {
                        "X": 0.3, "Y": 0.0, "Z": 0.0,
                        "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0,
                        "CaptureSettings": [capture],
                    }
                },
            }
        },
    }


def teardown():
    sh(f"docker rm -f {SIM_NAME}")


def start_sim(settings_path, world_uproject, gpu):
    teardown()
    world_dir = Path(world_uproject).parent
    cmd = [
        "docker", "run", "-d", "--name", SIM_NAME,
        "--ipc", "shareable",
        "--gpus", f'"device=nvidia.com/gpu={gpu}"',
        "-v", f"{world_dir}:/world",
        "-v", f"{settings_path}:/settings.json:ro",
        "-v", f"{DDC_VOLUME}:/home/ue4/.config/Epic",
        SIM_IMAGE,
        "bash", "-lc",
        f"{ENGINE} /world/{Path(world_uproject).name} -game -RenderOffScreen -nosound "
        f"-unattended -stdout -settings=/settings.json",
    ]
    r = sh(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"could not start simulator: {r.stderr.strip()}")


def client_run(script_args, timeout, outdir=None):
    """Run a client script inside the sim's network namespace.

    `outdir` is what the container sees as /out. It is a parameter rather than a constant
    because callers write their results to different directories, and a hardcoded mount sends
    a caller's images somewhere it will not look for them -- silently, since the write itself
    succeeds.
    """
    outdir = Path(outdir) if outdir else (REPO / "out/capture-exp")
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "run", "--rm",
        "--network", f"container:{SIM_NAME}",
        "--ipc", f"container:{SIM_NAME}",
        "-v", f"{REPO / 'vendor/Cosys-AirSim/PythonClient'}:/client:ro",
        "-v", f"{REPO / 'scripts'}:/scripts:ro",
        "-v", f"{outdir}:/out",
        CLIENT_IMAGE, "python3",
    ] + script_args
    return sh(cmd, timeout=timeout)


def wait_for_rpc(timeout_s):
    """Poll until AirSim answers, failing loudly if the simulator died on the way up."""
    probe = ["-c", (
        "import cosysairsim as airsim, sys\n"
        "c = airsim.MultirotorClient(); c.ping(); print('up')\n"
    )]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        alive = sh(f"docker inspect -f '{{{{.State.Running}}}}' {SIM_NAME}").stdout.strip()
        if alive != "true":
            logs = sh(f"docker logs --tail 30 {SIM_NAME}").stdout
            raise RuntimeError(f"simulator exited during startup:\n{logs}")
        r = client_run(probe, timeout=60)
        if r.returncode == 0 and "up" in r.stdout:
            return time.time()
        time.sleep(5)
    raise TimeoutError(f"AirSim did not answer RPC within {timeout_s}s")


def run_variant(name, overrides, args, outdir):
    log(f"--- variant {name}: {overrides or 'AirSim defaults'}")
    tmp = Path(tempfile.mkdtemp(prefix=f"capexp-{name}-"))
    try:
        settings = tmp / "settings.json"
        settings.write_text(json.dumps(
            build_settings(overrides, args.width, args.height, args.fov), indent=2))
        settings.chmod(0o644)

        t0 = time.time()
        start_sim(settings, args.world, args.gpu)
        wait_for_rpc(args.startup_timeout)
        log(f"    RPC up in {time.time() - t0:.0f}s; capturing")

        png = f"/out/{name}.png"
        r = client_run([
            "/scripts/_capture_client.py", "--out", png,
            "--vehicle", "Drone", "--camera", "front_center",
            "--x", str(args.x), "--y", str(args.y), "--z", str(args.z),
            "--yaw", str(args.yaw), "--settle", str(args.settle),
        ], timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"capture failed: {r.stderr.strip()[-600:]}")
        stats = json.loads(r.stdout.strip().splitlines()[-1])
        log(f"    mean={stats['mean']} std={stats['std']} "
            f"range(p01-p99)={stats['range_p01_p99']}")
        return {"variant": name, "overrides": overrides, "ok": True, **stats}
    except Exception as e:                                    # one bad variant must not
        log(f"    FAILED: {e}")                               # abort the whole matrix
        return {"variant": name, "overrides": overrides, "ok": False, "error": str(e)}
    finally:
        teardown()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", required=True, help="path to the .uproject to capture in")
    ap.add_argument("--gpu", default="0", help="render GPU (0 = the 3080 on carbonite)")
    # Defaults are the pose that framed the trees in out/trees_pointed.png.
    ap.add_argument("--x", type=float, default=50.0)
    ap.add_argument("--y", type=float, default=-30.0)
    ap.add_argument("--z", type=float, default=-12.0)
    ap.add_argument("--yaw", type=float, default=315.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--startup-timeout", type=float, default=900.0)
    ap.add_argument("--only", help="comma-separated variant names to run")
    args = ap.parse_args()

    outdir = REPO / "out/capture-exp"
    outdir.mkdir(parents=True, exist_ok=True)

    wanted = set(args.only.split(",")) if args.only else None
    variants = [v for v in VARIANTS if not wanted or v[0] in wanted]
    if wanted and not variants:
        print(f"no variant matched {args.only}; known: {[v[0] for v in VARIANTS]}", file=sys.stderr)
        return 2

    log(f"world={args.world}")
    log(f"pose=({args.x}, {args.y}, {args.z}) yaw={args.yaw} -- identical for every variant")

    results = [run_variant(n, o, args, outdir) for n, o in variants]

    (outdir / "results.json").write_text(json.dumps(results, indent=2))
    print()
    log("results (higher range_p01_p99 = more contrast = less washed out)")
    print(f"  {'variant':<16} {'mean':>8} {'std':>8} {'range':>8}  overrides")
    for r in results:
        if r["ok"]:
            print(f"  {r['variant']:<16} {r['mean']:>8.2f} {r['std']:>8.2f} "
                  f"{r['range_p01_p99']:>8.2f}  {r['overrides'] or 'defaults'}")
        else:
            print(f"  {r['variant']:<16} {'FAILED':>8}  {r['error'][:60]}")
    log(f"images + results.json in {outdir}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
