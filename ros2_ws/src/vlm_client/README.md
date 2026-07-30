# `vlm_client` — slow target-generator + fast tracker

**Status:** placeholder. Created in **Phase 3**.

The SPF / Fly0 / OnFly decomposition: a **slow semantic reasoner** (VLM, ~0.3–0.5 Hz)
emits a 3D goal; a **fast geometric tracker** (EGO-Planner, 50 Hz) drives PX4 offboard.

- Talks to an **OpenAI-compatible** endpoint (vLLM at dev time, TensorRT-LLM onboard).
- Grounding pipeline: VLM annotates 2D waypoint(s) → back-project with depth +
  intrinsics → 3D goal → planner.
- **Never let the tracker block on the VLM** (Standing Order 3). The `ttl` watchdog in
  `interfaces` is the mechanism.

Latency methodology: timestamp image-in → target-out, report **p50/p95**. Onboard budget
is **≤ 1 s** (`docs/reference/02_development_plan.md:195`).
