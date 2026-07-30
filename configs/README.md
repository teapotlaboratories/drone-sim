# `configs/` — per-lane YAML overrides

**Status:** placeholder. Populated in **Phase 1**.

Parameter overlays applied on top of the shared launch composition, so the same graph
runs in every lane with only its transport and timing swapped.

Expected contents: per-lane `use_sim_time` / transport settings, EKF2 parameter sets,
planner tuning (`d_safe`, `v_max`), VLM endpoint + model config, and the **evaluation
success threshold** (5 m vs 20 m — parameterized deliberately).

Secrets never live here. Pass tokens, Wi-Fi credentials, and setup keys via environment
or secret files, never committed (`.ai/AGENTS.md:465`).
