#!/usr/bin/env bash
# PX4 container entrypoint.
#
# NOTE: no `set -u`. ROS 2's setup.bash references unbound variables by design
# (AMENT_TRACE_SETUP_FILES) and dies under it. Do not "tidy" this back in.
set +u
set -e

# ROS is OPTIONAL here, and normally absent. This image carried ROS only because the
# uXRCE-DDS agent used to be built into it; the agent now runs in sim-ros2, so the PX4
# runtime image ships without ROS at all (SIM-19). Sourcing unconditionally under `set -e`
# would kill the container outright the moment ROS is not installed -- which is exactly what
# the slimmed image is.
if [ -f /opt/ros/"${ROS_DISTRO:-jazzy}"/setup.bash ]; then
  source /opt/ros/"${ROS_DISTRO:-jazzy}"/setup.bash
  [ -f /ros2_ws/install/setup.bash ] && source /ros2_ws/install/setup.bash
fi

# The XRCE agent links Jazzy's libfastrtps, so it only runs with ROS sourced — which the
# two lines above guarantee for anything launched through this entrypoint.

exec "$@"
