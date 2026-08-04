> **FROZEN — HISTORICAL RECORD. Preserved exactly as written; not maintained.**
>
> This is the simulator landscape survey that opened the project, and the source of its
> original three-stack recommendation. **Two of the three stacks it recommends are ones this
> repo no longer contains** — the Gazebo baseline and Isaac Sim + Pegasus are both retired —
> and the monorepo layout, phase plan and architecture at the end are superseded.
>
> **Read it as evidence, never as instructions.** The file paths, image tags, container
> names and directory layout it proposes refer to things that have been renamed or deleted.
> Its dated findings about the simulator ecosystem, and its PX4 ↔ ROS 2 facts, are preserved
> unchanged and largely still hold; see [`../README.md`](../README.md) for which parts are
> still worth reading.
>
> **The repo today is one simulator: Unreal Engine 5.8 + Cosys-AirSim + PX4 SITL + ROS 2
> Jazzy.** Task-ID renames: [`../id-map.md`](../id-map.md). The repo:
> [`README.md`](../../../README.md).

# Drone Simulation Stack for PX4 + ROS 2 + VLM Sim-to-Real (2026)

## TL;DR
- **Run a dual-simulator stack.** Use **PX4 Gazebo (Harmonic) + ROS 2 Jazzy** as your fast, CI-friendly, physics/SITL baseline, and **NVIDIA Isaac Sim 5.1 + Pegasus Simulator** as your photorealistic perception/RL simulator. Keep **Unreal Engine 5 + Cosys-AirSim** as the photorealism/VLN-benchmark reproduction lane, because your three target papers (SPF, Fly0, OnFly) evaluate on Unreal/AirSim-family or racing sims — none of them uses Isaac.
- **Microsoft AirSim is dead** (development discontinued Dec 15, 2023; team laid off). Do not build new work on it. Its living successors are **Colosseum** (CodexLabs UE5 fork), **Cosys-AirSim** (UE5, best sensor set), and **Project AirSim** (IAMAI fork, Ubuntu 22 only). For Isaac, drone PX4 integration is via **Pegasus Simulator**.
- **Your hardware maps cleanly.** Pixhawk 6C + Jetson Orin NX 16GB + PX4 + uXRCE-DDS is essentially the Fly0/OnFly platform. Serve VLMs onboard with TensorRT + AWQ (OnFly's recipe, Qwen3-VL-4B). Progress SITL → Gazebo photoreal/GPS → Isaac perception → UE5 benchmark reproduction → PX4 HITL on the 6C.

## Key Findings

### 1. Simulator landscape (2026)
- **Microsoft AirSim: archived/deprecated.** Microsoft stated it would release a new platform and "subsequently archiv[e] the original 2017 AirSim." Business Insider (Ashley Stewart, Oct 2023) reported the team was told the whole team would be laid off and the project discontinued, confirmed for **December 15, 2023**. Repos: https://github.com/microsoft/AirSim, https://en.wikipedia.org/wiki/AirSim
- **Project AirSim (IAMAI fork):** Former Microsoft AirSim engineers at IAMAI Simulations released Project AirSim under MIT, DARPA-supported. Supports **Windows 11 and Ubuntu 22 only** — not Ubuntu 24.04. https://github.com/iamaisim/ProjectAirSim · https://iamaisim.github.io/ProjectAirSim/
- **Colosseum (CodexLabsLLC):** Maintained AirSim fork; main branch targets **Unreal Engine 5.6**, supports PX4/ArduPilot SITL + HITL. README states Ubuntu 22.04 is "not currently supported due to Vulkan support" and recommends Docker — a real risk for native Ubuntu 24.04. https://github.com/CodexLabsLLC/Colosseum · https://codexlabsllc.github.io/Colosseum/
- **Cosys-AirSim (Univ. Antwerp, Cosys-Lab):** The most feature-rich living AirSim fork on **UE5 (targets 5.5)**, with GPU-LiDAR, Echo sensors, annotation/instance-segmentation cameras, updated ROS 2 C++ wrapper, Nanite/Lumen, and camera distortion (chromatic aberration, motion blur, lens distortion). External autopilot via MAVLink. https://github.com/Cosys-Lab/Cosys-AirSim · https://cosys-lab.github.io/Cosys-AirSim/
- **NVIDIA Isaac Sim 5.x + Isaac Lab:** General access for Isaac Sim 5.0 / Isaac Lab 2.2 at SIGGRAPH (Aug 11, 2025); Isaac Sim 5.1 current, 6.0 at Early Developer Release. Isaac Sim 5.x uses Python 3.11. Ubuntu 24.04 + ROS 2 Jazzy work but with documented rough edges (rclpy Python 3.11-vs-3.12 mismatch; Nav2 costmap issues). RTX raytraced sensors: RGB, stereo, depth, RTX Lidar (rotating + solid-state via JSON config), IMU (dedicated GPU CUDA-stream codepath), via the modular ROS 2 bridge. https://docs.isaacsim.omniverse.nvidia.com/latest/overview/release_notes.html
- **Pegasus Simulator (ISR/IST Lisbon):** The de-facto PX4-on-Isaac extension, BSD-3. Docs: "2025-10-26: Pegasus Simulator v5.1.0 is released for Isaac 5.1.0. This version is NOT compatible with older versions of Isaac Sim." (tested on Ubuntu 22.04 LTS, NVIDIA driver 550.163.01, PX4-Autopilot v1.14.3). ICUAS 2024 paper DOI 10.1109/ICUAS60882.2024.10556959. https://github.com/PegasusSimulator/PegasusSimulator · https://pegasussimulator.github.io/PegasusSimulator/
- **PX4 official default sim = Gazebo (Harmonic).** PX4 docs: "Gazebo Classic is being downgraded to community supported and is no longer recommended as the default simulation solution." Gazebo has camera/LiDAR/depth/IMU/GPS/baro/mag, ROS 2 via ros_gz + uXRCE-DDS, lockstep, custom SDF worlds. https://docs.px4.io/main/en/sim_gazebo_gz/ — A lightweight headless alternative, **SIH**, runs physics inside PX4 (IMU/GPS/baro/mag only).
- **Newton physics:** Linux Foundation press release (San Jose, Sept 29, 2025): "the Linux Foundation … welcomed Newton, an open source, GPU-accelerated, extensible physics engine … Co-developed by Disney Research, Google DeepMind, and NVIDIA." Newton 1.0 stable ~March 17, 2026. Plugs into Isaac Lab 3.0 / Isaac Sim 6.0 as a swappable backend, but experimental and validated on a limited robot set — **not yet a drone path.** https://docs.isaacsim.omniverse.nvidia.com/6.0.0/physics/newton_physics.html · https://github.com/newton-physics/newton
- **Aerial Gym Simulator (NTNU-ARL):** Isaac Gym-based, thousands of parallel multirotors, GPU raycast LiDAR/depth/segmentation, geometric SE(3) controllers on GPU — for RL, not photorealistic perception. https://github.com/ntnu-arl/aerial_gym_simulator · https://github.com/ntnu-arl/rl_nav

### 2. PX4 + ROS 2 integration
- **ROS 2 Jazzy Jalisco is the LTS pairing for Ubuntu 24.04.** PX4 v1.15/v1.16 use **uXRCE-DDS**, not MAVROS. Path: uxrce_dds_client (in PX4 firmware) ↔ **Micro XRCE-DDS Agent** ↔ **px4_msgs**. MAVROS is the legacy ROS1-era bridge; px4_ros_com is now only example code. https://docs.px4.io/main/en/middleware/uxrce_dds · https://docs.px4.io/main/en/ros2/user_guide
- **Critical version coupling:** the px4_msgs branch MUST match the PX4 firmware's compiled uORB message set. Mismatch silently breaks topics. https://github.com/PX4/px4_msgs
- **External-sim interface:** Most external simulators talk to PX4 SITL over the **Simulator MAVLink API** (local TCP port 4560; HIL_SENSOR, HIL_GPS, HIL_ACTUATOR_CONTROLS, HIL_STATE_QUATERNION, HIL_OPTICAL_FLOW), with lockstep. **Gazebo and SIH are exceptions.** Default UDP ports: 14550 (GCS/QGC), 14540 (offboard). https://docs.px4.io/main/en/simulation/
- **Emerging:** Zenoh (rmw_zenoh) is a future PX4↔ROS 2 middleware (Tier-1 in ROS 2 Kilted Kaiju; binaries for Rolling/Jazzy/Humble) but not yet the default RMW.

### 3. Sensor fidelity
- **Isaac Sim (best synthetic realism):** RTX raytraced Lidar (rotating + solid-state; each RTX sensor needs its own viewport), stereo/depth cameras (RealSense D455 digital twin includes a 6-axis IMU), IMU with a dedicated GPU CUDA-stream codepath, RTX sensors using Hydra time (omni.timeline) for accurate sim-time tracking. Publish to ROS 2 as PointCloud2/LaserScan/Image via OmniGraph nodes.
- **Cosys-AirSim:** GPU-LiDAR with tunable noise and ground-truth labels, Echo sensors, camera distortion, IMU/GPS with realistic drift/multipath; data in ROS-standard coordinates (not NED by default).
- **Gazebo:** Full camera/LiDAR/depth/IMU/GPS/baro/mag; the Gazebo Classic depth model emulates an Intel RealSense D455.
- **VIO/VINS caveat:** IMU–camera timestamp synchronization is the recurring pain point across all sims. Isaac's Hydra-time RTX sensors + GPU IMU codepath are the most defensible for VINS-Fusion / OpenVINS / ORB-SLAM3, but you must measure timestamp jitter and rate stability before trusting sim VIO results; validate against Gazebo lockstep as a control.
- **Domain randomization:** Isaac Lab and Cosys-AirSim (dynamic weather, day/night, terrain randomization) both support it.

### 4. Photorealism / environment assets
- **Cesium (georeferenced real-world terrain).** Cesium for Unreal and Cesium for Omniverse stream Google Photorealistic 3D Tiles, Bing/OSM buildings, and georeferenced WGS84 terrain; Cesium for Unreal can stream Gaussian-splat payloads via 3D Tiles. Apache-2.0. https://cesium.com/use-cases/drones/ · https://github.com/CesiumGS/cesium-unreal-samples
- **Isaac Sim + Cesium gotcha:** In Isaac Sim 4.5, the Fabric Scene Delegate (needed for 3D-tile streaming) and PhysX cannot run simultaneously — photoreal rendering OR physics-accurate dynamics, not both. https://www.seokhyeonbyun.com/projects/starbelt-drone-simulation/
- **UE5 assets:** Fab/Marketplace, Quixel Megascans, Nanite/Lumen. **Isaac/Omniverse:** OpenUSD + SimReady assets.
- **3DGS/NeRF:** OpenFly (arXiv:2502.18041) integrates Unreal Engine, GTA V, Google Earth, and 3D Gaussian Splatting for real-to-sim rendering.

### 5. VLM experimentation — what your target papers actually used
- **See-Point-Fly (SPF, arXiv:2509.22653, CoRL 2025):** "We achieve 93.9% and 92.7% overall success rates in simulation and real-world settings, respectively," and SPF "sets a new state of the art in DRL simulation benchmark, outperforming the previous best method by an absolute margin of 63%." **Important:** that "DRL" is the **Drone Racing League Simulator**, *not* a deep-RL/AirSim benchmark — a common misreading. Real-world used a **DJI Tello EDU** via DJITelloPy. Closed-loop VLM cycle ≈ 1.5–3 s. Method: VLM annotates 2D waypoints → projects to 3D displacement with adaptive step size. https://github.com/Hu-chih-yao/see-point-fly · https://spf-web.pages.dev/
- **Fly0 (arXiv:2602.15875, NUDT):** Uses **Unreal Engine 4 + Microsoft AirSim**, evaluated on **AerialVLN and OpenFly** ("24 distinct urban and suburban maps … over 14,000 human-annotated navigation instructions"). Three-stage decouple: MLLM 2D pixel grounding → depth-informed back-projection → LiDAR + **Ego-Planner** collision-free trajectory. SR ~70.4 on AerialVLN, improving SR >20% and cutting NE ~50%. Real platform: **Pixhawk 6C Mini + PX4 + Jetson Orin NX 16GB**. https://github.com/xuzhenxing1/Fly0
- **OnFly (arXiv:2603.10682, SYSU/SUSTech/HKUST-GZ):** "10 high-fidelity 3D scenes in UE 4.27 … The benchmark contains 150 tasks," and "In simulation, OnFly improves task success from 26.4% to 67.8%." Onboard on **Jetson Orin NX** with "TensorRT … AWQ quantization to the LLM … keeping the ViT in FP16 … cache visual features … reuse the LLM's KV-cache … and use CUDA Graphs." Uses **Qwen3-VL-4B-AWQ**; dual-agent (high-freq target generator + low-freq monitor); Mid360 LiDAR + D435 depth. https://github.com/Robotics-STAR-Lab/OnFly
- **Benchmarks:** AerialVLN (ICCV 2023, https://github.com/AirVLN/AirVLN), OpenFly, OpenUAV/UAV-Need-Help, CityNav, TypeFly (arXiv:2312.14950), AeroVerse, UAVBench (arXiv:2511.11252), EmbodiedCity. https://github.com/Sautenich/Awesome-Aerial-Vision-Language-Navigation
- **VLM serving on Orin NX:** **jetson-containers** (https://github.com/dusty-nv/jetson-containers) provides pre-built vLLM, MLC-LLM, Ollama, llama.cpp. vLLM supports AWQ/W4A16/GPTQ-Int4/bitsandbytes; MLC-LLM and llama.cpp use INT4/GGUF (MLC ~2× llama.cpp throughput). Ollama has a **documented Qwen3-VL GPU-offload bug** on JetPack 6.2.1 (issue #13247), while qwen2.5vl:3b offloads correctly. **No official Orin-NX-specific VLM tokens/sec numbers are published** — benchmark on your own hardware.
- **Latency/decomposition pattern:** All three use "**slow semantic reasoner + fast geometric tracker/planner**". Target the SPF cycle envelope (≈1.5–3 s VLM decisions) with a high-rate geometric controller in between.

### 6. Hardware / deployment
- **Isaac Sim workstation:** minimum RTX 3070 / 8 GB VRAM / 32 GB RAM / 50 GB SSD; "good" = RTX 4080 / 16 GB / 64 GB; "ideal" = RTX 6000 Ada / 48 GB VRAM / 64 GB RAM. **GPUs without RT cores (A100/H100) are unsupported.** Isaac Sim 5.x needs Python 3.11; NVIDIA production driver ≥ 580.65.06 recommended on Linux. NGC container `nvcr.io/nvidia/isaac-sim:5.1.0` runs headless for CI/RL. https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_workstation.html · https://isaac-sim.github.io/IsaacLab/main/source/setup/installation/index.html
- **UE5 / Cosys-AirSim (lighter):** ~6 GB VRAM, 16–32 GB RAM, ~50 GB install.
- Both containerize with the NVIDIA Container Toolkit; use `xhost +local:` for X11.

### 7. SITL → HITL path
- PX4 SITL → PX4 **HITL on the actual Pixhawk 6C**. PX4 docs: HITL "is community supported and maintained … may or may not work with current versions of PX4"; real PX4 firmware in HIL mode over USB/UART, sim bridging MAVLink to QGroundControl. Enable via QGC → Setup → Safety → HITL Enabled. https://docs.px4.io/main/en/simulation/hitl
- **Keep one ROS 2 graph across sim and real:** identical `/fmu/in` and `/fmu/out` namespaces, `use_sim_time:=true` only in sim, same `ros2 launch` composition, swap only the transport. Orin NX ↔ Pixhawk 6C over UART. https://docs.px4.io/main/en/companion_computer/holybro_pixhawk_jetson_baseboard

## Details

### Comparison matrix

| Sim | Ubuntu 24.04 | ROS 2 Jazzy | PX4 SITL | Photoreal | Sensors | Headless/CI | Maintenance | License |
|---|---|---|---|---|---|---|---|---|
| **Gazebo Harmonic** | Yes | Yes (ros_gz) | Yes (PX4 default) | Low–med | All | Excellent | Active | Apache-2.0 |
| **Isaac Sim 5.1 + Pegasus** | Yes (rough edges) | Yes (rough edges) | Yes (MAVLink) | Very high (RTX) | All (RTX raytraced) | Yes (NGC) | Active | Isaac EULA / BSD-3 |
| **Cosys-AirSim (UE5.5)** | Via Docker / native | Yes (C++ wrapper) | Yes (MAVLink HIL) | Very high | RGB / GPU-LiDAR / GPS / IMU | Partial | Active | MIT |
| **Colosseum (UE5.6)** | Docker recommended | Wrapper | Yes | High | AirSim set | Partial | Semi-active | MIT |
| **Project AirSim (IAMAI)** | No (Ubuntu 22 only) | Limited | Yes | High | AirSim set | Partial | Early | MIT |
| **Aerial Gym** | Yes | N/A (RL) | No | Low (raycast) | depth / LiDAR / seg | Yes | Active | BSD |
| **Microsoft AirSim** | No | No | Legacy | Med | AirSim set | — | DEAD (Dec 2023) | MIT |

### Recommended architecture

**Primary: dual-simulator.**
1. **Gazebo Harmonic + PX4 v1.16 SITL + ROS 2 Jazzy** — fast iteration, CI, GPS-nav, controls, lockstep, headless RL.
2. **Isaac Sim 5.1 + Pegasus Simulator** — photorealistic perception, RTX Lidar/stereo/depth, domain randomization, VLM-in-the-loop and RL. Pegasus bridges PX4 over MAVLink so the same ROS 2 graph is reused unchanged.

**Secondary / benchmark-reproduction lane: Unreal Engine 5 + Cosys-AirSim** — to reproduce AerialVLN/OpenFly-style benchmarks and host Cesium georeferenced terrain.

**Block diagram (data flow):**
`Simulator {Gazebo | Isaac+Pegasus | UE5+Cosys-AirSim}` ↔ `PX4 SITL {gz-bridge for Gazebo; Simulator MAVLink API TCP 4560 lockstep for others}` ↔ `Micro XRCE-DDS Agent (UDP 8888)` ↔ `ROS 2 Jazzy graph [perception → planner → VLM client]` ↔ `QGroundControl (UDP 14550)` ↔ `rosbag2 / MCAP`.

**Containerization:** Docker Compose + NVIDIA Container Toolkit — services for `px4-sitl`, `micro-xrce-agent`, `sim` (GPU passthrough, X11/Wayland), `ros2-ws`, `vlm-server`, `qgc`. Isaac from NGC.

**Monorepo layout:**
```
drone-sim/
  docker/            # compose files, per-service Dockerfiles
  px4/               # PX4-Autopilot submodule (pinned v1.16)
  ros2_ws/src/
    px4_msgs/        # branch-matched to PX4 release
    perception/      # VIO, depth, lidar processing
    planning/        # ego-planner-style trajectory + avoidance
    vlm_client/      # target-generator + fast-tracker nodes
    bringup/         # ros2 launch: sim.launch.py / real.launch.py
  sim/
    gazebo/          # SDF worlds/models
    isaac/           # Pegasus standalone scripts + USD scenes
    ue5/             # Cosys-AirSim project + Cesium
  vlm/               # jetson-containers configs, AWQ model recipes
```

**Phased build plan:**
- **Phase 0 — Foundations:** Ubuntu 24.04 + ROS 2 Jazzy + PX4 v1.16 + Micro XRCE-DDS Agent + px4_msgs + Gazebo Harmonic. Validate offboard control and rosbag2/MCAP logging.
- **Phase 1 — GPS-based nav:** Gazebo baseline; add UE5+Cesium for georeferenced terrain. Waypoint/mission nav; inject GPS dropouts.
- **Phase 2 — Vision-based nav + obstacle & collision avoidance:** Isaac Sim 5.1 + Pegasus for RTX camera/stereo/depth/Lidar; VIO (OpenVINS/VINS-Fusion), Ego-Planner; domain randomization.
- **Phase 3 — VLM experimentation:** Replicate SPF/Fly0/OnFly. Slow VLM target-generator + fast geometric tracker; depth back-projection; VLM served onboard (TensorRT + AWQ, Qwen3-VL-4B). Reproduce AerialVLN/OpenFly in the UE5 lane.
- **Phase 4 — HITL & sim-to-real:** PX4 HITL on the Pixhawk 6C, then real flights on the Orin NX with the identical ROS 2 graph.

### Pitfalls / version pinning
- **Isaac Sim ↔ ROS 2 coupling:** Isaac 5.x ships Python 3.11; ROS 2 Jazzy uses Python 3.12 → `rclpy._rclpy_pybind11` import errors. Use NVIDIA's documented Jazzy workspace build or run ROS 2 in Docker.
- **PX4 ↔ px4_msgs:** must be branch-matched, or DDS type mismatches silently break topics.
- **UE5 ↔ Cosys-AirSim/Colosseum:** pin exact engine versions (Cosys 5.5, Colosseum 5.6).
- **Pegasus ↔ Isaac:** each Pegasus release is explicitly NOT backward-compatible (v5.1.0 ↔ Isaac 5.1.0).
- **Cesium FSD vs PhysX** conflict in Isaac 4.5 (verify in 5.x).
- **Ubuntu 24.04 driver/glibc:** NVIDIA driver ≥ 580.65.06 for Isaac 5.x.
- **Gazebo Harmonic + PX4 on 24.04:** open PX4 issues report sporadic Accel/Mag TIMEOUT crashes (#25089) and a v1.16-alpha SITL error (#24159) — pin to a known-good PX4 tag.

## Recommendations
1. **Start now on Gazebo Harmonic + PX4 v1.16 + ROS 2 Jazzy** (Phases 0–1). Lowest risk; stabilize the ROS 2 graph, uXRCE-DDS, and CI before adding rendering complexity.
2. **Stand up Isaac Sim 5.1 + Pegasus in parallel** on an RTX 4080/16 GB-or-better workstation for Phase 2–3. **8 GB VRAM is insufficient** — budget ≥16 GB.
3. **Keep Cosys-AirSim/UE5 as the benchmark-reproduction lane.** **Do NOT invest in Microsoft AirSim (dead) or Project AirSim (Ubuntu 22 only).**
4. **Mirror OnFly's onboard serving** (TensorRT + AWQ LLM, FP16 ViT, Qwen3-VL-4B) via jetson-containers; validate closed-loop latency against SPF's ≈1.5–3 s envelope.
5. **Thresholds that change the plan:** Isaac↔Jazzy Python friction → run ROS 2 in Docker; sim VIO timestamp jitter → validate on Gazebo lockstep first; Cesium FSD/PhysX persists in 5.x → do georeferenced terrain in UE5+Cesium; Ollama Qwen3-VL bug → serve via vLLM or MLC.

## Caveats
- **SPF's "DRL benchmark" = Drone Racing League Simulator, not a deep-RL AirSim benchmark.**
- Isaac Sim on Ubuntu 24.04 + Jazzy has multiple open NVIDIA-forum issues; workable-but-rough, not turnkey.
- OnFly's exact per-step latency numbers (Table IV) were not extractable from the paper text; read them from the PDF.
- No official VLM tokens/sec figures published for Orin NX 16 GB specifically — benchmark on your own hardware.
- Newton physics is beta/early and not yet a drone-ready path; PhysX remains the drone default.
- HITL is explicitly "community supported" by PX4 — budget integration time.
