# `evaluation` — metrics and batch runner

**Status:** placeholder. Created in **Phase 3**, extended in **Phase 4**.

Implements every metric, because the target papers disagree: **SR, SPL, NE, OSR,
collision count / CR, time-to-target, path length, intervention rate** (optionally
nDTW/CLS).

**The success threshold is a config parameter, not a constant.** AerialVLN/OpenFly use
**20 m**; Fly0 and OnFly use **5 m**. Both are correct — record which was used with every
result (`docs/reference/02_development_plan.md:171`, Standing Order 6).

Batch runner consumes `scenarios/*.yaml` and emits a metric table alongside the MCAP
bags. Metric computation is host-side logic → unit-tested.
