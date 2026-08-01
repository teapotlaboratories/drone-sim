# 2026-07-31 — `/clock` bridge and launch composition

**Tasks:** `P1-03a` (make `use_sim_time` actually usable), `P1-03` (`sim.launch.py` + bringup).
**Lane:** A. SITL only; no hardware involved.

> Kept as the work happens, per `.ai/AGENTS.md`. The previous two worklogs were written
> after the fact and said so; this one is not.

---

## Why these two together

`docs/lane-a/conventions.md` §4 freezes `use_sim_time: true` as the rule — and then records
that it is **not reachable**: nothing publishes `/clock`, so turning it on freezes every
node's timers at zero and the node hangs in a way that looks exactly like a deadlocked
controller. Every node therefore runs on wall clock, which is wrong by the ~2.7% the
nested-Docker RTF deficit costs.

`P1-03` (launch composition) is where `use_sim_time` would be set, so building the launch
files without the clock first would mean writing a parameter that cannot be switched on.

Second motive, from using the stack all week: **`docker compose down` discards the `control`
build**, because it lives in the container filesystem rather than a volume. Both the seeded
runner and the recorder rebuild it on every start. That is the manual step `P1-03` should
remove.

---

## Progress log

### Starting state

- `/clock` topic: absent (`ros2 topic list | grep -c '^/clock$'` → 0)
- `ros-jazzy-ros-gz` packages: not installed in the Lane A image
- `ros2_ws/src/bringup/`: README placeholder only, no package

*(entries appended below as the work happens)*

### `ros_gz_bridge` — tested before baking it in

`ros-jazzy-ros-gz-bridge` **1.0.22-1noble.20260615.142443** is in the image's existing apt
sources.

One thing worth checking rather than assuming: it depends on `ros-jazzy-gz-msgs-vendor` and
`ros-jazzy-gz-transport-vendor` — **vendored** gz libraries, not the system's
`libgz-transport13` / `libgz-msgs10` that Harmonic uses. If the vendored versions belonged
to a different gz generation, the bridge would run happily and see no topics — the same
shape of silent failure as the BEST_EFFORT QoS mismatch.

Tested in a throwaway container against the live stack before touching the Dockerfile:

```
/clock present: 1
sec: 36  ->  sec: 40      (advancing)
[ros_gz_bridge]: Creating GZ->ROS Bridge:
  [/world/default/clock (gz.msgs.Clock) -> /world/default/clock (rosgraph_msgs/msg/Clock)]
```

The vendored libraries do see the Harmonic server. No mismatch.

**Topic name:** Gazebo publishes `/world/default/clock`; ROS convention is `/clock`, so the
bridge is remapped. The world name is in that topic path, so a different world means a
different source topic — it is a launch argument, not a constant.

### Layer placement cost a rebuild

First attempt added `ros-gz-bridge` to the ROS install block near the top of the Dockerfile,
which is where ROS packages "belong" by tidiness. That **invalidates every layer after it —
including the 20–40 minute PX4 build** — for a package with no relationship to PX4. The
rebuild blew a 10-minute budget and was killed.

Moved to a late layer, just before `EXPOSE`:

```
real  0m27.789s
ros-gz-bridge 1.0.22-1noble.20260615.142443
```

**28 seconds against tens of minutes.** Grouping by subject matter is the wrong instinct in
a Dockerfile; grouping by *rate of change* is the right one. Recorded in the Dockerfile
itself so the next person does not "tidy" it back.

The build now asserts `parameter_bridge` exists and records the version in
`/etc/drone-sim-versions`, so a silent packaging change shows up as a build failure rather
than as a bridge that carries nothing.

### Installing the bridge into the Lane A image BREAKS GAZEBO

After the rebuild, PX4 could not start Gazebo at all:

```
INFO  [init] Waiting for Gazebo world...   (x6)
ERROR [init] Timed out waiting for Gazebo world
```

The mechanism, isolated:

```
gz sim --versions                      -> 8.14.0        (fine)
. /opt/ros/jazzy/setup.bash
LD_LIBRARY_PATH now contains            /opt/ros/jazzy/opt/gz_transport_vendor/lib
                                        /opt/ros/jazzy/opt/gz_msgs_vendor/lib
gz sim --versions                      -> prints the help text   (BROKEN)
```

`ros-gz-bridge` pulls `gz_transport_vendor`, which installs its own
`libgz-transport13.so.13.5.0` and puts it on `LD_LIBRARY_PATH` when ROS is sourced. That
**shadows the system Harmonic library and breaks the `gz` CLI**.

The px4-sitl service runs its command through `bash -lc`, which sources
`/etc/profile.d/10-ros.sh` — so PX4 got the vendored libraries and could no longer launch
its own simulator. Before this change, sourcing ROS was harmless because the vendor
packages were not installed.

**This is exactly the version-coupling class `versions.lock` exists for**, and it does not
announce itself: both packages install cleanly, `gz sim` works until ROS is sourced, and the
failure surfaces as PX4 "waiting for Gazebo".

**Fix: keep the bridge out of the image that runs Gazebo.** The vendored libraries are only
harmful to a process that launches `gz` — the bridge itself uses them correctly. So the
containers get different images: `px4-sitl` and `xrce-agent` stay on plain `lane-a`, and the
ROS 2 side gets a thin image with the bridge added.

That also happens to be the first real step of `D-06`: the flight-controller image and the
companion image stop being the same thing.

### `use_sim_time` works — first time in this project

```
ros2 launch bringup sim.launch.py use_sim_time:=true
  [clock_bridge] Creating GZ->ROS Bridge: [/world/default/clock -> /clock]
  [offboard_control] armed
  [offboard_control] reached takeoff altitude 10.0 m
  [offboard_control] landed and disarmed
  result: success 4/4  [0.156, 0.09, 0.15, 0.16]
```

The conventions document can stop saying `use_sim_time` is specified-but-unreachable.

### `ros2 launch` did not return

The first run exited **124** — a timeout on a flight that had already succeeded. `ros2
launch` keeps running after the controller lands, because the clock bridge is still alive
and launch has no reason to stop. Harmless interactively, fatal for the scenario runner or
anything that needs an exit status.

`on_exit=Shutdown()` on the controller node fixes it: **exit 0 in 47 s**.

Worth noting the shape — the flight was fine, the *harness* was not, and a naive reading of
"exit 124" would have blamed the flight.

### Structure

- `control.launch.py` — the controller alone. Identical in sim and on the aircraft; a future
  `real.launch.py` includes it and adds the hardware transport, nothing else.
- `sim.launch.py` — the clock bridge plus that include. Everything simulator-only lives
  here, so the "only the transport is swapped" claim stays true.

`use_sim_time` still **defaults to false**, deliberately: enabling it without a `/clock`
publisher freezes every timer, and a stack launched with `clock_bridge:=false` should fail
visibly rather than hang.
