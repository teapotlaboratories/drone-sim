#!/usr/bin/env python3
"""Run one seeded scenario against the simulator and emit a structured result.

    ./scripts/run_scenario.py scenarios/square-10m.yaml --seed 3

WHY THIS IS A HOST SCRIPT AND NOT A ROS NODE
--------------------------------------------
It orchestrates the *simulator*, not the flight: it brings the stack up with seed-derived
placement, then runs the controller inside it. In-graph metrics belong in
`ros2_ws/src/evaluation/` later; driving containers does not.

WHAT A SEED MEANS HERE
----------------------
A seed selects a scenario VARIANT. It does NOT reproduce a trajectory. The stack is not
bit-reproducible — measured, not assumed: two back-to-back runs with identical config
gave waypoint errors [0.225, 0.104, 0.154, 0.204] and [0.118, 0.076, 0.158, 0.187]. A run
is therefore evidence about conditions, and only a success RATE over many seeds is
evidence about reliability.

**And right now a seed controls LESS than it used to — say so rather than let a reader
assume otherwise.** The retired Gazebo harness seeded wind and vehicle mass through a
generated world overlay. This simulator has no equivalent yet: environmental diversity
needs Cosys-AirSim's own wind API, which is `SIM-07`. Until that lands, a seed moves the
**spawn pose** and nothing else, so N seeded runs are closer to N repeats. They are still
worth running — flaky failures surface under repetition — but do not describe them as
seeded *conditions*.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIM_UP = REPO / "scripts" / "sim_up.sh"

# The ROS 2 container the bring-up script creates. Kept as a module constant because
# run_gate.py reaches for it too, and two spellings of a container name is exactly the
# drift that turns a healthy stack into "No such container" halfway through a gate.
ROS2 = "sim-ros2"


def sh(cmd: list[str], *, env: dict | None = None, timeout: int = 900,
       capture: bool = True) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, env=full_env, timeout=timeout,
                          capture_output=capture, text=True)


def dexec(*args: str) -> list[str]:
    """`docker exec` into the ROS 2 container, argv-style (no shell unless asked for one)."""
    return ["docker", "exec", "-i", ROS2, *args]


# A scenario `name` becomes part of container paths and of an `rm -rf`, so it is
# validated rather than trusted. Today scenarios are repo-local and this is latent; it
# stops being latent as soon as external scenario sets are ingested — external files
# driving a delete. A name like "sq; touch /out/PWNED; echo" or "../../opt/px4/build"
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
    state that anything else could disturb, which would make "seed 3" mean different
    things in different runs of this script.

    ONLY the spawn pose is returned, because only the spawn pose is applied. An earlier
    version of this function also drew wind and mass; those fed a Gazebo world overlay
    that no longer exists. Returning numbers nothing consumes is worse than returning
    none — the gate printed a wind speed for every run while every run flew in still air.
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


def stack_is_up() -> bool:
    r = sh(["docker", "inspect", "--format", "{{.State.Running}}", ROS2], timeout=30)
    return r.returncode == 0 and r.stdout.strip() == "true"


def resolve_world(scenario: dict, cli_world: str) -> str:
    """The world to fly, from the CLI or the scenario. NO DEFAULT -- one must say.

    The scenario's `world:` field used to be inert: it was declared, and nothing ever read it,
    so `world: default` in square-10m.yaml documented an intention the harness could not honour.
    That matters because the scenario's premise is load-bearing -- square-10m says "an empty
    world, no obstacles by design", and its results mean nothing if it is flown somewhere else.
    A run could silently contradict its own scenario and the report would not mention it.

    There is also no bundled default any more. Falling back to Blocks whenever nobody said
    otherwise is how a gate run ends up describing a world it was never pointed at.
    """
    world = (cli_world or "").strip() or str(scenario.get("world", "") or "").strip()
    if not world:
        sys.exit("no world: give --world PATH.uproject, or set `world:` in the scenario. "
                 "There is deliberately no default -- a run must state the world it flew.")
    if world == "default":
        sys.exit("`world: default` is no longer accepted -- it was never resolved to anything "
                 "and read as a fallback that did not exist. Give a real .uproject path, e.g. "
                 "vendor/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject")
    # Anchor a RELATIVE path to the repo, not to the caller's cwd. The scenario ships one --
    # `vendor/Cosys-AirSim/.../Blocks.uproject` -- and resolving it against cwd made the gate
    # work only when run from the repo root: from anywhere else it reported "world not found"
    # naming a file that plainly exists. Every other path in these scripts anchors to REPO.
    p = Path(world)
    if not p.is_absolute():
        p = (REPO / p).resolve()
    if not p.is_file():
        sys.exit(f"world not found: {world}"
                 + (f" (resolved to {p})" if str(p) != world else ""))
    return str(p)


def restart_stack(variant: dict, world: str = "", settings: str = "") -> None:
    """Cold-start the whole simulator at the seed's spawn pose.

    `sim_up.sh` is the ONLY supported bring-up: it waits for the vehicle to settle before
    PX4 connects and then VERIFIES the EKF origin, restarting PX4 if it came up stale. A
    stack assembled any other way flies or does not depending on container start order,
    and the failure looks exactly like a control bug (SIM-09/SIM-10).

    It blocks until the origin is verified and exits non-zero if it cannot be, so there is
    no separate health-wait here — the script IS the barrier.
    """
    # NED, and the script takes yaw in DEGREES while the variant carries radians. Z stays 0:
    # the vehicle is released at the level's own ground height, which is what every scenario
    # in this repo assumes. Converting in one place, next to the units comment, because a
    # silent radians/degrees mix-up yields a vehicle facing the wrong way and a plausible,
    # wrong, waypoint error.
    spawn = (f"{variant['spawn_x']},{variant['spawn_y']},0,"
             f"{round(math.degrees(variant['spawn_yaw']), 3)}")
    # --spawn=VALUE for the same reason sim_up.sh relays it that way: a negative X is common
    # and a bare "-3.656,..." reads as a flag to anything using argparse downstream.
    cmd = [str(SIM_UP), f"--spawn={spawn}"]
    if world:
        cmd += ["--world", world]
    if settings:
        cmd += ["--settings", settings]
    r = sh(cmd, timeout=1200, capture=False)
    if r.returncode != 0:
        raise RuntimeError(f"sim_up.sh failed (exit {r.returncode}) — stack not flyable")


def run_flight(scenario: dict, seed: int) -> dict:
    """Fly one seeded mission and return its result.

    ARTIFACTS ALWAYS GO TO <repo>/out, and that is not configurable. `sim_up.sh` bind-mounts
    that directory to /out inside the containers, so the bag and the result file are written
    by the container to a path the host cannot choose per run. This function used to accept an
    `outdir` argument and ignore it entirely -- a parameter that promised something the body
    never honoured. Removed rather than wired through: see run_gate.py --outdir, which
    controls the report and says so.
    """
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

    # MCAP alongside the result: named by scenario AND seed, so an artifact can always be
    # traced back to the run that produced it, and recording the topic set the SCENARIO
    # declares rather than a list baked into the harness.
    topics = scenario.get("record_topics") or [
        "/fmu/out/vehicle_local_position", "/fmu/out/vehicle_status_v1",
        "/mission/status", "/mission/result",
    ]
    bad = [t for t in topics if not t.startswith("/") or " " in t]
    if bad:
        raise ValueError(f"record_topics contains invalid topic names: {bad}")
    bag = f"/out/{tag}"
    # Clear BOTH the bag and the result file before the run.
    #
    # Without clearing the result, a flight that never starts is scored from whatever
    # `/out/<tag>.json` a previous run left behind — observed exactly that: a run whose
    # controller died at import reported `success 4/4` from a file written twenty minutes
    # earlier. The gate calls this for every seed, so that is a mechanism for laundering a
    # failure into a pass. `rm` as argv, with no shell involved at all.
    sh(dexec("rm", "-rf", bag, result_in_container), timeout=60)

    # VIDEO, on by default. Started before the flight and stopped after, so the recording
    # brackets it rather than clipping the takeoff -- the same reason the bag does.
    #
    # It records over the AirSim RPC, not from ROS 2 topics, because `airsim_node` is not
    # running during a gate run: sim_up.sh does not start it. Subscribing to camera topics
    # would have recorded nothing at all, silently.
    video_in_container = f"/out/{tag}.mp4"
    if os.environ.get("SIM_NO_VIDEO", "") not in ("1", "true", "yes"):
        sh(dexec("rm", "-f", video_in_container), timeout=60)
        for f in ("watch_video.py", "airsim_rpc_client.py"):
            sh(["docker", "cp", str(REPO / "scripts" / f), f"{ROS2}:/tmp/{f}"], timeout=60)
        sh(["docker", "exec", "-d", ROS2, "bash", "-lc",
            f"cd /tmp && python3 /tmp/watch_video.py --out {video_in_container} "
            f"> /tmp/watch_video.log 2>&1"], timeout=60)
    host_result_path = REPO / "out" / f"{tag}.json"
    host_result_path.unlink(missing_ok=True)

    # The recorder needs the ROS environment, so it needs a shell — but the tag and the
    # topic list go in through -e and are referenced as variables. The shell then expands
    # VALUES; it never parses scenario text as code.
    recorder = subprocess.Popen(
        ["docker", "exec", "-i", "-e", f"TAG={tag}", "-e", f"TOPICS={' '.join(topics)}",
         ROS2, "bash", "-lc",
         '. /opt/ros/jazzy/setup.bash && . /ros2_ws/install/setup.bash && cd /out && '
         'ros2 bag record -s mcap -o "$TAG" $TOPICS'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)

    # try/finally so a controller timeout cannot leak the recorder into the NEXT run.
    # Without it, `sh()` raising TimeoutExpired skipped the pkill entirely and left an
    # orphaned `ros2 bag record` running — precisely when clean evidence matters most.
    try:
        cmd = dexec("bash", "-lc",
                    "cd /ros2_ws && . install/setup.bash && "
                    "ros2 run control offboard_control --ros-args " + " ".join(args))
        proc = sh(cmd, timeout=600)
    finally:
        sh(dexec("bash", "-lc", "pkill -INT -f '[r]os2 bag record' || true"), timeout=60)
        try:
            recorder.wait(timeout=60)
        except subprocess.TimeoutExpired:
            recorder.kill()
    time.sleep(2)

    # Stop the recorder before reading the result, so the file is finalised whichever way the
    # flight ended. SIGINT rather than SIGKILL: the writer must release the mp4 or the file is
    # unplayable, and a video that cannot be opened is worse than no video -- it looks like
    # evidence.
    sh(dexec("bash", "-lc", "pkill -INT -f watch_video.py || true"), timeout=60)
    time.sleep(1.5)

    # DID A VIDEO ACTUALLY APPEAR? `docker exec -d` reports success whenever the container
    # exists, even when the command cannot run -- the same trap the collision witness already
    # guards against. A missing video must not FAIL the run (it is evidence, not a verdict),
    # but nine videos for ten seeds must not pass unremarked either.
    video_written = False
    if os.environ.get("SIM_NO_VIDEO", "") not in ("1", "true", "yes"):
        r = sh(dexec("test", "-s", video_in_container), timeout=30)
        video_written = (r.returncode == 0)
        if not video_written:
            print(f"  video: NONE written for {tag} — see /tmp/watch_video.log in {ROS2}",
                  flush=True)

    host_result = REPO / "out" / f"{tag}.json"
    if host_result.exists():
        res = json.loads(host_result.read_text())
        res["video_written"] = video_written
        return res
    # Fall back to the log line, so a missing file does not erase the evidence.
    m = re.search(r"result: (\{.*\})", proc.stdout or "")
    if m:
        return json.loads(m.group(1))
    return {"outcome": "failure",
            "failure_reason": "no result produced",
            "stdout_tail": (proc.stdout or "")[-800:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one seeded scenario against the simulator.")
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--seed", type=int, required=True)
    # REQUIRED, no default -- same reasoning as run_gate.py. It selects where this run's
    # summary JSON is written and nothing else; the MCAP bag and the controller's result file
    # always land in <repo>/out, the directory sim_up.sh mounts to /out in the containers.
    ap.add_argument("--outdir", type=Path, required=True,
                    help="where to write <scenario>-seed<N>-run.json. The bag and the "
                         "controller result always go to <repo>/out regardless.")
    ap.add_argument("--no-video", action="store_true",
                    help="skip the video (~37 MB). Recording is ON by default.")
    ap.add_argument("--world", default="",
                    help=".uproject to load. Overrides the scenario's `world:`. One of the two "
                         "MUST be set -- there is no default.")
    ap.add_argument("--settings", default="", help="settings.json selecting/tuning sensors")
    ap.add_argument("--no-restart", action="store_true",
                    help="reuse the running stack; the spawn pose from the seed is then "
                         "NOT applied, so say so in any result you report")
    a = ap.parse_args()

    scenario = load_scenario(a.scenario)
    variant = derive_variant(scenario, a.seed)
    if a.no_video:
        os.environ["SIM_NO_VIDEO"] = "1"
    world = resolve_world(scenario, a.world)
    a.outdir.mkdir(parents=True, exist_ok=True)

    print(f"scenario : {scenario.get('name')}  seed {a.seed}")
    print(f"variant  : spawn ({variant['spawn_x']}, {variant['spawn_y']}) "
          f"yaw {variant['spawn_yaw']} rad")

    started = time.time()
    if a.no_restart:
        if not stack_is_up():
            sys.exit(f"--no-restart given but container {ROS2} is not running; "
                     "bring the stack up with ./scripts/sim_up.sh first")
        print("stack    : reusing (spawn pose NOT applied)")
    else:
        print("stack    : cold-starting via sim_up.sh")
        restart_stack(variant, world, a.settings)

    result = run_flight(scenario, a.seed)
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
