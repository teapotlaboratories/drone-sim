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
import importlib.util
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

# The GPU-LiDAR drop counter lives in ONE place (scripts/lidar_drops.py) because three callers
# need to agree on what the line says and which container says it -- see that module's header.
_ld_spec = importlib.util.spec_from_file_location(
    "lidar_drops", Path(__file__).resolve().parent / "lidar_drops.py")
ld = importlib.util.module_from_spec(_ld_spec)
_ld_spec.loader.exec_module(ld)
UNREAL, READBACK_DROP = ld.UNREAL, ld.READBACK_DROP


def sh(cmd: list[str], *, env: dict | None = None, timeout: int = 900,
       capture: bool = True) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, env=full_env, timeout=timeout,
                          capture_output=capture, text=True)


def dexec(*args: str) -> list[str]:
    """`docker exec` into the ROS 2 container, argv-style (no shell unless asked for one)."""
    return ["docker", "exec", "-i", ROS2, *args]


RECORD_CHASE = REPO / "scripts" / "record_chase.sh"
APPLY_PARAMS = REPO / "scripts" / "apply_px4_params.py"


def apply_limits(limits: dict, tag: str) -> dict:
    """Put the scenario's flight envelope into PX4 before anything flies.        (SIM-31)

    FATAL ON FAILURE, unlike the chase recording. A capture that fails costs a video; an
    envelope that fails to apply produces a run whose numbers describe a completely different
    aircraft, reported under a scenario name that claims otherwise. That is worse than no run.

    Returns the parameters actually read back, which the caller records as provenance -- the
    APPLIED values, not the requested ones.
    """
    if not limits:
        return {}
    out = REPO / "out" / f"{tag}-limits.json"
    r = sh([sys.executable, str(APPLY_PARAMS), "--limits", json.dumps(limits),
            "--out", str(out)], timeout=300)
    if r.returncode != 0:
        raise RuntimeError("flight envelope not applied:\n" + (r.stdout or "") + (r.stderr or ""))
    print((r.stdout or "").rstrip())
    try:
        return json.loads(out.read_text())
    except Exception:
        return {}


def chase_available() -> bool:
    """Is the renderer running with a screen, so its chase camera can be recorded? (SIM-29)

    PROBED, never assumed. `--no-restart` reuses whatever stack is already up, so the flag this
    run was invoked with says nothing about the stack it is flying against.

    THE CHECK THAT MATTERS IS THE FILESYSTEM SOCKET, and it took two attempts to get there.

    `xdpyinfo` succeeding proves only that SOME X server answers on that number: every
    container here shares one network namespace, X binds an ABSTRACT socket scoped to the
    netns, so the answer may come from another container. Probing it alone on a headless stack
    found QGroundControl's Xvfb and recorded its map view as a chase video.

    Adding `pgrep -x Xvfb` did NOT close that. It proves a server exists here; it does not
    prove it is the one answering. With the renderer on :77, `DISPLAY_NUM=99` passes both --
    the local :77 Xvfb satisfies the pgrep, QGC's :99 satisfies the xdpyinfo -- and the bug is
    back, one stale export away.

    X's OTHER socket, /tmp/.X11-unix/X<N>, lives in this container's filesystem and is
    therefore the only container-local evidence available. The original bug report's own
    diagnostic showed it: that directory was EMPTY in the renderer while :99 answered.
    (SIM-29, review PR 50)
    """
    # The display number is resolved HERE and interpolated into the command. `docker exec` does
    # not forward the caller's environment into the container, so a `${DISPLAY_NUM}` left for
    # the container's shell would expand to empty -- probing `DISPLAY=:` and reporting every
    # display-mode stack as headless.
    num = os.environ.get("DISPLAY_NUM", "77").lstrip(":")
    if not num.isdigit():
        print(f"  chase: DISPLAY_NUM={num!r} is not a display number", file=sys.stderr)
        return False
    checks = [
        # container-local: proves the server is OURS, and on THIS number
        f"test -S /tmp/.X11-unix/X{num}",
        # and that it is a live Xvfb serving it, not a socket a dead one left behind
        f"pgrep -x -a Xvfb | grep -qE ' :{num}( |$)'",
        # and that it actually answers
        f"DISPLAY=:{num} xdpyinfo >/dev/null 2>&1",
    ]
    return all(sh(["docker", "exec", UNREAL, "bash", "-lc", c], timeout=30).returncode == 0
               for c in checks)


def chase(*args: str) -> bool:
    """Drive record_chase.sh, NEVER fatally.

    The chase recording is a convenience, not evidence the gate scores on. A capture that fails
    -- a full disk, a renderer that died, an ffmpeg that would not start -- must not fail a
    flight that otherwise flew, so every call here is advisory and says so on stderr.
    """
    # sh() is subprocess.run(..., timeout=900), which RAISES TimeoutExpired -- and a raise from
    # the `finally` below would abort a flight that had already succeeded, skip the
    # recorder.wait()/kill() under it (orphaning the `ros2 bag record` this try/finally exists
    # to reap) and have run_gate.py score the seed as "runner raised". A capture problem must
    # not fail a flight that flew, which is what this function's first line promises.
    #                                                                          (review, PR 50)
    try:
        r = sh([str(RECORD_CHASE), *args], timeout=900)
    except Exception as exc:
        print(f"  chase: {' '.join(args)} raised (non-fatal): {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return False
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        print(f"  chase: {' '.join(args)} failed (non-fatal): {tail[-1] if tail else '?'}",
              file=sys.stderr)
    return r.returncode == 0


# A healthy landing keeps the Unreal actor and the physics integrator within a few centimetres:
# measured 0.07 m over one landing, 0.1122 m across 40. Anything beyond this is the SIM-27
# divergence rather than RPC sampling skew between two calls.
POSE_SPLIT_M = 0.5


def _max_abs_dz(path_in_container: str) -> float | None:
    """Largest |phys_z - pose_z| the probe saw, or None if it recorded nothing usable."""
    r = sh(dexec("cat", path_in_container), timeout=60)
    if r.returncode != 0:
        return None
    best = None
    for line in (r.stdout or "").splitlines():
        if '"dz"' not in line:
            continue
        try:
            d = abs(json.loads(line)["dz"])
        except Exception:
            continue
        best = d if best is None else max(best, d)
    return best


readback_drops = ld.readback_drops
drops_during = ld.drops_during


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


def restart_stack(variant: dict, scenario: dict, world: str = "", settings: str = "") -> None:
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
    # SIM-31: the scenario may declare a BASE pose, and the seed jitters around it rather than
    # replacing it. Keeping both means a scenario can start on a rooftop, or tilted, without
    # giving up seeded variation -- and a gate over such a scenario still varies what it varied
    # before. z/pitch/roll are not seeded, so they come through untouched.
    base = scenario.get("spawn", {}) or {}
    spawn = ",".join(str(v) for v in (
        round(float(base.get("x", 0.0)) + variant["spawn_x"], 3),
        round(float(base.get("y", 0.0)) + variant["spawn_y"], 3),
        float(base.get("z", 0.0)),
        round(float(base.get("yaw_deg", 0.0)) + math.degrees(variant["spawn_yaw"]), 3),
        float(base.get("pitch_deg", 0.0)),
        float(base.get("roll_deg", 0.0)),
    ))
    # --spawn=VALUE for the same reason sim_up.sh relays it that way: a negative X is common
    # and a bare "-3.656,..." reads as a flag to anything using argparse downstream.
    cmd = [str(SIM_UP), f"--spawn={spawn}"]
    # Where the world sits on Earth. Passed as LAT,LON,ALT because sim_up.sh relays it verbatim
    # to apply_spawn.py, which is the one place that validates it.
    origin = scenario.get("origin_geopoint") or {}
    if origin:
        cmd += [f"--origin={origin['latitude']},{origin['longitude']},"
                f"{origin.get('altitude_m', 0.0)}"]
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
    limits = scenario.get("limits", {}) or {}
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

    # Baseline the renderer's drop counter BEFORE anything flies, so what is reported is this
    # flight's drops and not the container's lifetime total. See readback_drops().
    # BEFORE the recorders start and well before anything arms. A refusal here should cost a
    # bring-up, not a flight that has to be thrown away afterwards.
    applied_limits = apply_limits(limits, tag)

    drops_before = readback_drops()

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
    # CHASE VIDEO, opt-in.                                                          (SIM-29)
    #
    # A SECOND video, and deliberately not a replacement: `watch_video.py` above records what
    # the drone SEES (a vehicle-mounted camera over the AirSim RPC), while this records what the
    # drone DOES -- the chase view, the only one the aircraft itself appears in. They answer
    # different questions, so both are kept.
    #
    # OPT-IN, unlike the RPC video, for two measured reasons: it needs the stack brought up with
    # `sim_up.sh --display`, and at ~63 MB per run a 40-seed gate adds ~2.5 GB on top of the
    # ~2.0 GB of per-seed video out/ already holds.
    chase_on = (os.environ.get("SIM_CHASE_VIDEO", "") in ("1", "true", "yes")
                and chase_available())
    if os.environ.get("SIM_CHASE_VIDEO", "") in ("1", "true", "yes") and not chase_on:
        print("  chase: SIM_CHASE_VIDEO is set but the renderer has no display — "
              "bring the stack up with ./scripts/sim_up.sh --display", file=sys.stderr)
    chase_mp4 = REPO / "out" / f"{tag}-chase.mp4"
    if chase_on and (REPO / "out" / ".chase-recording").exists():
        # Stale state from a previous run that died between start and stop would refuse this
        # one. `stop` is the documented way to clear it and is safe when nothing is recording.
        chase("stop", "--no-distinct")

    # LANDING PROBE, always on.                                                    (SIM-27)
    #
    # The 10-seed gate found a landing that never terminates, and the investigation established
    # something worse than the original guess: AirSim synthesises EVERY sensor PX4 receives --
    # IMU, GPS, barometer, magnetometer, distance, LiDAR -- from one physics integrator state.
    # Verified for the rangefinder specifically: UnrealDistanceSensor::getRayLength traces from
    # `pose.position`, the physics pose, using the actor only to obtain the World. So if that
    # state is wrong, NOTHING downstream can contradict it, and no sensor you could add would.
    #
    # Exactly two things read the Unreal ACTOR instead: the cameras, and simGetVehiclePose
    # (-> PawnSimApi::getUUPosition -> GetActorLocation). That makes the actor-vs-integrator
    # comparison the only cheap independent witness in the stack, which is why it is recorded
    # for every run rather than bolted on after the next failure.
    #
    # ~15 RPC calls/s against a soak that sustained 924/s without incident.
    probe_in_container = f"/out/{tag}-landing.jsonl"
    sh(dexec("rm", "-f", probe_in_container), timeout=60)
    for f in ("probe_landing.py", "airsim_rpc_client.py"):
        sh(["docker", "cp", str(REPO / "scripts" / f), f"{ROS2}:/tmp/{f}"], timeout=60)
    sh(["docker", "exec", "-d", ROS2, "bash", "-lc",
        f"cd /tmp && python3 /tmp/probe_landing.py --out {probe_in_container} "
        f"> /tmp/probe_landing.log 2>&1"], timeout=60)

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
    # STARTED HERE, immediately before the try, and NOT next to the other recorders further up.
    # Between that point and this one sit five `sh()` calls and a Popen, any of which can raise
    # TimeoutExpired on a wedged docker daemon -- and a raise there would escape run_flight with
    # a 60 fps 1080p grab still running for up to MAX_SECONDS. run_gate.py catches that and
    # moves to the next seed, so the orphan would record the NEXT seed's flight into the
    # previous seed's file. Zero gap between start and the finally that stops it.
    #                                                                          (review, PR 50)
    if chase_on:
        chase_on = chase("start", "--out", str(chase_mp4))

    try:
        cmd = dexec("bash", "-lc",
                    "cd /ros2_ws && . install/setup.bash && "
                    "ros2 run control offboard_control --ros-args " + " ".join(args))
        proc = sh(cmd, timeout=600)
    finally:
        sh(dexec("bash", "-lc", "pkill -INT -f '[r]os2 bag record' || true"), timeout=60)
        # The probe stops HERE, not after the happy path. `sh(cmd, timeout=600)` raising
        # TimeoutExpired is precisely the never-terminating-landing case this probe exists to
        # catch, and stopping it outside the finally would leave it polling for its full 1200 s
        # -- overlapping the next seed's flight, and its own probe, under --reuse.
        sh(dexec("bash", "-lc", "pkill -INT -f probe_landing.py || true"), timeout=60)
        # Stopped HERE for the same reason the probe is: a controller timeout must not leave a
        # 60 fps screen grab running into the next seed's flight. --no-distinct because the
        # mpdecimate pass decodes the whole file (~10 s), which across a 40-seed gate is ~7
        # minutes spent on a per-seed number nobody reads.
        if chase_on:
            chase("stop", "--no-distinct")
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

    # HAND THE ARTIFACTS BACK TO THE OPERATOR. sim-ros2 runs as root, so everything written
    # through the /out bind mount -- the bag directory, its metadata, the result JSON, the mp4 --
    # lands root-owned on the host. The operator can read it and cannot delete it: pruning old
    # runs fails with "Permission denied" on a file they appear to own the directory of, and the
    # only ways out are sudo on an immutable host or a throwaway container.
    #
    # Found while pruning out/: gate artifacts were root:root while park-tour artifacts were not,
    # because run_park_tour.sh already does exactly this and this path never did. Same mount,
    # same container, two behaviours -- so the fix belongs here rather than in a cleanup script.
    #
    # Best-effort by design: this is tidying, and a run that flew must not be failed over file
    # ownership. `sh()` is subprocess.run WITHOUT check=True and no caller reads the result, so a
    # chown of a path that never appeared (an absent mp4) is already harmless -- no `|| true`
    # needed, and therefore no shell needed. Argv, exactly like the `rm -rf` of these same three
    # paths above: interpolating them into a shell string would undo the reason that one is argv.
    # (Four paths now -- the probe log joined them -- while the rm -rf above still clears two.)
    sh(dexec("chown", "-R", f"{os.getuid()}:{os.getgid()}",
             bag, result_in_container, video_in_container, probe_in_container), timeout=120)

    # DID A VIDEO ACTUALLY APPEAR? `docker exec -d` reports success whenever the container
    # exists, even when the command cannot run -- the same trap the collision witness already
    # guards against. A missing video must not FAIL the run (it is evidence, not a verdict),
    # but nine videos for ten seeds must not pass unremarked either.
    # DID THE PROBE ACTUALLY RECORD? Checked AFTER the flight and after the probe was stopped --
    # an earlier draft of this ran it moments after launch, where `test -s` passes on two seconds
    # of pre-flight samples and max|dz| is ~0 by construction. A check that runs before the thing
    # it checks is the exact defect this block was added to fix.
    #
    # `docker exec -d` returns 0 whenever the container exists, even for a command that cannot
    # run -- collision_witness.py documents that trap. Without this a whole gate can ship with
    # zero landing artifacts while every run reports success.
    r = sh(dexec("test", "-s", probe_in_container), timeout=30)
    probe_written = (r.returncode == 0)
    if not probe_written:
        print(f"  probe: NO landing data for {tag} — see /tmp/probe_landing.log in {ROS2}",
              flush=True)

    # And READ it. An artifact nobody looks at is not a witness: a run whose probe recorded a
    # 30 m actor/integrator split would otherwise still print PASS with nothing said.
    max_dz = _max_abs_dz(probe_in_container) if probe_written else None
    if max_dz is not None and max_dz > POSE_SPLIT_M:
        print(f"  probe: ACTOR/INTEGRATOR SPLIT during {tag} — max |phys_z - pose_z| "
              f"= {max_dz:.3f} m (healthy landings stay under {POSE_SPLIT_M} m). SIM-27.",
              flush=True)

    video_written = False
    if os.environ.get("SIM_NO_VIDEO", "") not in ("1", "true", "yes"):
        r = sh(dexec("test", "-s", video_in_container), timeout=30)
        video_written = (r.returncode == 0)
        if not video_written:
            print(f"  video: NONE written for {tag} — see /tmp/watch_video.log in {ROS2}",
                  flush=True)

    # How many LiDAR scans the renderer dropped while this flight was in the air. Attached to
    # EVERY return path below, including the failure ones: a run that produced no result is
    # exactly when you want to know whether the renderer was also in trouble.
    drops = drops_during(drops_before, readback_drops())
    if drops > 0:
        print(f"  lidar: {drops} GPU-LiDAR readback drop(s) during {tag} — scans were lost, "
              f"see `{READBACK_DROP}` in {UNREAL}", flush=True)
    elif drops < 0:
        print(f"  lidar: readback drop count UNKNOWN for {tag} — {UNREAL} log unreadable",
              flush=True)

    host_result = REPO / "out" / f"{tag}.json"
    if host_result.exists():
        res = json.loads(host_result.read_text())
        res["video_written"] = video_written
        res["lidar_readback_drops"] = drops
        res["probe_written"] = probe_written
        res["chase_video"] = str(chase_mp4) if (chase_on and chase_mp4.exists()) else None
        res["applied_limits"] = applied_limits or None
        res["max_pose_split_m"] = max_dz
        return res
    # Fall back to the log line, so a missing file does not erase the evidence.
    m = re.search(r"result: (\{.*\})", proc.stdout or "")
    if m:
        res = json.loads(m.group(1))
        res["lidar_readback_drops"] = drops
        res["probe_written"] = probe_written
        res["chase_video"] = str(chase_mp4) if (chase_on and chase_mp4.exists()) else None
        res["applied_limits"] = applied_limits or None
        res["max_pose_split_m"] = max_dz
        return res
    return {"outcome": "failure",
            "failure_reason": "no result produced",
            "lidar_readback_drops": drops,
            "probe_written": probe_written,
            "chase_video": str(chase_mp4) if (chase_on and chase_mp4.exists()) else None,
            "applied_limits": applied_limits or None,
            "max_pose_split_m": max_dz,
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
        restart_stack(variant, scenario, world, a.settings)

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
