# 2026-08-01 — `C-06`: the Cosys-AirSim ROS 2 wrapper on Jazzy

**Task:** `C-06` — compile the Cosys-AirSim ROS 2 wrapper against ROS 2 Jazzy / Ubuntu 24.04.
**Lane:** C. **Nothing flies here** — no Unreal Engine, no GPU, no simulator, no aircraft.

> Kept as the work happens. Dated 2026-08-01 because the session crossed midnight UTC; the
> work began the evening of 2026-07-31 Pacific.

---

## Why this runs first

The project chose **Jazzy over doc 04's Humble** on the reasoning that doc 04's
recommendation was inherited from upstream example docs rather than measured. Research then
showed upstream itself now documents Jazzy on Ubuntu 24.04 — but also that **upstream's CI
never builds the ROS 2 wrapper on any distro**: its only workflow runs `setup.sh`,
`build.sh` and `MavLinkTest --help`, and never invokes `colcon`. A GitHub issue search for
"jazzy" in that repo returns zero results.

So "tested with ROS2 Jazzy" is a maintainer assertion with no automated signal behind it.
This task converts it into evidence, for ~30 minutes and no engine image.

**It runs before `C-01`/`C-02` deliberately.** The wrapper is an ordinary `ament_cmake`
package; discovering a distro incompatibility *after* a 24 GB engine pull and a multi-hour
UE build would be the most expensive possible ordering of the same two facts.

---

## Progress log

### Starting state

- `vendor/` held the Lane A trees only; no Cosys-AirSim checkout.
- `colcon` 0.3.x, `cmake` 3.28.3, `g++` 13.3.0, Python 3.12.3 present. **`clang` absent.**
- ROS 2 Jazzy installed system-wide.

### Checkout — SHA verified, not assumed

```
git clone --depth 1 --branch 5.8-v3.4.1 https://github.com/Cosys-Lab/Cosys-AirSim.git
→ a552dd6cd517b8d5d26629ad88004356c3007326   (matches versions.lock exactly)
→ 1.3 GB on disk
```

`.repos` had this entry stubbed out as `version: TODO-verify  # pin a known-good UE5.5
commit`. Now activated with the SHA, and its section header corrected — Lane C is a
**Phase 2** concern now, not Phase 4, and the engine is **5.8**, not 5.5.

### Two pre-existing `.repos` defects, found on the way in

`.repos` states at the top that *"Versions here MUST agree with versions.lock"*. A
mechanical comparison of the two files says otherwise:

| Entry | `.repos` said | `versions.lock` says | Severity |
|---|---|---|---|
| `Micro-XRCE-DDS-Agent` | `v2.4.2` | `v2.4.3` | **serious** |
| `px4_ros_com` | `main` | `release/1.16` | latent |

**The first one reconstructs a broken tree.** `versions.lock` records v2.4.2 as *genuinely
unbuildable* — eProsima deleted the Fast-DDS branch its superbuild pins, so the build dies
with `fatal: invalid reference: 2.12.x`. Anyone running `vcs import vendor < .repos` on a
fresh machine would have checked out the version that cannot compile. That is exactly the
*"written from the docs rather than from evidence reproduces a broken stack"* failure this
project has a rule against — sitting in the file that **is** the reconstruction mechanism.

The second is the latent asymmetry `versions.lock` already flagged: the development plan's
setup snippet branch-matches `px4_msgs` but clones `px4_ros_com` from `main`.

Both corrected, and both now pinned by **SHA** rather than tag/branch, as `.repos`'s own
header instructs.

**This wants a check, not vigilance.** Same class as the worklog-render gap found hours
earlier: a rule stated in a file, obeyed by hand, silently drifting. A tier-1 check
comparing `.repos` against `versions.lock` is a few lines and would have caught both.

### The wrapper's shape — better than feared

`ros2/src/` holds two packages: `airsim_interfaces` (messages) and `airsim_ros_pkgs` (the
wrapper). The wrapper's `CMakeLists.txt` pulls the C++ client library in directly:

```cmake
set(AIRSIM_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../../../)
add_subdirectory("${AIRSIM_ROOT}/cmake/rpclib_wrapper" rpclib_wrapper)
add_subdirectory("${AIRSIM_ROOT}/cmake/AirLib"        AirLib)
add_subdirectory("${AIRSIM_ROOT}/cmake/MavLinkCom"    MavLinkCom)
```

**So `colcon` builds AirLib itself** — there is no separate `build.sh` step to run first,
which removes the toolchain-mismatch worry that made the UE5.5 line awkward (its `build.sh`
had no `--ue-root` and exported a plain `CC=clang`).

### The three header claims, checked in the source rather than taken on trust

```
ros2/src/airsim_ros_pkgs/include/airsim_ros_wrapper.h
  43: #include <cv_bridge/cv_bridge.hpp>                    ← the Jazzy fix IS on this tag
  70: #include <tf2/LinearMath/Matrix3x3.h>                 ← deprecated shim, still .h
  71: #include <tf2/LinearMath/Quaternion.h>                ← deprecated shim, still .h
  72: #include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>    ← the Jazzy fix
```

Confirms both halves of the earlier finding: the `.hpp` forms that Jazzy requires are
present, and the `tf2/LinearMath` shims are not. Those shims produce deprecation warnings on
Jazzy; `CMAKE_CXX_FLAGS` sets `-Wall -Wextra` but **not** `-Werror`, so they will not fail
the build. Do not "fix" that by adding `-Werror`.

### Dependencies

Missing from this container and installed: `ros-jazzy-mavros-msgs`,
`ros-jazzy-geographic-msgs`. Already present: `pcl-conversions`, `tf2-sensor-msgs`,
`image-transport`, `joy`, `cv-bridge`, plus `libyaml-cpp-dev`, `libopencv-dev`, `libpcl-dev`.

Not vendored in the checkout and fetched by upstream's `setup.sh`: **rpclib 2.3.1** and
**eigen 3.4.1r**. Note both come from `WouterJansen/` forks rather than upstream rpclib and
eigen — worth recording as a supply-chain detail for `D-04`, since the Dockerfile will pull
the same two zips.

`setup.sh` on this tag is benign for a headless build: `unzip` via apt, two `wget`s, and a
`dialout` group edit that only matters for real serial hardware. **No clang install and no
Ubuntu-version branching** — unlike the 5.5 line the research described.

### RESULT — it builds on Jazzy

```
Starting >>> airsim_interfaces
Finished <<< airsim_interfaces
Starting >>> airsim_ros_pkgs
Finished <<< airsim_ros_pkgs [1min 6s]

Summary: 2 packages finished [1min 21s]
  1 package had stderr output: airsim_ros_pkgs
exit=0
```

**`colcon build --symlink-install` on ROS 2 Jazzy / Ubuntu 24.04 / g++ 13.3.0: exit 0 in
1 min 21 s.** No clang needed — the toolchain question that made the UE5.5 line awkward does
not arise, because `colcon` builds AirLib itself via `add_subdirectory`.

**The distro decision is now evidence rather than hypothesis.** Staying on Jazzy against
doc 04's Humble recommendation was argued from reasoning; it is now argued from a build.

**Warnings only — 0 errors:**

| Count | Warning |
|---|---|
| 26 | `-Wsign-compare` |
| 20 | `-Wclass-memaccess` |
| 2 | `-Wunused-variable` |
| 2 | `-Wattributes` (Eigen `EIGEN_ALIGN16` on `PointXYZRGBI`) |
| 1 | `-Wstrict-aliasing` |
| 1 | `-Wdeprecated-copy` |

All upstream's, none ours. `-Werror` is deliberately not set — do not add it.

### Not trusting the exit code — the `D-01` assertion, applied

`D-01` established that a build script's success banner proves nothing, and `P1-01` that a
green `colcon build` can hide a package that dies at import. So the artifacts were checked
rather than assumed:

```
install/airsim_ros_pkgs/lib/airsim_ros_pkgs/airsim_node   -> build/airsim_ros_pkgs/airsim_node
  size                12 MB
  unresolved libs     0          (ldd | grep -c 'not found')
  links against       /opt/ros/jazzy/   ← Jazzy, not some other distro on the box
airsim_interfaces     24 message types registered with `ros2 interface list`
```

**A caveat about my own first check:** the initial `find -type f -executable` reported
`airsim_node NOT FOUND`. That was wrong — `--symlink-install` makes the entry point a
**symlink**, which `-type f` excludes. The binary was there all along. Recorded because it is
the same shape as the failures this project keeps hitting: *the check was broken, not the
thing under test*, and a less careful reading would have logged a false negative.

### Running it surfaced something nobody was looking for

The node was run with no simulator, expecting it to sit retrying a connection. Instead:

```
Waiting for connection - X
Connected!
API Client Ver:4 (Min Req:1), API Server Ver:1 (Min Req:4)
Cosys-AirSim API server is of older version and not supported by this API client. Please upgrade!
SimMode: Multirotor
[INFO] [airsim_node]: Setting ROS wrapper to DRONE mode
[INFO] [airsim_node]: Publishing Barometer sensor 'barometer'
[INFO] [airsim_node]: Publishing Gps / Imu / Magnetometer sensors
[ERROR] [airsim_node]: Exception raised by the API, something went wrong.
terminate: Couldn't initialize rcl timer handle: the given context is not valid
exit 250
```

**It connected to something.** `ss -lntp` explains what:

```
LISTEN  0.0.0.0:41451   users:(("CarlaUE4-Linux-",pid=903836,fd=35))
LISTEN  0.0.0.0:2000    users:(("CarlaUE4-Linux-",pid=903836,fd=23))
LISTEN  0.0.0.0:2001    ...
LISTEN  0.0.0.0:2002    ...
```

**A CARLA UE4 instance is already running on this machine and is bound to `0.0.0.0:41451` —
the exact port Cosys-AirSim's RPC server uses.** CARLA is UE4-based and the CARLA-Air work
(`04`) reuses the AirSim API, so it answers the handshake well enough to report
`API Server Ver:1` against our client's `Min Req:4`, and the negotiation fails.

The process is **not visible in this container's process table** (`ps -p 903836` returns
nothing) though its socket is, so it is running on the host, outside the container. Both GPUs
are near-idle at the time of checking (3080: 111 MiB / 10240; 5060 Ti: 30 MiB / 16311), so it
is resident rather than actively rendering. **Not touched** — host-side processes need the
operator, and killing someone else's simulator to free a port is not a decision to take
unasked.

**Three things this is worth recording for:**

1. **A port conflict is waiting for Lane C.** `C-03`'s `sim` container will try to bind
   41451 and lose, or worse, our client will silently talk to CARLA again — which is exactly
   what just happened, and it *looked* like a successful connection. Pin the AirSim RPC port
   explicitly and assert what answered, rather than trusting "Connected!".
2. **It is bound to `0.0.0.0`, not loopback** — the same exposure class `D-02` fixed for the
   MAVLink ports, where an unauthenticated port on a routable interface let anyone arm the
   vehicle.
3. **The wrapper crashes rather than degrades** when an API call fails: the ROS context is
   torn down and a timer handle then throws (`exit 250`). Worth knowing before `C-04` trusts
   it to survive a simulator hiccup.

**What this accidentally proved, though:** the node initialises ROS, brings up its
publishers, negotiates the real AirSim RPC protocol against a real UE-based server, and
reports a version mismatch correctly. That is considerably more than "it compiles".

---

## Answer to `C-06`

**Builds on Jazzy. The stay-on-Jazzy decision is confirmed by evidence.**

`versions.lock: lane_c.cosys_airsim_ros2_wrapper.builds_against` can move off `TODO-verify`.
What remains unproven is everything downstream — the wrapper has not been run against an
actual Cosys-AirSim server, because that needs `C-02`'s engine image.

## Going further than C-06 asked: a stub server, and what it did and did not settle

`C-06` only asked whether the wrapper compiles. But three questions that `C-04` will need do
**not** require the engine, because they are answered entirely on the wrapper side. The
AirSim client speaks plain msgpack-RPC, so a stub can impersonate the server:
`scripts/airsim_rpc_stub.py`.

**It is a test fixture, not a simulator.** Every value it returns is fabricated by that
file. It proves how the wrapper *transforms* input; it says nothing about how AirSim
behaves, and it does not substitute for `C-02`.

**The port is a ROS parameter** (`host_port`, default 41451), so the stub ran on a spare
port and **the CARLA instance on 41451 was never touched.**

### Q1 — the topic surface: ANSWERED

The node reached `AirsimROSWrapper Initialized!` and created its publishers:

```
/airsim_node/Drone1/altimeter/barometer      /airsim_node/Drone1/odom_local
/airsim_node/Drone1/environment              /airsim_node/Drone1/vel_cmd_body_frame
/airsim_node/Drone1/global_gps               /airsim_node/Drone1/vel_cmd_world_frame
/airsim_node/Drone1/gps/gps                  /airsim_node/gimbal_angle_euler_cmd
/airsim_node/Drone1/imu/imu                  /airsim_node/gimbal_angle_quat_cmd
/airsim_node/Drone1/magnetometer/magnetometer  /airsim_node/instance_segmentation_labels
/airsim_node/object_transforms               /airsim_node/origin_geo_point
/tf   /tf_static
```

Confirms the shape recorded earlier: `/airsim_node/<vehicle>/…`, **entirely disjoint from
Lane A's `/fmu/*`**. Note `odom_local` and the two `vel_cmd_*` command topics — those are the
ones that would tempt someone to bypass PX4, which coupling `lane-c-topic-parity` exists to
prevent.

### Q2 — the `/clock` trap: ANSWERED, and it is worse than "wrong topic"

**There is no clock topic at all** — not `/clock`, and not `/airsim_node/clock`. `publish_clock`
defaults to `false`, so by default nothing publishes a clock. A stack that sets
`use_sim_time:=true` against this wrapper as-shipped gets frozen timers and a node that hangs
looking exactly like a deadlocked controller — **precisely the `P1-03a` failure**, arriving
from a different direction.

### Q3 — NWU vs ENU: answered from SOURCE, not from runtime

The stub never got far enough to publish odometry (see below), so this is **static evidence,
not a measurement.** It is still conclusive:

```cpp
// get_odom_msg_from_kinematic_state, airsim_ros_wrapper.cpp
odom_msg.pose.pose.position.y = -odom_msg.pose.pose.position.y;
odom_msg.pose.pose.position.z = -odom_msg.pose.pose.position.z;
odom_msg.pose.pose.orientation.y = -odom_msg.pose.pose.orientation.y;
odom_msg.pose.pose.orientation.z = -odom_msg.pose.pose.orientation.z;
odom_msg.twist.twist.linear.y  = -odom_msg.twist.twist.linear.y;
odom_msg.twist.twist.linear.z  = -odom_msg.twist.twist.linear.z;
odom_msg.twist.twist.angular.y = -odom_msg.twist.twist.angular.y;
odom_msg.twist.twist.angular.z = -odom_msg.twist.twist.angular.z;
```

**y and z negated, x untouched — that is NED → NWU.** ENU would additionally swap x and y.
And `convert_tf_msg_to_enu()` is defined at line 1600 and called from **nowhere**; all five
TF call sites use `convert_tf_msg_to_ros()`:

```
1600: void AirsimROSWrapper::convert_tf_msg_to_enu(...)   ← definition only
1636/1653/1670/1687/1706: convert_tf_msg_to_ros(...)      ← every actual call
```

So upstream's doc claim — *"the right-handed coordinate frame of the ROS standard"* — is
wrong, and anything written against REP-103 will be **yaw-rotated 90°**. Confirmed as a
source fact; a runtime confirmation against the real server is still worth doing in `C-02`,
where it costs one `ros2 topic echo`.

### Where the stub ran out of road, and why I stopped

Getting the node to *publish* meant satisfying the client's msgpack casts one crash at a
time. Every nested struct must be complete — an empty `{}` throws `std::bad_cast` rather
than defaulting:

| Iteration | Failing call | Fix |
|---|---|---|
| 1 | `simListInstanceSegmentationObjects` | null → `[]`; a vector cast cannot take null |
| 2 | `simGetGroundTruthEnvironment` | full `EnvironmentState` map (`RpcLibAdaptorsBase.hpp:487`) |
| 3 | `getMultirotorState` | complete `collision` + `gps_location` + `rc_data` sub-structs |
| 4 | `simIsPaused` | null → `false` |
| 5 | *(DDS)* `BadParamException: The string contains null characters` | **stopped here** |

The fifth is a different class — a DDS serialization fault from a string field, most likely
the segmentation-label plumbing my stub feeds with empty arrays. Chasing it means
reimplementing more of AirSim's surface, which is **exactly the "don't reinvent upstream"
line**, for a question `C-02` answers for free.

**So: two of three questions answered, the third answered statically, and the stub kept** —
it is ~200 lines, it already gets the wrapper to full initialisation, and it is the cheapest
way to exercise wrapper-side behaviour without a 24 GB image. Its limits are written into
its own docstring.

**Two process notes worth recording**, because both wasted time:
`pkill -f airsim_rpc_stub` **killed the shell running it** — `-f` matches the whole command
line, and my own command contained that string. Then `pgrep`/`grep '[a]irsim_node'` reported
the node **ALIVE when it had crashed**, matching the grep's own command line. Twice, the
check was broken rather than the thing under test — the same shape as the
`find -type f` false negative above.

## A bug I introduced while writing this up, and the guard it earned

Adding the `airsim-rpc-port-conflict` coupling to `versions.lock`, I replaced the
`- id: lane-c-topic-parity` line instead of inserting before it. The result:

- `lane-c-topic-parity` **silently vanished** from the couplings list;
- its body was left orphaned under the new id, giving that mapping **two `assert:` keys**;
- **`yaml.safe_load` parsed it without complaint**, because PyYAML accepts duplicate keys
  and quietly keeps the last one.

So the existing "does `versions.lock` parse" check went **green on a corrupted lock file**,
and a coupling asserting the project's central sim-to-real claim had disappeared. It was
caught only because a later script happened to search for the string
`- id: lane-c-topic-parity` and could not find it.

**Fixed, and then automated rather than remembered.**
`scripts/check_repos_manifest.py` now loads both files through a loader that **raises on
duplicate keys**, and separately asserts every coupling has a unique `id` and carries
`assert` / `why` / `severity`. Verified by re-introducing the exact clobber:

```
versions.lock has a duplicate key:
  in "<unicode string>", line 969, column 5:
      - id: airsim-rpc-port-conflict
  duplicate key 'assert' (line 982) - PyYAML would silently keep the last one
```

**Worth naming plainly:** "it parses" was never the same claim as "it says what I meant",
and this file is the one place in the project where a silent semantic change is most
expensive. Three checks were added today and all three exist because a rule was being
obeyed by hand until it wasn't.

## Next

- `C-01` — the Epic image tag is confirmed and Cesium supports UE5.8; storage decided
  (internal NVMe, see `docker/todo.md` `D-04`).
- `C-02` — 24 GB engine pull and the UE build.
- Resolve the 41451 port conflict before `C-03`.

