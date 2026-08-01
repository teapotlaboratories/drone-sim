# What the controller is doing, published continuously so a recorded run explains itself.
#
# This exists because runs here are NOT reproducible — identical configuration back to back
# gives different trajectories — so the MCAP is the only evidence a failed seed leaves. A
# bag with only PX4 topics can show the vehicle in the wrong place without showing which
# waypoint the controller believed it was flying to, or why it gave up.
#
# Frames are ENU throughout, per docs/lane-a/conventions.md §3. PX4's NED never appears
# outside the control node's adapter.

std_msgs/Header header

# State machine, mirroring control.offboard_control.State. Numbers rather than a string:
# the contract is the enumeration, and these constants travel with the message so
# `ros2 interface show` explains a bag without reference to the source.
uint8 STATE_WAIT_FOR_FCU     = 0
uint8 STATE_STREAM_SETPOINTS = 1
uint8 STATE_REQUEST_OFFBOARD = 2
uint8 STATE_ARM              = 3
uint8 STATE_TAKEOFF          = 4
uint8 STATE_WAYPOINTS        = 5
uint8 STATE_LAND             = 6
uint8 STATE_DONE             = 7
uint8 STATE_FAILED           = 8
uint8 state

# 0-based, and only meaningful in STATE_WAYPOINTS.
uint16 waypoint_index
uint16 waypoint_total

# Where the controller is trying to be, and how far off it is. Together these separate
# "the controller aimed at the wrong place" from "the controller aimed correctly and the
# vehicle did not get there" — the two failures look identical in a PX4-only bag.
geometry_msgs/Point target_enu
float32 distance_to_target_m

# Set only once state is STATE_FAILED. Empty otherwise.
string failure_reason
