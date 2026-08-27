# SIM-38 — a scenario can ask for sensors, and three ways of reporting nothing

The request was small: *"when running a scenario or a test, can we also have an option to also get
sensor other than rgb?"* A run gave you PX4 telemetry plus RGB — the vehicle camera and the chase
view. Depth, LiDAR and the inertial suite were synthesised by the simulator and thrown away.

## Why it was not just a flag

`record_topics` was **already generic** — a scenario could always name any topic, and its own
comment anticipated this: *"a scenario with obstacles will want depth and the planner's
trajectory, and that should not require editing the harness."*

What it could not do was make the topic **exist**. `sim_up.sh` does not start `airsim_node`
(`SIM-37` put the wrapper in the image, not in the bring-up), so a scenario listing a depth topic
recorded an empty one — and reported success, because the topic was listed, the recorder ran, and
the bag exists.

So the feature is three things, not one:

```yaml
sensors:
  - depth
  - lidar
  - imu
```

1. **Start the perception graph and block until topics appear.** Recording first produces a bag
   with the topics present and no messages in them, which reads as a successful capture of an
   empty world rather than as a mistake.
2. **Append to `record_topics`, never replace it.** A depth frame with no vehicle pose is not
   evidence of anything. Cameras bring their `camera_info` automatically — frames without
   intrinsics and a TF frame are useless for anything geometric, and finding that out after the
   flight is too late.
3. **Count the messages actually in the bag afterwards, and fail the run if a requested sensor
   recorded nothing.** This is the owner's requirement taken literally: *"bag is very important
   and we should always be able to get this if enabled."*

The vocabulary comes from `verify_sensors.py` rather than being invented, so the two cannot drift
about what "depth" means. An unknown name is **fatal**: a typo like `lidar_points` would otherwise
record nothing and read as "this world has no LiDAR".

## Verified by flying it

| | Blocks | CitySample |
|---|---|---|
| flight | PASS 0.784 m | FAIL — parked `SIM-27`, unrelated |
| bag | 2.0 GB | 2.6 GB |
| depth + `camera_info` | 1481 / 1481 | 1948 / 1948 |
| GPU-LiDAR | 736 | 973 |
| IMU / GPS / mag / odom | 34855 each | 78548 each |

The usual bag is ~840 KB, so the size alone says the data arrived. The pairing of depth frames with
exactly as many `camera_info` messages is the part worth noticing — that is the half people forget.

## Three bugs, one shape

Every one was **something failing silently and returning "no evidence"** — which is
indistinguishable from "no problem". That is the same failure the feature exists to prevent, and it
appeared three times in the code written to prevent it.

**1. Counted on the host, where `rosbag2_py` does not exist.** It is a ROS package; it lives in
`sim-ros2`. The counter returned `{}` on every run, and "cannot read the bag" took the same branch
as "the bag is fine". A run holding **78548 IMU messages** reported `sensor_message_counts: None`.

**2. Attached to one of THREE return paths — and not the one that runs.** `run_flight` returns from
the host result file, from a stdout fallback, and from "no result produced". I decorated the
fallback. `probe_written` and `chase_video` are duplicated across the same three paths for the same
reason; this is that pattern's third victim. One `_with_sensor_evidence()` helper now covers all
of them, and the test pins all three rather than counting to two.

**3. Used `dexec`, which runs `docker exec` without a login shell.** So `/etc/profile.d` never
runs, ROS is not on the path, and `import rosbag2_py` fails with `ModuleNotFoundError` — swallowed
by the `except`. **This is the trap this repo documents in three places and bakes `ros-profile.sh`
into the image to avoid**, and it still caught me. The fix is `bash -lc`, with the script passed
through `docker exec -e` rather than interpolated, so a topic name containing a shell
metacharacter cannot be executed.

Each was found by **running the thing**, not by reading it. Bug 1 needed a flight whose bag
provably had data. Bug 2 needed the gate report from a passing run. Bug 3 needed the counter to be
called for real. A passing test suite said nothing about any of them, which is why the tests added
here pin the three failures rather than the happy path.

## Cost, measured

Perception adds ~30 s of settle per bring-up, and on CitySample the rates are about a third of what
Blocks posts — depth ~10.5 Hz, GPU-LiDAR ~5.5 Hz — under real scene load. Bags go from ~840 KB to
2–2.6 GB. The owner's call was that the bag matters more than the size, so this is default **off**
and per-scenario, not a global switch: a 40-seed gate would pay it forty times.

## Left undone

`rgb` is in the vocabulary but untested — every run so far declared the other six. The vehicle
camera video and the chase view already cover RGB, so nothing needed it yet.
