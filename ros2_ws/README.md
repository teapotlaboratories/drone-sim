# `ros2_ws/` — ROS 2 Jazzy workspace

The application-side colcon workspace: **the graph you fly**, and the one that would reach a
real Pixhawk unchanged. Everything here is original glue — message contracts, the offboard
controller, launch composition. Third-party trees are *not* vendored here; they are pinned in
[`../.repos`](../.repos) and checked out under [`../vendor/`](../vendor/).

**Three packages build. Five directories are README-only stubs.**

| Package | Type | What it is |
|---|---|---|
| [`src/interfaces`](src/interfaces/) | `ament_cmake` (`drone_interfaces`) | `MissionStatus`, `MissionResult` — what makes a recorded run self-describing |
| [`src/control`](src/control/) | `ament_python` | the offboard controller, the single ENU↔NED conversion point, and the example mission |
| [`src/bringup`](src/bringup/) | `ament_python` | `perception.launch.py` and `control.launch.py` |

`src/evaluation`, `src/perception`, `src/planning`, `src/state_estimation` contain a README
and nothing else — no `package.xml`, no source. **colcon has never built them.** They are
sketches of where work would go, not packages.

```bash
cd ros2_ws && colcon build --symlink-install && source install/setup.bash
```

Built with system ROS 2 Jazzy (**Python 3.12**).

## How it actually gets built in a run

`scripts/sim_up.sh` mounts this tree at `/ros2_ws_src` inside the `sim-ros2` container, copies
exactly `interfaces`, `control` and `bringup` into a container-local `/ros2_ws`, and
colcon-builds them there — so `build/`, `install/` and `log/` land in the container and never
in the repo. That container is deleted and recreated by the next `sim_up.sh`, which is why a
source change needs no image rebuild and why a rebuilt stack always starts from the tree on
disk.

**The Cosys-AirSim ROS 2 wrapper is not part of this workspace.** It is built separately
into `/airsim_root` by `scripts/build_airsim_wrapper.sh` — it has to be built in place
against the vendored tree, and its packages must not be entangled with ours. It also has to
be rebuilt after every `sim_up.sh`, which deletes the container it lives in. Source both:

```bash
. /opt/ros/jazzy/setup.bash
. /airsim_root/ros2/install/setup.bash     # airsim_node, airsim_interfaces
. /ros2_ws/install/setup.bash              # control, bringup, drone_interfaces
```

Topic, namespace and frame conventions are frozen in
[`../docs/conventions.md`](../docs/conventions.md) — the graph is meant to survive the
transport swap, so a rename here is a change to the sim-to-real contract.
