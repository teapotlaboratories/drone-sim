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
# The AirSim wrapper overlay, sourced BEFORE /ros2_ws to match the order every manual
# invocation in this repo has used.                                              (SIM-37)
# Without it `ros2 launch bringup perception.launch.py` fails with "package 'airsim_ros_pkgs'
# not found" and the stack has no camera, depth, LiDAR or AirSim-IMU topics -- which reads
# exactly like a world whose sensors are broken. Guarded on the file so an older image, or a
# container built before this line existed, still opens a usable shell.
[ -f /airsim_root/ros2/install/setup.bash ] && . /airsim_root/ros2/install/setup.bash
[ -f /ros2_ws/install/setup.bash ] && . /ros2_ws/install/setup.bash
