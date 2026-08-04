# 2026-08-04 — Three stacks become one, and two defects fall out of the move

**`SIM-18`.** The repo carried three parallel simulator stacks. It now carries one, and that
one has no qualifier in front of it — it is *the simulator*. This is the record of what
moved, what was deliberately left alone, and the two real defects that only became visible
because things moved.

**Headline: the cleanup found more than it removed.** A rename exposed a bring-up that could
never have worked on a fresh machine, and a new check found a production image that had been
flying for three days without ever being written down.

---

## Why now

Two of the three stacks were dead weight with an ongoing cost.

The Isaac Sim path had **never run on this machine** — it SIGSEGVs on driver 610.43.03
against its validated 580.65.06, and no Pegasus release exists for the Isaac 6.0 that avoids
the crash. It was deferred on 2026-07-29 and nothing changed since.

The Gazebo baseline is the more interesting case, because it *worked*. It flew a 10/10 seeded
gate. But by 2026-08-03 its only remaining job was to be a comparison for a stack that had
outgrown it — while its compose file, its container smoke test, its world-overlay generator
and its seeded-wind harness all had to keep building, keep passing CI, and keep being read by
anyone trying to work out which of two flight gates was the real one.

**That is the cost that justified the removal: not disk, but a fork in every document and
every script.**

---

## What moved

| | |
|---|---|
| Deleted | the compose stack · the container smoke test · the world/wind overlay generator and its tests · the demo recorder · the `vlm/` and `vlm_client` placeholders · the Gazebo and Isaac asset stubs |
| Renamed | `sim_up.sh` · `verify_sensors.py` · `measure_sensor_rates.sh` · `record_flight.py` · `perception.launch.py`; images `drone-sim/px4`, `drone-sim/unreal`, `drone-sim/video`; containers `sim-*`; volume `sim-ddc` |
| Rebuilt | `docker/px4.Dockerfile` without Gazebo; `docker/ros2.Dockerfile` without the `/clock` bridge; `docker/video.Dockerfile` as a thin ffmpeg layer |
| Rewired | `run_scenario.py` drives `sim_up.sh` instead of compose; `run_gate.py` keeps its VOID/FAIL scoring and becomes this simulator's gate |
| Archived | the retired backlogs and the four research reports, under `docs/history/`, banner-stamped as frozen |
| Renumbered | `C-NN` → `SIM-NN`, mapping in `docs/history/id-map.md` — git history still says `C-11` and must not be rewritten |

174 tracked files → 163. The naming inventory that drove it was built by enumerating every
occurrence across tracked files and classifying it by kind — file names, image tags, container
names, volume names, shell variables, ROS identifiers, YAML keys, prose — rather than by
running a substitution and reading the diff. That distinction mattered: a global replace would
have rewritten CSS class names, the English word inside "PCIe lanes", and the `gz_advanced_plane`
airframe target, none of which are the thing being renamed.

---

## Defect 1 — the bring-up could not survive a cold shader cache

**Renaming the derived-data-cache volume orphaned the warm cache.** The next cold start was
therefore the first genuinely cold start this stack has ever had, and it failed:

```
[sim] waiting for the vehicle to settle (5 reads within 0.05 m)
[sim] starting XRCE agent, PX4, QGC and the ROS 2 workspace
[sim] waiting for /fmu/out telemetry and a FINITE EKF origin
[sim] FATAL: no finite EKF origin appeared -- /fmu/out silent ... or the EKF never initialised
```

The stack was **fine**. It came up 80 seconds after the script gave up.

```
sim-unreal started            07:47:05
Game Engine Initialized       07:50:24     <- 199 s, compiling shaders
wait_for_fmu budget                120 s   <- 60 iterations x 2 s
```

**Why it had been invisible.** Answering AirSim RPC — which is what the settle-wait polls —
happens early, well before Unreal has finished initialising. With a warm cache the gap is
~30 s and nothing notices. Every previous run on this machine inherited a warm cache, so the
120 s budget had never been tested against the case it exists for.

**And that case is exactly the reproducibility goal.** A fresh machine *always* has a cold
cache. The bring-up as written could not have worked on one, on the first try, ever.

**Fix: wait for the event, not for a number of seconds.** A separate `wait_for_sim_link`
polls PX4's own log for `Simulator connected on TCP port 4560`, with a 900 s budget, progress
output every 60 s, and an early abort if the renderer container dies — so a long shader
compile reads as progress rather than as a hang. It runs *before* the origin wait, because
the two fail for different reasons and one combined timeout cannot tell them apart.

Migrating an existing cache instead of recompiling is one command:

```bash
docker run --rm -v <old-volume>:/from -v sim-ddc:/to alpine cp -a /from/. /to/
```

---

## Defect 2 — a production image that was never written down

Deleting the compose file removed a tier-1 CI step: `docker compose config`, which had been
catching references to things that do not exist. The class of defect did not go away with the
file, so it was replaced by `scripts/check_image_refs.py` — every `drone-sim/...` reference
anywhere in the tree must name an image declared under `images:` in `versions.lock`.

**On its first run it failed, and it was right.**

```
FAIL: 15 reference(s) to an image this repo does not build:
  scripts/sim_up.sh:147: drone-sim/unreal:ue5.8 is not declared under images: in versions.lock
  docker/unreal.Dockerfile:106: drone-sim/unreal:ue5.8 ...
```

The Unreal engine image — 57.5 GB, the renderer, the container every other container joins
for its network and IPC namespaces — **had been built and flown for three days without an
entry in the lock.** Not a stale entry: no entry. `versions.lock` calls itself the authority
on what this project builds, and it had never mentioned the largest thing it builds.

Two false positives from the first run are worth recording, because both are in the check now:

- a filesystem path `…/projects/drone-sim/venvs/vllm` read as an image called
  `drone-sim/venvs` — fixed with a negative lookbehind, so a reference cannot be the tail of
  a longer path;
- `drone-sim/airsim-client.` at the end of a prose sentence swallowed the full stop.

---

## The Gazebo strip, measured rather than assumed

`docker/px4.Dockerfile` now runs `Tools/setup/ubuntu.sh --no-sim-tools` and explicitly
reinstates the five packages that flag drops which are *not* Gazebo (`bc`, `libeigen3-dev`,
`protobuf-compiler`, `pkg-config`, `libxml2-utils`).

```
with Gazebo      11.6 GB
without Gazebo   11.0 GB      <- 600 MB, ~5%
```

**That is a smaller win than the argument for doing it implied**, and worth writing down as
such: the case for removing Gazebo from this image is that the renderer is Unreal and nothing
here ever loads a `gz` world, not that it saves meaningful disk. It cost four image rebuilds.

The build now *asserts* the strip — `! dpkg -s gz-harmonic && ! command -v gz` — because an
apt install elsewhere in the chain could pull it back in silently. NuttX is deliberately kept:
real Pixhawk 6C firmware is flashed from that tree, and `--no-nuttx` would slim the image
while quietly removing that capability.

`ros-gz-bridge` went from the ROS 2 image at the same time. It had no consumer — `/clock`
comes from the simulator, remapped in `perception.launch.py` — and with it went
`gz_transport_vendor`, whose `libgz-transport13` shadowed any system Gazebo the moment ROS
was sourced.

---

## Two things deliberately NOT done

**The worklogs are frozen.** They keep their original wording and their original filenames,
including the retired terminology. They are dated records of what was actually done, and a
worklog edited to match a later decision stops being evidence. One consequence to know:
`docker/px4.Dockerfile:14` cites a worklog whose *filename* still carries the old scheme, and
that link is correct — it is the only occurrence of the old vocabulary left in any active
file, and it must not be "fixed".

**Wind and vehicle mass are no longer seeded.** They came from a generated Gazebo world
overlay that no longer exists. A seed now moves the spawn pose and nothing else, and
`run_scenario.py` says so at the top rather than reporting a wind speed nothing applies —
the previous gate printed one for every run while every run flew in still air. Restoring real
environmental diversity needs Cosys-AirSim's own wind API and belongs to `SIM-07`.

**So do not describe a gate run from this simulator as covering varied conditions.** It
covers repetition. That is still worth having — flaky failures surface under repetition — but
it is a weaker claim than the retired harness could make, and the honest move is to say so
rather than inherit the old wording.

---

## Verified by running it

A clean build proves nothing about flight, so the pivot was checked by flying it.

**Cold bring-up**, exercising both the new link wait and the pre-existing origin repair loop —
and the repair loop earned its keep on a genuinely stale origin:

```
[sim] renderer link up after 10s
VOID: EKF origin is STALE: ref_alt 114.168 m vs GPS 123.284 m = 9.116 m apart (tolerance 1 m)
[sim] origin stale; restarting PX4 (attempt 1/2)
OK:   EKF origin sane: ref_alt 123.284 m vs GPS 123.284 m = 0.000 m apart
[sim] stack up and origin verified -- safe to fly
```

**The example mission**, flown end to end over ROS 2 with the wrapper rebuilt from the three
recorded patches:

```
leg 1  target [ 25.0,   0.0, -8.0]   err 1.517 m   settled 0.093 m   11.7 s   ok
leg 2  target [  0.0,  25.0, -8.0]   err 1.856 m   settled 73.603 m   9.5 s   ok
leg 3  target [-25.0,   0.0, -8.0]   err 1.507 m   settled 0.256 m   16.0 s   ok
leg 4  target [  0.0, -25.0, -8.0]   err 1.045 m   settled 0.144 m   14.4 s   ok
leg 5  target [  0.0,   0.0, -8.0]   err 1.420 m   settled 0.298 m   14.4 s   ok
landed: true      worst 1.856 m against a 2.0 m tolerance      MCAP 3.0 GB
```

Tier-1 CI passes all eight checks.

### One observation left open rather than explained away

**Leg 2 reports a settled error of 73.603 m.** Its arrival error was 1.856 m, and legs 1, 3, 4
and 5 all settle inside 0.3 m. Leg 3 then arrives correctly at `[-25.2, 0.15]`, so whatever
happened did not derail the mission.

`park_tour.py` and `offboard_control.py` are **functionally unchanged** by this work — the
only edits are a task ID and two documentation paths — so this is not something the pivot
introduced. It is either a bad settle sample or a real excursion between waypoints, and the
MCAP for the run is kept.

**It is recorded here undiagnosed on purpose.** The mission passed on its stated criterion and
it would have been easy to quote "5/5 legs, worst 1.856 m" and move on; a number in the
summary that disagrees with the other four by two orders of magnitude is exactly the kind of
thing this project has learned costs a day later.

---

## The review found two real defects in this work, and one of them I had just written

Run against the PR before merge. Both are in code this change introduced.

**1. `grep -q` on a pipe, under `pipefail` — the same trap, twice in one session.**
`wait_for_sim_link` detected the link with `docker logs sim-px4 2>&1 | grep -q '...'`.
`grep -q` exits on its first match and closes the pipe; `docker logs` then takes SIGPIPE and
the pipeline returns 141. Under `set -o pipefail` the `if` therefore reads **false at exactly
the moment the match succeeds** — but only when PX4 has printed enough after the match to
still be writing. So the link is never detected, the bring-up burns the full 900 s, and it
fails with "PX4 never connected" on a stack that connected fine.

It passed twice in testing because the log happened to be small enough both times. **A
timing-dependent failure that passes your verification run is worse than one that always
fails.** Fixed by capturing to a variable and matching with `case` — no pipe at all.

The same defect had been caught and fixed in `docker/video.Dockerfile` earlier the same day,
where `ffmpeg -encoders | grep -q libx264` failed the image build with exit 141. Knowing the
trap did not stop me writing it again eighty lines later.

**2. Moving the harness off compose silently deleted an artifact assertion.** The retired
compose stack wrote `/ros2_ws/.build-ok` only after proving
`install/control/lib/control/offboard_control` existed and `import drone_interfaces.msg`
worked, and gated a healthcheck on it. `sim_up.sh` echoes `BUILD_OK` and **nothing reads it**.

Nothing downstream could catch the gap either: `wait_for_fmu` and `verify_origin` both source
`/ros2_ws/install/setup.bash`, which the image already ships populated with `px4_msgs` — so
both pass on the base image alone. The script would print **"safe to fly"** over a container
with no `control` package in it, the gate would exec `ros2 run control offboard_control`, get
`Package 'control' not found`, write no result, and score the seed as a **flight failure** —
for every seed, with the compiler output already discarded to `/dev/null`.

A build error reported as a control defect is exactly the failure shape this project keeps
paying for: the EKF-origin bug presented the same way for a full day.

Fixed with a `wait_for_workspace` barrier that asserts both artifacts before writing the
marker, and surfaces the build tail on timeout. **Verified by breaking the code it guards** —
a syntax error in `control/setup.py` now yields:

```
Failed   <<< control [0.09s, exited with code 1]
[sim] FATAL: the ROS 2 workspace did not build within 300s. Do NOT score runs on this
             stack -- a missing 'control' package presents as a flight failure, not a
             build failure.
```

Eight lower-severity findings were also fixed: a `versions.lock` pointer in three
`settings.json` files that the section rename had broken (`simulator.px4` → `renderer.px4`),
`check_image_refs.py` splitting `git ls-files` on whitespace instead of `-z` (fail-open on any
path with a space), the Gazebo pin that both new `versions.lock` headers *claimed* had moved
to `retired:` but had not, `autopilot.px4` still describing a `gz_x500` build and a `GZ_IP`
launch env, and four stale comments naming the deleted compose stack.

**Worth noting about the review itself:** 38 candidate findings, 20 refuted on inspection.
Several of the refutations were the useful part — "this is byte-identical to `main`" and "this
describes pre-existing behaviour" kept real-looking findings out of the fix list.

---

## What this changes for anyone working here

- The bring-up is `./scripts/sim_up.sh`. There is no compose file, and there never was one for
  this stack.
- The backlog is `docs/todo.md`, IDs are `SIM-NN`, and git history still says `C-NN` — the map
  is `docs/history/id-map.md`.
- The retired work is under `docs/history/`, frozen and banner-stamped. It is kept because the
  measurements that retired those stacks are the reason not to repeat them.
- `docs/conventions.md` was *promoted* out of the archive rather than filed with it. Six source
  files cite it by path, and it is still the frozen contract for the ROS 2 graph.
