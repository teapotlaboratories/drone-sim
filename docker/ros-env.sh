# Sourced by every login shell (/etc/profile.d) in the Lane A containers.
#
# `docker compose exec` bypasses the image ENTRYPOINT, so a shell opened that way would
# otherwise have no ROS environment: AMENT_PREFIX_PATH empty, px4_msgs invisible, and
# `ros2 topic list` reporting zero topics on a perfectly healthy stack. Mounting this into
# /etc/profile.d fixes exec shells without rebuilding the image.
#
# NOTE: this only applies to LOGIN shells, so use `bash -lc '...'`:
#   docker compose exec ros2 bash -lc 'ros2 topic list'
[ -f /opt/ros/jazzy/setup.bash ] && . /opt/ros/jazzy/setup.bash
[ -f /ros2_ws/install/setup.bash ] && . /ros2_ws/install/setup.bash
