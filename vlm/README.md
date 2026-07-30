# `vlm/` — serving configs & model recipes

**Status:** placeholder. Populated in **Phase 3–4**.

- **Dev-time:** vLLM/SGLang on **GPU 1 (RTX 5060 Ti, 16 GB)** via
  `CUDA_VISIBLE_DEVICES=1` — vLLM honours it. Confirmed 16 GB variant, so Qwen3-VL-8B
  AWQ fits with KV headroom.
- **Onboard:** jetson-containers + TensorRT-LLM, **Qwen3-VL-4B-AWQ**, FP16 ViT,
  KV-cache reuse, CUDA graphs (OnFly's recipe).

**Two hard rules:**

1. **Never Ollama onboard.** Qwen3-VL 2B/4B loads entirely onto the CPU with zero layers
   offloaded to the GPU on Jetson Orin / JetPack 6.2.1 (`ollama/ollama#13247`).
2. **Qwen3-VL-30B-A3B does not fit** — ~17 GB at INT4 before the vision tower and KV
   cache. Serve 2B/4B/8B locally or use a remote endpoint
   (`docs/reference/03_hardware_assessment.md:6`).

Cap `max_pixels`; use `--quantization awq` (`int4` is not a valid vLLM value). Model
weights live outside the repo (`/home/deck/Developments/models`, 7 TB drive for new
pulls).
