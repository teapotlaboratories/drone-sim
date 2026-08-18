#!/usr/bin/env python3
"""The simulator's flight gate: success rate over N seeded runs (SIM-07).

    ./scripts/run_gate.py scenarios/square-10m.yaml            # 10 seeds, restarting each
    ./scripts/run_gate.py scenarios/square-10m.yaml --reuse    # faster, weaker (see below)

Exits non-zero unless every run succeeds. That is the point: the criterion is
SR = 100% over 10 seeded runs, and "a single green run is not a pass".

WHAT THIS NUMBER MEANS — AND WHAT IT DOES NOT
---------------------------------------------
Read this before quoting an SR from here.

1. **It measures repeat-reliability, not seed-diversity.** The seed drives the spawn pose
   and nothing else, which in an empty world changes almost nothing the controller can
   see: PX4's local frame origin is set wherever the EKF initialises, so a home-relative
   mission is unchanged. Ten seeded runs are therefore closer to ten repeats. They are
   still worth running — flaky failures show up under repetition — but the word "seeded"
   is doing less work than it looks.

   **Environmental diversity is genuinely absent, not merely weak.** The retired Gazebo
   harness varied wind and vehicle mass through a generated world overlay; the equivalent
   here is Cosys-AirSim's own wind API and is not wired up. Do not describe a run from
   this gate as covering varied conditions until it is.

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
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Imported, not re-declared: two copies of "which number means stale" is exactly the kind of
# drift that makes a void look like a pass.
_ekf_spec = importlib.util.spec_from_file_location(
    "check_ekf_origin", REPO / "scripts" / "check_ekf_origin.py")
_ekf = importlib.util.module_from_spec(_ekf_spec)
_ekf_spec.loader.exec_module(_ekf)
ORIGIN_STALE = _ekf.VOID_STALE
ORIGIN_UNKNOWN = _ekf.VOID_UNKNOWN

_spec = importlib.util.spec_from_file_location("run_scenario", REPO / "scripts" / "run_scenario.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

# The witness plumbing lives in ONE place now (scripts/collision_witness.py). It used to exist
# here in Python and again in bash inside run_park_tour.sh, and the two copies disagreed about
# what an unreadable witness means -- this one failed the run, that one passed it.
_cw_spec = importlib.util.spec_from_file_location(
    "collision_witness", Path(__file__).resolve().parent / "collision_witness.py")
cw = importlib.util.module_from_spec(_cw_spec)
_cw_spec.loader.exec_module(cw)


def check_run(result: dict, scenario: dict, collisions: int = 0,
              collision_detail: str = "") -> tuple[bool, str]:
    """Decide pass/fail for ONE run, explicitly rather than trusting `outcome`.

    A gate that only reads `outcome` inherits whatever the controller decided to call
    success. These re-derive it from the numbers, so a controller bug that mislabels a
    flyaway as success still fails the gate.
    """
    # Collisions FIRST. Every number below describes where the vehicle ended up, and after an
    # impact those numbers describe a crash that happened to land near the waypoint.
    if collisions > 0:
        return False, collision_detail or f"{collisions} collision(s)"
    if collisions < 0:
        return False, collision_detail or "collision state unknown"

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


# How long to keep waiting for the EKF to establish an origin after a stack restart, and
# how often to re-ask. sim_up.sh returns once it has verified an origin, but the gate must not
# assume that: --reuse skips the restart entirely, and a stack brought up by hand has had no
# such verification. PX4 can be running with ref_alt still NaN for several seconds.
ORIGIN_WAIT_S = 90
ORIGIN_POLL_S = 3


def _run_origin_check() -> int:
    """Run check_ekf_origin.py inside the ROS 2 container. Returns its exit code.

    MUST run in the container, not here: `ros2` does not exist on the gate host, so running
    the checker locally would make EVERY run void and the gate could never pass again -- a
    check that fails closed on its own plumbing disables a gate as surely as one that fails
    open. Piped over stdin rather than assuming the repo is mounted at a known path.

    Returns -1 for "could not even run the checker", which the caller treats as void.
    """
    checker = REPO / "scripts" / "check_ekf_origin.py"
    if not checker.is_file():
        return -1
    try:
        p = subprocess.run(
            rs.dexec("bash", "-lc",
                     ". /opt/ros/jazzy/setup.bash && python3 - --quiet"),
            input=checker.read_text(), capture_output=True, text=True, timeout=120)
    except Exception:
        return -1
    return p.returncode


def _origin_void_reason() -> str:
    """Empty string if the EKF origin is sane; otherwise why this run is VOID.

    WAITS for an origin before judging, which the first version did not.

    A stack can be up while PX4 still publishes `ref_alt` as NaN -- the EKF has not yet
    established an origin. Checking immediately therefore
    races the estimator: the checker correctly reports VOID_UNKNOWN, the seed is voided, and
    because ANY void blocks the criterion, one slow start turns the whole gate INCONCLUSIVE.
    A 10-seed run passed 10/10 with zero voids before this wait existed -- by timing
    coincidence, not by construction, and it would flake on a slower box or a heavier
    scenario. `sim_up.sh` already got this right with `wait_for_fmu`.

    The two void codes are treated DIFFERENTLY, which is the whole reason they are distinct:

      VOID_UNKNOWN (3)  no origin yet -> KEEP WAITING. Transient by definition.
      VOID_STALE   (2)  origin exists and disagrees with GPS -> VOID IMMEDIATELY. Waiting
                        cannot help; an EKF origin is set once, so it will never re-settle.
    """
    deadline = time.time() + ORIGIN_WAIT_S
    last = None
    while True:
        rc = _run_origin_check()
        if rc == 0:
            return ""
        last = rc
        if rc == ORIGIN_STALE:
            return ("EKF origin is STALE -- it disagrees with GPS, and an EKF origin is set "
                    "once, so waiting cannot fix it. Restart PX4 after the sim has settled "
                    "(scripts/sim_up.sh does this). This run is VOID, not a failure.")
        if time.time() >= deadline:
            break
        time.sleep(ORIGIN_POLL_S)
    if last == -1:
        return ("EKF-origin check could not run at all; an unverifiable stack must never "
                "read as verified. This run is VOID, not a failure.")
    return (f"no EKF origin appeared within {ORIGIN_WAIT_S}s of the stack reporting healthy "
            f"(last exit {last}). This run is VOID, not a failure.")


def score(runs: list[dict], reuse: bool) -> dict:
    """Turn per-run records into the gate's verdict. Pure, so the VOID semantics below are
    testable without a simulator.

    VOID vs FAIL is the whole point (SIM-10). A run against a stack whose
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


def _drops_total(runs) -> int:
    """Total GPU-LiDAR scans lost across the gate.  (SIM-24)

    Counts only runs that actually reported a number. `None` means the run never got far enough
    to ask, and -1 means the renderer log could not be read — neither is zero, and summing them
    as zero would report a clean total for a gate that measured nothing. Those runs are counted
    separately as `lidar_readback_drops_unknown_runs`.
    """
    return sum(r["lidar_readback_drops"] for r in runs
               if isinstance(r.get("lidar_readback_drops"), int)
               and r["lidar_readback_drops"] > 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="The simulator's success-rate flight gate.")
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--start-seed", type=int, default=1)
    ap.add_argument("--reuse", action="store_true",
                    help="reuse one stack for every run (faster, weaker — see the module "
                         "docstring; the spawn pose is then never applied)")
    # REQUIRED, and deliberately without a default. It controls where the gate REPORT is
    # written and NOTHING else -- the per-seed MCAP bags and result JSON always land in
    # <repo>/out, because that is the directory sim_up.sh bind-mounts to /out inside the
    # containers. A default here would imply the two travel together. They do not.
    ap.add_argument("--outdir", type=Path, required=True,
                    help="where to write <scenario>-gate.json. Per-seed bags and results are "
                         "NOT affected: they always go to <repo>/out, the path mounted into "
                         "the containers.")
    ap.add_argument("--world", default="", help=".uproject to load (default: bundled Blocks)")
    ap.add_argument("--settings", default="", help="settings.json selecting/tuning sensors")
    ap.add_argument("--no-video", action="store_true",
                    help="skip the per-seed VEHICLE-camera video (~37 MB each). ON by default.")
    # CHASE IS ON BY DEFAULT, because hard stop 5 requires it on every flight test and the
    # vehicle camera cannot satisfy it -- a camera bolted to the aircraft can never show the
    # aircraft. This gate recorded 40 vehicle-camera videos and zero chase videos before
    # SIM-34, which is the failure mode the rule exists to prevent, produced by the tool meant
    # to enforce it. Opt out explicitly and the run says so; do not opt out silently.
    ap.add_argument("--no-chase", action="store_true",
                    help="skip the chase-camera video. It is ON by default because hard stop 5 "
                         "requires it; the vehicle camera cannot substitute for it. Costs ~63 MB "
                         "per seed on Blocks and ~290 MB on CitySample, and forces the renderer "
                         "onto an Xvfb display.")
    ap.add_argument("--no-origin-check", action="store_true",
                    help="skip the pre-run EKF-origin assertion (SIM-10). Only for stacks "
                         "where /fmu/out/vehicle_gps_position is unavailable -- without it a "
                         "mis-ordered stack is scored as a control failure.")
    a = ap.parse_args()

    scenario = rs.load_scenario(a.scenario)
    name = scenario.get("name", "scenario")
    seeds = list(range(a.start_seed, a.start_seed + a.seeds))
    # Resolve the world BEFORE anything is brought up: a wrong path should fail in a second,
    # not after the first stack restart.
    world = rs.resolve_world(scenario, a.world)
    # run_flight has no argparse of its own, so the flag is relayed through the environment it
    # already reads. Set here rather than in the caller's shell so --no-video works the same
    # way whether the gate was invoked by hand or by run_local_ci.sh.
    if a.no_video:
        os.environ["SIM_NO_VIDEO"] = "1"
    # Set BEFORE any bring-up: restart_stack reads this to decide whether the renderer needs a
    # display, so setting it later would give the first seed a stack that cannot record.
    if not a.no_chase:
        os.environ["SIM_CHASE_VIDEO"] = "1"
    else:
        os.environ.pop("SIM_CHASE_VIDEO", None)
        print("chase    : DISABLED by --no-chase -- this run does not satisfy the "
              "chase-camera rule, and its results should say so")
    a.outdir.mkdir(parents=True, exist_ok=True)

    print(f"gate     : {name}  ·  {len(seeds)} seeds  ·  "
          f"{'REUSE one stack' if a.reuse else 'restart per run'}")
    print(f"criterion: SR = 100% (every run reaches every waypoint inside the accept radius)")
    print()

    if a.reuse:
        # One stack, seed-independent defaults, left up. The spawn pose is never applied,
        # which is the whole reason --reuse is not a gate run.
        # "the seed's jitter", not "the spawn pose": a scenario declaring `spawn:` DOES get its
        # base pose and origin applied to this single stack now.               (review, PR 53)
        print("bringing up a single stack for all runs (the seed's pose jitter is never applied)...")
        rs.restart_stack({"spawn_x": 0.0, "spawn_y": 0.0, "spawn_yaw": 0.0},
                         world, a.settings, scenario=scenario)

    runs, started = [], time.time()
    for i, seed in enumerate(seeds, 1):
        variant = rs.derive_variant(scenario, seed)
        t0 = time.time()
        if not a.reuse:
            rs.restart_stack(variant, world, a.settings, scenario=scenario)
        # Assert the stack is measurable BEFORE flying it. A stale EKF origin makes the
        # vehicle report an altitude tens of metres wrong; the run would look like a control
        # failure and would be indistinguishable from one in the report (SIM-10).
        void_reason = "" if a.no_origin_check else _origin_void_reason()
        if void_reason:
            result = {"outcome": "void", "failure_reason": void_reason}
            ok, why = False, void_reason
            ncol, cdetail = 0, ""      # never flew; no collision claim to make
        else:
            witness = cw.start()
            try:
                result = rs.run_flight(scenario, seed, world, stack_restarted=not a.reuse)
            except rs.EnvelopeError as exc:
                # Stop the observer before leaving. It was started with `docker exec -d`, so
                # exiting here would leave watch_collisions.py running in the sim container with
                # no gate report to explain it.                                (review, PR 53)
                if witness:
                    try:
                        cw.stop_and_score()
                    except Exception:
                        pass
                # NOT a flight failure. The scenario's envelope could not be applied, so the
                # aircraft was never given the configuration this gate claims to be testing.
                # Scoring it would report "SR 0%, control failure" for a harness fault.
                #                                                              (review, PR 53)
                sys.exit(f"\nABORTING THE GATE: {exc}\n"
                         "No run is scored -- this is a configuration fault, not a flight result.")
            except Exception as exc:                  # a crashed run is a failed run,
                result = {"outcome": "failure",       # never an aborted gate
                          "failure_reason": f"runner raised: {exc}"}
            if witness:
                ncol, cdetail = cw.stop_and_score(
                    a.outdir / f"{name}-seed{seed}-collisions.json")
            else:
                ncol, cdetail = -1, "collision witness failed to start"
            ok, why = check_run(result, scenario, ncol, cdetail)
        runs.append({
            "seed": seed, "passed": ok, "reason": why,
            "collisions": ncol if not void_reason else None,
            "video_written": result.get("video_written"),
            # SIM-29: --no-distinct exists specifically for this path, so the mp4s are produced
            # by a gate run -- and were then unreferenced by the only report anyone reads for
            # one. An artifact nobody can find is not evidence.               (review, PR 50)
            "chase_video": result.get("chase_video"),
            "void": bool(void_reason),
            "waypoint_errors_m": result.get("waypoint_errors_m"),
            "worst_error_m": _worst(result.get("waypoint_errors_m")),
            "spawn_pose_applied": not a.reuse,
            "variant": variant,
            "mcap": f"out/{name}-seed{seed}",
            # GPU-LiDAR scans lost to an empty readback during this run (SIM-24). Recorded, and
            # deliberately NOT scored -- see the note where the totals are printed below.
            "lidar_readback_drops": result.get("lidar_readback_drops"),
            # SIM-27. Largest actor-vs-integrator gap the probe saw. Healthy landings stay under
            # ~0.11 m (measured over 40); a real split would be metres. Recorded, not scored --
            # same reasoning as the drop count, and nothing has ever exceeded it.
            "max_pose_split_m": result.get("max_pose_split_m"),
            "probe_written": result.get("probe_written"),
            "seconds": round(time.time() - t0, 1),
        })
        drops = runs[-1]["lidar_readback_drops"]
        print(f"  [{i:>2}/{len(seeds)}] seed {seed:<3} "
              f"{'VOID' if void_reason else ('PASS' if ok else 'FAIL'):4}  "
              f"worst {runs[-1]['worst_error_m']:.3f} m  "
              f"{runs[-1]['seconds']:.0f}s"
              + (f"  lidar-drops {drops}" if drops else "")
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
        # SIM-24. Recorded so a re-score is possible later; see the print below for why it is
        # not part of the verdict.
        "lidar_readback_drops_total": _drops_total(runs),
        "max_pose_split_m": max((r["max_pose_split_m"] for r in runs
                                 if isinstance(r.get("max_pose_split_m"), (int, float))),
                                default=None),
        "runs_without_probe_data": sum(1 for r in runs if r.get("probe_written") is False),
        "lidar_readback_drops_unknown_runs": sum(
            1 for r in runs if r.get("lidar_readback_drops") is not None
            and r["lidar_readback_drops"] < 0),
        "wall_seconds": elapsed,
        "caveats": [
            "The seed drives the spawn pose only; in an empty world that changes almost "
            "nothing the controller sees. Wind and sensor noise are NOT seeded — the "
            "simulator's wind API is not wired up, so every run flew in still air.",
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
    # SIM-24. REPORTED, NOT SCORED, and that is a deliberate choice rather than an omission.
    #
    # This gate's criterion is flight control -- waypoint tracking. A lost LiDAR scan does not
    # make the tracking wrong, so failing a run over one would be scoring a dimension the
    # criterion does not claim. VOIDing is also wrong for the same reason: the run DID measure
    # what it says it measured.
    #
    # And any threshold would be invented rather than measured. The condition has never been
    # observed occurring naturally -- zero drops across a 90-minute soak with 45 flights -- so
    # there is no evidence for where "a few" ends and "the sensor is dead" begins. Printing the
    # number is what the evidence currently supports; picking a cutoff is not.
    total_drops = summary["lidar_readback_drops_total"]
    unknown = summary["lidar_readback_drops_unknown_runs"]
    if total_drops:
        print(f"  lidar drops  : {total_drops} across {len(runs)} run(s) — scans were LOST. "
              f"Not scored (this gate measures flight control), but the LiDAR data in those "
              f"MCAPs is incomplete.")
    if unknown:
        print(f"  lidar drops  : UNKNOWN for {unknown} run(s) — the renderer log was unreadable")
    split = summary["max_pose_split_m"]
    if split is not None:
        print(f"  pose split   : max |phys_z - pose_z| {split:.3f} m across {len(runs)} run(s)"
              + ("   <<< SIM-27, investigate" if split > rs.POSE_SPLIT_M else ""))
    if summary["runs_without_probe_data"]:
        print(f"  pose split   : NO probe data for "
              f"{summary['runs_without_probe_data']} run(s) — not measured, not clean")
    print(f"  report       : {out}")
    print()
    if summary["met"]:
        print("  PASS — flight gate criterion met")
    elif verdict["voids"]:
        print(f"  INCONCLUSIVE — {verdict['voids']} run(s) VOID: the stack's EKF origin was")
        print("                 not verified, so those runs did not measure the flight code")
        print("                 at all. Fix the bring-up ordering (scripts/sim_up.sh)")
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
