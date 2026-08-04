# `interfaces` — message contracts (`drone_interfaces`)

**Status:** ✅ built and flying. An `ament_cmake` / `rosidl` package; the directory is
`interfaces`, the package name is **`drone_interfaces`**.

| Message | Published | Purpose |
|---|---|---|
| `MissionStatus` | continuously by the controller | state machine step, waypoint index, target and distance, failure reason |
| `MissionResult` | once, when the controller terminates | outcome, per-waypoint settled errors, altitude, accept radius, mission source |

**These exist because runs are not reproducible.** Identical configuration back to back gives
different trajectories, so the MCAP is the only evidence a failed run leaves — and a bag
carrying only PX4 topics shows the vehicle in the wrong place without showing which waypoint
the controller believed it was flying to, or why it gave up. `MissionStatus` separates "the
controller aimed at the wrong place" from "the controller aimed correctly and the vehicle did
not get there"; in a PX4-only bag those two failures are identical.

**`MissionResult` duplicates the JSON summary the runner reads, on purpose.**
`scripts/run_scenario.py` is a host script driving containers, with no ROS environment and no
way to subscribe. JSON is the host-side transport; this is the graph-side one, and it lands in
the bag so the bag carries its own verdict.

Two details worth keeping:

- **The state machine is an enumeration, not a string.** The constants travel with the
  message, so `ros2 interface show` explains a recorded run without reference to the source.
- **Frames are ENU throughout** (`docs/conventions.md` §3). PX4's NED never appears outside
  the control node's adapter, and waypoint errors are the *settled* error — measured after
  the hold, not on first entry to the acceptance radius, so a fast fly-through cannot score
  well.

Anything added here is a contract: it reaches the aircraft unchanged, so a field added for a
simulator-only convenience is a change to the sim-to-real boundary.
