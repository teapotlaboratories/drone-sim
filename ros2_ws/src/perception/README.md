# `perception` — a sketch, not a package

**There is no package here.** This directory contains this README and nothing else — no
`package.xml`, no source, no tests. **colcon has never built it.**

**The sensor data already exists without it.** `bringup`'s `perception.launch.py` starts the
Cosys-AirSim wrapper, and the graph carries RGB (`bgr8`), metric depth (`32FC1`), GPU-LiDAR
`PointCloud2`, IMU/GPS/magnetometer/barometer, TF, `/clock`, and ground-truth instance
segmentation and object transforms. Topic names, types and **measured** rates are in
[`../../../docs/quickstart.md`](../../../docs/quickstart.md) §3. What is missing is not data;
it is anything that consumes it.

## What would go here

Thin wrappers around pinned upstreams — **do not reimplement SLAM or mapping**:

- `isaac_ros_visual_slam` (cuVSLAM) — VIO, ROS 2 Jazzy, Jetson-supported. Tracked as
  `SIM-05` in [`../../../docs/todo.md`](../../../docs/todo.md), deprioritised rather than
  abandoned: the imagery is ready for it whenever it comes back.
- `isaac_ros_nvblox` — GPU mapping → Nav2 costmap + 3D reconstruction.
- **OctoMap** — a CPU fallback, so anything CI-shaped can run without a GPU runner.

## Four measured constraints anything built here inherits

- **The IMU is a polled snapshot, not a stream.** The wrapper fetches `getImuData()` once per
  state-timer tick and publishes the latest sample, so ~15% of messages carry duplicate
  timestamps even at a correct poll period, and spacing is non-uniform. `ros2 topic hz`
  reports a healthy-looking 333 Hz and tells you none of this. That is a live design
  constraint for cuVSLAM, which wants a dense, evenly-spaced stream. IMU messages also ship
  with **zero covariances** (`// todo covariances` upstream).
- **Frames are NWU, not ENU**, despite upstream's docs — measured yaw missed ENU by 97.3° and
  NWU by 7.3°. Anything consuming these topics against REP-103 will be yaw-rotated 90° until
  the conversion is taken from `control/frames.py`, the project's single conversion point.
- **`camera_info` resolves in TF only because of a local patch.** Upstream set its `frame_id`
  without the vehicle prefix, so it was the lone outlier against the image and the static TF,
  and `image_proc` / `depth_image_proc` could not resolve the camera frame. See
  [`../../../docs/vendor/cosys-airsim.md`](../../../docs/vendor/cosys-airsim.md).
- **Imagery is capped at 20 Hz and LiDAR at 10 Hz by the launch file, not by the hardware** —
  measured throughput on stock settings is 18.7 Hz and 10.0 Hz, **94%** and **100%** of those
  ceilings. The remaining headroom is the RPC round trip (~21.7 Hz for Scene+Depth at
  640×480), not the renderer — and there is no lockstep, so raising a sensor's cost degrades
  the simulation rather than slowing it deterministically.

**Measure before trusting.** IMU–camera timestamp synchronization is the recurring pain point
across every simulator this project surveyed, and the survey's own advice was to measure
timestamp jitter and rate stability before trusting sim VIO results
(`../../../docs/history/reference/01_sim_stack_report.md:48`). It also suggested validating
against Gazebo lockstep as a control — **that control no longer exists here**, and there is
no lockstep in Cosys-AirSim to replace it with, so the reference is the simulator's own
ground-truth kinematics instead.
