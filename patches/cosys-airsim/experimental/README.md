# Experimental patches — NOT applied by `scripts/build_airsim_wrapper.sh`

The build script globs `patches/cosys-airsim/*.patch`, so anything in this subdirectory is
deliberately excluded from the shipped configuration.

## `0004-scene-capture-ldr.patch`

Changes the `Scene` capture source from `SCS_FinalToneCurveHDR` to `SCS_FinalColorLDR`
(`PIPCamera.cpp:178`), matching what every other image type already uses.

**Status: unvalidated. It has never actually run.**

It was built and deployed on 2026-08-02 and recorded as a negative result — but on 2026-08-03
the plugin loader was found to have been resolving a *different* copy: `--force` had left
backups inside `Plugins/`, Unreal de-duplicates plugins by name+version and kept the stale one,
so the patched binary was ignored. The md5 check that "verified" it inspected the file on disk,
not the one the engine loaded. So the negative result says nothing about the patch.

**It is not needed.** The washout it was written to fix was traced to three other causes
entirely (RGB/BGR in the measurement client, the camera being inside world geometry, and Lumen
GI being explicitly disabled). With those addressed on a **stock** plugin, AirSim's capture
matches Unreal's own render of the same view to 1.15 of 255.

**If it is ever revisited**, re-test `TargetGamma` with it. Gamma came back as a clean null on
stock (1.4 → 1.0 changed nothing), which is odd for a setting that is genuinely applied at
`PIPCamera.cpp:747` — plausibly because the HDR capture source bypasses the render target's
gamma encode. If so, `TargetGamma` would only start to matter *with* this patch, and the
"gamma is refuted" finding is strictly a stock-HDR result.

See `docs/worklog/2026-08-03-c11-washout-root-cause.md`.
