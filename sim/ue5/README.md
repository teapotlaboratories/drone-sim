# Lane C — UE5.5 + Cosys-AirSim

**Status:** placeholder. Populated in **Phase 4**. Treat as high-risk / optional.

Benchmark-reproduction lane only — AerialVLN/OpenFly-style evaluation. Build
Cosys-AirSim from source against UE5.5; Docker base
`ghcr.io/epicgames/unreal-engine:dev-slim-5.5.4` (requires EpicGames org access).

**Cesium is render/data-gen only.** Cesium for Omniverse requires the Fabric Scene
Delegate, which is mutually exclusive with PhysX — *"You cannot do Cesium and PhysX
together."* Fly physics on baked static meshes
(`docs/reference/02_development_plan.md:21`).

Never run UE5 shader compilation concurrently with a heavy Isaac Sim scene — 64 GB RAM
will not hold both (`docs/reference/03_hardware_assessment.md:66`).
