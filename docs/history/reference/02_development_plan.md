> **FROZEN — HISTORICAL RECORD. Preserved exactly as written; not maintained.**
>
> This is the phase-gated build plan the project was executed against, written when it ran
> **three parallel simulator stacks**. Its phase order, its parallel-stack sequencing, its
> two-PX4-tree requirement and its container topology all describe **stacks this repo no
> longer contains** — the Gazebo baseline and Isaac Sim + Pegasus are retired, and the second
> PX4 tree existed only for Pegasus.
>
> **Read it as evidence, never as instructions.** The setup commands, image tags, compose
> services, monorepo layout and task IDs it specifies refer to things that have been renamed
> or deleted. Its risk register, its version-coupling analysis and its evaluation metric
> definitions are preserved unchanged and are the parts still worth reading; see
> [`../README.md`](../README.md).
>
> **The repo today is one simulator: Unreal Engine 5.8 + Cosys-AirSim + PX4 SITL + ROS 2
> Jazzy.** Task-ID renames: [`../id-map.md`](../id-map.md). The repo:
> [`README.md`](../../../README.md).

# Executable Development Plan — Triple-Lane Drone Simulation Framework (PX4 · ROS 2 Jazzy · Isaac Sim · Gazebo · AirSim)

## TL;DR
- **Build in strict lane order:** Lane A (PX4 v1.16 + Gazebo Harmonic, headless, lockstep) first as the CI/iteration backbone; then Lane B (Isaac Sim 5.1 + Pegasus v5.1.0) for photorealistic perception/RL; then Lane C (UE5.5 + Cosys-AirSim) last and **only** for AerialVLN/OpenFly benchmark reproduction — treat Lane C as high-risk/optional. A single experienced engineer needs ~18–22 weeks to reach real-flight VLM navigation; a 3-person team ~9–11 weeks.
- **Two version-coupling landmines dictate the architecture and must be resolved in Phase 0:** (1) Pegasus Simulator v5.1.0 was developed/tested against **PX4-Autopilot v1.14.3**, not your pinned v1.16.x — Pegasus docs verbatim: *"We have tested Pegasus Simulator with Isaac Sim 5.1.0 release on Ubuntu 22.04LTS with NVIDIA driver 550.163.01. The PX4-Autopilot used during development was v.14.3."* Lane B must run a **separate PX4 v1.14.3 checkout** while Lanes A and real hardware use v1.16.x + uXRCE-DDS. (2) Isaac Sim 5.1 ships **Python 3.11** while ROS 2 Jazzy on Ubuntu 24.04 is **Python 3.12**, so `rclpy` cannot be shared.

---

## Key Findings (decision-driving)

1. **Lane B version conflict is real and unavoidable.** Pegasus v5.1.0 (released 2025-10-26, for Isaac Sim 5.1.0) documents testing on Ubuntu 22.04, NVIDIA driver 550.163.01, PX4 v1.14.3. Your pinned firmware is v1.16.x, which introduced uORB message versioning and a required ROS 2 Message Translation Node. **Decision: run Pegasus's MAVLink SITL on a dedicated PX4 v1.14.3 build; keep uXRCE-DDS + px4_msgs (`release/1.16`) on your v1.16.x build for Lane A and real hardware.**

2. **Isaac Sim ↔ ROS 2 Jazzy Python mismatch is confirmed with an official workaround.** Isaac Sim 5.1 supports only Python 3.11; Jazzy debs are compiled for 3.12. Sourcing system Jazzy into Isaac's Python throws `ModuleNotFoundError: No module named 'rclpy._rclpy_pybind11'` (C-extension ABI mismatch). NVIDIA's documented fix ("Enabling rclpy, Custom ROS 2 Packages, and Workspaces with Python 3.11"): build a minimal Jazzy workspace with **Python 3.11** using the Isaac Sim ROS Workspaces repo Dockerfiles/`build_ros.sh`, launch Isaac Sim from that sourced terminal, and run application nodes from a *separate* system-Jazzy (3.12) terminal — the two halves meet over DDS.

3. **PX4 v1.16 + Gazebo Harmonic on Ubuntu 24.04 has documented instability** — `ERROR [sensors] Accel #0 fail: TIMEOUT!` / `ERROR [vehicle_magnetometer] MAG #0 failed: TIMEOUT!` — tracked in PX4 issues #25089, #24159, #24595, #26299. Documented root cause: Gazebo multicast flooding the host network (#24595), plus CPU/real-time-factor starvation. Mitigations: use the single-command `make px4_sitl gz_x500` launch (not the manual split-terminal/standalone method, which reproducibly triggers "bobbing" + TIMEOUT), constrain Gazebo transport to loopback, allocate enough cores, run headless.

4. **cuVSLAM (Isaac ROS Visual SLAM) targets ROS 2 Jazzy and is the recommended VIO for the Jetson Orin NX.** NVIDIA docs: *"This package is designed and tested to be compatible with ROS 2 Jazzy running on Jetson, an x86_64 system with an NVIDIA GPU, or a DGX Spark workstation."* `isaac_ros_nvblox` is likewise Jazzy + Jetson compatible and yields a Nav2 costmap and 3D reconstruction.

5. **EGO-Planner is the correct collision-free local planner — exactly what Fly0 uses** (50 Hz control loop, MLLM re-grounding at ~0.5 Hz). The canonical `ZJU-FAST-Lab/ego-planner` is ROS 1 (catkin, Ubuntu ≤20.04); **ROS 2 support lives in the EGO-Swarm repo** (set `drone_id=0`). A PX4-integration fork exists (`hyq123-cmd/px4_ego_planner`). Budget for a ROS 2 port/validation. OnFly instead uses an ESDF-based Fast Planner.

6. **Cesium georeferenced terrain is blocked by an unresolved FSD↔PhysX conflict.** Cesium for Omniverse *requires* the Fabric Scene Delegate, but FSD is mutually exclusive with PhysX. NVIDIA's error: *"Fabric Scene Delegate (FSD) does not support physics simulation, please disable FSD in render settings. Physics will be disabled,"* and an NVIDIA engineer stated *"You cannot do Cesium and PhysX together."* Still open as of Isaac Sim 6.0.0 / Cesium for Omniverse 0.29.0 (July 2026). **Decision: use Cesium only for non-physics photoreal rendering/data-gen; fly physics on baked static meshes.**

7. **Do not use Ollama for onboard VLM serving.** ollama/ollama issue #13247: Qwen3-VL 2B/4B *"is entirely loaded onto the CPU, and zero layers are offloaded to the GPU"* on Jetson Orin (JetPack 6.2.1), while Qwen2.5-VL:3B offloads correctly. Use vLLM/SGLang (dev) and TensorRT-LLM/jetson-containers (onboard).

---

## Phased Roadmap

### Phase 0 — Environment & Version Lock
**Goal / DoD:** Reproducible, pinned toolchain; every component passes its smoke test; a `versions.lock` committed.
**Exit criteria:** `make px4_sitl gz_x500` flies headless in a container with no Accel/Mag TIMEOUT over a 5-min run; `ros2 topic list` shows `/fmu/out/*`; QGC connects; Isaac Sim 5.1 launches and its Python-3.11 ROS bridge publishes `/clock`; a "hello VLM" call hits a vLLM OpenAI-compatible endpoint.
**Effort:** 8–12 engineer-days.
**Work items:** Ubuntu 24.04 + NVIDIA driver (newest Production Branch; verify against Isaac 5.1 release notes); NVIDIA Container Toolkit + CUDA-in-Docker validation; ROS 2 Jazzy via the **new `ros2-apt-source` .deb** (old apt-key method retired 2025-06-01); PX4 v1.16.x clone + submodules + `Tools/setup/ubuntu.sh` + build `gz_x500` and depth/lidar variants; Micro-XRCE-DDS-Agent (pin v2.4.2) + px4_msgs `release/1.16`; Isaac Sim 5.1 + Python-3.11 ROS workspace; second PX4 v1.14.3 checkout for Pegasus; Pegasus v5.1.0 editable install; QGC AppImage.
**Risks/mitigations:** Isaac driver-detection false negatives (Production Branch `.run`; clear `~/.cache/ov`, `~/.local/share/ov`); PX4 Accel/Mag TIMEOUT (headless + loopback transport + more cores).

### Phase 1 — Lane A Baseline: GPS Navigation + Offboard Control
**Goal / DoD:** Deterministic headless PX4+Gazebo SITL with a ROS 2 offboard controller flying GPS waypoint missions in lockstep; recorded to MCAP; CI run <10 min.
**Exit criteria:** automated test takes off, flies a 4-waypoint square, lands, SR=100% over 10 seeded runs; MCAP artifact uploaded.
**Effort:** 10–15 engineer-days.
**Work items:** offboard control node (`TrajectorySetpoint`/`OffboardControlMode`/`VehicleCommand`); `sim.launch.py` with `use_sim_time:=true`; frozen topic/namespace conventions for sim↔real parity; seeded scenario runner; MCAP recording; GitHub Actions headless SITL job.
**Risks:** SITL flakiness in CI (retry ×2 + RTF-floor assertion); lockstep desync → PX4 failsafe (set `param set-default COM_OF_LOSS_T 15`).
**Unlocks:** **GPS navigation.**

### Phase 2 — Perception & Obstacle/Collision Avoidance
**Goal / DoD:** Depth/LiDAR-equipped X500 builds a local map and flies collision-free through a cluttered world with EGO-Planner.
**Exit criteria:** 0 collisions over 20 seeded cluttered-world runs; reach-goal success ≥80%; replan latency logged.
**Effort:** 15–20 engineer-days (dominated by EGO-Planner ROS 2 port/validation).
**Work items:** use `gz_x500_depth`, `x500_lidar_2d`, `x500_lidar_front`, `x500_lidar_down` (enumerate/verify the full list under `Tools/simulation` + PX4-gazebo-models at build time); mapping via `isaac_ros_nvblox` (GPU) or OctoMap (CPU fallback); port EGO-Planner (EGO-Swarm ROS 2, `drone_id=0`) and bridge trajectory to PX4 offboard; obstacle scenario library.
**Risks:** EGO-Planner ROS 2 port underestimated (fallback: ROS 1 bridge container); nvblox needs GPU (gate CI with OctoMap).
**Unlocks:** **obstacle avoidance, collision avoidance.**

### Phase 3 — VLM Vision-Nav + GPS-denied/VIO
**Goal / DoD:** Slow VLM target-generator + fast EGO-Planner tracker against a dev-time vLLM Qwen3-VL endpoint; 2D-waypoint→depth-back-projection→3D goal; GPS-denied flight on cuVSLAM VIO fed to EKF2.
**Exit criteria:** VLM-commanded SR ≥50% (SR@5m) on a 20-episode internal set; VIO-only hover drift <1 m/60 s; end-to-end target-gen latency budget documented.
**Effort:** 20–28 engineer-days.
**Work items:** `vlm_client` node (OpenAI-compatible), target-generator/tracker message contracts + watchdogs; depth back-projection; cuVSLAM → `/fmu/in/vehicle_visual_odometry` with EKF2 params (`EKF2_EV_CTRL`, `EKF2_HGT_REF`, `EKF2_EV_DELAY`, `EKF2_EV_NOISE_MD`, `EKF2_EVP/EVV/EVA_NOISE`); GPS-dropout injection; Lane B photoreal scenes via Pegasus (PX4 v1.14.3).
**Risks:** Isaac/ROS Python split; VLM latency 1–3 s causes oscillation (event-triggered re-grounding ~0.5 Hz per Fly0); EKF2 fails to converge on EV-only (stream 30–50 Hz odometry, align frames, watch for drift-to-origin as in PX4 #19859).
**Unlocks:** **vision-based navigation, VLM experimentation.**

### Phase 4 — Benchmark Reproduction + Onboard VLM + HITL/Real Flight
**Goal / DoD:** Reproduce AerialVLN/OpenFly-style eval; port VLM stack onboard Jetson Orin NX (TensorRT + AWQ); PX4 HITL then real X500 flight.
**Exit criteria:** AerialVLN SR within ~10 absolute points of Fly0's 70.43% (or a documented gap analysis); onboard decision latency ≤1 s; one successful real GPS-waypoint flight + one VLM-nav flight.
**Effort:** 25–35 engineer-days.
**Work items:** UE5.5 + Cosys-AirSim from source (precompiled Linux plugin targets UE5.2.1; the UE5.5 branch was a March-2025 pre-release — pin a known-good commit); AerialVLN/OpenFly scene + instruction ingestion; batch evaluation runner emitting SR/SPL/NE/OSR/CR; jetson-containers vLLM/TensorRT-LLM Qwen3-VL-4B-AWQ (FP16 ViT, KV-cache reuse, CUDA graphs); HITL bring-up on Pixhawk 6C; flight-test cards.
**Risks:** Cosys-AirSim UE5.5 pinning; Cesium FSD/PhysX conflict (render-only); Ollama Qwen3-VL Jetson bug #13247.

---

## Environment Setup — Exact Commands

**1. NVIDIA driver + container toolkit (Ubuntu 24.04):**
```bash
sudo ubuntu-drivers install    # or newest Production Branch .run
nvidia-smi
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
# add the toolkit repo, then:
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
docker run --rm --gpus all ubuntu nvidia-smi   # smoke test
```

**2. ROS 2 Jazzy (new apt-source method, post-2025-06-01):**
```bash
sudo apt install software-properties-common && sudo add-apt-repository universe
sudo apt update && sudo apt install curl -y
export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F\" '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo $VERSION_CODENAME)_all.deb"
sudo apt install /tmp/ros2-apt-source.deb && sudo apt update
sudo apt install -y ros-jazzy-desktop ros-dev-tools
# If migrating: sudo rm /etc/apt/sources.list.d/ros2.list /usr/share/keyrings/ros-archive-keyring.gpg
```

**3. PX4 v1.16.x + Gazebo Harmonic:**
```bash
git clone https://github.com/PX4/PX4-Autopilot.git --recursive
cd PX4-Autopilot && git checkout v1.16.0 && git submodule update --init --recursive
bash Tools/setup/ubuntu.sh          # Harmonic/Ionic/Jetty supported on 24.04
make px4_sitl gz_x500                # smoke — do NOT append _default
```

**4. Micro-XRCE-DDS-Agent + px4_msgs (branch-match rule):**
```bash
git clone -b v2.4.2 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git
cd Micro-XRCE-DDS-Agent && mkdir build && cd build && cmake .. && make && sudo make install && sudo ldconfig /usr/local/lib/
MicroXRCEAgent udp4 -p 8888          # smoke
mkdir -p ~/px4_ros/src && cd ~/px4_ros/src
git clone -b release/1.16 https://github.com/PX4/px4_msgs.git   # branch MUST match firmware
git clone https://github.com/PX4/px4_ros_com.git
cd ~/px4_ros && colcon build && source install/setup.bash
```

**5. Isaac Sim 5.1 + Python-3.11 ROS workspace (the mismatch workaround):**
- Install Isaac Sim 5.1 (container or workstation binary). Isaac ships **Python 3.11**.
- Clone the Isaac Sim ROS Workspaces repo; build `jazzy_ws` with **Python 3.11** via its Dockerfile/`build_ros.sh`.
- Launch Isaac Sim from that sourced terminal; run application nodes from a *separate* system-Jazzy (3.12) terminal.
- Smoke: publish `/clock` from Isaac; `ros2 topic echo /clock` from the system terminal.

**6. Pegasus Simulator v5.1.0 (Lane B, PX4 v1.14.3):**
```bash
git clone https://github.com/PegasusSimulator/PegasusSimulator.git
cd PegasusSimulator/extensions
ISAACSIM_PYTHON -m pip install --editable pegasus.simulator
# NOTE (v5.1.0 changelog): launch examples with `isaac_run`, not ISAACSIM_PYTHON
# Build a SEPARATE PX4 v1.14.3 for Pegasus MAVLink SITL
```

**7. UE5.5 + Cosys-AirSim (Lane C, Phase 4):** build from source. Docker base: `ghcr.io/epicgames/unreal-engine:dev-slim-5.5.4`.

**8. QGroundControl:** AppImage, `chmod +x`, add user to `dialout`, remove `modemmanager`.

---

## Containerization Plan
`docker-compose` services: `px4-sitl` (v1.16, Lane A), `px4-sitl-mavlink` (v1.14.3 for Pegasus), `xrce-agent`, `gazebo`, `isaac-sim` (GPU, Py3.11 ROS), `ros2-ws` (Py3.12 app nodes), `vlm-server` (vLLM/SGLang, GPU), `qgc`, `foxglove`/`rviz`, `recording`.
- **GPU passthrough:** NVIDIA runtime / `--gpus all`.
- **Display:** X11 via `/tmp/.X11-unix` + `xhost +local:docker`; prefer headless (`HEADLESS=1`) for CI/RL.
- **Network:** shared bridge; ports 14550 (QGC), 14540 (offboard), 4560 (Gazebo↔PX4 sim), 8888 (uXRCE-DDS). Constrain Gazebo transport to loopback (PX4 #24595).
- **Volumes:** shared `/rosbags`, `/scenarios`, `/models`.
- **Headless CI/RL:** CPU-only Lane A image for regression; Isaac/nvblox/VLM on a self-hosted GPU runner.

## Repo / Monorepo Structure
```
drone-sim/
├─ versions.lock            # pinned SHAs/tags: PX4 (x2: 1.16 + 1.14.3), px4_msgs, Isaac, Pegasus, AirSim, planner
├─ .repos                   # vcstool manifest (prefer over submodules for 3rd-party)
├─ docker/                  # per-service Dockerfiles + compose
├─ ros2_ws/src/
│  ├─ interfaces/           # msg/srv: TargetWaypoint, TrackerGoal, VlmQuery/Result
│  ├─ bringup/              # sim.launch.py, real.launch.py, shared includes
│  ├─ sim_bringup/          # Gazebo/Isaac/AirSim spawn + scenario
│  ├─ perception/           # depth, cuVSLAM/VIO glue, nvblox glue
│  ├─ state_estimation/     # EKF2 param sets, EV odometry publisher
│  ├─ planning/             # ego_planner_ros2 port + PX4 offboard bridge
│  ├─ control/              # offboard setpoint node
│  ├─ vlm_client/           # slow target-generator + fast tracker
│  └─ evaluation/           # metrics (SR/SPL/NE/OSR/CR), batch runner
├─ scenarios/               # seeded worlds, instruction sets (AerialVLN/OpenFly ingest)
└─ configs/                 # per-lane YAML overrides
```
- **Launch architecture:** shared includes parameterized by `use_sim_time` and namespace; identical topic names sim↔real (`/fmu/*`, `/vlm/target`, `/planner/trajectory`).
- **Git strategy:** vcstool `.repos` for third-party; submodules only for tightly-owned forks; Renovate/Dependabot on the lockfile.
- **VLM↔tracker contract:** `TargetWaypoint{header, geometry_msgs/PointStamped goal_3d, float32 confidence, uint8 source(VLM|TRACK), builtin_interfaces/Duration ttl}`; tracker publishes `TrackerGoal` at 50 Hz; VLM re-grounds ~0.5 Hz; watchdog invalidates goal after `ttl` → hover/hold.

## CI/CD Plan
- GitHub Actions jobs: `build` (colcon), `lint` (ament_lint / flake8 / cpplint), `unit`, `sitl-integration` (headless PX4+Gazebo Lane A, 10-min budget), `scenario-regression` (seeded; asserts SR + collision thresholds + RTF floor).
- Isaac/nvblox/VLM jobs on a **self-hosted GPU runner**.
- Artifacts: MCAP rosbags to object storage; flaky-test policy = retry ×2 **plus** an RTF-floor assertion.

## Evaluation / Benchmarking Framework
Implement all metrics (papers disagree):
- **SR** — stop within threshold. AerialVLN/OpenFly use **20 m** (OpenFly: *"a task is considered successful if the UAV stops within 20 m of the target"*); Fly0 and OnFly use **5 m** (Fly0: *"SR: the ratio of navigation episodes where the UAV terminates within a threshold distance (d_th=5m)"*). **Make the threshold a config parameter.**
- **SPL**, **NE**, **OSR**, **collision count / CR**, **time-to-target**, **path length**, **intervention rate**; optionally nDTW/CLS.
- **Scenario format:** YAML `{world, seed, spawn, goal, instruction}`.
- **Recording:** rosbag2→MCAP; record `/fmu/out/vehicle_local_position`, `/fmu/out/vehicle_odometry`, camera/depth, `/vlm/*`, `/planner/trajectory`, `/tf`. RGB-D at 640×480@30 Hz ≈ tens of GB/hour — budget ~50–100 GB per benchmark sweep.

**Comparison targets (published, verified):**

| System | Benchmark | SR | NE | Other | Threshold |
|---|---|---|---|---|---|
| Fly0 | AerialVLN | 70.43% | 27.19 m | Time 63.09 s | 5 m |
| Fly0 | OpenFly | 64.67% | 29.47 m | — | 5 m |
| SPF | AerialVLN (via Fly0) | 46.72% | 39.57 m | — | 5 m |
| OnFly | Own · 150 tasks | 67.8% | — | OSR 78.1% · CR 2.7% · FT 27.1 s | 5 m |
| SPF | OnFly bench | 26.4% | — | OSR 61.5% · CR 42.7% · FT 39.2 s | 5 m |
| SPF | DRL Simulator (own) | 93.9% | — | Real DJI Tello 92.7% | own |

- Fly0 backbone Qwen2.5VL-32B; 50 Hz EGO-Planner control, ~0.5 Hz re-grounding, d_safe=0.5 m, v_max=4.0 m/s.
- OnFly: onboard Jetson Orin NX decision cost ~0.81 s (4.73× speedup over 3.83 s baseline), monitoring ~1.15 s. Backbone Qwen3-VL-4B-AWQ; obstacle-mask dilation r=0.2; 70 s episode limit.
- **No paper here reports SPL.**

## VLM Integration Plan
- **Node split:** slow **target-generator** (VLM, ~0.3–0.5 Hz) → 3D goal; fast **tracker/planner** (EGO-Planner, 50 Hz) → PX4 offboard. Timeout: goal `ttl` watchdog → hover.
- **Grounding pipeline (SPF/Fly0):** VLM annotates 2D waypoint(s) → back-project with depth + intrinsics → 3D goal → EGO-Planner. Fly0 uses sensor depth (removing it drops SR from 70.43% to 56.47%). OnFly adds a dual-agent + semantic-geometric verifier.
- **Serving:** dev-time = workstation **vLLM/SGLang** + Qwen3-VL. Onboard = **Jetson Orin NX 16GB, jetson-containers, TensorRT-LLM + AWQ (LLM) / FP16 ViT, KV-cache reuse, CUDA graphs.** **Never Ollama onboard** (issue #13247).
- **Latency methodology:** timestamp image-in → target-out; report p50/p95; assert onboard decision ≤1 s.

## Obstacle / Collision Avoidance Stack
- **Recommended: EGO-Planner (EGO-Swarm ROS 2, `drone_id=0`) + nvblox mapping (GPU) with OctoMap CPU fallback** — mirrors Fly0/OnFly.
- `ZJU-FAST-Lab/ego-planner` = ROS 1 canonical; `ego-planner-swarm` = ROS 2 branch; `hyq123-cmd/px4_ego_planner` = PX4-integrated fork. `isaac_ros_nvblox` = actively maintained, Jazzy + Jetson. **PX4's old `PX4/avoidance` repo is deprecated.** Nav2 is ground-robot-oriented. Fast-Planner (used by OnFly), MARSIM, FIESTA/voxblox = alternatives.

## State Estimation / VIO Plan
- **Recommended: NVIDIA Isaac ROS Visual SLAM (cuVSLAM)** — ROS 2 Jazzy, GPU-accelerated, Jetson-supported. Alternatives: OpenVINS / VINS-Fusion / ORB-SLAM3.
- **Feed to PX4:** publish to `/fmu/in/vehicle_visual_odometry`; set `EKF2_EV_CTRL`, `EKF2_HGT_REF`, `EKF2_EV_DELAY`, `EKF2_EV_NOISE_MD`, `EKF2_EVP/EVV/EVA_NOISE`. Stream **30–50 Hz**. Verify with QGC MAVLink Inspector (`MAV_ODOM_LP=1`).
- **GPS-denied simulation:** inject GPS dropout, confirm EKF2 falls back to EV; measure hover drift; watch for "drift-to-origin" (PX4 #19859) indicating frame/param misconfiguration.

## Milestone Schedule

**Single engineer (~18–22 weeks):**

| Weeks | Phase | Milestone |
|---|---|---|
| 1–2 | 0 | Toolchain locked, all smoke tests green |
| 3–5 | 1 | Headless GPS-waypoint SITL + CI |
| 6–9 | 2 | Depth/LiDAR mapping + EGO-Planner collision-free flight |
| 10–14 | 3 | VLM target-gen/tracker + cuVSLAM VIO + GPS-denied |
| 15–22 | 4 | Lane C benchmark repro + onboard Jetson VLM + HITL/real flight |

**3-person team (~9–11 weeks):** Eng-A → Lanes A/CI + planning; Eng-B → Lane B/Isaac + perception/VIO; Eng-C → VLM + Lane C benchmark. Phases 1–3 parallelize after a shared ~1.5-week Phase 0.

**Workstation BOM:** RTX GPU ≥16 GB VRAM (24 GB+ recommended for Isaac + local VLM concurrently); ≥64 GB RAM; ≥2 TB NVMe; 12+ CPU cores.
**Drone BOM:** Holybro Pixhawk 6C; Seeed reComputer J4012 (Jetson Orin NX 16 GB); Holybro X500 V2 ARF; stereo/depth camera (RealSense D435-class); optional Livox Mid-360 LiDAR.
**Budget:** Isaac Sim = free (individual license); UE5 = free; cloud GPU only if no local RTX ≥24 GB.

## Risk Register

| Risk | L | I | Mitigation | Trigger → fallback |
|---|---|---|---|---|
| Pegasus ↔ PX4 version conflict | High | High | Separate v1.14.3 checkout for Lane B from day one | Designed around, not mitigated after |
| Isaac ↔ Jazzy Python split | High | Med | NVIDIA's Python 3.11 workspace; DDS boundary | >3 days lost → Isaac as pure DDS sensor source |
| EGO-Planner ROS 2 port effort | Med | High | Start from EGO-Swarm's ROS 2 branch | >1 week slip → ROS 1 bridge container |
| PX4 sensor TIMEOUT on 24.04 | Med | Med | Single-command launch, loopback gz, more cores, headless | Persists → run CI lane on PX4 v1.15 |
| `px4_msgs` drift | Med | High | Branch pin in `versions.lock`; CI asserts topics populate | Silent failure → CI topic assertion is the detector |
| Cesium FSD vs PhysX | High | Med | Cesium for rendering/data-gen only in Isaac | Georeferenced physics → Lane C UE5 |
| Lane C UE5 build fragility | High | Med | Pin known-good Cosys-AirSim commit | Unbuildable → Colosseum fallback or drop benchmark parity |
| Onboard VLM latency miss | Med | High | TensorRT + AWQ + FP16 ViT + KV-cache + CUDA graphs | >1 s → Qwen3-VL-2B, lower re-grounding rate |
| Sim VIO timestamp fidelity | Med | High | Measure jitter and rate stability first | Corrupted → validate against Lane A lockstep |
| HITL instability | Med | Med | PX4 documents HITL as community-supported | Blocked → extend SITL, go direct to tethered flight |
| Benchmark non-comparability | High | Med | Parameterise SR threshold; record which was used | Diverge → publish gap analysis, don't tune to match |
| GPU CI capacity | Med | Low | Self-hosted GPU runner, nightly not per-commit | Unavailable → manual pre-merge gate |

## Pitfalls / Version Pinning
- **Isaac Sim 5.x** ships Python 3.11; ROS 2 Jazzy uses 3.12 → `rclpy._rclpy_pybind11` import errors.
- **PX4 ↔ px4_msgs** must be branch-matched; v1.16 adds message versioning + translation node.
- **UE5 ↔ Cosys-AirSim/Colosseum:** pin exact engine versions (Cosys 5.5, Colosseum 5.6).
- **Pegasus ↔ Isaac:** v5.1.0 ↔ Isaac 5.1.0, explicitly not backward-compatible.
- **Cesium FSD vs PhysX** conflict.
- **Ollama Qwen3-VL Jetson GPU-offload bug** (issue #13247).
- **PX4 Gazebo Accel/Mag TIMEOUT** on 24.04 (issues #25089, #24159, #24595, #26299).

## Standing Orders
1. **Lock versions before writing code.** Two PX4 trees, two Python runtimes. This is the architecture, not a workaround.
2. **Freeze topic and namespace conventions in Phase 1.** They must reach the aircraft unchanged.
3. **Never let the tracker block on the VLM.** The `ttl` watchdog is a safety property.
4. **Use sensor depth, not VLM-estimated depth.** Fly0's ablation shows a 14-point SR drop without it.
5. **Assert the real-time-factor floor in CI.** Retries alone turn desync into an intermittent green build.
6. **Parameterise the success threshold.** 5 m and 20 m are both correct depending on the benchmark.
7. **Gate flight on HITL passing the identical SITL suite.** No exceptions.

## Caveats
- Version coupling is the dominant risk (Pegasus↔PX4, Isaac↔Jazzy Python, px4_msgs↔firmware, Cosys-AirSim↔UE5).
- Benchmark numbers are not apples-to-apples: different simulators, benchmarks, success thresholds (20 m vs 5 m), and metric sets.
- Isaac Sim driver detection can report a stale driver even when `nvidia-smi` is correct.
- Fly0 (arXiv:2602.15875) and OnFly (arXiv:2603.10682) are 2026 preprints; SPF is CoRL 2025. Verify final published versions.
- Unverified specifics to confirm at build time: exact Isaac Sim 5.1 NGC container tag; precise NVIDIA driver minimum for Isaac 5.1; complete enumeration of PX4 `gz_x500_*` / lidar model targets; cesium-omniverse issue #153 state.
