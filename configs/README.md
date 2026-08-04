# `configs/` — empty placeholder

**Nothing reads this directory.** It contains this README and no configuration, and no
script, launch file or Dockerfile in the repo references it. Said plainly so nobody goes
looking for the file that overrides something.

Configuration today lives where the thing it configures reads it, in three places:

| What | Where | Read by |
|---|---|---|
| the vehicle, its sensors, their tuning | [`../sim/ue5/settings.json`](../sim/ue5/settings.json) | the simulator, via `sim_up.sh --settings` |
| the mission, tolerances, recorded topics | [`../scenarios/*.yaml`](../scenarios/) | `scripts/run_scenario.py`, `scripts/run_gate.py` |
| the ROS 2 graph — namespace, `use_sim_time`, node parameters | launch arguments in [`../ros2_ws/src/bringup/launch/`](../ros2_ws/src/bringup/launch/) | `ros2 launch` |

**What would earn a place here:** parameters that belong to *none* of those — a set that
has to be identical in sim and on the aircraft, and so cannot live in a simulator settings
file or a scenario. EKF2 parameter sets for GPS-denied or HITL flight are the obvious
candidate: the same values must reach a real Pixhawk, and the transport swap is supposed to
be the only difference between the two.

The rule when something does land here: **it must have a reader.** A directory of YAML that
no code loads is worse than no directory, because it looks authoritative.

Secrets never live here — pass tokens, Wi-Fi credentials and setup keys via environment or
secret files, never committed (`.ai/AGENTS.md` → "Simulation & hardware notes"). That
includes the GitHub PAT the Unreal engine base image needs.
