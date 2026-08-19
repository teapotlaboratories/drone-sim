# Bring your own world

How to take an Unreal project that was never built for this simulator and fly a drone in it.

Everything here was learned converting real projects — Epic's **CitySample** most recently — and
every failure described is one that actually happened, with the measurement that identified it.
The automated path is [`scripts/convert_world.sh`](../scripts/convert_world.sh); the manual steps
are documented so you can debug it when it does not work.

> There is an illustrated version of this page at [`worlds.html`](worlds.html) — same content, with
> diagrams of the conversion pipeline, the World Partition failure, and the resting-`z` verdict.

```bash
./scripts/convert_world.sh /path/to/Your.uproject --map /Game/Maps/YourMap
./scripts/sim_up.sh --world /path/to/Your.uproject --spawn 0,0,-50
```

---

## Before you start: will it work at all?

| Requirement | Why |
|---|---|
| **UE 5.8** | The engine is pinned. A project targeting 5.2–5.5 usually still compiles, but expect warnings-as-errors (below). |
| **Linux-buildable** | Windows-only plugins or precompiled Win64-only binaries will not link. |
| **Fits in 10 GB VRAM** | The render GPU is the 3080. Epic's `Big_City_LVL` does not fit; `Small_City_LVL` does. |
| **On the internal NVMe, ideally** | UE5 streaming is latency-sensitive random I/O. A spinning disk works but loads slowly, and makes the streaming race below lose more often. |

**A1 or A2?** If the project has a `Source/` directory it is **A2** and its C++ must be compiled
against UE5.8. If it is content and Blueprints only, it is **A1** and needs no compile. The script
detects this and tells you; it changes how long the conversion takes far more than anything else.

---

## What the conversion actually does

### 1. Inject AirSim — the silent failure

`inject_airsim.py` copies the built plugin, enables it in the `.uproject`, and sets two config
keys. The important one:

```ini
GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode
```

**If you skip this, nothing works and nothing tells you.** The project keeps its own game mode,
AirSim never instantiates, nothing ever listens on TCP 4560, and PX4 sits forever in
`Waiting for simulator to accept connection on TCP port 4560`. There is no error line. City Park
and CitySample both hit this. Confirm with:

```bash
grep -i GlobalDefaultGameMode <your-project>/Config/DefaultEngine.ini
```

Pass `--map` too. Without it the project keeps its own default map, which frequently has no
ground the vehicle can land on.

### 2. Apply the Unreal-side vendor patches

`vendor/Cosys-AirSim` stays byte-identical to upstream; deviations live in
[`patches/cosys-airsim/`](../../patches/cosys-airsim/) and are applied to the *injected copy*.
The one that matters for modern worlds is `0005-worldpartition-streaming-source.patch` — see
**World Partition** below.

### 3. Build (A2 only)

Two things break, in this order, on essentially any project older than 5.8:

**`-Werror` on unreachable code.** UE5.8 ships a newer clang that promotes
`-Wunreachable-code-break` and `-Wunreachable-code-loop-increment` to errors. On CitySample this
hit **14 files** across Epic's own RuleProcessor and Traffic plugins *and* Cosys-AirSim. The fix
is to downgrade exactly those two, not to disable warnings-as-errors wholesale:

```csharp
AdditionalCompilerArguments += " -Wno-error=unreachable-code-break -Wno-error=unreachable-code-loop-increment";
```

**UBT then refuses that.** Because the editor target shares build products with `UnrealEditor`,
UBT rejects per-target compiler args and suggests `TargetBuildEnvironment.Unique` — which forces
a **full engine rebuild**, hours of work for two warnings. Take the cheap door instead:

```csharp
bOverrideBuildEnvironment = true;
```

The script inserts both into your `*Editor.Target.cs`, backing it up to `.pre-airsim` first.

Build the **editor** target, not the game target — `sim_up.sh` runs `UnrealEditor <project> -game`,
so the editor modules are what must exist.

---

## World Partition: the one that costs a day

**Symptom:** the level loads, PX4 connects, and the drone falls forever. Bring-up fails with
`no finite EKF origin`.

**Cause:** World Partition activates streaming cells around a registered **streaming source**,
normally the player pawn. AirSim spawns its vehicle without one, so **no cell ever loads** — the
map opens, `GenerateStreaming` completes, and nothing activates. There is then no collision
geometry anywhere in the level.

Measured on CitySample, resting `z` in **NED, where +z is DOWN**:

| Spawn | Resting `z` | Result |
|---|---|---|
| `0,0,0` | +332 m | fell through |
| `0,0,-150` | +139.5 m | fell through |
| `0,0,-150` + `wp.Runtime.EnableStreaming=0` | +1481 m | fell through |

Releasing higher only buys more fall, so **this is not a spawn-height problem** and not the
underground-spawn trap. The cvar route does not work either.

`patches/cosys-airsim/0005` fixes it by giving `AFlyingPawn` a
`UWorldPartitionStreamingSourceComponent`. After it, same world and spawn: resting
`z = -8.4e-05 m`, EKF origin sane, and a full 4/4 waypoint mission flies.

> ### It is necessary but NOT sufficient — a race remains
>
> Cell streaming takes seconds (`GenerateStreaming` measured 7–22 s) while the vehicle falls
> immediately. It can still pass the ground plane before the cells beneath it activate. Measured
> on identical builds and the same spawn: `z = -8.4e-05` (flew) / **`+1697`** (fell) /
> `-1.0e-03` (flew). **Roughly 2 runs in 3 succeed — retry on failure.**
>
> A real fix must hold the vehicle until streaming completes rather than race it. Unsolved; see
> `SIM-21`.

---

## Your world needs a scenario, not just a conversion                  (`SIM-36`)

A converted world is not yet flyable *evidence*. The missions in `scenarios/` are written for the
world each one declares, and their waypoints are **ENU relative to HOME** — so pointing an existing
scenario at your world with `--world` is now an **error**, not a shortcut:

```
scenario/world mismatch: this scenario declares … and --world says …
```

Write a scenario for your world — `scenarios/README.md` lists exactly what to change, and the one
that matters most is the **landing point**: `square-10m.yaml` returns to its takeoff corner, which
is harmless in an empty box and is how a landed aircraft ends up under a crowd in a city.

`--force-world` overrides the check and is recorded in the run's provenance.

## Verifying — the part people skip

A level that renders is not a world that flies. **Judge by resting `z`, not by the log.**

```bash
docker exec sim-ros2 bash -lc 'source /opt/ros/jazzy/setup.bash; \
  source /ros2_ws/install/setup.bash; ros2 topic echo --qos-reliability best_effort \
  --qos-durability volatile --once --field z /fmu/out/vehicle_local_position'
```

| Reading | Meaning |
|---|---|
| `z ≈ 0` | Resting on the ground. Good. |
| `z` large and **positive** | Fell through. Retry once (the race); if it never lands, there is no collision under the spawn. |
| `z` large and **negative** | Spawned in the air and still falling, or the spawn is above a rooftop. |
| `ref_alt = nan` | The EKF never got an origin — always a consequence of one of the above, never the cause. |

Then fly it for real:

```bash
docker cp scripts/verify_nav_interface.py sim-ros2:/tmp/
docker exec sim-ros2 bash -lc 'source /opt/ros/jazzy/setup.bash; \
  source /ros2_ws/install/setup.bash; python3 /tmp/verify_nav_interface.py'
```

That arms and flies a **simulated** vehicle — takeoff, waypoint, velocity and GPS-waypoint checks.
SITL only.

### Two measurements that lie

- **`cell load lines` is a bad proxy.** UE does not log per-cell activation at default verbosity,
  so grepping `LogWorldPartition` for `Loaded|Activated` returns **0 even when streaming works**.
  It reported failure on a run that flew perfectly.
- **A rebuild may not reach the pawn that runs.** AirSim spawns a *Blueprint subclass*
  (`BP_FlyingPawn_C`), not the C++ class, so a change that never propagated looks identical from
  outside. If you are changing pawn C++, log from `BeginPlay` and read it back from the running
  renderer — verify the artifact that *ran*.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| PX4 waits forever on 4560, AirSim logged **nothing** | `GlobalDefaultGameMode` is not `AirSimGameMode`. Re-run with `--force`. Raising `SIM_LINK_TIMEOUT` will not help. |
| PX4 waits, AirSim **has** logged, `ShaderCompileWorker` running | Genuinely still compiling shaders. Raise `SIM_LINK_TIMEOUT`; the `sim-ddc` volume makes the next run much faster. |
| Build fails on some *other* `-Werror` warning | Add it beside the two in `*Editor.Target.cs`, same form. |
| Build succeeds, no `.so` produced | Wrong target — you built the game target, not `*Editor`. |
| Wrong target chosen on a project with several | The script prefers `<Name>Editor.Target.cs`. A bare `*Editor` glob sorts alphabetically and picks e.g. `FooCookedEditor` — a content-cooking target, not the one `-game` runs. |
| `no such map: /Game/...` | The content path does not resolve to a `.umap`. The error lists the maps the project actually has. Caught before anything is modified. |
| Drone falls through | World Partition (above). Retry, then check collision under the spawn. |
| Vehicle spawns underground | Use `--spawn X,Y,-Z` to release from height and read the resting position back — that *is* a ground probe. `Z` is NED: **negative is up**. |

---

## What the conversion leaves behind

In your project (all reversible):

- `Plugins/AirSim/` — the injected plugin. `--force` moves any existing copy to
  `AirSim.bak.<timestamp>` rather than overwriting.
- `Config/DefaultEngine.ini` — game mode, default map, `SF_VULKAN_SM6`, cook directives.
  Backed up as `DefaultEngine.ini.pre-airsim` by the script.
- `Source/*Editor.Target.cs` — the two build settings. Backed up as `.pre-airsim`.
- `Binaries/`, `Intermediate/` — build output.

> **A stale plugin copy under `Plugins/` silently wins.** If you ever have two, the wrong one can
> load with no warning. `inject_airsim.py` checks for shadowing copies and refuses rather than
> guess.

---

## Known gap

**Since `SIM-37` the wrapper is baked into `drone-sim/ros2`, so a stack brought up by
`sim_up.sh` already has the sensor graph — this script is no longer part of bring-up.**
It rebuilds the wrapper inside a running container (deleting the image's copy first), which
is how a wrapper patch gets tested without a full image rebuild.

`inject_airsim.py` copies the **built** plugin from Blocks, so `patches/cosys-airsim/0005` does
not reach a converted world through the plugin itself — `convert_world.sh` applies it to the
project's own copy afterwards and rebuilds, which is why an A1 project with patches still needs a
build. Wiring the Unreal-side patches into the Blocks plugin build is unsolved (`SIM-21`);
`build_airsim_wrapper.sh` covers the ROS 2 wrapper only.

`CarPawn` has the same World Partition gap as `AFlyingPawn` and is deliberately untouched —
nothing in this project drives a car.
