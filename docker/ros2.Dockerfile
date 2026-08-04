# The ROS 2 / companion-computer image — the PX4 base plus what the AirSim wrapper needs.
#
# WHY THIS IS A SEPARATE IMAGE, AND NOT JUST THE BASE
# ---------------------------------------------------
# Role separation, and it is the same split the hardware will have: PX4 runs on the flight
# controller, the uXRCE-DDS agent and every ROS 2 node run on the companion computer. Here
# that is `sim-px4` against `sim-ros2`. Keeping them one artifact works right up until the
# Jetson, at which point the two halves have to be separable anyway.
#
# WHAT THIS IMAGE USED TO CARRY, AND WHY IT NO LONGER DOES
# --------------------------------------------------------
# It installed `ros-gz-bridge` to publish /clock from a Gazebo world. That bridge has no
# consumer now: /clock comes from the simulator itself, remapped in
# `ros2_ws/src/bringup/launch/perception.launch.py` (`/airsim_node/clock` -> `/clock`).
#
# The removal is worth more than the disk it frees. `ros-gz-bridge` pulls
# `gz_transport_vendor`, which installs its own libgz-transport13 and puts it on
# LD_LIBRARY_PATH whenever ROS is sourced, shadowing any system Gazebo and silently
# breaking the `gz` CLI for every process that sources ROS. That coupling is gone with it.
FROM drone-sim/px4:v1.16.0

# The Cosys-AirSim ROS 2 wrapper's dependencies, baked in rather than apt-installed on
# every bring-up by scripts/build_airsim_wrapper.sh.
#
# Baking them in is not just speed: the bring-up script installs them INSIDE a running
# container, so the dependency lived only in that container's writable layer and vanished
# on every teardown — a network outage between two runs turned a working stack into a
# build failure. python3-msgpack is the RPC client's only dependency and is needed by the
# settle-wait even when the wrapper is never built.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-geographic-msgs \
        ros-${ROS_DISTRO}-mavros-msgs \
        python3-msgpack \
        patch \
    && rm -rf /var/lib/apt/lists/* \
    # Assert on the artifacts, not on apt's exit status: a package that installs but ships
    # no headers fails at the wrapper's colcon build, twenty minutes later and one layer up.
    && test -d /opt/ros/${ROS_DISTRO}/include/geographic_msgs \
    && test -d /opt/ros/${ROS_DISTRO}/include/mavros_msgs \
    && python3 -c "import msgpack" \
    && echo "airsim-wrapper deps (geographic_msgs mavros_msgs msgpack)" \
       >> /etc/drone-sim-versions

# A login shell sources ROS, so `docker exec sim-ros2 bash -lc 'ros2 topic list'` works
# without every caller repeating the source lines. `docker exec` without `-l` bypasses this
# and reports 0 topics on a perfectly healthy stack — which has cost real debugging time
# here, so the profile script is baked in rather than bind-mounted by a compose file that
# no longer exists.
COPY docker/ros-profile.sh /etc/profile.d/10-ros.sh
RUN chmod 0644 /etc/profile.d/10-ros.sh
