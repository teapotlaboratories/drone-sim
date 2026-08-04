#!/usr/bin/env python3
"""A/B soak: does the capture path segfault, and is `compress` the discriminator?   (C-04 soak)

SITL only. Renders on GPU 0 exclusively -- GPU 1 is deliberately untouched, because another
unrelated simulator may be using it.

Two arms, identical except for one flag:

    compress=true   -> reaches CompressImageArray, which indexes bmp by width*height
    compress=false  -> iterate-not-index branch, safe on an empty buffer (what the ROS 2
                       wrapper actually uses)

  both survive        -> the capture path is not the trigger; stop blaming it
  only true crashes   -> mechanism confirmed; a one-line upstream guard fixes it
  both crash          -> the analysis is wrong and the fault is a different array

Upstream comments that the underlying read "seems to segfault every 2000 or so calls" -- a
COUNT, not a duration. So this drives captures as fast as RPC allows and records call number
AND elapsed time at failure, which distinguishes a per-call failure rate from an hourly one.
The original event took 57 minutes at whatever rate that session happened to run; if the
failure is count-driven it should arrive far sooner here.

The client cannot report its own death: when the simulator exits it takes the shared network
namespace with it. So the client flushes progress to a file every call, and this orchestrator
reads that file plus the simulator's own log to find out where it got to.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("capexp", REPO / "scripts" / "capture_experiment.py")
cap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cap)


def sim_alive() -> bool:
    r = subprocess.run(f"docker inspect -f '{{{{.State.Running}}}}' {cap.SIM_NAME}",
                       shell=True, capture_output=True, text=True)
    return r.stdout.strip() == "true"


def sim_log_tail(n=40) -> str:
    r = subprocess.run(f"docker logs --tail {n} {cap.SIM_NAME}",
                       shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr)


def read_progress(path: Path):
    """Last record, plus any anomalies seen. Tolerates a truncated final line."""
    last, anomalies, count = None, [], 0
    if not path.exists():
        return last, anomalies, count
    for line in path.read_text(errors="ignore").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        last = rec
        if "n" in rec:
            count = max(count, rec["n"])
        if "anomaly" in rec:
            anomalies.append(rec)
    return last, anomalies, count


def run_arm(name, compress, args, outdir):
    cap.log(f"=== arm '{name}' (compress={compress}) — GPU {args.gpu}, max "
            f"{args.max_calls} calls / {args.max_seconds:.0f}s")
    progress = outdir / f"{name}.jsonl"
    progress.unlink(missing_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix=f"soak-{name}-"))
    settings = tmp / "settings.json"
    settings.write_text(json.dumps(
        cap.build_settings({"LumenGIEnable": True, "LumenReflectionEnable": True},
                           args.width, args.height, 90.0), indent=2))
    settings.chmod(0o644)

    result = {"arm": name, "compress": compress}
    t0 = time.time()
    try:
        cap.start_sim(settings, args.world, args.gpu)
        cap.wait_for_rpc(args.startup_timeout)
        cap.log(f"    RPC up in {time.time() - t0:.0f}s; soaking")

        soak_t0 = time.time()
        r = cap.client_run([
            "/scripts/_soak_capture.py",
            "--progress", f"/out/{name}.jsonl",
            "--compress", "true" if compress else "false",
            "--vehicle", "Drone", "--camera", "front_center",
            "--max-calls", str(args.max_calls),
            "--max-seconds", str(args.max_seconds),
        ], timeout=args.max_seconds + 600, outdir=outdir)
        elapsed = time.time() - soak_t0

        alive = sim_alive()
        last, anomalies, count = read_progress(progress)

        result.update({
            "calls": count,
            "elapsed_s": round(elapsed, 1),
            "calls_per_s": round(count / elapsed, 2) if elapsed > 0 else None,
            "sim_alive_after": alive,
            "client_rc": r.returncode,
            "anomalies": len(anomalies),
            "anomaly_samples": anomalies[:5],
            "last_record": last,
        })
        if not alive:
            log = sim_log_tail(60)
            crash = [l for l in log.splitlines()
                     if "Assertion failed" in l or "out of bounds" in l or "Signal" in l]
            result["sim_crashed"] = True
            result["crash_lines"] = crash[:6]
            cap.log(f"    SIMULATOR DIED after {count} calls / {elapsed:.0f}s")
            for l in crash[:3]:
                cap.log(f"      {l.strip()[:150]}")
        else:
            result["sim_crashed"] = False
            cap.log(f"    survived {count} calls in {elapsed:.0f}s "
                    f"({result['calls_per_s']}/s), {len(anomalies)} anomalies")
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        cap.log(f"    arm FAILED to run: {e}")
    finally:
        cap.teardown()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default=str(
        REPO / "vendor/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject"))
    ap.add_argument("--gpu", default="0", help="render GPU — 0 is the 3080; GPU 1 is left alone")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--max-calls", type=int, default=20000)
    ap.add_argument("--max-seconds", type=float, default=1800.0)
    ap.add_argument("--startup-timeout", type=float, default=900.0)
    ap.add_argument("--only", help="comma-separated arm names: compress_true,compress_false")
    args = ap.parse_args()

    outdir = REPO / "out/lane-c/soak"
    outdir.mkdir(parents=True, exist_ok=True)

    arms = [("compress_true", True), ("compress_false", False)]
    if args.only:
        want = set(args.only.split(","))
        arms = [a for a in arms if a[0] in want]

    cap.log(f"world={args.world}")
    cap.log("GPU 1 is NOT used — another unrelated simulator may be on it")

    results = [run_arm(n, c, args, outdir) for n, c in arms]
    (outdir / "results.json").write_text(json.dumps(results, indent=2))

    print()
    cap.log("results")
    print(f"  {'arm':<16} {'calls':>8} {'sec':>8} {'calls/s':>8} {'crashed':>8} {'anomalies':>10}")
    for r in results:
        if "error" in r:
            print(f"  {r['arm']:<16} {'FAILED':>8}  {r['error'][:50]}")
            continue
        print(f"  {r['arm']:<16} {r['calls']:>8} {r['elapsed_s']:>8.0f} "
              f"{str(r['calls_per_s']):>8} {str(r['sim_crashed']):>8} {r['anomalies']:>10}")
    cap.log(f"detail in {outdir}/results.json")

    crashed = {r["arm"] for r in results if r.get("sim_crashed")}
    if crashed == {"compress_true"}:
        cap.log("VERDICT: mechanism CONFIRMED — compress=true crashes, compress=false survives")
    elif len(crashed) == 2:
        cap.log("VERDICT: analysis REFUTED — both arms crash, the fault is a different array")
    elif not crashed:
        cap.log("VERDICT: not reproduced under these conditions. NOT the same as 'fixed'.")
    else:
        cap.log(f"VERDICT: unexpected — crashed arms: {sorted(crashed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
