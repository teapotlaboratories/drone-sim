# Drone Simulation Stack Architecture: Photorealistic UE5 + PX4/ROS2 for VLM Autonomy Research

## TL;DR
- **Build the primary stack on Cosys-AirSim (University of Antwerp AirSim fork) running in Unreal Engine 5.5, containerized with headless Vulkan rendering, driving PX4 SITL in lockstep over MAVLink while simultaneously exposing the real-hardware uXRCE-DDS `/fmu/*` topics to ROS 2 Humble** — this is the only actively maintained option that satisfies all eight hard requirements (Unreal photorealism, PX4+ROS2, full sensor suite incl. GPU-LiDAR/event/segmentation, Cesium real-world maps, UE dynamic actors, and an AirSim-style frame-grab + velocity API ideal for VLM-in-the-loop).
- **Two candidates that look attractive are dead ends: Microsoft AirSim (archived Dec 15, 2023) and Colosseum (archived read-only July 11, 2026).** Project AirSim (IAMAI fork, MIT, UE5.2/5.7) is alive but young (v0.2.0, June 2026) with an early-stage ROS2 bridge and PX4 pinned to v1.12.3; keep it as a watch-item, not the foundation.
- **Fallback/complement: NVIDIA Isaac Sim + Pegasus Simulator for physically-grounded PX4/ROS2 fidelity, and Gazebo Harmonic as the PX4 sim-to-real ground-truth baseline.** Isaac is not Unreal (fails hard-req #3 strictly) and has a Cesium-FSD-vs-PhysX conflict, so it is a parallel track, not a drop-in.

---

## Decisions taken on this document — 2026-07-31

**This report is adopted.** Cosys-AirSim on UE5.5 is the project's **primary** simulator, and
**Phase 2 (perception + obstacle avoidance) moves from Gazebo to Lane C.** Lane B (Isaac Sim +
Pegasus) remains deferred on the driver conflict.

The report left three things open that the project had to settle before replanning. Each is
resolved below. **Where a decision contradicts the body of this document, the decision wins** —
the body is preserved as the research record, not edited to match.

### 1. Gazebo/Lane A is demoted, not retired

This report never said to drop Gazebo — Recommendation → Fallback #2 keeps it as "the always-on
PX4 sim-to-real ground-truth baseline for controls/regression". That is exactly its new role.

**Lane A stays always-on and frozen in scope.** It keeps tier-1 CI, the `P1-06` flight gate
(SR 10/10), and the controls/sim-to-real ground truth — because it is the only thing in the
project that currently *flies*, and Lane C is rated High-likelihood for build fragility. No new
capability work lands in Lane A; Phase 2 and beyond are built in Lane C.

The practical consequence: **a Lane C regression is measured against a Lane A run, not against a
previous Lane C run.** That is the whole point of keeping it.

### 2. ROS 2 distro — Jazzy, not Humble

**This document's Humble recommendation is not adopted.** It is inherited from upstream
AirSim/CARLA-Air documentation, which is where those projects happen to have their examples; it
is not a measured constraint of Cosys-AirSim.

Against that, `versions.lock` pins **Jazzy** across everything the project has actually built and
smoke-tested: Ubuntu 24.04 / Python 3.12.3, `ros-jazzy-desktop 0.11.0-1noble.20260616.084553`,
all four container images, `px4_msgs release/1.16` branch-matched to PX4 v1.16.0, the
`ros-jazzy-ros-gz-bridge` `/clock` bridge, and the Isaac ROS perception packages (cuVSLAM,
nvblox) which are themselves Jazzy. Switching to Humble means Ubuntu 22.04 and rewriting every
one of those pins to match a documentation preference rather than evidence.

**Decision: stay on Jazzy everywhere. Build the Cosys-AirSim ROS 2 wrapper from source against
Jazzy.** The risk — that the wrapper does not build on 24.04/Jazzy — is real and is *not* being
waved away: it becomes the first thing `C-02` measures, before any UE5 work is committed to. The
recorded fallback, if it genuinely will not build, is to run *only that node* in a Humble
sidecar container and bridge its topics — accepting cross-distro DDS interop risk in one place
rather than distro-flipping the whole project.

### 3. Phase numbering — mapped, not renumbered

This report's Phase 0–3 are **calendar weeks**; the project's Phase 0–4 are **capabilities with
measured exit criteria** (`SR = 100%/10 runs`, `0 collisions/20 runs`, `SR ≥ 50% (SR@5m)`). Those
are not the same kind of object, and the project's `P<phase>-<nn>` task IDs are load-bearing —
the scheme exists specifically so a task reference cannot mis-link as a GitHub `#N`.

**Decision: the project's numbering stays canonical; this report's phases map onto it.**

| This report | Weeks | Project equivalent | State |
|---|---|---|---|
| **Phase 0** — Gazebo Harmonic + PX4 + uXRCE-DDS + ROS 2 in Docker; lock the `/fmu/*` interface and the offboard node | 1–2 | Project **Phase 0 + Phase 1 (Lane A)** | ✅ **done** — exit criterion met 2026-07-31, SR 10/10 |
| **Phase 1** — Cosys-AirSim UE5 headless GPU container; validate PX4 lockstep; AirSim→ROS 2 image bridge; port the offboard node unchanged | 3–6 | Lane C **`C-01`–`C-04`** — a prerequisite *inside* project Phase 2, not a phase of its own | **next** |
| **Phase 2** — Cesium real-world maps, MassAI/City Sample actors, wind; benchmark render FPS and lockstep stability | 7–10 | **Split**: clutter/obstacle scenes → project **Phase 2**; georeferenced benchmark environments → project **Phase 4** | not started |
| **Phase 3** — attach the VLM inference container; close the See-Point-Fly loop | 11+ | Project **Phase 3** | not started |

**Two project gates have no equivalent in this report** and are not superseded by it: Phase 2's
`0 collisions over 20 seeded cluttered-world runs` and Phase 4's real Pixhawk 6C flight. This
report's decision thresholds (lockstep sustain, Cesium black-tile altitude) are adopted *in
addition*, as Lane C acceptance criteria.

### 4. The engine pin moves from UE5.5 to UE5.8 — added the same day

**This report's headline recommendation is UE5.5, and that is now out of date.** It was
correct when written: UE5.5 *was* the version Cosys-AirSim's CHANGELOG named as its targeted
stable. Upstream has since moved on, and the report's own hard requirement #2 (PX4 + ROS 2)
turns out to conflict with its engine choice.

**The conflict.** UE5.5 and ROS 2 Jazzy cannot be obtained from one upstream tag:

- The last UE5.5 release is `5.5-v3.3` (2025-04-16, SHA `e029c244…`). Upstream's v3.4
  CHANGELOG states the 5.5 branch "will no longer be receive updates or be actively
  maintained".
- The Jazzy fix — commit `83d1b81c`, rewriting `cv_bridge` and `tf2_geometry_msgs` includes
  from `.h` to `.hpp` — landed in **v3.4**, which targets **UE5.8**. The CHANGELOG entry
  reads "Fixed ROS2 header imports to support newer ROS2 distros such as Jazzy."

**Measured on `carbonite`, not inferred:** `ros-jazzy-cv-bridge 4.1.0-1noble.20260615.144656`
installs only `cv_bridge.hpp` — there is no `cv_bridge.h` shim under `/opt/ros/jazzy/include`,
and `tf2_geometry_msgs` likewise ships only `.hpp`. **`5.5-v3.3` is therefore measurably
unbuildable on this machine**, not merely unsupported on paper.

**Decision: pin tag `5.8-v3.4.1`, SHA `a552dd6cd517b8d5d26629ad88004356c3007326`, engine
UE5.8.** Its own docs name the exact environment this project already runs — *"The current
recommended and tested environment is **Ubuntu 24.04 LTS**"* and *"The following was tested
with ROS2 Jazzy"* — and it carries two further fixes Lane C would otherwise have hit
("Fixed incorrect TF-coordinate system for cameras for ROS2 node"; "Fixed incorrect TF
hierarchy order to `world->odom->vehicle->sensors`").

**This also inverts §2 above rather than merely overriding it.** Humble is now the *riskier*
distro: current upstream includes `<cv_bridge/cv_bridge.hpp>`, and Humble's `vision_opencv`
ships only `cv_bridge.h`, so acting on this report's Humble recommendation against current
upstream fails to compile immediately.

**The gate this raised is now cleared — and it reversed one of this report's assumptions.**
Cesium for Unreal's UE5.8 support was unverified when the decision was taken, which mattered
because Cesium is this report's hard requirement #7. Checked 2026-07-31: **Cesium for Unreal
v2.28.0 (2026-07-01) supports UE5.8**, with a downloadable UE5.8 binary rather than a roadmap
promise. The Epic engine image `dev-slim-5.8.0` exists, and UE5.8 is a released production
engine (2026-06-17), not a preview.

**The fallback inverted.** This report treats UE5.5 as the conservative choice. It is now the
riskiest one: Cesium **v2.29.0 removes UE5.5 support** ("Unreal Engine 5.6 or later is now
required"), making v2.28.0 the terminal Cesium release for 5.5. A UE5.5 stack would combine an
end-of-line Cosys-AirSim branch, a header patch carried by us, and a permanently frozen Cesium.
**UE5.8 is the only forward-supported path.**

**One correction to this report's container topology, from the same check.** The Epic engine
image is **Ubuntu 22.04**, not 24.04, and ROS 2 Jazzy has no jammy packages — so nothing Jazzy
can live inside the engine image. The separate `sim` and `ros2` containers this report proposes
are therefore **mandatory rather than a deployment preference**, and the AirSim↔ROS 2 boundary
must stay the RPC/MAVLink socket.

The pin nonetheless remains `TODO-verify` in `versions.lock`: the tag was *observed in the
registry*, not *pulled and run*. Existing is not working.

**What survives of this report unchanged:** the simulator *choice*. The argument for
Cosys-AirSim — the only actively maintained AirSim-lineage option, richest sensor suite,
PX4 SITL with uXRCE-DDS parity, MIT — is about the project, not the engine version. What does
not survive is the UE5.5-specific detail throughout the body below.

---

## Key Findings

### Simulator status and maintenance (as of July 2026)
- **Microsoft AirSim**: archived. Development shut down December 15, 2023; last engine was UE4.27. MIT license. Do not build on it.
- **Colosseum (CodexLabsLLC fork)**: **archived read-only on July 11, 2026**. Was the community UE5 successor (UE5.2 main branch, UE4.27 branch, last release v2.3.0 with UE5.4+/Ubuntu 24.04 support). MIT. Now a dead end despite its PX4+ROS2 support.
- **Cosys-AirSim (Cosys-Lab, Univ. of Antwerp)**: **actively maintained**. Stable v3.2 shipped for UE5.4 (plus a UE5.2.1 LTS), and the project's CHANGELOG states UE5.5 is now the targeted stable: "The latest available stable Unreal Engine version that is now targeted for release is 5.5… 5.4 will no longer be actively maintained." Adds ROS2 (C++), GPU-LiDAR, GPU-accelerated pulse-echo/radar plus UWB and Wi-Fi model-based simulation, event cameras, thermal/IR, PX4 HITL/SITL, MAVLink, Python/C++/MATLAB APIs, Nanite/Lumen. MIT. Documented in arXiv:2303.13381 ("Cosys-AirSim: A Real-Time Simulation Framework Expanded for Complex Industrial Applications"). **This is the strongest live Unreal option.**
- **Project AirSim (iamaisim fork)**: alive, MIT, DARPA-supported; IAMAI Simulations is "composed of former engineers from the original AirSim project at Microsoft." Supports **UE5.2 and UE5.7 only**. PX4 support is pinned — the docs state verbatim "Project AirSim supports PX4 v1.12.3. Other versions may work but are unsupported" (SITL + lockstep). Python client with `move_by_velocity_async`/`move_to_position_async` and `get_images`. ROS2 bridge (`ros2_node.py`, rclpy, Humble) exists but is early-stage. ~711 stars, latest release v0.2.0 (June 1, 2026, which added UE5.7 + DepthLiDAR). No official Docker image; no event camera.
- **NVIDIA Isaac Sim + Pegasus Simulator**: actively maintained; "2025-10-26: Pegasus Simulator v5.1.0 is released for Isaac 5.1.0. This version is NOT compatible with older versions of Isaac Sim" (tested on Ubuntu 22.04 LTS, NVIDIA driver 550.163.01, PX4-Autopilot v1.14.3). BSD-3, PX4 + ROS2 out of the box, photorealistic RTX. Cesium for Omniverse works but conflicts with PhysX under the Fabric Scene Delegate. Not Unreal.
- **Gazebo Harmonic**: PX4 core-supported default; best PX4/ROS2/uXRCE-DDS integration; not photorealistic. Official `px4io/px4-sitl-gazebo` Docker image.
- **CARLA / CARLA-Air**: CARLA is UE4.26/UE5.5, car-focused, best-in-class traffic/pedestrian AI. CARLA-Air (2026) unifies AirSim drone flight + CARLA world in one UE process, preserving both Python APIs and adding ROS2 examples for Humble. Young but promising for air-ground work.
- **Flightmare (UZH)**: Unity-based, MIT, decoupled render/physics, hundreds of agents; less actively maintained.
- **FlightGoggles (MIT)**: Unity + photogrammetry, perception/racing focus; largely dormant.
- **aerial-autonomy-stack (2026, Panerati et al., ICUAS 2026, arXiv:2602.07264)**: Gazebo-based, PX4/ArduPilot, ROS2, YOLO, Jetson Orin deployment. The paper claims it "supports over 20× faster-than-real-time, end-to-end simulation of a complete development and deployment stack—including edge compute and networking"; the repo ships a "ROS2 interface for 50Hz YOLOv8 on Jetson Orin's CSI IMX219-200 Camera" and holds the ODE physics timestep at 4 ms (250 Hz) for PX4 sims. Dockerized. Not photorealistic/Unreal but an excellent sim-to-real reference architecture that closely mirrors the user's exact hardware.

### The core tension
Requirement #3 (Unreal photorealism) + #5 (photorealistic environments) + #7 (real-world maps) + #8 (dynamic actors) pull toward the **game-engine (AirSim-lineage) camp**, whose PX4/ROS2 integration is bolted-on via MAVLink and a separate ROS bridge. Requirements #2 (PX4/ROS2 fidelity, lockstep, uXRCE-DDS) + sim-to-real transfer pull toward the **robotics-sim camp** (Gazebo, Isaac/Pegasus) whose rendering and real-world-map story is weaker. Cosys-AirSim is the best single compromise because it keeps AirSim's photorealism + sensor breadth while its PX4 SITL runs a uXRCE-DDS client that publishes the *same* `/fmu/*` topics your real Pixhawk 6C + Orin stack uses.

### Real-world maps
- **Cesium for Unreal** streams Google Photorealistic 3D Tiles and Cesium World Terrain into any UE5 project via a Cesium ion access token; proven with AirSim (SLAM studies) and works as a drop-in plugin. Google 3D Tiles require a Cesium ion account/token and are subject to Google Maps Platform terms.
- **Known pitfall**: at drone scale (10–500 ft AGL), Google 3D Tiles lack geometric/texture detail and can render black/unloaded; a documented issue in both Cesium-for-Unreal and Cesium-for-Omniverse.
- **OpenStreetMap → UE**: the `ue4plugins/StreetMap` plugin (Mike Fricker, Epic) imports `.osm` into UE as streets/buildings; multiple UE5 forks exist (e.g., Fristet/StreetMap with Blueprint support). OSM data is ODbL-licensed. UE5 PCG can procedurally populate.

### Wind and flight dynamics
- **Gazebo**: dedicated wind plugin (`windVelocityMean`/`windVelocityMax` in the world SDF, `gz_x500_windy`), affects PX4 flight dynamics; known fixed-wing airspeed/wind-estimation quirks (PX4-Autopilot issue #23756; SITL_gazebo issue #287).
- **AirSim/Cosys-AirSim**: wind set via API/`settings.json`; lockstep with `SteppableClock`, `UseTcp:true`, `LockStep:true`, and a `PressureFactorSigma` barometer tweak for fast GPS lock.
- **Isaac/Pegasus**: wind/drag in the drone dynamics model.

### Dynamic actors
- **UE5 MassAI / Mass Entity ECS + City Sample crowds and MassTraffic**: GPU-instanced pedestrians and traffic scaling to thousands of agents; ZoneGraph lanes + Mass Spawners script movement. City Sample provides Houdini-based procedural city generation (SideFX license required for the building generator).
- **CARLA Traffic Manager + pedestrian AI**: the gold standard for scripted traffic/pedestrian behavior, inheritable in CARLA-Air.

### VLM-in-the-loop
The target papers use a training-free VLM that grounds language instructions as 2D image annotations. See, Point, Fly (Hu, Lin, Lee, Su, Lee, Tsai, Lin, Chen, Ke, Liu; CoRL 2025, arXiv:2509.22653) states: "our key insight is to consider action prediction for AVLN as a 2D spatial grounding task. SPF harnesses VLMs to decompose vague language instructions into iterative annotation of 2D waypoints on the input image… transforms predicted 2D waypoints into 3D displacement vectors as action commands for UAVs." This maps directly onto an AirSim-style `get_images()` → VLM → `move_by_velocity_async()` loop, or onto ROS2 image topic → VLM → PX4 offboard `TrajectorySetpoint`.

## Details

### Comparison matrix (candidates × 8 requirements)

| Simulator | 1. Docker/headless GPU | 2. PX4 SITL + ROS2 | 3. Unreal (version) | 4. Sensor suite | 5. Photorealism | 6. VLM/vision + control API | 7. Real-world maps | 8. Dynamic actors | Status / License |
|---|---|---|---|---|---|---|---|---|---|
| **Cosys-AirSim** | Yes (UE5 headless Vulkan `-RenderOffScreen`) | PX4 SITL/HITL + lockstep (MAVLink); ROS2 C++ bridge; PX4 side runs uXRCE-DDS | **UE5.5 target, UE5.4 stable, UE5.2.1 LTS** | RGB, stereo(multi-cam), depth, segmentation, GPU-LiDAR, echo/radar+UWB+Wi-Fi, event, IR, IMU, GPS, baro, mag | High (Nanite/Lumen) | AirSim Python/C++ API: `simGetImages` + `moveByVelocity` | Cesium for Unreal, OSM/StreetMap | UE MassAI/City Sample | **Active**, MIT |
| **Project AirSim (IAMAI)** | Yes (`-RenderOffScreen`/`-nullrhi`; BYO container) | PX4 v1.12.3 SITL + lockstep; ROS2 early-stage (Humble) | **UE5.2 / UE5.7** | RGB, depth, segmentation, LiDAR/GPU-LiDAR/DepthLiDAR, IMU, GPS, baro, mag, radar, airspeed (no event cam) | High | Python async `move_by_velocity_async`, `get_images` | Cesium for Unreal (UE5) | UE MassAI/City Sample | **Active but young** (v0.2.0, Jun 2026), MIT |
| **Colosseum** | Yes (Docker recommended on 22.04) | PX4 SITL + lockstep; ROS2 | UE5.2 (main), UE5.4+ (v2.3.0), UE4.27 branch | AirSim sensor set | High | AirSim API | Cesium/OSM via UE | UE MassAI/City Sample | **Archived 2026-07-11**, MIT |
| **Microsoft AirSim** | Yes | PX4 SITL + lockstep; ROS1/ROS2 | UE4.27 | AirSim sensor set (no event) | Medium-High | AirSim API | Cesium (community) | Limited | **Archived 2023-12-15**, MIT |
| **Isaac Sim + Pegasus** | Yes (official containers, headless livestream) | **Best-in-class** PX4 + ROS2 (Pegasus); PX4 v1.14.3 | No (Omniverse RTX) | RGB, stereo, depth, RTX-LiDAR, IMU, etc. | Very High (RTX) | Python API + ROS2 | Cesium for Omniverse (FSD↔PhysX conflict) | Omniverse agents/GRADE | **Active** (v5.1.0, Oct 2025), BSD-3 / NVIDIA EULA |
| **Gazebo Harmonic** | Yes (official `px4io/px4-sitl-gazebo`) | **Reference** PX4 + ROS2 + uXRCE-DDS | No | RGB, depth, LiDAR, IMU, GPS, baro, mag | Low-Medium | ros_gz image topics | Limited (heightmaps) | Actors plugin (basic) | **Active**, Apache-2.0 |
| **CARLA / CARLA-Air** | Yes (`carlasim/carla` images) | CARLA-Air: AirSim PX4 flight + ROS2 examples (Humble) | **UE4.26 / UE5.5** | RGB, depth, semantic, LiDAR, radar, IMU, GPS | High | Python API (CARLA + AirSim) | OSM importer (`.osm`→road net) | **Best traffic/pedestrian AI** | Active; CARLA-Air young, MIT |
| **Flightmare** | Partial | PX4 via ROS bridge (dated) | No (Unity) | RGB-D, segmentation, rangefinder | Medium-High | Gym/ROS API | No | Limited | Low activity, MIT |
| **FlightGoggles** | Partial | ROS (dated) | No (Unity) | RGB, IR, collision | High (photogrammetry) | ROS API | Photogrammetry scenes | Moving gates only | Dormant, MIT |
| **aerial-autonomy-stack** | **Yes (Dockerized, Jetson Orin)** | PX4/ArduPilot SITL + ROS2 (excellent), >20× real-time | No (Gazebo) | Camera, LiDAR, IMU + YOLO | Low-Medium | ROS2 + Gym; 50 Hz YOLOv8 on Orin | No | Gazebo models | Active (2026), open |

### Sim-to-real alignment with the user's hardware
The user flies a Holybro Pixhawk 6C + Jetson Orin NX 16GB (Seeed reComputer J4012) with PX4 and uXRCE-DDS. The critical design decision is that **the ROS 2 interface in simulation must be identical to hardware**: PX4 SITL should run its `uxrce_dds_client` (`MicroXRCEAgent udp4 -p 8888`) exposing `/fmu/out/vehicle_odometry`, `/fmu/out/vehicle_status`, `/fmu/in/trajectory_setpoint`, etc., exactly as on the real vehicle. AirSim/Cosys-AirSim's MAVLink lockstep coexists with this: MAVLink drives the sim physics handshake while XRCE-DDS drives your autonomy code. This yields near-zero code change from SITL to the Orin. Note the Orin NX 16GB cannot host the UE5 renderer — the simulator must run on a dGPU workstation, with the Orin optionally joined for hardware/software-in-the-loop VLM inference.

## Recommendations

### Primary stack (recommended)
**Cosys-AirSim on UE5.5 (fall back to the UE5.4/5.2.1-LTS releases if a plugin dependency is not yet ported) + Cesium for Unreal + PX4 SITL (lockstep) + ROS 2 Humble via uXRCE-DDS, in a multi-container Docker Compose deployment.** Justification: it is the only live option meeting every hard requirement, its sensor suite (GPU-LiDAR, event, segmentation, stereo, IMU/GPS/baro/mag) is the richest, its AirSim API lineage matches the VLM research ecosystem, and its PX4-side XRCE-DDS gives you sim-to-real parity with the Pixhawk/Orin stack.

### Fallback / parallel tracks
1. **Isaac Sim + Pegasus Simulator** if you can relax the Unreal hard-requirement: superior physics/RTX and the cleanest PX4+ROS2, at the cost of the FSD↔PhysX Cesium conflict (render photorealistic OR physics, not both in one pass).
2. **Gazebo Harmonic** as the always-on PX4 sim-to-real ground-truth baseline for controls/regression, using the official Docker image; consider **aerial-autonomy-stack** as a ready-made Dockerized Gazebo+PX4+ROS2+Jetson-Orin reference since it targets the same edge hardware.
3. **Project AirSim** as a watch-item; re-evaluate for promotion once its ROS2 bridge matures and PX4 support moves past v1.12.3.
4. **CARLA-Air** specifically when a scenario needs dense, scripted urban traffic + pedestrians beneath the drone.

### Staged plan and thresholds
- **Phase 0 (weeks 1–2):** Stand up Gazebo Harmonic + PX4 + uXRCE-DDS + ROS2 Humble in Docker to lock the `/fmu/*` interface and offboard control node against your real airframe params.
- **Phase 1 (weeks 3–6):** Bring up Cosys-AirSim UE5 in a headless GPU container; validate PX4 lockstep (SteppableClock/UseTcp/LockStep) and the AirSim→ROS2 image bridge; port the offboard node unchanged.
- **Phase 2 (weeks 7–10):** Add Cesium for Unreal real-world maps and UE MassAI/City Sample actors + wind; benchmark render FPS and lockstep stability.
- **Phase 3 (weeks 11+):** Attach the VLM inference container; close the See-Point-Fly-style loop (frame → 2D waypoint → 3D velocity setpoint).
- **Decision thresholds:** if UE5 lockstep cannot sustain sensor cadence without PX4 timeouts, or GPU memory forces UE + VLM contention, split onto two GPUs/hosts or fall back to Isaac/Pegasus for the physics-critical experiments. If Cesium tiles render black below ~150 ft AGL for your AOIs, switch that scenario to OSM+PCG or photogrammetry meshes.

### Proposed architecture (container topology + data flow)

**Containers (Docker Compose, shared user-defined bridge network + `--gpus`; use host networking for DDS multicast):**
- **`sim` (GPU):** UE5 + Cosys-AirSim plugin + Cesium for Unreal. Launch packaged binary with `-RenderOffScreen` (Vulkan) under NVIDIA Container Toolkit (`NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility`). Exposes AirSim RPC on **TCP 41451**; MAVLink sim handshake on **TCP 4560**; camera/LiDAR via AirSim API.
- **`px4` (CPU):** PX4 SITL driven by AirSim over MAVLink. MAVLink offboard **UDP 14540** (onboard) / **14580**, GCS **UDP 14550**; connects to `sim` on TCP 4560 with lockstep. Runs `uxrce_dds_client` → **UDP 8888**.
- **`dds` + `ros2` (CPU, can be one container):** `MicroXRCEAgent udp4 -p 8888` bridging PX4 uORB → ROS2; `px4_msgs`; the Cosys-AirSim `airsim_ros2` node publishing `/airsim/drone/*` (`sensor_msgs/Image`, `sensor_msgs/PointCloud2`, `sensor_msgs/Imu`); your offboard controller publishing `/fmu/in/trajectory_setpoint` + `/fmu/in/offboard_control_mode`.
- **`vlm` (GPU):** subscribes to the image topic (or an RTSP/GStreamer stream out of the sim for realistic video-pipeline testing), runs the VLM, and emits velocity/waypoint setpoints back to `ros2` (which forwards to PX4 offboard).

**Data flow / lockstep clock:** UE renders → Cosys-AirSim generates sensor samples → advances the SteppableClock one step per sensor update → PX4 consumes IMU/GPS/baro/mag over MAVLink TCP 4560, so PX4's clock tracks the sim regardless of render latency. Concurrently, PX4 publishes `/fmu/out/vehicle_odometry`/`vehicle_status` via XRCE-DDS to ROS2. Camera frames flow AirSim→ROS2 `/airsim/drone/front_camera`. The VLM consumes frames + odometry, produces a 3D displacement, and writes `/fmu/in/trajectory_setpoint` (or `/mavros/setpoint_velocity/cmd_vel` if using MAVROS) closing the loop. For time synchronization on the ROS2 side, PX4's uXRCE-DDS bridges the OS/sim clock via `/fmu/out/timesync_status`; set `UXRCE_DDS_SYNCT` appropriately when running non-realtime.

**Real-world-map pipeline:** Cesium for Unreal georeferences the level (CesiumGeoreference) and streams Google 3D Tiles/terrain; set PX4 `LPE_LAT/LPE_LON` (and AirSim `OriginGeopoint`) to the same lat/lon so NED/ENU frames and the georeference coincide — this is where AirSim+Cesium coordinate mismatches bite.

**Dynamic actors:** UE MassAI ZoneGraph lanes + Mass Spawners (City Sample crowds + MassTraffic vehicles) for pedestrians/cars; wind via AirSim wind API affecting PX4 dynamics; time-of-day/rain via UE sky/weather.

## Caveats and known pitfalls
- **Two tempting options are archived**: AirSim (Dec 15, 2023) and Colosseum (July 11, 2026). Building on either is technical debt from day one.
- **UE5 container rendering**: Vulkan requires the explicit `-RenderOffScreen` flag (OpenGL falls back automatically without X11); GPU selection under `-RenderOffScreen` with the proprietary NVIDIA stack has historically ignored `SDL_HINT_CUDA_DEVICE` and defaulted to GPU 0; you must set `NVIDIA_DRIVER_CAPABILITIES=graphics` and mount the Vulkan/EGL ICD JSONs. `adamrehn/ue4-runtime` images are a proven base.
- **PX4 lockstep fragility**: slow UE frames can trigger PX4 SITL timeouts; certain PX4 releases historically broke AirSim compatibility (e.g., 1.11 vs 1.10). Pin known-good PX4/AirSim versions and use the barometer `PressureFactorSigma` tweak for GPS lock.
- **Cesium at drone scale**: Google 3D Tiles can go black/unloaded at 10–500 ft AGL due to LOD; keep tiles pinned around the moving vehicle or substitute photogrammetry/OSM meshes for low-altitude AOIs.
- **Cesium+AirSim coordinate system**: AirSim NED origin vs Cesium georeference vs UE centimeters must be reconciled or vehicle position will be offset/rotated.
- **Isaac Sim FSD↔PhysX conflict**: in Isaac 4.5, the Fabric Scene Delegate needed for Cesium tiles disables PhysX — you cannot get Cesium photorealism and physics-accurate dynamics in a single pass; plan separate rendering vs physics passes.
- **GPU memory pressure**: UE5 + VLM inference on one GPU will contend for VRAM; the Orin NX 16GB cannot host the renderer at all. Run the sim on a workstation dGPU and either give the VLM a second GPU or offload VLM inference to the Orin for HITL realism.
- **Project AirSim ROS2 maturity**: its richest topic documentation reads as an illustrative/AI-generated overview; verify against the `/ros` source before depending on exact topic names, and note PX4 is pinned to v1.12.3 with no event-camera support and no official Docker image.

## References
- Colosseum (CodexLabsLLC), archived 2026-07-11 — https://github.com/CodexLabsLLC/Colosseum ; releases https://github.com/CodexLabsLLC/Colosseum/releases ; docs https://codexlabsllc.github.io/Colosseum/
- Microsoft AirSim, archived 2023-12-15 — https://github.com/microsoft/AirSim ; docs https://microsoft.github.io/AirSim/ ; Wikipedia https://en.wikipedia.org/wiki/AirSim
- Cosys-AirSim (Univ. Antwerp) — https://github.com/Cosys-Lab/Cosys-AirSim ; releases https://github.com/Cosys-Lab/Cosys-AirSim/releases ; docs https://cosys-lab.github.io/Cosys-AirSim/ ; site https://cosys-airsim.com/ ; paper arXiv:2303.13381
- Project AirSim (IAMAI) — https://github.com/iamaisim/ProjectAirSim ; Architecture.md https://github.com/iamaisim/ProjectAirSim/blob/main/Architecture.md ; docs https://iamaisim.github.io/ProjectAirSim/ ; PX4 docs https://github.com/iamaisim/ProjectAirSim/blob/main/docs/controllers/px4/px4.md ; v0.2.0 https://github.com/iamaisim/ProjectAirSim/releases/tag/v0.2.0
- Pegasus Simulator — https://github.com/PegasusSimulator/PegasusSimulator ; docs https://pegasussimulator.github.io/PegasusSimulator/ ; paper arXiv:2307.05263 (ICUAS 2024, DOI 10.1109/ICUAS60882.2024.10556959)
- PX4 simulation & ROS2 — https://docs.px4.io/main/en/simulation/ ; AirSim https://docs.px4.io/main/en/sim_airsim/ ; ROS2 user guide https://docs.px4.io/main/en/ros2/user_guide ; uXRCE-DDS https://docs.px4.io/main/en/middleware/uxrce_dds ; Gazebo Classic wind https://docs.px4.io/main/en/sim_gazebo_classic/ ; multi-vehicle https://docs.px4.io/main/en/ros2/multi_vehicle
- AirSim PX4 lockstep & settings — https://microsoft.github.io/AirSim/px4_lockstep/ ; https://microsoft.github.io/AirSim/px4_setup/ ; https://microsoft.github.io/AirSim/settings/
- Wind/flight-dynamics issues — PX4-Autopilot #23756 https://github.com/PX4/PX4-Autopilot/issues/23756 ; SITL_gazebo #287 https://github.com/PX4/sitl_gazebo/issues/287 ; Gazebo weather-plugin study https://www.researchgate.net/publication/383735033
- Cesium for Unreal / Google 3D Tiles — https://cesium.com/learn/unreal/unreal-photorealistic-3d-tiles/ ; Omniverse quickstart https://cesium.com/learn/omniverse/omniverse-quickstart/ ; drone-scale black tiles issue https://community.cesium.com/t/cesium-isaac-sim-terrain-goes-black-at-drone-scale-10-500ft-google-photorealistic-3d-tiles-not-loading-close-up/45997 ; Isaac FSD↔PhysX https://www.seokhyeonbyun.com/projects/starbelt-drone-simulation/ ; AirSim+Cesium SLAM study https://www.researchgate.net/publication/380088136
- OSM→UE StreetMap plugin — https://github.com/ue4plugins/streetmap ; UE5 fork https://github.com/Fristet/StreetMap
- UE5 headless/offscreen rendering in containers — https://unrealcontainers.com/docs/use-cases/cloud-rendering ; ue4-runtime image https://hub.docker.com/r/adamrehn/ue4-runtime ; GPU-select issue https://answers.unrealengine.com/questions/1009375/index.html ; CARLA RenderOffScreen Vulkan issue https://github.com/carla-simulator/carla/issues/8079
- UE5 dynamic actors — City Sample docs https://dev.epicgames.com/documentation/unreal-engine/city-sample-project-unreal-engine-demonstration ; MassAI overview https://www.strayspark.studio/blog/crowd-traffic-simulation-ue5-mass-ai
- CARLA — https://carla.readthedocs.io/ ; CARLA-Air https://github.com/louiszengCN/CarlaAir ; paper HF https://huggingface.co/papers/2603.28032
- Flightmare — https://flightmare.readthedocs.io/ ; paper https://rpg.ifi.uzh.ch/docs/CoRL20_Yunlong.pdf
- aerial-autonomy-stack — https://github.com/JacopoPan/aerial-autonomy-stack ; releases https://github.com/JacopoPan/aerial-autonomy-stack/releases ; paper arXiv:2602.07264
- VLM autonomy — See, Point, Fly arXiv:2509.22653 (CoRL 2025), code https://github.com/Hu-chih-yao/see-point-fly ; OpenFly arXiv:2502.18041
- PX4 SITL Gazebo Docker — https://hub.docker.com/r/px4io/px4-sitl-gazebo