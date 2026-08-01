#!/usr/bin/env python3
"""Phase 1 exit criterion: success rate over N seeded runs (P1-06).

    ./scripts/run_gate.py scenarios/square-10m.yaml            # 10 seeds, restarting each
    ./scripts/run_gate.py scenarios/square-10m.yaml --reuse    # faster, weaker (see below)

Exits non-zero unless every run succeeds. That is the point: the criterion is
SR = 100% over 10 seeded runs, and "a single green run is not a pass".

WHAT THIS NUMBER MEANS — AND WHAT IT DOES NOT
---------------------------------------------
Read this before quoting an SR from here.

1. **It measures repeat-reliability, not seed-diversity.** The seed currently drives the
   spawn pose, which in an EMPTY world changes almost nothing the controller can see: PX4's
   local frame origin is set wherever the EKF initialises, so a home-relative mission is
   unchanged. Until `P1-04a` seeds the simulator's RNG (sensor noise) via standalone
   Gazebo, ten seeded runs are closer to ten repeats. They are still worth running — flaky
   failures show up under repetition — but the word "seeded" is doing less work than it
   looks.

2. **Runs are not reproducible.** Measured: two back-to-back runs with identical config
   against the same simulator gave waypoint errors [0.225, 0.104, 0.154, 0.204] and
   [0.118, 0.076, 0.158, 0.187]. A failing seed cannot be replayed by re-running it, which
   is exactly why every run keeps its MCAP.

3. **`--reuse` weakens it further.** One stack for all runs: roughly half the wall time,
   but the seed-derived spawn pose is never applied, so the runs differ only by whatever
   the simulator does differently on its own. The report says so on every line, and the
   summary refuses to call itself a full gate run.
"""

from __future__ import annotations

import argparse
import subprocess
import importlib.util
import math
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("run_scenario", REPO / "scripts" / "run_scenario.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def check_run(result: dict, scenario: dict) -> tuple[bool, str]:
    """Decide pass/fail for ONE run, explicitly rather than trusting `outcome`.

    A gate that only reads `outcome` inherits whatever the controller decided to call
    success. These re-derive it from the numbers, so a controller bug that mislabels a
    flyaway as success still fails the gate.
    """
    if result.get("outcome") != "success":
        return False, result.get("failure_reason") or "outcome not success"

    total = result.get("waypoints_total", 0)
    reached = result.get("waypoints_reached", 0)
    if total == 0 or reached != total:
        return False, f"reached {reached}/{total} waypoints"

    radius = float(scenario.get("tolerances", {}).get("accept_radius_m", 1.0))
    errors = result.get("waypoint_errors_m") or []
    if len(errors) != total:
        return False, f"{len(errors)} error samples for {total} waypoints"

    # Reject non-finite errors EXPLICITLY, before any comparison.
    #
    # This is the hole this gate was written to close and originally had: every comparison
    # against NaN is False, so `worst > radius` let a NaN through as a PASS, and `max()`
    # dropped it so the reported worst error was wrong too. The case where the error is
    # UNKNOWN must never be the case that looks clean. `None` is included because the
    # controller now emits JSON null rather than a bare NaN.
    for i, e in enumerate(errors):
        if e is None or not isinstance(e, (int, float)) or not math.isfinite(float(e)):
            return False, f"waypoint {i + 1} error is not a finite number: {e!r}"

    worst = max(float(e) for e in errors)
    if worst > radius:
        return False, f"waypoint error {worst} m exceeds accept radius {radius} m"

    return True, ""


def _origin_void_reason() -> str:
    """Empty string if the EKF origin is sane; otherwise why this run is VOID.

    Shells out to check_ekf_origin.py so there is exactly one implementation of the rule and
    one place it can be wrong. A checker failure (missing script, unexpected crash) is itself
    treated as void rather than as OK -- an unverifiable stack must never read as verified.
    """
    checker = REPO / "scripts" / "check_ekf_origin.py"
    if not checker.is_file():
        return f"EKF-origin checker missing at {checker}; treating the run as VOID"
    # MUST run inside the ros2 service, not here. `ros2` does not exist on the host, so
    # running the checker locally makes EVERY run void and the gate can never pass again --
    # a check that fails closed on its own plumbing is as useless as one that fails open.
    # Piped over stdin rather than assuming the repo is mounted at a known path in the
    # container; `python3 -` still parses the args that follow.
    try:
        p = subprocess.run(
            rs.COMPOSE + ["exec", "-T", "ros2", "bash", "-lc",
                          ". /opt/ros/jazzy/setup.bash && python3 - --quiet"],
            input=checker.read_text(), capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return f"EKF-origin check could not run ({exc}); treating the run as VOID"
    if p.returncode == 0:
        return ""
    return (f"EKF origin not verified (exit {p.returncode}); "
            f"see C-10. This run is VOID, not a failure.")


def score(runs: list[dict], reuse: bool) -> dict:
    """Turn per-run records into the gate's verdict. Pure, so the VOID semantics below are
    testable without a simulator.

    VOID vs FAIL is the whole point (C-10, and P1-08 for Lane A). A run against a stack whose
    EKF origin was mis-initialised did not measure the flight code at all -- the vehicle
    reports an altitude tens of metres wrong and the controller, which targets an absolute
    altitude, is commanded into the ground. Counting that as a failure blames code that is
    byte-identical to the one passing 10/10, and averaging it into a success rate makes the
    rate mean nothing.

    So voids are EXCLUDED from the rate, and separately they BLOCK the criterion. Excluding
    without blocking would let a gate where 9 of 10 runs were void report 100%.
    """
    valid = [r for r in runs if not r.get("void")]
    voids = [r for r in runs if r.get("void")]
    passed = sum(1 for r in valid if r["passed"])
    sr = passed / len(valid) if valid else 0.0
    return {
        "passed": passed,
        "total": len(runs),
        "valid_total": len(valid),
        "voids": len(voids),
        "success_rate": round(sr, 4),
        "sr_perfect": bool(valid) and sr == 1.0,
        # `met` needs a perfect rate over a NON-EMPTY set of valid runs, a real gate run,
        # and no voids at all.
        "met": bool(valid) and sr == 1.0 and not reuse and not voids,
    }


def _worst(errors) -> float:
    """Worst error for reporting — 0 when there is nothing usable, never a silent drop."""
    usable = [float(e) for e in (errors or [])
              if isinstance(e, (int, float)) and math.isfinite(float(e))]
    return max(usable) if usable else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 success-rate gate.")
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--start-seed", type=int, default=1)
    ap.add_argument("--reuse", action="store_true",
                    help="reuse one stack for every run (faster, weaker — see the module "
                         "docstring; the spawn pose is then never applied)")
    ap.add_argument("--outdir", type=Path, default=REPO / "out")
    ap.add_argument("--no-origin-check", action="store_true",
                    help="skip the pre-run EKF-origin assertion (C-10). Only for stacks "
                         "where /fmu/out/vehicle_gps_position is unavailable -- without it a "
                         "mis-ordered stack is scored as a control failure.")
    a = ap.parse_args()

    scenario = rs.load_scenario(a.scenario)
    name = scenario.get("name", "scenario")
    seeds = list(range(a.start_seed, a.start_seed + a.seeds))
    a.outdir.mkdir(parents=True, exist_ok=True)

    print(f"gate     : {name}  ·  {len(seeds)} seeds  ·  "
          f"{'REUSE one stack' if a.reuse else 'restart per run'}")
    print(f"criterion: SR = 100% (every run reaches every waypoint inside the accept radius)")
    print()

    if a.reuse:
        # One stack, seed-independent defaults, left up. No wind either: a single stack
        # cannot carry per-seed wind, which is another reason --reuse is not a gate run.
        print("bringing up a single stack for all runs (no per-seed wind)...")
        rs.restart_stack({"spawn_x": 0.0, "spawn_y": 0.0, "spawn_yaw": 0.0})

    runs, started = [], time.time()
    for i, seed in enumerate(seeds, 1):
        variant = rs.derive_variant(scenario, seed)
        t0 = time.time()
        vdir = ""
        if not a.reuse:
            # Build the per-seed physics overlay and hand it to the stack. Calling
            # restart_stack(variant) alone would run with NO WIND while the report happily
            # printed the seed's wind speed — a gate quietly measuring something other
            # than what it claims.
            vdir = rs.build_variant_overlay(scenario, variant, f"{name}-seed{seed}")
            rs.restart_stack(variant, vdir)
        # Assert the stack is measurable BEFORE flying it. A stale EKF origin makes the
        # vehicle report an altitude tens of metres wrong; the run would look like a control
        # failure and would be indistinguishable from one in the report (C-10).
        void_reason = "" if a.no_origin_check else _origin_void_reason()
        if void_reason:
            result = {"outcome": "void", "failure_reason": void_reason}
            ok, why = False, void_reason
        else:
            try:
                result = rs.run_flight(scenario, seed, a.outdir)
            except Exception as exc:                  # a crashed run is a failed run,
                result = {"outcome": "failure",       # never an aborted gate
                          "failure_reason": f"runner raised: {exc}"}
            ok, why = check_run(result, scenario)
        runs.append({
            "seed": seed, "passed": ok, "reason": why,
            "void": bool(void_reason),
            "waypoint_errors_m": result.get("waypoint_errors_m"),
            "worst_error_m": _worst(result.get("waypoint_errors_m")),
            "spawn_pose_applied": not a.reuse,
            # Ground truth: did an overlay actually get built for this run? The earlier
            # version reported `wind_speed_ms > 0` instead — a field named for the physics
            # that actually echoed a sampled number, and which therefore misreported the
            # exact case the scenario-declares fix exists for (a seed drawing ~0 wind on a
            # scenario that DOES declare wind still gets the overlay). It was also the
            # field used to verify that fix, so the check could not have caught its own
            # failure.
            "overlay_applied": bool(vdir),
            "overlay_dir": vdir,
            "variant": variant,
            "mcap": f"out/{name}-seed{seed}",
            "seconds": round(time.time() - t0, 1),
        })
        print(f"  [{i:>2}/{len(seeds)}] seed {seed:<3} "
              f"{'VOID' if void_reason else ('PASS' if ok else 'FAIL'):4}  "
              f"worst {runs[-1]['worst_error_m']:.3f} m  "
              f"wind {variant.get('wind_speed_ms', 0):.2f} m/s  "
              f"{runs[-1]['seconds']:.0f}s"
              + (f"  — {why}" if not ok else ""))

    verdict = score(runs, a.reuse)
    passed, sr = verdict["passed"], verdict["success_rate"]
    elapsed = round(time.time() - started, 1)

    summary = {
        "scenario": name,
        "seeds": seeds,
        "runs": runs,
        "passed": passed,
        "total": verdict["total"],
        "valid_total": verdict["valid_total"],
        "voids": verdict["voids"],
        "success_rate": sr,
        "criterion": "SR == 1.0 over independent seeded runs, with zero VOID runs",
        # `met` requires BOTH a perfect rate and a real gate run. --reuse never applies the
        # spawn pose, so it cannot satisfy the criterion no matter how green it looks —
        # and a gate that prints "criterion met" next to "not a full gate run" is exactly
        # the kind of artifact that gets quoted without its caveat.
        "met": verdict["met"],
        "sr_perfect": verdict["sr_perfect"],
        "mode": "reuse" if a.reuse else "restart-per-run",
        "wall_seconds": elapsed,
        "caveats": [
            "The seed drives the spawn pose only; in an empty world that changes almost "
            "nothing the controller sees. Sensor noise is not seeded until P1-04a.",
            "Runs are not reproducible: identical config gives different waypoint errors. "
            "A failing seed cannot be replayed — use its MCAP.",
        ] + (["--reuse: one stack for all runs, so the spawn pose was NEVER applied. This "
              "is not a full gate run."] if a.reuse else []),
    }
    out = a.outdir / f"{name}-gate.json"
    out.write_text(json.dumps(summary, indent=2))

    print()
    print(f"  success rate : {passed}/{verdict['valid_total']}  ({sr*100:.0f}%)"
          + (f"   [{verdict['voids']} VOID excluded]" if verdict["voids"] else ""))
    print(f"  wall clock   : {elapsed:.0f}s")
    print(f"  report       : {out}")
    print()
    if summary["met"]:
        print("  PASS — Phase 1 exit criterion met")
    elif verdict["voids"]:
        print(f"  INCONCLUSIVE — {verdict['voids']} run(s) VOID: the stack's EKF origin was")
        print("                 not verified, so those runs did not measure the flight code")
        print("                 at all. Fix the bring-up ordering (scripts/lane_c_up.sh)")
        print("                 and re-run. Voids are excluded from the rate above, never")
        print("                 counted as failures.")
    elif a.reuse and summary["sr_perfect"]:
        print("  INCONCLUSIVE — every run passed, but --reuse never applied the spawn")
        print("                 pose, so this does not satisfy the criterion. Re-run")
        print("                 without --reuse to claim the gate.")
    else:
        print("  FAIL — criterion is SR = 100%")
    return 0 if summary["met"] else 1


if __name__ == "__main__":
    sys.exit(main())
