# Sourced by every login shell in the ground-station container.
#
# `docker exec` WITHOUT -l bypasses this, and then `ros2 topic list` reports nothing against a
# perfectly healthy graph. That has cost real debugging time in this project more than once, so
# the profile script is baked into the image rather than left to each caller.
[ -f /opt/ros/jazzy/setup.bash ] && source /opt/ros/jazzy/setup.bash
# px4_msgs, copied from the companion image at build time.
[ -f /ros2_ws/install/setup.bash ] && source /ros2_ws/install/setup.bash
# drone_interfaces + webui, copied in and built by sim_up.sh at bring-up.
[ -f /gcs_ws/install/setup.bash ] && source /gcs_ws/install/setup.bash
