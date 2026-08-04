# `sim/ue5/` — the simulator's vehicle and sensor configuration

**Unreal Engine 5.8 + Cosys-AirSim**, tag `5.8-v3.4.1`, SHA `a552dd6c` (`versions.lock`).
`settings.json` here is the file the simulator is started with — it decides which vehicle
exists, which sensors it carries, and how each one is tuned.

| File | What it is |
|---|---|
| `settings.json` | the reviewed default: PX4 external-autopilot mode, IMU/GPS/magnetometer/barometer, a 16-channel GPU-LiDAR, and one `front_center` camera publishing Scene + DepthPlanar |
| `examples/minimal-no-lidar.json` | GPU-LiDAR disabled, cameras at 320×240. Verified: the `gpulidar` topic disappears from the graph and `image.width` reads `320` |
| `examples/with-chase-camera.json` | a second camera, to show how camera blocks compose |
| `.settings.run.json` | **generated, git-ignored.** The run-time copy `sim_up.sh` actually mounts |

```bash
cp sim/ue5/settings.json my-settings.json
$EDITOR my-settings.json
./scripts/sim_up.sh --settings my-settings.json
```

**A run never modifies the committed file.** Whatever you pass with `--settings` is
normalised into `.settings.run.json` and mounted from there, so your configuration and the
reviewed default cannot drift into each other — and a spawn override (`--spawn`) rewrites
only that run-time copy.

## Four things in this file that fail silently if you get them wrong

- **The `Sensors` block REPLACES the defaults; it does not extend them.** AirSim only
  creates default sensors "when none specified in json". An earlier version of this file
  listed *only* the barometer, leaving the vehicle with no IMU, GPS or magnetometer — PX4
  armed, then auto-disarmed with `Preflight Fail: ekf2 missing data`, which reads exactly
  like a control bug. List every sensor you want, every time.
- **Camera pose keys are not optional.** A camera declared without `X/Y/Z/Pitch/Roll/Yaw`
  keeps AirSim's NaN sentinels, reaches `FRotator::Quaternion` as `P=nan Y=nan R=nan`, and
  **SIGSEGVs the simulator during `BeginPlay`** — a crash, not a validation error.
- **`LumenGIEnable`, `LumenReflectionEnable` and `ForceUpdate` are why the imagery is
  photoreal.** With all three set, the capture matches Unreal's own render of the same view
  to **1.15 of 255**; drop them and the capture renders with global illumination and
  reflections forced off. They cost ~8.6% RGB and ~10% LiDAR throughput. Root cause:
  [`../../docs/worklog/2026-08-03-c11-washout-root-cause.md`](../../docs/worklog/2026-08-03-c11-washout-root-cause.md).
- **`"LockStep": true` is dead code in Cosys-AirSim** — confirmed 2026-08-01. It is
  silently ineffective, so the simulator runs free-running and every timing number here is
  a free-running number. Never quote a real-time factor from this stack as deterministic.
  The GPU-LiDAR shares the GPU with the renderer, so raising its channel or measurement
  counts degrades frame rate rather than slowing the clock.

Sensor keys worth knowing, the topics each one produces, and their measured rates are in
[`../../docs/quickstart.md`](../../docs/quickstart.md) §2–§3.
