#!/usr/bin/env python3
"""Run one seeded scenario against the Lane A stack and emit a structured result (P1-04).

    ./scripts/run_scenario.py scenarios/square-10m.yaml --seed 3

WHY THIS IS A HOST SCRIPT AND NOT A ROS NODE
--------------------------------------------
It orchestrates the *simulator*, not the flight: it restarts the stack with seed-derived
environment, then runs the controller inside it. In-graph metrics belong in
`ros2_ws/src/evaluation/` later; driving `docker compose` does not.

WHAT A SEED MEANS HERE
----------------------
A seed selects a scenario VARIANT. It does NOT reproduce a trajectory. The stack is not
bit-reproducible — measured, not assumed: two back-to-back runs with identical config
against the same simulator gave waypoint errors [0.225, 0.104, 0.154, 0.204] and
[0.118, 0.076, 0.158, 0.187]. A run is therefore evidence about conditions, and only a
success RATE over many seeds is evidence about reliability. That is exactly why the Phase 1
exit criterion is SR over 10 seeded runs rather than one green run.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(REPO / "docker" / "compose.yaml")]


def sh(cmd: list[str], *, env: dict | None = None, timeout: int = 900,
       capture: bool = True) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, env=full_env, timeout=timeout,
                          capture_output=capture, text=True)


# A scenario `name` becomes part of container paths and of an `rm -rf`, so it is
# validated rather than trusted. Today scenarios are repo-local and this is latent; it
# stops being latent in Phase 4, which ingests AerialVLN/OpenFly scenario sets — external
# files driving a delete. A name like "sq; touch /out/PWNED; echo" or "../../opt/px4/build"
# would otherwise be interpolated straight into a shell command.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def load_scenario(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML is required on the host: pip install --user pyyaml")
    with open(path) as fh:
        scenario = yaml.safe_load(fh)
    if not isinstance(scenario, dict):
        sys.exit(f"{path}: scenario must be a mapping")
    name = scenario.get("name", "")
    if not SAFE_NAME.match(str(name)):
        sys.exit(f"{path}: scenario name {name!r} must match {SAFE_NAME.pattern} — it is "
                 "used in filesystem paths and shell commands")
    return scenario


def derive_variant(scenario: dict, seed: int) -> dict:
    """Everything the seed controls, in one place, so it is auditable.

    Uses random.Random(seed) rather than the global RNG: the global one is process-wide
    state that anything else could disturb, which would make "seed 3" mean different things
    in different runs of this script.
    """
    rng = random.Random(seed)
    cfg = scenario.get("seeded", {}) or {}
    xy = float(cfg.get("spawn_xy_jitter_m", 0.0))
    yaw = float(cfg.get("spawn_yaw_jitter_rad", 0.0))
    return {
        "spawn_x": round(rng.uniform(-xy, xy), 3),
        "spawn_y": round(rng.uniform(-xy, xy), 3),
        "spawn_yaw": round(rng.uniform(-yaw, yaw), 4),
    }


def wait_healthy(container: str, timeout_s: int = 240) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = sh(["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
               timeout=30)
        if r.stdout.strip() == "healthy":
            return True
        time.sleep(5)
    return False


def restart_stack(variant: dict) -> None:
    """Recreate the WHOLE stack, never just px4-sitl.

    Every other service joins px4-sitl's network namespace, so recreating it alone leaves
    them attached to a namespace that no longer exists: `ros2 topic list` returns ZERO
    topics against a stack that looks healthy. Verified 2026-07-30 — 0 topics after
    recreating px4-sitl alone, 24 after recreating everything.
    """
    pose = f"{variant['spawn_x']},{variant['spawn_y']},0,0,0,{variant['spawn_yaw']}"
    env = {"PX4_GZ_MODEL_POSE": pose}
    sh(COMPOSE + ["down"], timeout=300)
    sh(COMPOSE + ["up", "-d", "--force-recreate"], env=env, timeout=600)
    for svc in ("lane-a-px4", "lane-a-qgc"):
        if not wait_healthy(svc):
            raise RuntimeError(f"{svc} never became healthy")


def run_flight(scenario: dict, seed: int, outdir: Path) -> dict:
    mission = scenario.get("mission", {})
    tol = scenario.get("tolerances", {})
    flat: list[float] = []
    for wp in mission.get("waypoints_enu", []):
        flat.extend(float(v) for v in wp)

    tag = f"{scenario.get('name', 'scenario')}-seed{seed}"
    result_in_container = f"/out/{tag}.json"

    args = [
        "-p", f"takeoff_altitude:={mission.get('takeoff_altitude_m', 10.0)}",
        "-p", f"accept_radius:={tol.get('accept_radius_m', 1.0)}",
        "-p", f"hold_seconds:={tol.get('hold_seconds', 2.0)}",
        "-p", f"state_timeout_s:={tol.get('state_timeout_s', 60.0)}",
        "-p", f"result_path:={result_in_container}",
    ]
    if flat:
        args += ["-p", "waypoints_enu:=[" + ",".join(str(v) for v in flat) + "]"]

    # MCAP alongside the result (P1-05 in embryo): named by scenario AND seed, so an
    # artifact can always be traced back to the run that produced it.
    bag = f"/out/{tag}"
    # `rm` as argv, with no shell involved at all.
    sh(COMPOSE + ["exec", "-T", "ros2", "rm", "-rf", bag], timeout=60)

    # The recorder needs the ROS environment, so it needs a shell — but the tag goes in
    # through -e and is referenced as "$TAG". The shell then expands a VALUE; it never
    # parses the scenario's text as code.
    recorder = subprocess.Popen(
        COMPOSE + ["exec", "-T", "-e", f"TAG={tag}", "ros2", "bash", "-lc",
                   '. /opt/ros/jazzy/setup.bash && cd /out && '
                   'ros2 bag record -s mcap -o "$TAG" '
                   '/fmu/out/vehicle_local_position /fmu/out/vehicle_status_v1'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)

    # try/finally so a controller timeout cannot leak the recorder into the NEXT run.
    # Without it, `sh()` raising TimeoutExpired skipped the pkill entirely and left an
    # orphaned `ros2 bag record` running — precisely when clean evidence matters most.
    try:
        cmd = COMPOSE + ["exec", "-T", "ros2", "bash", "-lc",
                         "cd /ros2_ws && . install/setup.bash && "
                         "ros2 run control offboard_control --ros-args " + " ".join(args)]
        proc = sh(cmd, timeout=600)
    finally:
        sh(COMPOSE + ["exec", "-T", "ros2", "bash", "-lc",
                      "pkill -INT -f '[r]os2 bag record' || true"], timeout=60)
        try:
            recorder.wait(timeout=60)
        except subprocess.TimeoutExpired:
            recorder.kill()
    time.sleep(2)

    host_result = REPO / "out" / f"{tag}.json"
    if host_result.exists():
        return json.loads(host_result.read_text())
    # Fall back to the log line, so a missing file does not erase the evidence.
    m = re.search(r"result: (\{.*\})", proc.stdout or "")
    if m:
        return json.loads(m.group(1))
    return {"outcome": "failure",
            "failure_reason": "no result produced",
            "stdout_tail": (proc.stdout or "")[-800:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one seeded scenario against Lane A.")
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--outdir", type=Path, default=REPO / "out")
    ap.add_argument("--no-restart", action="store_true",
                    help="reuse the running stack; the spawn pose from the seed is then "
                         "NOT applied, so say so in any result you report")
    a = ap.parse_args()

    scenario = load_scenario(a.scenario)
    variant = derive_variant(scenario, a.seed)
    a.outdir.mkdir(parents=True, exist_ok=True)

    print(f"scenario : {scenario.get('name')}  seed {a.seed}")
    print(f"variant  : spawn ({variant['spawn_x']}, {variant['spawn_y']}) "
          f"yaw {variant['spawn_yaw']}")

    started = time.time()
    if a.no_restart:
        print("stack    : reusing (spawn pose NOT applied)")
    else:
        print("stack    : restarting with seed-derived spawn pose")
        restart_stack(variant)

    result = run_flight(scenario, a.seed, a.outdir)
    result.update({
        "scenario": scenario.get("name"),
        "seed": a.seed,
        "variant": variant,
        "spawn_pose_applied": not a.no_restart,
        "wall_seconds": round(time.time() - started, 1),
    })

    summary = a.outdir / f"{scenario.get('name')}-seed{a.seed}-run.json"
    summary.write_text(json.dumps(result, indent=2))
    print(f"outcome  : {result.get('outcome')}  "
          f"({result.get('waypoints_reached')}/{result.get('waypoints_total')} waypoints, "
          f"{result.get('wall_seconds')}s)")
    print(f"result   : {summary}")
    return 0 if result.get("outcome") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
