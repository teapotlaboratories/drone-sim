# Lane B — Isaac Sim 5.1 + Pegasus scenes

**Status:** placeholder. Populated in **Phase 3**.

Pegasus standalone scripts and USD scenes for photorealistic RTX perception.

**Two hard constraints:**

1. **Render on GPU 0 (RTX 3080).** Pin with `--/renderer/activeGpu=0
   --/physics/cudaDevice=0`. The index comes from the Omniverse `.log`
   `[gpu.foundation]` table, **not** `nvidia-smi` ordering, and
   `CUDA_VISIBLE_DEVICES` does **not** control the Vulkan RTX renderer
   (`docs/reference/03_hardware_assessment.md:72`).
2. **PX4 v1.14.3, not v1.16.x.** Pegasus v5.1.0 was developed against v1.14.3 over the
   MAVLink SITL API. Lane B gets its own PX4 checkout.

VRAM is the binding constraint at 10 GB — cap scene complexity, RTX-sensor count, and
resolution. Each RTX sensor needs its own viewport.
