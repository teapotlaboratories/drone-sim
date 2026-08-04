> **FROZEN — HISTORICAL RECORD. Preserved exactly as written; not maintained.**
>
> This is the go/no-go assessment of this workstation against the project's original
> three-stack design. Much of it is scoped to **Isaac Sim, a stack this repo no longer
> contains**, and the machine's NVIDIA driver has since moved past the versions discussed
> here.
>
> **Read it as evidence, never as instructions.** The stack names, task IDs and component
> layout it refers to have been renamed or deleted. Every figure is preserved unchanged,
> including the document's own caveat that the VRAM numbers are order-of-magnitude planning
> figures from forum reports rather than measurements.
>
> **Its GPU work split is still the operating rule** — render on the RTX 3080, infer on the
> 16 GB RTX 5060 Ti — which is the main reason this document is kept. See
> [`../README.md`](../README.md).
>
> **The repo today is one simulator: Unreal Engine 5.8 + Cosys-AirSim + PX4 SITL + ROS 2
> Jazzy.** Task-ID renames: [`../id-map.md`](../id-map.md). The repo:
> [`README.md`](../../../README.md).

# Drone Sim Stack on Core i9 + RTX 3080 + RTX 5060 Ti + 64 GB: Go/No-Go Verdict

## TL;DR
- **QUALIFIED YES — the stack holds up, but only if you assign the RTX 3080 (not the Blackwell RTX 5060 Ti) as the primary Isaac Sim / rendering GPU, and treat the RTX 5060 Ti as the VLM-serving / secondary card.** The RTX 5060 Ti (Blackwell, sm_120) has multiple documented Isaac Sim 5.1 startup crashes on recent driver branches; the Ampere RTX 3080 is a proven, stable renderer.
- **VRAM is the binding constraint and the 3080's 10 GB is *below* Isaac Sim 5.1/6.0's stated 16 GB minimum.** Lane A (Gazebo) and the VLM server (Qwen3-VL 2B/4B/8B AWQ) fit comfortably; Lane B (Isaac Sim with multiple RTX sensors) and Lane C (UE5.5 + Cosys-AirSim) will run but require capping scene complexity, sensor count, and resolution. A 12 GB 3080/3080 Ti materially improves the outlook; a single 24 GB card would remove nearly all friction.
- **Do not run Qwen3-VL-30B-A3B locally alongside Isaac Sim** — ~31.1B total parameters, ~17 GB at INT4 (fits a 24 GB RTX 4090 "with room for a small batch"). It will not co-reside with a sim workload on any card you own. Serve 2B/4B/8B locally, or push the large VLM to a remote endpoint.

## Key Findings

**1. Isaac Sim's official minimum jumped to 16 GB VRAM / RTX 4080.** Isaac Sim 5.0, 5.1 and 6.0 all list identical x86_64 GPU tiers: **Minimum** = GeForce RTX 4080, 16 GB VRAM; **Good** = RTX 5080, 16 GB; **Ideal** = RTX PRO 6000 Blackwell, 48 GB. This is a deliberate hike — Isaac Sim 4.5.0 had listed an 8 GB RTX 3070 as minimum, and NVIDIA staff still cite that older floor on the forums. GPUs without RT cores (A100/H100) are explicitly unsupported. Both your cards have RT cores and clear the architectural bar, but **both fall below the current 16 GB minimum except the RTX 5060 Ti 16 GB variant**.

**2. Blackwell (RTX 5060 Ti) support on Isaac Sim is real but fragile — and can be broken by a driver that is *too new*.** Isaac Sim 5.x/6.x run on Blackwell, but NVIDIA forums and IsaacSim GitHub carry numerous startup-crash reports specific to RTX 50-series, concentrated on the 595.xx driver branch, with the fix being a driver *downgrade* (to 591.74 or 580). Isaac Sim 5.1/6.0's validated Linux driver is **580.65.06**; IsaacSim GitHub Issue #229 shows even a 5070 on 580.65.06 hitting `Warp CUDA error: Failed to get driver entry point 'cuDeviceGetUuid'` with the diagnosis "my driver is too new for this version of Isaac Sim." NVIDIA forum staff note "there's been report of invalidated driver past 580 on 5.1.0." **This driver-version tension is the single biggest risk in the build.**

**3. A single modern NVIDIA driver serves both Ampere and Blackwell.** The R580 branch validates CUDA 13.x across Maxwell→Blackwell, so one driver covers both cards. There is no fundamental mixed-architecture conflict — the conflict is version-specific.

**4. Isaac Sim GPU pinning is documented and works**, which is what makes the two-GPU task split viable.

**5. CPU/RAM: 64 GB clears Isaac Sim's "Good/Ideal" RAM tier; a Core i9 clears the top CPU tier.** NVIDIA's Isaac Sim benchmark reference machine is an "Intel i9-14900k CPU and 32GB of DDR5 RAM" — you have 2× that RAM. PX4 SITL lockstep is single-thread-latency-sensitive: CPU starvation produces the documented `Accel #0 fail: TIMEOUT!` / `MAG #0 failed: TIMEOUT!` errors.

## Details

### Hardware specifications

| Spec | RTX 3080 (10 GB) | RTX 3080 (12 GB) | RTX 3080 Ti | RTX 5060 Ti (8/16 GB) |
|---|---|---|---|---|
| Architecture | Ampere (GA102), sm_86 | Ampere | Ampere | Blackwell (GB206-300), sm_120 |
| CUDA cores | 8,704 | 8,960 | 10,240 | 4,608 |
| RT cores | 68 (2nd gen) | ~70 (2nd gen) | 80 (2nd gen) | 36 (4th gen) |
| Tensor cores | 272 (3rd gen) | (3rd gen) | 320 (3rd gen) | 144 (5th gen, FP4/FP8) |
| Memory | 10 GB GDDR6X | 12 GB GDDR6X | 12 GB GDDR6X | 8 or 16 GB GDDR7 |
| Bus width | 320-bit | 384-bit | 384-bit | 128-bit |
| Bandwidth | 760 GB/s | ~912 GB/s | ~912 GB/s | 448 GB/s |
| TDP | 320 W | 350 W | 350 W | 180 W |
| PCIe | 4.0 x16 | 4.0 x16 | 4.0 x16 | 5.0 x8 (physical) |
| MSRP | $699 | — | $1199 | $379 / $429 |

The RTX 3080 has ~1.9× the CUDA cores and ~1.7× the memory bandwidth of the RTX 5060 Ti, and ~10–13% higher aggregate raster/compute performance despite being ~4 years older. The 5060 Ti's advantages are VRAM capacity (16 GB variant), far lower power, 4th-gen RT cores, and 5th-gen Tensor cores with native FP4/FP8 and DLSS 4. **For this workload the 3080's raw RT/compute throughput matters more**, because Isaac Sim's RTX renderer and RTX-sensor ray tracing are throughput/bandwidth-bound, and none of the sim components require FP4/DLSS 4.

### Blackwell / RTX 50-series support

- **Isaac Sim:** Runs on Blackwell but with documented instability. Forums show RTX 5060 Ti, 5070 Ti, 5080 and RTX PRO 6000 Blackwell all hitting `rtx.scenedb.plugin` startup crashes and `TLAS limit` errors, frequently tied to the 595.xx driver branch and resolved by downgrading. IsaacSim GitHub Issue #537: "Isaac Sim fails to detect CUDA device with NVIDIA driver 595.79 (works with 580)."
- **Isaac Lab / PyTorch:** Blackwell requires CUDA 12.8+ and PyTorch built with sm_120 kernels (cu128 wheels). The IsaacLab documented fix for 50-series training hangs is the cu128 nightly PyTorch; stock wheels hang before printing the model structure.
- **Isaac ROS (cuVSLAM, nvblox):** Targets x86_64 discrete NVIDIA GPUs, tested on ROS 2 Jazzy/Humble. **The ESS DNN-stereo-disparity plugin only runs on GPUs with compute capability sm_80 and above** — the RTX 3080 (sm_86) and RTX 5060 Ti (sm_120) both clear this. Blackwell viability depends on the CUDA/TensorRT version in the Isaac ROS container — verify per release.

### Per-component VRAM & performance footprint

- **Isaac Sim idle:** ~3–5 GB observed (nvidia-smi on RTX 4090 showed ~3.9 GiB on a light stage). A small getting-started scene consumed ~3.2 GB before OOM-crashing on an 8 GB RTX 3060 laptop.
- **Complex scenes:** Isaac Sim 5.1 docs, verbatim: "GPUs with less than 16GB VRAM may be insufficient to run a complex scene rendering more than 16MP per frame." NVIDIA staff recommend a **minimum of 12 GB**; 6 GB "can only handle very simple models and scenes."
- **RTX sensors:** Each RTX Lidar/camera "requires GPU memory" and **each RTX sensor must be attached to its own viewport**. Isaac Sim 6.0 benchmark docs: "Lower VRAM GPUs (under 12GB) may not be able to render all sensors." No published per-sensor GB figure.
- **Isaac Lab RL:** additional RAM and VRAM per parallel environment.
- **UE5.5 + Cosys-AirSim:** Epic requires ≥8 GB VRAM for Nanite/Lumen; community consensus: 8 GB "breaks under real UE5 workloads," 12 GB entry-level, 16 GB safe baseline. 10 GB is workable for modest AirSim scenes but will stutter on large Cesium terrain.
- **Gazebo Harmonic:** light GPU load; binding constraint is CPU.
- **vLLM Qwen3-VL (AWQ / 4-bit), weights only — add vision tower + KV cache headroom:**
  - Qwen3-VL-2B ≈ 1.9 GB → fits 8 GB
  - Qwen3-VL-4B ≈ 3.3 GB (Q4), min 6 GB, comfortable at 8 GB
  - Qwen3-VL-8B ≈ 6.1 GB (Q4), min 8 GB, comfortable 12–16 GB
  - Qwen3-VL-30B-A3B (MoE, ~31.1B total) ≈ 17 GB at INT4 → needs ~24 GB card; **does NOT fit in 16 GB** alongside anything else (~68 GB at FP16)
  - VL KV cache is heavy: a vLLM issue shows Qwen3-VL-32B-AWQ (20 GB weights) consuming ~36 GB at 0.9 util for only ~8k context. Cap `max_pixels`, and prefer `--quantization awq` (`int4` is not a valid vLLM quantization value).

**Fit-by-card (VLM):** 8 GB → 2B/4B comfortably, 8B tight; 10 GB → 8B usable; 12 GB → 8B comfortable; 16 GB → 8B with large KV/image headroom, but 30B-A3B still will not fit.

### CPU, RAM, storage

- **PX4 SITL lockstep** locks PX4 and Gazebo to the same step; CPU starvation triggers the documented `Accel/Mag TIMEOUT` failures. A Core i9 has ample headroom *if* you don't saturate all cores with UE5 shader compilation simultaneously.
- **Isaac Sim RAM:** 64 GB is the "Good"/"Ideal" tier and double NVIDIA's benchmark reference machine.
- **UE5.5:** editor + shader compilation is heavily multithreaded (core-count-bound); Epic's practical guidance for serious work is 64 GB. Running a UE5 shader compile *while* Isaac Sim + vLLM + ROS 2 + QGC are live will exceed comfortable 64 GB usage — stagger these.
- **Storage (unspecified by user):** Isaac Sim "Ideal" calls for 1 TB NVMe SSD. **Provision ≥1 TB NVMe; this is a likely silent bottleneck.**

### Mixed-architecture multi-GPU & PCIe

- One R580+ driver serves both Ampere and Blackwell (CUDA 13.x spans both). No fundamental conflict.
- **Isaac Sim GPU selection:** use the documented kit flags — `--/renderer/activeGpu=<n>` (and `--/physics/cudaDevice=<n>`), or `--/renderer/multiGpu/enabled=false` to force single-GPU. The GPU index comes from the Omniverse `.log` `[gpu.foundation]` table, **not** nvidia-smi ordering. Critically, **`CUDA_VISIBLE_DEVICES` does not control the Vulkan-based RTX renderer** — use the kit flags for Isaac Sim. vLLM *does* respect `CUDA_VISIBLE_DEVICES`.
- **Heterogeneous multi-GPU *rendering* in Omniverse is buggy** (documented corrupted stereo output when rendering multiple render products across GPUs). **Do not span one Isaac Sim render across both dissimilar cards.** The strategy is *task partitioning*, not split-rendering.
- **PCIe on consumer Core i9 (LGA1700/1851):** CPU exposes 16 (+4) lanes; a dual-GPU board splits the primary x16 into **x8/x8**, and the RTX 5060 Ti is natively PCIe 5.0 x8 anyway. x8 on Gen4/Gen5 costs low-single-digit % for a single GPU and effectively nothing for memory-bandwidth-bound LLM inference — **not a meaningful bottleneck**.

## Recommendations

**Stage 0 — Driver/OS baseline (do this first).** Ubuntu 24.04 + ROS 2 Jazzy is correct for Isaac Sim 5.1 (Python 3.11) and Isaac ROS. Because Blackwell needs a recent branch but Isaac Sim validates 580.65.06 and breaks on 595.xx, target the newest R580 production driver that still launches Isaac Sim. **Verify Isaac Sim launches before installing anything else.** Pin the driver and hold it.

**Stage 1 — GPU work assignment (the core recommendation).**
- **RTX 3080 → primary display + Isaac Sim (Lane B) + UE5 (Lane C) rendering + Isaac ROS perception.** Stronger renderer (1.9× cores, 1.7× bandwidth), and Ampere — zero Blackwell driver-crash exposure. Pin with `--/renderer/activeGpu` to the 3080's `[gpu.foundation]` index.
- **RTX 5060 Ti → vLLM/SGLang Qwen3-VL server (via `CUDA_VISIBLE_DEVICES`).** The 16 GB variant holds an 8B AWQ model with generous KV headroom; FP4/FP8 Tensor cores and 180 W TDP make it an efficient always-on inference card; isolating it from the RTX renderer sidesteps the Blackwell/Isaac Sim instability entirely.
- If your RTX 5060 Ti is the **8 GB** variant: serve only Qwen3-VL-2B/4B locally.

**Stage 2 — Downscale ladder if it doesn't hold up:** (1) reduce RTX sensor count and camera resolution; (2) lower texture-streaming budget (default 0.6 → lower), keep Motion BVH off; (3) cut Isaac Lab parallel-env count; (4) drop VLM model size or move it to a remote endpoint; (5) reduce UE5 scene scale / disable Lumen for AirSim runs. Never run UE5 shader compilation and heavy Isaac Sim scenes concurrently.

**Stage 3 — Upgrade decision.**
- **Highest-value: replace the pair with a single 24 GB card** (RTX 3090/4090-class, or a 24 GB+ Blackwell workstation card once Isaac Sim's validated driver catches up). Clears the 16 GB minimum with headroom, removes mixed-arch complexity.
- **Cheaper interim: swap the 10 GB 3080 for a 12 GB 3080/3080 Ti.** Clears NVIDIA's practical 12 GB floor; the 384-bit bus (~912 GB/s) helps large scenes.
- **Keep both cards** if your workflow naturally splits sim-vs-VLM.

**Power/thermals:** RTX 3080 (320 W) + RTX 5060 Ti (180 W) = ~500 W of GPU TDP plus a Core i9 (~125–253 W). Budget an **850 W–1000 W 80+ Gold (ATX 3.x)** PSU and ensure airflow. Confirm two slots physically fit (the 3080 is often 2.7-slot).

**Thresholds that change the recommendation:**
- Isaac Sim only ever runs headless/light scenes → the 3080 alone suffices.
- You need Qwen3-VL-30B-A3B or larger locally → acquire a 24 GB+ card or go remote.
- You need >2 RTX stereo pairs + Lidar simultaneously in one Isaac scene → 16 GB is the floor.

## Caveats
- **NVIDIA does not publish exact per-sensor or absolute scene VRAM figures.** The idle (~3–5 GB) and small-scene (~3.2 GB) numbers are individual forum nvidia-smi reports. Isaac Sim 6.0's benchmark "GPU Memory Tracked" metric appears to undercount the true renderer + texture-streaming footprint. Treat all VRAM figures as order-of-magnitude planning numbers.
- **Qwen3-VL AWQ serving figures for 2B/4B/8B** are derived from Q4_K_M GGUF weight sizes via a third-party guide, not official Qwen benchmarks; real vLLM totals are higher once the vision encoder loads and KV cache is pre-allocated.
- **The Blackwell/Isaac Sim crash reports are version-specific and evolving.** Routing rendering to the Ampere 3080 makes the build robust regardless.
- **Isaac Sim 5.1.0 is marked "no longer supported"** in NVIDIA's docs (superseded by 6.0). Verify Pegasus Simulator v5.1.0 compatibility, or plan the 6.0 migration — 6.0 requires Python 3.12, which changes the ROS 2 integration path.
- **RTX 3080 laptop variants** ship with 8–16 GB at lower TDP; the analysis assumes desktop cards.

## References
- Isaac Sim requirements — https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html (also /5.0.0/, /4.5.0/, /6.0.0/)
- Isaac Sim benchmarks — https://docs.isaacsim.omniverse.nvidia.com/6.0.0/reference_material/benchmarks.html
- RTX sensors — https://docs.isaacsim.omniverse.nvidia.com/latest/sensors/isaacsim_sensors_rtx.html
- RTX Lidar ROS 2 tutorial — https://docs.isaacsim.omniverse.nvidia.com/5.0.0/ros2_tutorials/tutorial_ros2_rtx_lidar.html
- Isaac Sim 5.1 RTX 5060 Ti crash — https://forums.developer.nvidia.com/t/isaac-sim-5-1-crashes-on-startup-with-rtx-5060-ti-blackwell-sm-120-rtx-scenedb-plugin-crash/366252
- RTX 5060 laptop GPU error — https://forums.developer.nvidia.com/t/isaac-sim-error-on-rtx-5060-gb-laptop-gpu/371956
- IsaacSim issue #537 — https://github.com/isaac-sim/IsaacSim/issues/537
- IsaacSim issue #229 — https://github.com/isaac-sim/IsaacSim/issues/229
- IsaacLab discussion #1888 (cu128 PyTorch) — https://github.com/isaac-sim/IsaacLab/discussions/1888
- Isaac Lab installation — https://isaac-sim.github.io/IsaacLab/main/source/setup/installation/index.html
- CUDA Blackwell compatibility guide — https://docs.nvidia.com/cuda/blackwell-compatibility-guide/
- R580 driver release notes — https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-159-03/
- Omniverse Linux troubleshooting (GPU selection) — https://docs.omniverse.nvidia.com/dev-guide/latest/linux-troubleshooting.html
- Forcing a single GPU — https://forums.developer.nvidia.com/t/how-to-make-isaac-sim-use-only-one-gpu/272718
- IsaacLab issue #987 — https://github.com/isaac-sim/IsaacLab/issues/987
- Multi-GPU render corruption — https://forums.developer.nvidia.com/t/corrupted-render-when-running-sdg-with-multiple-render-products-and-multiple-gpus/332504
- Low-VRAM guidance — https://forums.developer.nvidia.com/t/can-i-run-isaac-sim-with-rtx-3050-6gb-low-profile/306588
- Viewport/VRAM thread — https://forums.developer.nvidia.com/t/isaac-sim-viewport-turns-black-after-a-short-time-controller-initially-works-then-only-black-screen/350701
- Isaac ROS ESS (sm_80 requirement) — https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_dnn_stereo_depth/isaac_ros_ess/index.html
- Isaac ROS nvblox — https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_nvblox/index.html
- Isaac ROS Visual SLAM — https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_visual_slam
- Unreal Engine hardware specs — https://dev.epicgames.com/documentation/en-us/unreal-engine/hardware-and-software-specifications-for-unreal-engine
- PX4 Gazebo SITL — https://docs.px4.io/main/en/sim_gazebo_gz/
- PX4 issue #25089 — https://github.com/PX4/PX4-Autopilot/issues/25089
- Pegasus Simulator — https://pegasussimulator.github.io/PegasusSimulator/
- Qwen3-VL 4B vs 8B VRAM guide — https://codersera.com/blog/qwen3-vl-4b-vs-qwen3-vl-8b-benchmarks-vram-guide/
- Qwen3-VL-30B-A3B AWQ — https://huggingface.co/QuantTrio/Qwen3-VL-30B-A3B-Instruct-AWQ
- vLLM issue #28626 (VL KV cache) — https://github.com/vllm-project/vllm/issues/28626
- PCIe lane allocation — https://computingforgeeks.com/pcie-lanes-for-gpu-nvme-builds/
