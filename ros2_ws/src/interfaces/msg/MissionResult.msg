# The outcome of one flight, published once when the controller terminates.
#
# The same summary the runner reads as JSON. It is duplicated ON PURPOSE rather than
# replaced: `scripts/run_scenario.py` is a HOST script driving docker compose, with no ROS
# environment and no way to subscribe. JSON is the host-side transport; this is the
# graph-side one, and it lands in the MCAP so a bag carries its own verdict.

std_msgs/Header header

string OUTCOME_SUCCESS = success
string OUTCOME_FAILURE = failure
string outcome

# Empty on success.
string failure_reason

uint16 waypoints_reached
uint16 waypoints_total

# One entry per waypoint REACHED, in order. Distances are the settled error — measured
# after the hold, not on first entry to the acceptance radius, so a fast fly-through
# cannot score well.
float32[] waypoint_errors_m

float32 takeoff_altitude_m
float32 accept_radius_m

# "scenario" when a scenario supplied waypoints_enu, "built-in-square" otherwise. Recorded
# because a square side is meaningless for an arbitrary path, and a bag should not have to
# be cross-referenced with a scenario file to know which it was.
string mission_source
