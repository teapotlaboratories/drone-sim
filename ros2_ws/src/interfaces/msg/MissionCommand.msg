# A hand-flying command: what the operator wants the aircraft to do next.       (SIM-45)
#
# This fills the "mission spec IN" half of the `/mission/*` namespace that
# docs/conventions.md §2 froze on 2026-07-30. The namespace was frozen carrying both
# directions; only status and result had ever been built. Nothing is renamed here.
#
# WHY A TOPIC AND NOT A SERVICE. The web interface reaches the graph through
# rosbridge, and rosbridge's publish allowlist (`topics_pub_glob`) is enforced per
# TOPIC -- see rosbridge_library/capabilities/publish.py, which checks the glob before
# it creates the registration. Services are governed by a separate glob that this
# project sets to `[]` precisely so a browser cannot reach any node's set_parameters.
# Expressing the command as a topic is what lets ONE name be allowed and everything
# else refused.
#
# ONLY `offboard_control` WITH `manual:=true` ACTS ON THIS. In mission mode the
# subscription does not exist, so a gate run cannot be perturbed by a stray publish.

std_msgs/Header header

# Take off to `altitude_m` and hold there. Accepted only from STATE_IDLE.
string COMMAND_TAKEOFF = takeoff
# Land, then return to STATE_IDLE. Accepted from STATE_HOVER and from the climb.
string COMMAND_LAND = land
# Hold position. Accepted from STATE_HOVER; a no-op that keeps the stream alive.
string COMMAND_HOLD = hold
# Nudge the hold point. Accepted from STATE_HOVER only, so it is reachable only through a
# take-off that already satisfied the SITL interlock. Uses the delta fields below. (SIM-47)
string COMMAND_MOVE = move
string command

# Metres, ENU (up positive), per conventions §3 -- the aircraft's NED never appears
# outside the control node's adapter. Ignored by LAND, HOLD and MOVE. Zero or negative
# means "use the node's takeoff_altitude parameter", so a caller that only wants the
# default does not have to know it.
float32 altitude_m

# --- MOVE only -----------------------------------------------------------------  (SIM-47)
#
# A delta, in the BODY frame: FLU -- x FORWARD, y LEFT, z UP -- which is the body frame
# conventions §3 freezes. Body rather than world because an operator watching the chase
# camera thinks "forward", not "north". The node rotates it into ENU with
# `control.frames.flu_to_enu`, which is the ONE place that rotation happens and is unit
# tested, per conventions §3.
#
# THESE ARE REQUESTS, NOT COMMANDS. The node clamps every one of them against its
# `move_*` parameters -- a per-step cap, a radius around the take-off point, and an
# altitude band -- and logs whatever it clamped. A browser cannot widen that envelope;
# see docs/todo.md SIM-47.
float32 forward_m
float32 left_m
float32 up_m

# Radians, counter-clockwise, relative to current heading. Also clamped per step.
float32 yaw_delta_rad
