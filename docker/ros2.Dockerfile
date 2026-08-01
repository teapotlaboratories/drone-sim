# The ROS 2 / companion-computer image — Lane A plus the Gazebo bridge.
#
# WHY THIS IS A SEPARATE IMAGE, AND NOT A LAYER ON THE SHARED ONE
# ---------------------------------------------------------------
# `ros-gz-bridge` pulls `gz_transport_vendor`, which installs its own
# libgz-transport13.so.13.5.0 and puts it on LD_LIBRARY_PATH whenever ROS is sourced. That
# **shadows the system Harmonic library and breaks the `gz` CLI**:
#
#     gz sim --versions              -> 8.14.0
#     . /opt/ros/jazzy/setup.bash
#     gz sim --versions              -> prints help text; gz is broken
#
# The px4-sitl service runs its command through `bash -lc`, which sources ROS — so with the
# bridge in the shared image, PX4 could no longer launch its own simulator and sat in
# "Waiting for Gazebo world" until it timed out. Neither package complains; the failure
# only shows up as a simulator that will not start.
#
# The vendored libraries are not wrong, they are just wrong *for a process that launches
# gz*. So the split is by role: whatever runs Gazebo stays on plain `lane-a`, and the ROS 2
# side gets the bridge here.
#
# This is also the first real step of D-06 — the flight-controller image and the companion
# image stop being the same artifact, which is how they will be deployed in Phase 4 anyway
# (PX4 on the Pixhawk, the agent and ROS 2 nodes on the Jetson).
FROM drone-sim/lane-a:v1.16.0

# The bridge that publishes /clock, so use_sim_time is usable at all (P1-03a). Asserted at
# build time: a silent packaging change would otherwise surface as a bridge that runs and
# carries nothing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ros-${ROS_DISTRO}-ros-gz-bridge \
    && rm -rf /var/lib/apt/lists/* \
    && test -x /opt/ros/${ROS_DISTRO}/lib/ros_gz_bridge/parameter_bridge \
    && dpkg -s ros-${ROS_DISTRO}-ros-gz-bridge \
         | awk '/^Version:/{print "ros-gz-bridge " $2}' >> /etc/drone-sim-versions \
    && echo "ros_gz_bridge parameter_bridge present"

# DO NOT add anything here that needs to launch `gz`. If this image ever has to run the
# simulator, the vendored-library shadowing above comes back.
