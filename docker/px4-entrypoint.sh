#!/usr/bin/env bash
# PX4 container entrypoint.
#
# NOTE: no `set -u`. ROS 2's setup.bash references unbound variables by design
# (AMENT_TRACE_SETUP_FILES) and dies under it. Do not "tidy" this back in.
set +u
set -e

source /opt/ros/"${ROS_DISTRO:-jazzy}"/setup.bash
[ -f /ros2_ws/install/setup.bash ] && source /ros2_ws/install/setup.bash

# The XRCE agent links Jazzy's libfastrtps, so it only runs with ROS sourced — which the
# two lines above guarantee for anything launched through this entrypoint.

exec "$@"
