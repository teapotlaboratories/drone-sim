# 2026-08-01 — `C-02`: the UE5.8 engine image

**Task:** `C-02` — pull `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0` and build
Cosys-AirSim against it.
**Lane:** C. **Nothing flies here** — no PX4, no simulator run, no aircraft.

> Kept as the work happens.

---

## What this task has to prove

`C-01` established the tag *exists* by querying the registry for ~17 KB. That is not the
same claim as the image *works*, and the distinction has been written into `versions.lock`
deliberately: `lane_c.unreal_engine.status` stays `TODO-verify` until the image is **pulled
and run**, not merely observed.

Two specific things carried forward as unverified, both of which this task settles:

1. **The Ubuntu 22.04 base.** Read from the registry's config blob, never from a pulled
   image. It is architecturally load-bearing — ROS 2 Jazzy has no jammy packages, so it
   forces Lane C into at least two containers with the AirSim↔ROS 2 boundary as a socket.
2. **The 24 GB / 30-layer size.** Measured from the manifest; extracted size unknown until
   now, and the disk decision (`D-04`: internal NVMe, because the 7 TB external is a 7200 RPM
   spinning disk) was made on the compressed figure.

## Progress log

### Starting state

- Disk: **270 GB free** on the internal NVMe (`/var/lib/docker` lives here by decision).
- Reclaimed the build cache first, as `D-04` specified — 72 MB, less than the 17.4 GB
  recorded earlier, because the gate runs had already churned it.
- `docker login ghcr.io` with the existing `gh` token. **This writes a credential to
  `~/.docker/config.json`**, which was previously absent — recorded because `D-04` calls the
  credential requirement a reproducibility hazard, and this is the moment the hazard becomes
  concrete on this machine.
- `docker manifest inspect` re-confirmed the tag resolves before committing to the download.

### Housekeeping caught on the way in

`.claude/` appeared as an untracked directory after a permission rule was added, and
`.gitignore` had no entry for it — so the next `git add -A` would have committed
`settings.local.json`, a per-operator permission allow-list, into a shared repo. Ignored.

### The pull — 9 minutes, and the digest matched

```
start 05:34:52Z  →  end 05:43:57Z   (9m05s)
Digest: sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46
```

**Byte-identical to the digest recorded from the registry query in `C-01`.** The pin
reproduces.

| | |
|---|---|
| compressed (manifest) | 24.0 GB / 30 layers |
| **extracted on disk** | **57.4 GB** — a 2.4× expansion |
| of which the engine | 54 GB at `/home/ue4/UnrealEngine` |
| internal NVMe free | 270 GB → **215 GB** |

The `D-04` storage decision was taken on the *compressed* figure. 57.4 GB against 270 GB is
still comfortable, but it is worth restating what that leaves: **215 GB free before any UE
build output, Cosys-AirSim plugin, or packaged project.** The `D-04` watch item — revisit
below ~100 GB — is now one large build closer than it looks.

### Ubuntu 22.04 — confirmed a third time, and this time from inside

```
os : Ubuntu 22.04.2 LTS (jammy)
```

Read from `/etc/os-release` in a running container. Previously this rested on the registry
config blob, and before that on a research agent's report. **All three agree**, and the
architectural consequence stands: ROS 2 Jazzy has no jammy packages, so Lane C is at least
two containers with the AirSim↔ROS 2 boundary as an RPC/MAVLink socket. Not a preference.

### It is a real, promoted UE 5.8.0

```json
{ "MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 0, "IsPromotedBuild": 1 }
```

Not a preview, not a nightly.

### The toolchain finding that changes the build command

```
bundled     /home/ue4/UnrealEngine/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/
              Linux_x64/v26_clang-20.1.8-rockylinux8/
UnrealBuildTool.dll   present
system clang / clang++  ABSENT     (gcc 11 and g++ present)
Setup.sh / GenerateProjectFiles.sh  ABSENT — this is a PRE-BUILT engine, not a source tree
```

Two things follow, and both are actionable:

1. **`--ue-root` is mandatory, not advisory.** Cosys-AirSim's `build.sh` on the 5.8 line
   supports it and uses the engine's bundled clang. There is **no system clang in this
   image**, so a build that does not pass `--ue-root` has no compatible compiler at all —
   it will not fall back gracefully. The bundled toolchain is **clang 20.1.8**, exactly what
   upstream is documented to have built v3.4.1 with, so the ABI-mismatch risk that made the
   UE5.5 line awkward does not arise here.
2. **`dev-slim` ships an *installed* engine, not a source checkout.** `Setup.sh` and
   `GenerateProjectFiles.sh` are absent. Plugin builds go through `UnrealBuildTool.dll`,
   which is present. Anything in upstream's instructions that assumes a source tree needs
   translating.

### Status: pulled and probed, NOT built against

`versions.lock` moves `lane_c.unreal_engine` from `TODO-verify` to `observed` — the image
exists, pulls, runs, and contains the engine it claims. It does **not** move to `LOCKED`,
because nothing has been compiled against it yet. *Existing is not working; running a probe
is not building a plugin.*

*(…and then it was, an hour later. See below.)*

---

## The plugin build — and the one-second failure that produced `D-04`

`./build.sh --ue-root /home/ue4/UnrealEngine` on the **stock** engine image died in **one
second**:

```
++ which cmake
+  CMAKE=
exit=1
```

**`dev-slim` has no cmake.** The tempting move is `apt install cmake` in a running container
and carry on. That would have worked and been unreproducible — the exact thing the
reproducibility goal exists to prevent. So instead, every tool the scripts invoke was
enumerated in one pass rather than one crash at a time:

| | |
|---|---|
| needed by `build.sh` / `setup.sh` | `clang cmake gcc make rsync unzip wget zip` |
| already in `dev-slim` | `make unzip zip gcc g++ git curl python3 tar patch pkg-config` |
| **missing, and added** | **`cmake` `rsync` `wget`** |

That is `docker/lane-c.Dockerfile` — the engine pinned **by digest** (not the moving
`dev-slim-5.8` alias), plus those three, **+33 MB** on a 57.4 GB base.

**`clang` is deliberately not installed.** The engine ships
`v26_clang-20.1.8-rockylinux8` and `--ue-root` points `CC`/`CXX` at it. A system clang would
recreate the ABI mismatch against UE's linker that the UE5.5 line already documented —
`build.sh` agrees, refusing `--ue-root` together with `--gcc` because *"Unreal Engine's
bundled toolchain is Clang-only"*.

### A version-recording layer that silently recorded nothing

The Dockerfile's first version extracted the engine version with an inline Python f-string
containing escaped quotes — a `SyntaxError` on this interpreter. **The layer succeeded
anyway**, because the redirect still created the file, and wrote `ue_engine=` empty.

A version-recording step that silently records nothing is worse than none: *it looks like
evidence*. Same class as the build script that printed `BUILD OK` after `make` exited 2.
Fixed with `set -eux` plus explicit non-empty assertions, and **verified by breaking it** —
the guard fires with `FATAL: a version probe produced an empty value`.

```
ue_engine=5.8.0        ue_toolchain=v26_clang-20.1.8-rockylinux8
base_os=22.04 jammy    cmake=3.22.1    rsync=3.2.7
```

### RESULT — it builds

```
Cosys-AirSim airlib c++ plugin is built!      exit=0, warnings only
```

Upstream prints a success banner, and this repo has been lied to by one before, so the
artifacts were asserted:

| Assertion | Result |
|---|---|
| `libAirLib.a` | **4.8 MB** |
| `libMavLinkCom.a` / `librpc.a` | 1.9 MB / 1.2 MB |
| `Unreal/Plugins/AirSim/Source/AirLib` | **1,226 files** (deps 1,042) |
| `…/AirLib/src` stripped, as `build.sh` intends | yes |
| **`readelf -p .comment libAirLib.a`** | **`clang version 20.1.8`** |

**That last row is the decisive one.** It is the artifact naming its own compiler: the
engine's bundled clang, *not* the image's system gcc 11. `--ue-root` genuinely took effect,
which is the whole reason the UE5.8 line was chosen over backporting onto UE5.5.

### Status after this

| Entry | Was | Now | Why |
|---|---|---|---|
| `unreal_engine` | `TODO-verify` | **`LOCKED`** | pulled, probed, and something real compiled against it |
| `cosys_airsim` | `TODO-verify` | **`observed`** | the AirLib plugin library builds |
| `cosys_airsim_ros2_wrapper` | — | `LOCKED` | from `C-06` |

**`cosys_airsim` is deliberately not `LOCKED`.** The plugin *library* builds. Nothing has
compiled the UE plugin through UnrealBuildTool, nothing has packaged a project, and the
simulator has never run — no PX4 link, no `/fmu/*` parity, no frames measured.
**Building is not running.**

## The plugin compiles against the engine — the test that actually decided UE5.8

**Terminology correction:** earlier notes in this log said "package". That is a different,
heavier job (`RunUAT`, producing a distributable binary). What was run is compiling the
**editor target** — building the AirSim plugin's C++ against Unreal's engine headers.

**Why it is the step that matters.** Everything before it proved a *standalone* library
compiles: `libAirLib.a` includes no Unreal headers and would build with no engine present at
all. The AirSim **plugin** is different code — it subclasses engine types and calls engine
APIs. An Epic rename or signature change between 5.5 and 5.8 could only surface here.

```
1/3  update_from_git.sh        rsync the plugin + AirLib into Blocks/Plugins/
2/3  GenerateProjectFiles.sh   against /home/ue4/UnrealEngine
3/3  Build.sh BlocksEditor Linux Development

[79/81] Link libUnrealEditor-AirSim.so
[80/81] Link libUnrealEditor-Blocks.so
Result: Succeeded          71.71 s, 81/81 actions
```

Faster than expected — Unreal Build Accelerator, and the engine itself is prebuilt.

**Artifacts on the host mount, asserted rather than trusted:**

| | |
|---|---|
| `Plugins/AirSim/Binaries/Linux/libUnrealEditor-AirSim.so` | **5.5 MB, 77 AirSim symbols** |
| `Binaries/Linux/libUnrealEditor-Blocks.so` | 576 KB |
| `Intermediate/` + plugin `Intermediate/` | ~510 MB — disk moved only 216 → 215 GB |

**A false alarm I checked before reporting.** `ldd` on the plugin module says **12
unresolved libraries**, which looks alarming. All twelve are *engine modules* —
`libUnrealEditor-Core.so`, `-Engine.so`, `-RenderCore.so`, `-RHI.so` and so on — which live
in the engine tree, not on the host. Re-run inside the image with the engine lib path:
**zero unresolved**. Expected for a UE plugin, not a defect. Worth recording that the check
was run rather than the number waved away.

**One more way the 5.8 line is coherent:** `Blocks.uproject` declares
`EngineAssociation: 5.8`, an exact match — no convert-in-place prompt. At the 5.5 tag it
declared `5.4`, which triggers that prompt and is a documented point where a user hit a
failed compile.

### Status now

| Entry | Status | Meaning |
|---|---|---|
| `unreal_engine` | **LOCKED** | pulled, probed, built against |
| `cosys_airsim` | **LOCKED** | plugin compiles and links against the UE5.8 API |
| `cosys_airsim_ros2_wrapper` | **LOCKED** | from `C-06` |

**LOCKED means the pin is proven buildable — not that the lane works.** The simulator has
never run: no rendering, no PX4 over the MAVLink SITL API, no `/fmu/*` topics, no parity
diff against Lane A, no frames measured, no lockstep verified. And CARLA still holds
`0.0.0.0:41451`, the port `C-03` needs. **Compiling is not running.**

## Next

- `C-03`: PX4 over the MAVLink SITL API, `/fmu/*` parity diffed against Lane A, and the
  port-41451 conflict resolved before anything binds it.
- Optionally package a shipping binary (`RunUAT`) — not needed to run the editor — the genuinely hours-long step, and what
  `UnrealBuildTool` is for.
- Then `C-03`: PX4 over the MAVLink SITL API, `/fmu/*` parity diffed against Lane A, and the
  port-41451 conflict resolved before anything else binds it.
- `docs/vendor/cosys-airsim.md` still needs writing: the build writes artifacts *into* the
  vendored tree (`build_release/`, `AirLib/lib/`, `Unreal/Plugins/`). That is upstream's own
  layout rather than a source edit, and `vendor/` is git-ignored, but the tree is no longer
  byte-identical to the checkout and the vendoring notes should say so.

