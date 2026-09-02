# SIM-45 — a web interface, over ROS 2 rather than beside it

**Date:** 2026-09-01. New work, requested by the owner after three site-survey failures in a row
(`SIM-44`'s tree, the parked car, the takeoff corridor) were each diagnosed by teleporting a
disarmed vehicle and photographing it through a throwaway script.

Written as I go. Nothing has been flown.

## What the existing stack decides for you

Five facts, read out of the code before anything was designed:

1. **`sim_up.sh` publishes no ports.** In the default `NET_MODE=shared` the renderer owns a
   private network namespace and every other container joins it (`sim_up.sh:485-560`). A browser
   on the host can reach nothing at all today. Ports have to be published on `sim-unreal`,
   because it is the namespace owner.
2. **Teardown removes exactly five container names** (`sim_up.sh:252`). A sixth container is the
   failure this ticket explicitly warns about, so the whole feature has to be processes inside
   `sim-ros2`.
3. **The chase camera is not a ROS topic and cannot be fetched over RPC.**
   `AirSimCameraDirector` has no binding and `simGetImages` serves vehicle-mounted cameras only —
   `record_chase.sh` documents both, and reads the view off the Xvfb screen instead.
4. **The vehicle cameras are already JPEG.** `compressed_image_transport` is baked into
   `drone-sim/ros2` and measured at 15.1 Hz / 32.9 KB (q95) and 17.9 Hz / 12.8 KB (q70). But
   `airsim_node` is not started by `sim_up.sh`, so those topics do not exist on a default
   bring-up.
5. **`offboard_control` is a fire-once mission machine.** Every state carries a timeout and the
   node exits at `DONE`. There is no state in which it holds a hover waiting for a human — which
   is the entire difference between a gate run and hand-flying.

## The transport decision, and the one it cost

I put two options to the owner. A purpose-built HTTP+SSE server with a hard-coded two-command
allowlist would have needed no new dependency and would have been narrow by construction. The
owner chose **`rosbridge_suite`**, which is the right call against this project's primary rule —
reuse upstream, don't reinvent — and moves the cost from "write a transport" to "make a
deliberately general bridge obey hard stop 1".

That trade is the interesting part of this ticket, so it is written down rather than assumed
away. See *Making a general bridge safe* below.

## Verifying the three things the new design depends on

None of this was taken from memory.

**Availability.** Checked in a throwaway `drone-sim/ros2:v1.16.0` container:

```
ros-jazzy-rosbridge-suite     2.7.0-1noble.20260615.164124
ros-jazzy-web-video-server    3.1.0-1noble.20260615.150732
ffmpeg                        7:6.1.1-3ubuntu5
```

**The publish allowlist is real.** `rosbridge_library/capabilities/publish.py` checks
`topics_pub_glob` **before** it creates the topic registration and returns silently on no match.
`topics_pub_glob` and `topics_sub_glob` are separate parameters. Two traps read out of
`rosbridge_websocket.py` at the same time, both of which would have produced a page that looked
locked down and was not:

- the globs are **strings containing a list literal** (`"['/mission/command']"`), not string
  arrays;
- `parse_glob_string("")` returns `None`, and `None` means **no checking at all**. An
  unset glob is an open bridge, not a closed one.

**The chase camera can be a ROS topic after all.** An X server binds an *abstract* unix socket,
and Linux scopes abstract sockets to the **network namespace** — which every container in this
stack shares. This repo has already paid for that fact once: `sim_up.sh:75` records `:99`
resolving to QGroundControl's Xvfb and the chase recorder cheerfully filming a map view.

Probed with two throwaway containers sharing one netns, which is exactly `sim-ros2`'s position
relative to the renderer:

```
filesystem socket /tmp/.X11-unix/X77 visible from the second container : False
abstract socket   @/tmp/.X11-unix/X77 from the second container        : CONNECTED
ffmpeg -f x11grab -i :77 -frames:v 5 -f mpjpeg                         : 5 JPEG frames, 8270 B
```

So the renderer's chase view can be **published into the graph** by a node in `sim-ros2`, after
which it is indistinguishable from any other camera as far as the browser is concerned — and the
browser talks to nothing but ROS 2, which is what the owner asked for.

One detail worth keeping: the probe logged `Cannot get the image data ... major_code:130`
(MIT-SHM) and fell back to a plain read. That is because the probe shared only the *network*
namespace. The real stack shares IPC too (`--ipc container:`), so the shared-memory path is
available there. The fallback works either way, which is the useful half.

`record_chase.sh` is **not** touched. An earlier sketch had it grow a `stream` subcommand and
share its hard-won X-readiness checks; publishing a topic instead removes that risk from a script
the gate depends on.

## Making a general bridge safe

Hard stop 1 says commanding the real aircraft needs per-run human approval, and a browser button
is the opposite of that. Four measures, in the order they take effect:

1. `topics_pub_glob:="['/mission/command']"` — the browser can publish that topic and no other.
   `/fmu/in/vehicle_command` is unreachable.
2. `services_glob:="[]"` and `actions_glob:="[]"` — otherwise the page could call any node's
   `set_parameters` and retune `takeoff_altitude` mid-flight. rosbridge appends `/rosapi/*` to a
   non-`None` services glob on its own, so roslibjs still resolves topic types.
3. `address` bound to loopback. Under `NET_MODE=host` the container namespace *is* the host's,
   so this is the only thing keeping the buttons off the VPN — the same exposure `sim_up.sh:104`
   records for MAVLink port 14540.
4. **A proof-of-sim interlock in `offboard_control`, not in the transport.** Manual mode refuses
   to start, and refuses each command, unless the AirSim RPC answers. It is a *positive* proof
   that a simulator is present rather than an absence-of-hardware check, and at that position it
   guards every route to `/mission/command` rather than only the browser one.

Measure 4 is the only AirSim RPC call in the feature. It is read-only, and it is an interlock
rather than a control or telemetry path.

## What was built

21 files added, 11 changed. The shape that matters:

```
browser ──:8080 page ──┐
        ──:9090 rosbridge ── /mission/command ──▶ offboard_control manual:=true
        ──:8181 web_video_server ──┬── /chase/image/compressed  ← chase_camera (x11grab :77)
                                   └── /airsim_node/…/image/compressed  ← airsim_node
```

**`offboard_control` gained a manual mode, not a sibling.** A `manual` parameter, default
false, adds two states — `IDLE` (on the ground, disarmed, nothing streaming) and `HOVER`
(holding the takeoff setpoint) — and one subscription. TAKE OFF walks
`IDLE → STREAM_SETPOINTS → REQUEST_OFFBOARD → ARM → TAKEOFF → HOVER` through the existing
`_do_*` handlers unchanged; LAND walks `HOVER → LAND → IDLE`. `WAYPOINTS` is never entered.
Every existing caller gets byte-identical behaviour.

Three decisions inside it worth recording:

- **`IDLE` and `HOVER` are exempt from the per-state timeout, and nothing else is.** The
  timeout exists so a controller waiting on an `arming_state` that never arrives cannot hang
  a CI job for its whole budget. Applied to a state that waits on a *human*, the same
  mechanism fails an aircraft for doing exactly what was asked.
- **A hand-flying session is many sorties.** In mission mode `DONE`/`FAILED` are terminal and
  the node exits, which is what the gate needs. In manual mode the terminal `MissionStatus`
  and the `MissionResult` are published first — so each sortie leaves the same evidence a
  mission run does — and *then* the machine recycles to `IDLE`. A node that exited after the
  first landing would leave a page whose buttons silently do nothing.
- **The command is validated in the callback and applied in the timer.** Mutating `state`
  from the executor thread would race every handler in the file, and the symptom would be an
  aircraft that changed state halfway through a tick.

**The policy is not in the state machine.** `control/manual_policy.py` imports nothing, so
`tests/` asserts the table the node actually runs rather than a re-expression of it — which
is the convention `test_park_tour.py` documents as a deliberate trade for arithmetic, and
which is not good enough for the table that decides whether a browser may arm an aircraft.
`offboard_control` maps the strings onto its `State` enum at import and raises if any name
stops resolving, so drift is a start-up crash rather than a button that quietly stops
working. The same argument moved the rosbridge allowlist into `webui/allowlist.py` and the
MJPEG framing into `webui/mjpeg.py`: `webui.launch.py` and `chase_camera.py` cannot be
imported without a ROS 2 environment, and a test that needs the target can only ever pass on
the target.

**Ports.** `sim_up.sh` published nothing at all before this. `sim-unreal` — the namespace
owner — now publishes `127.0.0.1:8080`, `:9090` and `:8181`, in the `shared` branch only.
Docker can only publish at container creation, so making it conditional would mean deciding
at bring-up whether the operator might later want to fly by hand, and getting it wrong costs
a teardown and restart of a 57 GB renderer. Nothing listens until `web_ui.sh` starts. Under
`NET_MODE=host` no port is published — the combination is invalid — and the bind address does
the work instead.

## Verification so far, and what is still owed

Off-target: **32 new tests, all passing**, and the full local CI tier 1 is green.

- the transition table, including a standing property rather than an example: any state that
  can arm must also be able to stop
- the interlock refuses a closed port, refuses a server that answers with something that is
  not a msgpack-RPC reply (**failing open is the entire risk**), accepts a well-formed one,
  and never raises — it is called from a timer callback
- the allowlist: one publishable topic, no wildcard in it, the glob rendered in the string
  form rosbridge actually parses, services and actions closed, loopback default — plus a test
  that the launch file still *reads* the shared module rather than having re-inlined its own
  copy, which would leave every other assertion passing while describing nothing
- the MJPEG splitter: multiple frames per read, a partial tail kept and completed across
  reads, leading noise discarded, and a lone trailing `0xFF` retained because it may be the
  first byte of the next `SOI` — dropping it would lose one frame per read, forever, at a
  rate nothing would flag

## Then it was run, and seven things were wrong

The off-target tests were green and the design was right. Everything below was found by
bringing the stack up, and **none of it is the kind of defect a test in this repo could have
caught** — every one is a claim about the target.

### 1. An `ldd` assertion that failed because ROS was not sourced

The image build stopped on my own artifact check. `ldd` reported **three** libraries "not
found" for a perfectly healthy `web_video_server` — `librclcpp` and friends — because a
Dockerfile `RUN` gets `bash -o pipefail -c`, not a login shell, so `/etc/profile.d` never runs.

What makes this worth writing down is how it was missed: I had checked that binary by hand
first, with `docker exec … bash -lc`, which **does** source ROS and reported 0. That is the
exact login-vs-non-login trap `docker/ros-profile.sh` exists for, and it was in the same file
I was editing. The fix is one `source` line, matching what the `MicroXRCEAgent` block ten
layers above already does. **Verify in the shell the thing actually runs in.**

### 2. A missing `setup.cfg`, and a launch that could not find its own executable

```
package 'webui' found at '/ros2_ws/install/webui', but libexec directory
'/ros2_ws/install/webui/lib/webui' does not exist
```

`colcon build` reported **success** and finished in 2.84 s. The console script had gone to
`install/webui/bin/chase_camera` instead of `install/webui/lib/webui/chase_camera`, because
ament_python packages carry a `setup.cfg` that redirects `install_scripts`, and the new
package did not have one. Every other package in the tree does.

### 3. `pgrep -f offboard_control` matched a comment

`web_ui.sh start` refused to start the controller — *"offboard_control is already running"* —
on a stack where no controller had ever run. `pgrep -f` was matching **PID 44**, the sim-ros2
entrypoint: one enormous `bash -lc` whose text contains `offboard_control` twice, once in a
comment and once in the `[ -x /ros2_ws/install/control/lib/control/offboard_control ]` build
assertion.

`CLAUDE.md` warns about the two neighbours of this — `pgrep -f` matching the asking shell, and
`pgrep -x` silently seeing nothing for names over 15 characters (`offboard_control` is 16). This
is the third member of the family: **a liveness check that reads a comment as a process.** It
fails in the "already running" direction, so it presented as a warning rather than a crash.

Liveness is now asked of the graph — `ros2 node list` — which cannot match a comment.

### 4. The chase camera published at 9.99 Hz and the browser got 22 bytes

`ros2 topic hz /chase/image/compressed` → **9.991 Hz**. The HTTP stream for the same topic →
**22 bytes, zero frames**: the multipart boundary and nothing else.

I had published the chase camera **BEST_EFFORT**, reasoning that for video the newest frame is
the only interesting one. The reasoning is fine; the setting was wrong, because the *consumer*
decides. `web_video_server` subscribes RELIABLE, and a RELIABLE subscriber matches **nothing**
from a BEST_EFFORT publisher.

This is the **mirror** of the trap `docs/quickstart.md` already documents for `/fmu/out/*`,
where PX4 publishes BEST_EFFORT and a default RELIABLE subscription sees silence against a
completely healthy stack. Same incompatibility, opposite direction — and no node warns about
it. `airsim_node` publishes its compressed images RELIABLE, which is why the drone camera
worked from the first attempt and only the one I wrote did not.

After the change: **120 frames of 1920x1080 in 10 s**, and the first frame decodes.

### 5. `/fmu/out/sensor_gps` does not exist

`SensorGps` is the message **type**; PX4 v1.16 advertises it on
**`/fmu/out/vehicle_gps_position`**. I had used the type name as the topic name.

The page showed em dashes for fix and satellites, which is precisely the designed behaviour for
a field that never arrives — and is how this was caught rather than shipped. Had the page
rendered a plausible `0` instead, a survey tool would have been reporting *no GPS fix* on a
stack with 15 satellites and a 3-D lock. `eph` was added while fixing it.

### 6. SIGTERM does not run `destroy_node`, so ffmpeg outlived its node

`web_ui.sh stop` needed a SIGKILL escalation, and an `x11grab` survived it. rclpy installs a
handler for **SIGINT only**; SIGTERM takes the default action and kills the interpreter
outright, so `ChaseCamera.destroy_node()` — whose whole job is terminating the ffmpeg it
spawned — never ran. An orphaned grabber holds the renderer's X connection, so the next
`start` would find the display taken.

### 7. A zombie is not a running process, and `pgrep` cannot tell

With SIGINT fixed, `stop` still escalated on `chase_camera`. Everything here is started with
`docker exec -d`, whose shell exits without waiting, so an exited node lingers as `<defunct>`
until PID 1 reaps it — and `pgrep` matches it. The teardown was reporting a clean shutdown as
a stubborn one and signalling a corpse. Filtered on `ps -eo stat=` now.

Also worth noting: the first `stop` printed *"chase camera up"* about a process that was
already dead, because `ros2 node list` lagged the exits by several seconds. `stop` now waits
for the graph to agree before printing a verdict.

## What was measured, on the stack

| | |
|---|---|
| image | 4.59 → **4.68 GB** (+90 MB), all four artifact assertions pass |
| bring-up | **91 s**, EKF origin **0.000 m** from GPS (tolerance 1 m) |
| chase camera | topic **9.99 Hz**; through `web_video_server` **120 frames / 10 s** at 1920x1080 |
| drone camera | topic 5.9 Hz; **63 frames / 10 s**, first frame 118,281 B, well-formed |
| roslibjs | served at sha256 `3c510df2…`, byte-identical to the pin |
| telemetry | **9 of 9 topics, every field the page reads present** |
| allowlist | publish to `/fmu/in/vehicle_command` → **0 bytes on the topic** |
| sortie 1 | 12.0 m commanded → **11.98 m**, held 12 s at 12.01 m, landed in 19 s |
| sortie 2 | 6.0 m commanded → **6.14 m**, landed in 11 s — same node, no restart |

### The allowlist, proven rather than asserted

Driven over the websocket exactly as the page does. rosbridge's own log:

```
[WARN] [rosbridge_websocket]: [Client b3d84b72-...] No match found for topic,
       cancelling publish to: /fmu/in/vehicle_command
```

and `ros2 topic echo /fmu/in/vehicle_command`, running throughout, captured **zero bytes**. The
payload used was `command: 512` (`REQUEST_MESSAGE`), not an arm — the claim under test is that
the **topic** is unreachable regardless of payload, and proving that with a live arm command
would be proving it the reckless way.

The same session published `hold` to `/mission/command`, which arrived:

```
[INFO] [offboard_control]: command received: hold
[WARN] [offboard_control]: refused hold in state idle; allowed from ['hover']
```

Both halves of the design in two lines: the allowed topic reaches the node, and the state
policy refuses the command with a reason the operator can read.

### The interlock, and the sortie recycle

```
[INFO] [offboard_control]: manual mode: SITL interlock satisfied -- AirSim RPC server version 4
[INFO] [offboard_control]: FCU alive; home ENU=(-0.00, -0.00) waypoints=[]
[INFO] [offboard_control]: state: wait_for_fcu -> idle
```

`waypoints=[]` is the manual-mode assertion holding: no mission is built, so a bag from a
hand-flown sortie cannot later be misread as a 4-waypoint run that reached none of them. Each
sortie published its own result and recycled:

```
result: {"outcome": "success", ..., "square_side_m": null, "mission_source": "manual"}
manual: sortie ended (done) -- idle
```

### What the chase camera showed on the first frame

The point of the whole ticket, on frame one: the aircraft on a CitySample pavement, a parked
car alongside, bollards to the right — and a **pedestrian standing directly over it**, with the
engine's own overlay reading `Collision#211 with BP_CrowdCharacter_C_7`. The world origin is on
a crowd walking route.

That is the class of fact the last three failures were made of, and it took one glance instead
of a throwaway script and an afternoon. Recorded: `out/sim45-handfly-chase.mp4` (138.5 s, 8309
frames grabbed, 1312 distinct).

Reproduce it:

```bash
./scripts/sim_up.sh --display --world assets/CitySample/CitySample.uproject
./scripts/web_ui.sh start        # then open http://127.0.0.1:8080
./scripts/web_ui.sh stop
./scripts/sim_up.sh --down
```

## The gate: 2/3, and the failing seed is not this ticket's

`citysample-gate1`, 3 seeds, run because this change touched `offboard_control` and
`sim_up.sh` and nothing else in the repo would catch a regression there.

```
  [ 1/3] seed 1   PASS  worst 0.842 m  208s
  [ 2/3] seed 2   FAIL  worst 0.828 m  358s  — timeout in state land
  [ 3/3] seed 3   PASS  worst 0.835 m  188s

  success rate : 2/3  (67%)          FAIL — criterion is SR = 100%
```

**The gate is red, and it was red before this work.** Seed 2 is the parked car the handoff of
2026-09-01 already names: *"seed 2 of the last gate bounced against a car in a parking bay,
physics reporting a steady 1.9 m/s descent while the position oscillated ±0.1 m, so PX4 never
registered touchdown and LAND timed out."*

That is not taken on trust. Three independent things say so:

**1. The flight was perfect; only the landing was not.** Seed 2 reached 2 of 2 waypoints at
0.761 m and 0.828 m — indistinguishable from the two seeds that passed. `mission_source` in its
result is `"scenario"`, not `"manual"`, and `manual:=true` appears **zero** times in the gate
log: the new mode was never entered.

**2. The landing trace is the documented fault, measured.** The final 26 s of seed 2:

```
actor z      : 0.322 .. 0.345 m   — a 0.023 m band. THE VEHICLE IS NOT MOVING.
reported vz  : +1.76 m/s          — a steady descent that never terminates
final actor z: 0.345 m            — resting on something, and not on the ground
```

The actor is stationary on a surface while the integrator keeps reporting a descent, so every
sensor PX4 receives describes a fall that is not happening, touchdown never registers, and
`LAND` runs out its 180 s budget. That is `SIM-27`'s shape exactly.

**3. The same seed fails the same way on the code from before this ticket.** Rather than argue
that the manual-mode branches are inert in mission mode, `offboard_control.py` was reverted to
`HEAD` — byte-identical, zero occurrences of `manual` — and seed 2 flown again:

| | verdict | worst | landing trace |
|---|---|---|---|
| with `SIM-45` | FAIL, `timeout in state land`, 358 s | 0.828 m | z range **0.023 m**, descent **+1.76 m/s**, final z 0.345 m |
| `HEAD`, pre-change | FAIL, `timeout in state land`, 334 s | 0.847 m | z range **0.026 m**, descent **+1.73 m/s**, final z 0.364 m |

Same failure, same signature, same seed, on code that has never heard of this feature. **The
gate did not move.**

It is still red, and that is the site problem the handoff left open — the survey the tree hunt
never did, pointed DOWN instead of up. Which is, exactly, what this ticket built the tool for.

## Teardown

```
  containers (sim-*)               none running
  pgrep -x UnrealEditor            none
  Xvfb on :77                      none
  GPU compute apps                 none holding memory
  teardown verified
```

Confirmed independently afterwards: `docker ps -a` lists no `sim-*` container and
`nvidia-smi` reports no compute apps.

## Where this leaves `SIM-45`

Done and verified end to end: the page, both cameras, the telemetry, the allowlist, the
interlock, two hand-flown sorties, and a gate that is unchanged. **41 off-target tests**, nine
of them written after the live run to pin the defects it exposed — the `ldd`-without-ROS
ordering, the missing `setup.cfg`, the `pgrep`-matches-a-comment trap, the chase camera's
reliability, the GPS topic name, SIGINT-versus-SIGTERM, and the zombie.

Not done, and deliberately out of scope: manual translation and yaw, waypoint editing,
recording from the browser, a map. And the thing the gate is still waiting on — a downward
site survey of the landing point, which is now a browser tab rather than an afternoon.

---

# SIM-46 — the same day: it becomes a ground station

The owner's response to `SIM-45` landing was *"lets make it its own container, faithful to the
real thing"*. That is a fidelity argument, and it settles a design question rather than
reopening one: **on real hardware the ROS 2 graph runs on the companion computer and a web page
that flies the aircraft does not — it runs on someone's laptop.** `sim-qgc` has modelled that
boundary since the Gazebo era; this is its sibling.

It is a safety improvement too. `rosbridge_suite` is a *general* ROS-to-websocket bridge, and
hosting one on the companion computer is what hard stop 1 is about.

## What moved, and the one thing that could not

| | `SIM-45` | `SIM-46` |
|---|---|---|
| rosbridge, `web_video_server`, the page | `sim-ros2` | **`sim-webui`** |
| `chase_camera` + ffmpeg | `sim-ros2` | **stays** |
| `offboard_control` + the SITL interlock | `sim-ros2` | **stays**, unchanged |

**The chase camera cannot move, and the reason is the interesting half.** The mechanical
reason is that it reads the renderer's screen through an X abstract socket scoped to the
network namespace. The real reason is that **a real aircraft has no chase camera**. It is
simulator scaffolding, in the same family as ground truth, and a ground station that could
produce it would be *less* faithful, not more.

So the split is two packages, physical rather than conventional: `webui` (the page, its launch,
the allowlist — **no Python nodes at all**) and `chase_camera` (the node and `mjpeg.py`). Not
`perception/`, whose README says plainly it is "a sketch, not a package" held for cuVSLAM and
nvblox.

## `drone-sim/webui:v1.16.0`, 2.56 GB

`ros-base` rather than `desktop`, plus rosbridge_suite and web_video_server. `px4_msgs` arrives
by `COPY --from=drone-sim/ros2:v1.16.0` — **the same built artifact, not merely the same pinned
SHA**. `versions.lock` calls version coupling the architecture; two independent builds of one
SHA satisfy that nominally, one shared build satisfies it literally, and it avoids a second
15-minute message build. Multi-stage `COPY --from` is already house style.

The build asserts the ground station has no path to the simulator. **One of those assertions
was wrong and the build refused to let it through**, which is the assertion working:

> `ffmpeg` is a hard `Depends:` of `ros-jazzy-web-video-server` and cannot be removed without
> removing the server this image exists to run.

The claim was narrowed to what is true — no AirSim RPC client, no msgpack, no vendored
Cosys-AirSim, no chase camera — and the boundary now rests on *there being no node here that
opens a display*, which is the thing that was actually load-bearing. A claim narrowed to what
is true is worth more than a stronger one that has to be forced.

## The container list was the trap, and it was named in advance

`sim_up.sh` held the list **twice**: `teardown()` removed them, and the verifier greped a
separately written list. Adding a sixth container to one and not the other gives the worst
outcome the script has — a container left running under a teardown that printed "verified",
which is the failure the hard stops record. It is now one `STACK_CONTAINERS` array, both
consumers derive from it, and `tests/test_stack_containers.py` asserts that nothing else in the
file names three or more containers.

## Four things the split broke, all found by running it

**1. The ground-station image had no compiler.** `drone_interfaces` is a `rosidl` package built
from the repo at bring-up — the same source both sides build, so the interface contract cannot
drift — and `ros-base` ships no gcc or make:

```
CMake Error: CMAKE_C_COMPILER not set, after EnableLanguage
```

The container came up with `px4_msgs` working (24 `/fmu/out` topics visible) and
`MissionCommand` missing, so the browser could have read everything and published nothing.

**2. `pkill -f chase_camera` would have signalled the uXRCE-DDS agent's supervisor.** This is
the `SIM-45` trap returning through a door I opened myself: moving the package put the string
`chase_camera` into `sim_up.sh`'s sim-ros2 entrypoint (the `for p in interfaces control bringup
chase_camera` copy loop), so `pgrep -f`/`pkill -f` matches **PID 44**. The `SIM-45` version only
*misreported*; this one would have SIGINT'd the shell supervising the agent, taking every
`/fmu/*` topic down with it. Caught by the test written for the first occurrence, which is the
best argument for having written it. The patterns are now
`chase_camera.launch.py` and `lib/chase_camera/chase_camera`, neither of which appears in
`sim_up.sh`, and the test asserts that.

**3. The page requested every camera with percent-encoded slashes.** The one that took longest,
because three separate wrong theories came first.

`app.js` built its stream URL with `encodeURIComponent(topic)`, turning `/chase/image` into
`%2Fchase%2Fimage`. **`web_video_server` does not url-decode that parameter** — it hands the raw
string to the ROS name validator:

```
Invalid topic name: topic name must not contain characters other than
alphanumerics, '_', '~', '{', or '}':  '%2Fchase%2Fimage'
```

And it fails **quietly**: HTTP 200, the multipart boundary written, connection closed. 22 bytes.
The browser sees a successful response and an `<img>` that never decodes, so the pane is empty —
indistinguishable from "no camera is publishing". What finally separated them was the same
topic answering **22 bytes to the page and 100 frames / 10 s to curl**, because curl had been
sending raw slashes all along. Slashes are legal in a query value (RFC 3986 §3.4), so the fix is
to leave them raw rather than to work around anything.

**The three theories that were wrong first**, recorded because the pattern is the useful part:

| theory | why it was believed | why it was wrong |
|---|---|---|
| a headless-Chrome artifact | `--virtual-time-budget` really does cut off endless streams | the panes stayed empty under real time and `--headless=new` too |
| the `load` event never fires for a multipart stream | plausible, and browsers genuinely differ | an `<img>` with the *unencoded* URL reported 1920x1080 immediately |
| `display:none` prevents Chrome decoding the stream | also plausible | fixed it, `display` became `block`, `naturalWidth` stayed 0 |

Two of those produced changes that were kept anyway — polling `naturalWidth` is more robust
than trusting `load` on a stream that never ends, and a pane that is always displayed is one
less thing to reason about. But neither was the bug, and saying so is the point: **three
confident explanations in a row, none of them measured, before a server log settled it in one
line.** `SIM-27`'s worklog records the same shape.

**4. `/snapshot` is broken for these topics** (empty reply, `Removed Stream` 0.4 s later) while
`/stream` is fine. Not chased down; noted because it sent one of the diagnostics above off in
the wrong direction, and because a future change that switches endpoints will meet it.

## Verified on the stack

Bring-up **79 s**, EKF origin 0.000 m from GPS. Process placement, read straight from `ps`:

```
sim-webui  (ground station)      rosbridge_websocket · web_video_server · http.server
sim-ros2   (companion computer)  offboard_control · chase_camera · airsim_node · MicroXRCEAgent
```

- **9 of 9 telemetry topics**, every field the page reads, subscribed from the ground station
- both video streams, the chase camera published on the **companion** and served to the browser
  by the **ground station** — crossing the boundary over DDS exactly as it would from a laptop
- the publish allowlist still holds from its new home: `/fmu/in/vehicle_command` → **0 bytes on
  the topic**, `No match found for topic, cancelling publish` in the ground station's log
- **one hand sortie, flown from the ground station**: 12.0 m commanded → **12.00 m**, 0.03 m to
  target, held 30 s, landed and recycled to `idle` in 19 s. `mission_source: "manual"`,
  outcome success.

The flight driver had to move to `sim-webui` mid-run, because `tornado` left `sim-ros2` with
rosbridge — which is the split asserting itself: commands originate at the ground station now.

Screenshots: `out/sim46-webui-hover.png` (hovering — TAKE OFF greyed, LAND and HOLD live, ARMED
/ OFFBOARD(14) / in air) and `out/sim46-webui-idle.png`, where the drone's own camera is pointed
directly at a pedestrian's legs. Chase recording: `out/sim46-handfly-chase.mp4`, 110 s.

Teardown verified: no containers, no `Xvfb` on `:77`, no GPU compute apps.

---

# SIM-47 — the drone you can actually move

The owner asked one question — *"where is the button to control the drone movement?"* — and
there wasn't one. `SIM-45` shipped TAKE OFF, LAND and HOLD and recorded "manual translation and
yaw" as out of scope, because the ticket asked for take-off and land *"at minimum"* and the
minimum is what got built.

**That was the wrong call, and the question is the evidence.** The interface exists because
three failures came down to *where the aircraft was put*. What it did was rise vertically from
the spawn, hover, and descend onto the same spot. It inspected one column of air. **It surveyed
the one place you already know about.**

## The leash was the work, not the movement

Movement itself is nearly free: `HOVER` already holds `target_enu` and `_tick` already streams
it at 20 Hz, so moving is nudging that target. No new state, no second control path.

What needed thought is that an unbounded delta from a browser is how an aircraft flies into a
building nobody could see. Four bounds, all ROS parameters, **enforced in the node and never in
the page** — the same argument that put the SITL interlock at the thing that arms rather than in
the transport: `ros2 topic pub` does not run the page.

| bound | default | stops |
|---|---|---|
| `move_step_max_m` | 5.0 | one command teleporting the hold point across the map |
| `move_radius_max_m` | 50.0 | drifting far from where the operator watched it take off |
| `move_alt_min_m` / `max` | 2.0 / 60.0 | descending into the ground, climbing out of sight |
| `move_yaw_step_max_rad` | π/4 | a spin |

**Clamped, not refused**, and every clamp is logged with what was asked and what was granted. A
refusal at the boundary makes a held key do nothing with no explanation; a clamp gives the
operator a fence they can feel.

Two details that are decisions rather than defaults:

- **Order: step, then altitude, then radius.** Step first because it bounds the *request* — a
  10 km delta must not reach the radius check and be "clamped" into a legal but wildly
  unintended place. Radius last because it must be the final word on where the aircraft ends up.
- **The nudge is applied to the hold point, not to the vehicle's present position.** Measuring
  from where the aircraft happens to be lets a burst of commands accumulate tracking error, each
  one measured from a position that had not finished arriving. Measuring from the target keeps a
  held key linear.

## Deltas are body frame, and the rotation went where conventions says

An operator watching the chase camera thinks "forward", not "north". So a delta is FLU body
frame, rotated into ENU by the node. That rotation is a fresh opportunity for a sign error, and
conventions §3 is explicit — *"One conversion, in one function, with a unit test"* — so it lives
in `control/frames.py` as `flu_to_enu`, with tests pinning the sign an FRD convention would flip
(`facing north, left is west`).

## Flown, and the body frame proved itself in the air

One 90 s sortie, driven over rosbridge exactly as the page does. Take off to 15 m from `(0, 0)`:

```
4 x forward 5 m          N=  0.0  E= 21.4     forward is EAST  (ENU yaw 0 faces east)
yaw 90 deg left, 6 steps
3 x forward 5 m          N= 16.5  E= 20.3     forward is now NORTH
```

**That is the body frame demonstrating itself**: the same command moved the aircraft east, then
north, because the aircraft had turned. A world-frame delta would have kept going east.

Then the fence, probed deliberately:

```
move forward 500 m   ->  moved 3.6 m     WARN  move clamped: step 500.0 m -> 5.0 m
move up     -500 m   ->  15.0 -> 9.9 m   WARN  move clamped: climb -500.0 m -> -5.0 m
descend to the floor ->  2.1 m           WARN  move clamped: altitude -2.0 m -> floor 2.0 m
```

Every bound bit, every one announced. Note the second line: the **step cap** bit before the
altitude floor could, which is the ordering above working as designed.

**It took off from `(0, 0)` and landed at `(20.0, 20.1)` — 28 m away.** That is the whole
capability that was missing, in one number: the aircraft can now be put somewhere other than
where it started, which is what a site survey is.

A second short sortie captured the interface with the controls live:
`out/sim47-webui-movement.png` — the D-pad, altitude, yaw and step slider enabled in `HOVER`,
with the aircraft 20 m north and 35 m east of its spawn. Chase recording of both sorties:
`out/sim47-survey-chase.mp4` (311 s).

**302 tests** (17 new), local CI tier 1 green, teardown verified.

## What is still open

The downward site survey itself. `SIM-47` built the thing that makes it possible and the flight
above proves it works, but the survey — descending over `citysample-gate1`'s landing point and
looking at what seed 2 keeps landing on — has not been flown. That remains `SIM-44`'s open half,
and it is now a browser tab rather than an afternoon.

## And then it was made sleeker — and reverted

Asked for mid-flight, so it is a redesign rather than a rewrite: every element id `app.js`
touches survived, which is the constraint that kept it honest — the markup could be
restructured freely as long as the data bindings did not move.

Three changes carry it:

- **The camera views got the space, and their labels moved onto the video.** A separate caption
  bar cost 30 px of height per pane and added two more horizontal rules to a page that already
  had plenty. The label now floats on a gradient over the top of the frame.
- **Telemetry became one band instead of six cards.** Same values, same topic names — those are
  what make a reading checkable against `ros2 topic echo`, so none were dropped — but hairline
  dividers on a single surface instead of six borders competing with the video for attention.
- **The SITL warning became a chip, not a full-width banner.** It has to stay visible, because
  this page arms an aircraft. But a warning that is always shouting stops being read, and a
  yellow bar was competing with the thing the operator is actually looking at.

Also: state, altitude, the command buttons and the movement pad now sit in **one row**. The
first cut left a gap inside that row, because the last grid column was `1fr` and stretched the
movement cell; a trailing `1fr` collects the slack on the right instead.

The palette went darker and lower-contrast (`#0a0c0f` ground, hairline `rgba(255,255,255,.07)`
borders) so the video is the brightest thing on the page, which is what an operator is there to
look at.

Verified on the running stack rather than in a mockup: `out/sim47-webui-sleek.png`, taken in
`HOVER` at 13.99 m with both cameras live and the movement pad enabled. The intermediate
capture caught the aircraft after it had landed, which incidentally photographed the refusal
path working — `refused move in state idle; allowed from ['hover']`, in red, in the log strip.

**The owner reverted it**: *"I like the previous design better. its more clearer."*

Which is the right call and worth recording rather than quietly undoing. The restyle optimised
for density and for the video dominating the page. What it cost was **labelling**: six bordered
groups with their own headings are easier to scan than one band of hairline-separated columns,
and a full-width banner is read where a chip is skimmed past. On a tool whose entire job is
letting someone see what is where, legible beats sleek.

The revert is a **reconstruction, not a `git checkout`** — none of this has been committed, so
there was no earlier version on disk to restore. `index.html` and `style.css` were rewritten
back, and `app.js`'s connection chip returned to its original classes. Three things were
deliberately kept, because they were bug fixes rather than styling:

- `.viewport img { display: block }` — the Chrome MJPEG deadlock
- the `naturalWidth` poll instead of trusting `load` on an endless stream
- the unencoded slashes in the camera URL

Verified the same way as the restyle, on a running stack in `HOVER` at 14.01 m with both
cameras live and the movement pad enabled: `out/sim47-webui-reverted.png`. Reconstructing from
memory and *claiming* it matches would have been the easy version of this; the screenshot is
the reason it can be claimed at all.

**A note for next time:** this is the second thing in this session that could not be recovered
from git because nothing has been committed. The working tree is now carrying `SIM-45`,
`SIM-46` and `SIM-47` in full.

---

# SIM-48 — the depth camera (2026-09-02)

*"There is a depth camera sensor on the drone. Correct? If so, can you also show it in the web
interface?"*

Correct. `sim/ue5/settings.json:157` configures `ImageType: 1` — **DepthPlanar**, Z-distance
rather than ray length — at 640x480, arriving as
`/airsim_node/PX4/front_center_DepthPlanar/image`: `sensor_msgs/Image`, **`32FC1`, metres**,
16.6 Hz. The gate scenarios already record it.

**But it could not just be added as a third pane**, and that turned out to be the whole ticket.
`web_video_server` knows `bgr8` and `bgra8` and nothing else — its streamers carry no
min/max or colormap parameters. Checked against the shipped `.so` rather than its README:

```
encodings the streamers know : bgr8  bgra8
float/range parameters       : (none)
```

Pointed at a float topic it fails or renders garbage. Depth has to become an image first.

## The two decisions worth having an opinion about

A node converts `32FC1` metres to a colourised JPEG and publishes a `CompressedImage` — the
shape `chase_camera` already established, so `ros_compressed` forwards it untranscoded. The
judgement is in `colorize.py`, which imports only numpy so the tests exercise the real
arithmetic.

**A FIXED range, not per-frame auto-scaling.** Normalising each frame to its own min and max is
the obvious implementation and it is wrong here: it makes the picture flicker as the aircraft
moves and, worse, makes a colour mean a different distance in every frame — so no two frames
can be compared. That is exactly the question a survey asks (*is that wall 5 m away or 20*).
There is a test that pins it: the same 10 m surface must render identically whether the rest of
the frame is empty or full of returns.

**Invalid returns get their own colour, not the far end of the ramp.** AirSim reports sky as a
very large value, and NaN and inf are both possible. Folding those onto "far" draws a confident
distance where there is no measurement — the same rule this page already follows for numbers
(*a value that never arrived must not look like a measurement*), applied to pixels. NaN also has
to be filled *before* the arithmetic, not just masked after: it propagates, and through the
uint8 cast it becomes 0, which is indistinguishable from "very close".

Beyond the band is **clamped but still valid** — a wall closer than `near` is still a wall, and
marking it "no data" would hide the most important thing in the frame.

## It runs on the simulator side, and that is bandwidth not tidiness

The raw topic is 640x480x4 = **1.2 MB per frame**, about 157 Mbit/s at 16.6 Hz.
`docker/ros2.Dockerfile` already records the measurement that raw imagery does not survive a WAN
while JPEG does (raw: ~0 images and 90% of telemetry lost; JPEG: 16.4 Hz at 1.7 Mbit/s). So the
colourising happens beside the source in `sim-ros2` and the ground station receives kilobytes.

New package `depth_view` rather than folding it into `chase_camera`: that package is named for
its one node and shares no code with this one — no ffmpeg, no X.

## The legend is published, not hard-coded

A colourised depth image without a scale is decoration: it looks like data and cannot be read.
But a scale that *disagrees* with the colour map is worse than none, because it looks
authoritative — and it would, the moment anyone launched the node with a different `far_m`.

So `depth_view` publishes `[near, far]` on `/depth_view/range`, latched (`TRANSIENT_LOCAL`, so
a browser opened later still gets it), and the page draws its labels from that. A test asserts
the page contains no hard-coded range, and another asserts the CSS "no return" swatch still
matches `NO_RETURN_BGR` in the Python — they are in different languages and must agree, or the
key labels a colour the image never contains.

## Verified on the stack

```
depth_view: /airsim_node/PX4/front_center_DepthPlanar/image -> /depth_view/image/compressed
            [0.5..40.0 m, 10.0 Hz max]
depth_view: legend stops [0.5, 10.4, 20.2, 30.1, 40.0] m
depth_view: 57 frames published, 28 dropped to the rate cap
/depth_view/range          -> [0.5, 40.0]
/depth_view/image/compressed  6.2 Hz
through web_video_server      53 frames / 10 s, 2665 KiB, first frame 51,426 B
```

The frame itself is the proof the range decision was right: ground dark, bollards and benches
resolving individually against the plaza, trees mid-ramp, the buildings behind in red, and the
**sky black** — no-return, plainly not "far". `out/sim48-webui-depth.png` shows all three panes
with the legend reading `0.5 · 10.4 · 20.3 · 30.1 · 40.0 m`.

**319 tests** (17 new), local CI tier 1 green. No image rebuild was needed: `cv_bridge`,
OpenCV 4.6.0 and numpy are already in `drone-sim/ros2`.

One trap avoided rather than hit: adding `depth_view` to `sim_up.sh`'s package copy loop puts
that string into the sim-ros2 entrypoint, so a bare `pkill -f depth_view` would have matched
PID 44 — the shell supervising the uXRCE-DDS agent. Same trap as `SIM-46`, one ticket later;
the patterns are `depth_view.launch.py` and `lib/depth_view/depth_view`, and a test asserts
neither appears in the entrypoint.

---

# SIM-49 — a spotlight for the camera views (2026-09-02)

*"is there a way to put the multiple videos in a spotlight? like make it the biggest"*

Three equal panes was fine at two and wrong at three: the view an operator is actually using
was the same size as the two they were not, and every pane shrank as another was added. One
large, two stacked beside it, click a thumbnail or press 1/2/3 to promote it.

## The constraint that shaped it

**The swap is CSS grid placement and nothing else** — no DOM move, no `src` reassignment.
Either tears down the `multipart/x-mixed-replace` connection and re-opens it, so the view just
asked for goes blank for about a second, at exactly the moment someone wanted to look at it.

Proven rather than assumed, by promoting the depth pane over CDP and reading `naturalWidth`
one second later — a torn-down stream reads 0:

```
before   spot=chase   chase 1920 (spot)   drone 640 (thumb)   depth 640 (thumb)
+1.0 s   spot=depth   chase 1920 (thumb)  drone 640 (thumb)   depth 640 (spot)
+3.0 s   spot=depth   chase 1920 (thumb)  drone 640 (thumb)   depth 640 (spot)
```

Classes swapped, every stream still decoding. A test pins it from the other side:
`setSpotlight` may not contain `.src`, `appendChild`, `insertBefore`, `prepend` or
`replaceChild`.

## What follows from keeping all three alive

**`ros_threads` goes from 2 to 6.** The page now holds three streams open permanently, and
`web_video_server`'s default is two. A starved request does not error — it answers HTTP 200,
writes the multipart boundary and closes, which a browser renders as an empty pane. That is
precisely the shape that cost the long hunt in `SIM-46`, so two spare threads is a cheap way to
keep it off the table. Verified concurrently:

```
/chase/image                                80 frames / 8 s
/airsim_node/PX4/front_center_Scene/image   74 frames / 8 s
/depth_view/image                           51 frames / 8 s
```

**Digits, not letters, for the shortcuts.** `W A S D`, `R F` and `Q E` fly the aircraft; a
layout shortcut sharing those would move the vehicle on a mis-hit. There is a test asserting
the two sets do not intersect.

**The choice is remembered** in `localStorage`, every access guarded — a private window or
blocked site data throws outright, and the page has to render anyway.

**Thumbnails drop what cannot be read at that size**: the depth legend, the fallback text, the
topic name in the caption. They gain a `↗` and a button role, so the affordance exists for a
keyboard and a screen reader too, not only for the cursor.

## One layout bug, found by looking

The first version left about 360 px of dead space beside the spotlight: `align-items: start`
put both thumbnails at the top of the sidebar while a 4:3 depth frame letterboxed into a 16:9
pane made the spotlight much taller. Two equal grid rows that the spotlight spans, plus
thumbnails whose viewport grows to its row instead of dictating height from a fixed ratio.

`out/sim49-webui-spotlight.png` is the result, with depth promoted.

**322 tests** (3 new), local CI tier 1 green, teardown verified.

---

# The review pass (2026-09-02)

`/code-review high` over the whole uncommitted change set — 65 files, ~7,450 insertions. Two
notes on running it that mattered more than they should have:

- **19 of the paths were untracked**, and a plain diff shows a review none of them. `git add -N`
  (intent-to-add, nothing committed) is what made the new packages visible. Without it the
  review would have covered the modified files only and reported a clean bill on the half of
  the work that was new.
- The vendored `roslib.min.js` was excluded from the diff deliberately — reviewing a minified
  blob is wasted effort. It then turned up as a finding for a different reason.

**Eleven findings, all legitimate.** Four were verified by direct test before being accepted,
rather than taken on the reviewer's word.

## The two that mattered, and both were mine

**A movement nudge could drop the aircraft into failsafe.** `MOVE` re-checked the SITL
interlock, which opens a socket with a 2 s timeout — and `_apply_command` runs on the timer
thread, the same one as `_publish_setpoint`. My own file records that PX4 leaves offboard after
`COM_OF_LOSS_T` = 1.0 s. A stalled AirSim RPC during World Partition streaming — exactly when
it stalls — would have starved the setpoint stream for seconds. The interlock was also checked
*before* the state check, so even a command that was going to be refused paid the full stall.

I placed that interlock deliberately and wrote three paragraphs about why it belonged at the
node that arms rather than in the transport. I never asked which thread it ran on.

The fix splits on **whether setpoints are streaming**, not on which command it is: `TAKEOFF`
arrives in `IDLE`, where `_tick` deliberately streams nothing, so it checks live and remains a
real per-takeoff proof. `MOVE` arrives in `HOVER`, where the stream is the only thing holding
offboard, so it reuses the take-off verdict and dials nothing.

**A yaw-only keypress commanded a 40 m descent.** `clamp_move` applied the 60 m ceiling to the
*absolute* target on every MOVE, including one whose delta was `(0, 0, 0)`. Hovering at 100 m —
the page allows a take-off to 120 — one press of `Q` snapped the hold point to 60 m.

Verified before accepting:

```
hovering at 100 m, yaw-only MOVE (delta 0,0,0)
  -> target z = 60.0   notes = ['altitude 100.0 m -> ceiling 60.0 m']
```

**A fence built to keep the aircraft safe was itself a way to command a dive.** The band now
widens to include wherever the aircraft already is: a fence, not a magnet. Climbing is still
refused, descending still works, and the real ceiling reapplies once back inside.

## Three were the SIM-27 work from the handoff

`run_park_tour.sh` read `d['collisions']` and `d['ground_contacts']`; the rewritten witness
writes neither. Confirmed by comparing the key sets — they do not intersect. Its `except:
print(0)` also reported an unreadable file as "0 ground contacts", which is the substitution
this project's collision scoring exists to prevent.

`flush()` had been dropped from the polling loop, so the file was written exactly twice while
`collision_witness.py` still claimed it "flushes continuously". A witness killed between those
two writes left the initial all-`None` record and the run scored UNKNOWN — precisely when the
simulator is stalled, which is the case the witness exists to catch. It now flushes at each
bracket transition, and the claim is true again.

`run_gate.py` let "collision state unknown" outrank the controller's real `failure_reason` for
any run that never crossed the 2 m gate — every `timeout in state arm`, every failed offboard
handover. That is the misdiagnosis `SIM-27`'s own header is an argument against: *the order
decides which sentence the report carries*.

## Four smaller, all mine

ffmpeg's stderr was a pipe nothing read until after the loop — ~64 KiB of warnings and it
blocks on write, stops producing stdout, and the reader blocks forever behind a frozen pane.
Drained on its own thread now. **The review suggested merging stderr into stdout; that would
have been wrong** — that stream carries JPEG bytes and text would corrupt frames. There is a
test asserting `stderr=subprocess.STDOUT` never appears.

`roslib.min.js` was untracked and not ignored — simply never added. A fresh clone would have
built a `sim-webui` whose page loads with no `ROSLIB`, so every button silently does nothing:
the exact failure the vendor README says vendoring exists to prevent, against hard stop 6. Now
tracked, with a test that shells out to `git ls-files` to keep it that way.

Missing telemetry rendered as the literal string `"null"` — verified: `1.0 / null / 2.0` — and
because that is a non-null string, `put()` treated it as a measurement and did not mark it
stale. That breaks the rule written at the top of the same file.

A bad `far_m`/`near_m` threw per-frame from a callback instead of refusing to start; a refused
take-off still mutated `self.alt`.

## Verified by flying the broken case

**332 tests** (10 new regression guards), CI tier 1 green. Then a sortie at **80 m** —
deliberately above the 60 m ceiling, which is the configuration both HIGH findings needed and
the one the earlier 12 m sortie could never have exposed:

```
hovering at 80.0 m, nav_state=14
  4 x yaw-only        altitude change +0.04 m        (was: -40 m)
  climb +5            80.0 -> 80.0 m, refused        WARN altitude 85.0 m -> no higher than 80.0 m
  6 x translate       nav_states seen: [14]          never left OFFBOARD
  outcome             success, landed and disarmed
```

**A flight test that passes is not coverage.** The sortie flown when this feature was built was
at 12 m, below the ceiling, and never pressed yaw while high — so it exercised neither bug.

One honest note about the verification script itself: it printed `ALL CHECKS PASSED` with a
final altitude of 6.52 m, which looked wrong. Manual mode recycles *both* `DONE` and `FAILED`
to `idle`, so "state == idle" was a weaker assertion than it appeared. Reading the controller's
own result settled it — `outcome: success`, `landed: true` — and the 6.7 m is real: the
aircraft flew 24 m forward and landed on a structure above its take-off point.
