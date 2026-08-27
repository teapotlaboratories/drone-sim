# SIM-37 — the sensors were never part of bring-up, and three attempts to put them there

Found while trying to satisfy `SIM-11`'s acceptance criterion 2 — *"`verify_sensors.py` passes
against that world, with re-measured rates"* — which could not be measured at all until this was
fixed by hand.

## The bug, and the shape of its failure

`build_airsim_wrapper.sh` compiled the AirSim ROS 2 wrapper **into a running container** at
`/airsim_root`. That does not survive a teardown, and `sim_up.sh` never rebuilt it. So a freshly
brought-up stack had no `airsim_ros_pkgs`: `perception.launch.py` died with *"package not found"*,
and there were no camera, depth, LiDAR, AirSim-IMU, magnetometer or odometry topics.

**The first measurement reported 9 of 9 checks FAILED, "no messages".** That reads exactly like a
world whose sensors are broken. They were fine; the graph was never running. Reporting it would
have been a clean false negative about CitySample.

It went unnoticed because **nothing in the flight path needs it**. The gate and `run_scenario.py`
use PX4 telemetry over uXRCE-DDS. Every flight in the preceding week — the 40-seed gate, both
CitySample gates — flew with no cameras and no LiDAR.

## Three attempts

**1. `COPY vendor/Cosys-AirSim/...` into the image.** Built, worked, and **broke the goal the
ticket exists to serve**: `vendor/` is gitignored and reconstructed by `vcs import` in quickstart
step 0.2, while images build in step 0.1. On a fresh clone the `COPY` fails with "not found in
build context". I made the image depend on a directory that does not exist yet, in a change whose
stated purpose was "a fresh machine reaches a working stack from the repo alone".

**2. Clone at the pinned SHA.** Failed in `colcon`. `external/rpclib`, `AirLib/deps` and
`AirLib/lib` are not in git.

**3. Clone plus the two downloads.** Chasing (2) produced the actual shape, and it is far smaller
than it looked. **The wrapper does not link the prebuilt `libAirLib.a`** — `CMakeLists.txt:31` does
`add_subdirectory(${AIRSIM_ROOT}/cmake/AirLib)`, so colcon compiles AirLib from source with the
image's gcc, exactly as it already did inside a running container. `AirLib/include`, `AirLib/src`
and `cmake/` are all tracked. Only **two downloads** were missing — rpclib and eigen — and
`setup.sh` fetches them.

So: clone at `a552dd6c`, sparse to the five subtrees the build reads, fetch rpclib 2.3.1 and eigen
3.4.1r from the URLs `setup.sh` names. Fresh clone builds, no ordering rule, no cache, no hidden
state, no second toolchain.

**Cloning also made the pin checkable.** `test "$(git rev-parse HEAD)" = "$COSYS_SHA"`, as the XRCE
block already does. A `COPY` of a working tree carries no `.git`, so the SHA being echoed into
`/etc/drone-sim-versions` asserted a provenance nothing could verify — an operator with local edits
would have shipped an image whose manifest named a commit it did not contain.

## What review caught, and the two that sting

**I claimed more than landed.** *"A stack brought up by `sim_up.sh` alone has the sensor graph"* —
repeated in eight places, and **false**. Nothing starts `airsim_node`. A cold stack has the
**package**; it has zero `/airsim_node/*` topics until someone runs `perception.launch.py`. The
claim that this unblocks recording sensor topics in `record_topics` did not follow either: you
would record empty topics. Corrected everywhere to "installed, still launch it".

**I inserted seven doc notes with a regex and never read the output.** They landed inside a callout
about PX4 image size, between a lead-in and the bullet list it introduced, under a "Known gap"
heading about a different subsystem, and in a file that never mentions the script — so "this
script" had no antecedent. Meanwhile the text they contradicted stayed put, leaving several files
asserting both things at once. All seven reverted and rewritten by hand.

Also: a "Ctrl-C now if that is not what you meant" warning that **never paused** — it printed and
then `rm -rf`'d within a second, so the safeguard destroyed the thing it warned about. It refuses
now unless `FORCE=1`. `WORKDIR` had been left at `/airsim_root`, silently changing the container's
default directory. The fetched rpclib/eigen versions were unasserted, and eigen would have drifted
**silently** where rpclib fails loudly. And the login-shell comment had drifted 100 lines from the
`COPY` it describes.

## Verified

- Image builds **exit 0** from the clone path, with the SHA assertion and both dependency-version
  assertions passing.
- `/etc/drone-sim-versions` carries `cosys-airsim 5.8-v3.4.1 a552dd6c…`, now backed by `rev-parse`.
- **Cold stack, no `build_airsim_wrapper.sh`, no manual source lines: 14 of 14
  `verify_sensors.py` checks pass.** The same sequence gave 9 of 9 FAIL before.
- Image 4.40 → 4.59 GB.
- An end-to-end `run_scenario.py` flight, because `ros-profile.sh` now changes **every login
  shell** — including the bag recorder's and the controller's, which are in the flight path.
  Sensor checks alone would not be evidence for that.

## The rates this unblocked, for the record

`SIM-11` criterion 2, measured on CitySample once the wrapper existed:

| sensor | Blocks | CitySample |
|---|---|---|
| RGB | 31.2 Hz | **10.6 Hz** |
| Depth | 29.6 Hz | **10.5 Hz** |
| GPU-LiDAR | 17.4 Hz | **5.5 Hz** |
| IMU | 366 Hz / 311 distinct | 333 / ~307 |

Perception loses about two thirds under real scene load; the IMU does not care. Every rate this
project had quoted came from an empty grey box.
