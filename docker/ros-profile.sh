# Sourced by every login shell (/etc/profile.d) in the stack's containers.
#
# `docker exec` bypasses the image ENTRYPOINT, so a shell opened that way would otherwise
# have no ROS environment: AMENT_PREFIX_PATH empty, px4_msgs invisible, and `ros2 topic
# list` reporting zero topics on a perfectly healthy stack. Baked into /etc/profile.d by
# docker/ros2.Dockerfile so an exec shell is usable without every caller repeating it.
#
# NOTE: this only applies to LOGIN shells, so use `bash -lc '...'`:
#   docker exec sim-ros2 bash -lc 'ros2 topic list'
[ -f /opt/ros/jazzy/setup.bash ] && . /opt/ros/jazzy/setup.bash
[ -f /ros2_ws/install/setup.bash ] && . /ros2_ws/install/setup.bash
