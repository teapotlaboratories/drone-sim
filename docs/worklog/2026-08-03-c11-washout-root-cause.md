# 2026-08-03 — C-11: finding what actually causes the AirSim capture washout

Continues the thread from 2026-08-02, which ended with a **negative result**: the
`SCS_FinalToneCurveHDR` → `SCS_FinalColorLDR` patch was built, deployed, verified running by
md5, and did **not** change the washout. That retired the hypothesis the whole thread rested
on and left the mechanism unknown.

## Where the previous attempts went wrong

Seven interventions had failed at that point — six world-side (scalability ini, SM5→SM6, the
UE4→UE5 package conversion, `AtmosphericFog`→`SkyAtmosphere`) and one plugin-side (the capture
source). Every one of them targeted *the world* or *the output encoding*. None targeted **how
the capture component itself is configured**, which is what the accumulated negative evidence
was actually pointing at: if the world renders correctly in Unreal's own screenshot and
incorrectly through `simGetImages`, in the same process on the same frame, the difference has
to live in the component doing the capturing.

So this session started by reading the vendor source instead of proposing another fix.

## What the source says

Grepping `PIPCamera.cpp` for the capture setup immediately killed my leading hypothesis and
turned up two better ones.

**Killed on sight — `bAlwaysPersistRenderingState`.** I expected this to be `false` (the UE
default), which would discard temporal history every capture and break auto-exposure, TSR and
Lumen accumulation. It is explicitly set to `true` at `PIPCamera.cpp:459`. Not the cause; cost
nothing to rule out.

**Candidate 1 — a gamma applied to the Scene camera and nothing else.**

```
AirSimSettings.hpp:170    static constexpr float kSceneTargetGamma = 1.4f;
AirSimSettings.hpp:1526   capture_setting.target_gamma = settings_json.getFloat("TargetGamma",
AirSimSettings.hpp:1527       capture_setting.image_type == 0 ? CaptureSetting::kSceneTargetGamma : Utils::nan<float>());
PIPCamera.cpp:747-748     if (!std::isnan(setting.target_gamma))
                              render_target->TargetGamma = setting.target_gamma;
```

`ImageType 0` is Scene. Every other image type gets `NaN` and leaves the render target's gamma
alone. So Scene — and only Scene — gets **gamma 1.4 applied on top of an already tone-curved
image**, which lifts midtones. That is a milky wash, and it explains why the defect is specific
to the one image type anyone looks at.

**Candidate 2 — Lumen explicitly turned OFF for the capture.**

```
AirSimSettings.hpp:217-218   bool lumen_gi_enabled = false;
                             bool lumen_reflections_enabled = false;
PIPCamera.cpp:701-715        if (capture_setting.lumen_gi_enabled) { ... Lumen ... }
                             else { bOverride_DynamicGlobalIlluminationMethod = 1;
                                    DynamicGlobalIlluminationMethod = ::None; }
                             (same shape for ReflectionMethod)
```

The `else` branch is the important part. When the setting is false the code does not simply
leave the capture alone — it **overrides** global illumination and reflections to `None`. City
Park is authored for Lumen, and Unreal's own render uses it. So the AirSim capture has been
rendering with GI and reflections disabled this entire time. Missing GI reads exactly as flat,
ambient-only, hazy lighting.

Our `sim/ue5/settings.json` sets neither key, so Lane C has been running at **gamma 1.4 with
Lumen GI and reflections off** for every capture in this investigation.

Both are `settings.json` keys — `TargetGamma`, `LumenGIEnable`, `LumenReflectionEnable` (note
upstream's typo in the neighbouring `LumenSceneLightningDetail`, which must be matched exactly).
**No plugin rebuild is needed to test either**, which is the first time in this thread that an
experiment has been cheap.

## The measurement rig

The failure mode of the previous attempts was not bad hypotheses, it was untrustworthy
measurement — three investigations were confounded by the camera being buried in terrain and a
fourth by `simPause` returning stale frames. Ad-hoc containers and hand-run captures are why
those went unnoticed, so this session builds the rig properly first:

- `docker/airsim-client.Dockerfile` — pinned RPC client, replacing throwaway `pip install`s.
- `scripts/_capture_client.py` — encodes the two hard-won rules: **never `simPause` before
  `simGetImages`**, and **re-assert the pose in a loop up to the grab** (setting it once does
  not hold the vehicle; gravity wins).
- `scripts/capture_experiment.py` — runs the variants as a **2×2 factorial** from an
  **identical pose in an identical world**, one simulator run each (these settings are parsed
  at startup and cannot be swapped over RPC).

The experiment vehicle is `SimpleFlight`, not PX4: the capture path is identical and it removes
the autopilot, uXRCE-DDS and the EKF origin from a measurement none of them affect.

The headline metric is **`range_p01_p99`** (1st→99th percentile of luminance), not `mean`.
Mean was the metric used previously and it is the wrong one: a washed-out image is not merely
*bright*, it is **tonally compressed**. Mean can sit still while contrast collapses.

## The rig immediately found a defect in the rig's own premises

The first run crashed the simulator at startup, and the crash log carried something far more
important than the crash:

```
LogPluginManager: Warning: The same version (v3) of plugin 'AirSim' exists at
  'AirSim.bak.1785739115/AirSim.uplugin' and 'AirSim/AirSim.uplugin'
  - second location will be ignored
```

Unreal's plugin manager walks `Plugins/` **recursively** and de-duplicates by name+version,
keeping one location and ignoring the rest. The world had four copies, because
`inject_airsim.py --force` moves the previous plugin aside to `Plugins/AirSim.bak.<ts>` — a
**sibling inside the scanned directory**. The copy the engine kept was
`AirSim.bak.1785739115`, md5 `2122e037` — **stock upstream**. The copy it ignored was
`Plugins/AirSim`, md5 `1599713e` — **the LDR-patched build**.

**So yesterday's negative result is void.** The LDR patch was never loaded. The md5 check that
"verified" it examined the right file and answered the wrong question: what sat on disk at
`Plugins/AirSim`, not what the engine resolved. (The companion check — `strings | grep -c
FinalToneCurve` returning 0 — proved nothing either: it returns 0 on the *stock* build too,
because those enum names are not stored as strings.)

This is a defect in my own injector, shipped in PR #33. Fixed three ways: backups now go to
`<project>/AirSimBackups/`, **outside** `Plugins/`; `shadowing_plugin_copies()` refuses to
finish an injection that would leave a second copy behind; and five regression tests cover it,
including the case that matters — a backup outside `Plugins/` must *not* be flagged.

The crash itself was mine too: a camera declared without explicit `X/Y/Z/Pitch/Roll/Yaw` keeps
AirSim's NaN sentinels and reaches `FRotator::Quaternion` as `P=nan Y=nan R=nan`, taking the
simulator down in `BeginPlay`. Not a validation error — a SIGSEGV.

## Results — the 2×2

Run against **stock upstream** plugin (`2122e037`), so any fix found here applies to vanilla
Cosys-AirSim rather than to our patched tree.

| variant | mean | std | range (p01–p99) |
|---|---|---|---|
| baseline (AirSim defaults) | 179.57 | 44.00 | 143.98 |
| `TargetGamma: 1.0` | 179.59 | 43.96 | 143.58 |
| **`LumenGIEnable` + `LumenReflectionEnable`** | 174.89 | 46.90 | **160.42** |
| both | 174.97 | 46.89 | 160.57 |

**`TargetGamma` is refuted** — a clean negative from a proper factorial. 1.4 → 1.0 moves the
image by −0.4 range, i.e. noise. The setting is real and is applied, but the render target's
gamma does not reach this output path.

**Lumen is real**: +16.4 range, +2.9 std. The combined cell adds nothing over Lumen alone, so
the entire effect is Lumen's and the two factors do not interact.

## What the 2×2 could not explain, and the sweep could

Both frames were *still* badly washed, and both still carried the out-of-focus concrete border
that means the camera is inside geometry. Since capture settings are parsed at startup but
**pose is free over RPC**, one simulator run can walk many positions — `capture_pose_sweep.py`.

| z (m up) | mean | std | range | p01 |
|---|---|---|---|---|
| 2 | 168.6 | 45.3 | 168.5 | 83.4 |
| 10 | 191.0 | 60.4 | **198.5** | 53.6 |
| 40 | 208.2 | 47.9 | 168.6 | 83.5 |
| 80 | 219.7 | 38.3 | 132.9 | 120.2 |
| 300 | 242.3 | 14.1 | 68.4 | 184.1 |

**The washout scales with altitude.** Mean climbs monotonically to near-white, contrast
collapses to 68, and the *black point* rises from 53 to 184 — darks lifted toward one bright
colour. That is **atmospheric fog / aerial perspective**, a property of the world, not a defect
in the capture pipeline. It also retro-explains the original side-by-side "proof" that AirSim
renders worse than Unreal: those two frames were taken from **different viewpoints**, which I
recorded at the time as a caveat and then reasoned past.

## A bug in my own measurement client

The sweep left a cyan cast that no hypothesis explained, so I checked the client against
AirSim's own PNG encoder on an identical frame (`scripts/_check_channel_order.py`):

```
raw  as-BGR  B= 183.53 G= 178.21 R= 158.47
png  (BGR)   B= 158.47 G= 178.21 R= 183.53
mean|raw - png|         = 19.59
mean|raw_swapped - png| =  0.00      <- exact
```

**Cosys-AirSim returns the uncompressed buffer as RGB, not BGR.** Reading it as BGR swaps red
and blue — which does not look like a bug, it looks like a plausible cyan colour cast, and was
read as one for a full day. Every PNG saved in this investigation has red and blue swapped.
Fixed, along with the luminance weights, which were applying 0.299/0.114 to the wrong channels.

## Where it landed

Three independent problems, conflated into one symptom called "the washout":

1. **Colour cast** — my client read RGB as BGR. Affects saved images only, never the simulator.
2. **Veiling haze** — the camera was inside world geometry; a near surface smeared by
   depth-of-field is itself a milky film over everything behind it.
3. **Flat lighting** — `LumenGIEnable`/`LumenReflectionEnable` default to `false`, and
   `PIPCamera.cpp:701-715` does not merely skip Lumen, it forces GI and reflections to `None`
   in a world authored for Lumen.

With all three addressed, at z = −10 m: contrast 144 → **197.8**, and the frame reads as a park
— blue sky, white cloud, green foliage, the lake, a car on the road. `out/lane-c/capture-exp/
before_after.png`.

The residual brightness (mean 191, p99 pinned at 251) is the sky blowing out on a bright day
and is an exposure-tuning question (`AutoExposureCompensation`), not a defect.

## The premise, finally measured

Everything above *explained* the gap without *measuring* it, and this thread has twice mistaken
one for the other — so: AirSim's capture against Unreal's own render, **from the same pose**.

Making it apples-to-apples turns on one line of vendor source:

```
SimModeBase.cpp:2120  CameraDirector->initializeForBeginPlay(..., getCamera("fpv"), ...)
```

The viewport's FPV camera is the PIPCamera literally **named `"fpv"`** — which is why earlier
attempts that compared the viewport against `front_center` were comparing two different
cameras, and why "FPV ≠ front_center" looked like a mystery. Declare the camera as `"fpv"` and
both paths resolve to the same `APIPCamera` at the same transform: Unreal renders that actor's
`UCineCameraComponent` via `HighResShot`, AirSim reads that actor's `USceneCaptureComponent2D`.
Both captures are issued inside a single pose-holding loop, so the vehicle cannot drift between
them.

Six scenes, close-up to far. `HighResShot` letterboxes to the viewport aspect, so the bars are
cropped before scoring — left in, they would manufacture the very difference being measured.

**With Lumen enabled — they match:**

| scene | AirSim mean | native mean | Δmean | AirSim contrast | native contrast | Δcontrast |
|---|---|---|---|---|---|---|
| treeline_close | 200.38 | 201.20 | −0.82 | 169.59 | 160.95 | **+8.64** |
| treeline_mid | 195.86 | 194.54 | +1.32 | 198.32 | 197.35 | +0.97 |
| treeline_far | 206.90 | 205.97 | +0.93 | 170.34 | 169.31 | +1.03 |
| park_high | 220.57 | 220.16 | +0.41 | 133.97 | 134.27 | −0.30 |
| lakeside | 197.78 | 196.42 | +1.36 | 176.00 | 173.28 | +2.72 |
| path_north | 183.86 | 181.64 | +2.22 | 192.14 | 189.61 | +2.53 |

Worst brightness disagreement is **2.2 levels out of 255**. AirSim is slightly *more* saturated
in every scene and has *more* contrast at close range. **`simGetImages` does not render worse
than Unreal.** The claim this investigation was built on is false.

**With AirSim's stock defaults — the gap is real and it is Lumen:**

| | Δmean vs native | Δcontrast vs native | Δsaturation |
|---|---|---|---|
| stock (Lumen off) | +2.6 … **+8.6** | −20.3 … +3.7 | −6.4 … +0.3 |
| Lumen on | −0.8 … +2.2 | −0.3 … +8.6 | +0.4 … +2.7 |

Turning Lumen on closes a **20-point contrast gap** at close range and **16.8** at altitude.
The one scene that barely moves (`path_north`, +3.7) is mostly open sky, where GI contributes
little — which is the right sanity check on the mechanism.

## One genuine residual difference: aliasing, and it is not small

Re-run at 1920×1080 (`out/lane-c/vs-native-1080/`), where tone agreement is tighter still —
worst |Δmean| is **1.15**. But AirSim's frames carry hard aliased leaf edges with red/blue
colour fringing where the native render is cleanly resolved.

Measured as high-frequency energy (|image − median3|, which a median filter isolates because it
removes speckle while keeping genuine edges), picking the window that maximises *excess* over
native so the metric cannot just be rewarding real foliage detail:

| scene | AirSim | native | ratio (worst window) | ratio (full frame) |
|---|---|---|---|---|
| treeline_close | 46.21 | 15.41 | **3.00×** | 2.66× |
| treeline_mid | 49.43 | 17.97 | 2.75× | 2.44× |
| lakeside | 46.80 | 17.84 | 2.62× | 2.51× |

**AirSim carries ~2.5× the high-frequency energy of the native render across the whole frame.**

This also undercuts a number reported above: at 1080p AirSim appears to *beat* native on
contrast (+6.6 to +13.8). That is most likely its own speckle widening the p01–p99 spread, not
genuine tonal range. The contrast advantage should be treated as an artifact until measured on
a denoised frame.

**The mechanism is in the source, and it is deliberate:**

```
PIPCamera.cpp:455-459  setCaptureUpdate(capture, nodisplay)
                           capture->bCaptureEveryFrame = !nodisplay;
                           capture->bCaptureOnMovement = !nodisplay;
                           capture->bAlwaysPersistRenderingState = true;
PIPCamera.cpp:186-188  //We set all cameras to start as nodisplay
                       //This improves performance because the capture components are no longer
                       //updating every frame and only update while requesting an image
```

Every camera starts as `nodisplay`, so `bCaptureEveryFrame` is **false** and the capture renders
one shot per `simGetImages`. `bAlwaysPersistRenderingState` is true, so the temporal history
*buffer* survives — but with no per-frame captures there are no frames to accumulate into it, so
TSR/TAA never converges and foliage aliases. The main viewport, rendering continuously, does
converge. That is the entire difference.

## Attacking the aliasing on stock Cosys-AirSim

Constraint: **no plugin patch**. Three levers qualify, all measured at 1920×1080 with the
achievable `simGetImages` rate recorded alongside, because a fix that halves the frame rate is
not obviously a fix — Lane C already claims 31 Hz RGB.

One check first: `showToScreen()` (`PIPCamera.cpp:406`) touches only the cine-camera component,
never the scene captures. So running in `Fpv` view mode does **not** enable per-frame capture,
which is why the aliasing survived the vs-native run. The mechanism holds.

Absolute high-frequency energy, `treeline_mid` (native reference ≈ 7.0):

| variant | hf_airsim | vs baseline | capture fps |
|---|---|---|---|
| baseline (stock defaults) | 20.71 | — | 10.6 |
| **`ForceUpdate: true`** | **17.83** | **−13.9 %** | 10.4 |
| `r.AntiAliasingMethod 1` (FXAA) | 21.12 | +2.0 % | 10.9 |
| ForceUpdate + FXAA | 18.03 | −12.9 % | 10.7 |
| supersample 2× → downsample | 19.98 | −3.5 % | **4.5** |

**`ForceUpdate` wins, and it is free** — the frame-rate difference is inside run-to-run noise.

**FXAA is a trap in the ratio metric.** Its *ratio* looked best (2.04× vs 2.94×) purely because
it degraded the **native** reference too (hf 7.04 → 10.36) — it is worse than TSR. AirSim's own
noise went slightly *up*. Ratios are only meaningful when the denominator holds still; the
absolute column is the one to read.

**Supersampling is a bad trade**: 2.3× the frame-rate cost for 3.5%.

## What ForceUpdate is actually fixing

A Lumen-off control separates the two candidate sources:

| | Lumen on | Lumen off |
|---|---|---|
| baseline | 20.71 | 17.56 |
| ForceUpdate | 17.83 | 17.61 |

Lumen costs **+3.15** of noise, and `ForceUpdate` recovers essentially all of it (−2.88). With
Lumen off, `ForceUpdate` does nothing (17.56 → 17.61). So the fraction it removes is
**stochastic Lumen GI sampling noise**, denoised by the temporal accumulation that per-frame
capture finally makes possible. `ForceUpdate` buys Lumen's tonal benefit at Lumen's noise cost
removed — the two settings belong together.

## An honest limit on the metric

A residual ~17.6 vs native ~7.0 survives every lever: not Lumen, not temporal, and — tellingly —
**not meaningfully reduced by 2× supersampling**. Genuine geometric edge aliasing would have
fallen substantially under a box filter. That it did not suggests a large part of this "2.5×"
is not AirSim noise at all but **native's TSR softening the reference**: `|image − median3|`
cannot distinguish speckle from sharpness, and the native frame is measurably blurrier.

The colour fringing in the crops is a real artifact and `ForceUpdate` visibly removes it. The
*magnitude* of the remaining gap is not trustworthy and should not be quoted as "AirSim is 2.5×
noisier" without a metric that separates sharpness from speckle — a matched-blur comparison, or
scoring only chroma against a luma-preserving baseline.

## Still open
- **Lumen has a cost.** +16 range is real; the frame-rate price has not been measured, and Lane
  C's sensor rates (31 Hz RGB) are a shipped claim.
- **Spawn placement** remains unfixed. `z = −10` was found by sweeping, not derived; the
  underlying problem — an arbitrary world has no obligation to put usable ground at the origin
  — is untouched.
