# `docker/demo/` — human-facing demos

Things you run to **look at** the simulator. None of this is needed by CI, and none of it
belongs in the shipped image — that is why it lives here rather than in
[`../`](../) (Dockerfile + entrypoint) or [`../../tests/`](../../tests/) (the acceptance
gate).

| File | What it does |
|---|---|
| `lane-a-video.Dockerfile` | Thin image on top of `drone-sim/lane-a` adding Xvfb, ffmpeg, xterm, openbox, and an unprivileged `qgcuser` |
| `lane-a-fly.py` | Arms, takes off, hovers, lands over MAVLink — with `COMMAND_ACK` results and altitude readback |
| `lane-a-record-flight.sh` | Records one pane: the Gazebo GUI during a flight |
| `lane-a-record-quad.sh` | Records four panes: Gazebo GUI · QGroundControl · PX4 CLI · MAVLink script |

## Build the demo image

```bash
docker build -f docker/demo/lane-a-video.Dockerfile -t drone-sim/lane-a-video:v1.16.0 .
```

Base layers are cached, so this takes ~1 minute rather than rebuilding PX4.

## Record a four-pane flight

```bash
docker run --rm --shm-size=2g -e OUTDIR=/out -e RES=1920x1080 -e ALT=10 -e HOVER_S=45 \
  -v "$PWD/out:/out" \
  -v "$PWD/docker/demo/lane-a-record-quad.sh:/record.sh:ro" \
  -v "$PWD/docker/demo/lane-a-fly.py:/fly.py:ro" \
  -v "$PWD/vendor/tools/QGroundControl.AppImage:/qgc.AppImage:ro" \
  drone-sim/lane-a-video:v1.16.0 bash /record.sh
```

Everything is headless — one Xvfb display with software GL (`llvmpipe`), openbox for window
management, `xdotool` for tiling, `ffmpeg x11grab` for capture. No GPU or physical display
needed.

## Things that cost time to discover

- **QGC is not in the image** (172 MB, and CI has no use for it) — bind-mount it from
  `vendor/tools/`.
- **QGC refuses to run as root** and exits with a dialog. The demo image adds `qgcuser` and
  drops privileges for that pane only.
- **Ports matter when QGC and the script run together.** PX4 streams GCS telemetry to
  **14550** and onboard/offboard to **14540**. Binding 14550 in the script steals the GCS
  link and QGC shows *"Comms Lost"* — so `lane-a-fly.py` uses **14540**, which is also how
  the real vehicle is wired.
- **Xvfb needs `-ac +extension GLX +extension RANDR +render -noreset`**, or the Gazebo GUI
  dies mid-run with `XIO: fatal IO error 2 on X server :99`.
- **`MAV_CMD_NAV_TAKEOFF` alone does not take off.** PX4 ACKs it, then auto-disarms
  ("Disarmed by auto preflight disarming"). The working sequence is `MIS_TAKEOFF_ALT` +
  `DO_SET_MODE(AUTO.TAKEOFF)`.
- **Arming needs a GCS heartbeat.** `rcAndDataLinkCheck` refuses with *"Preflight Fail: No
  connection to the ground control station"*. `lane-a-fly.py` sends 1 Hz `HEARTBEAT` — i.e.
  it *is* a minimal GCS — rather than disabling a safety check.
- **QGC's window will not tile** (known limitation): it resizes itself after its first-run
  dialog and `xdotool windowsize` does not stick. Needs openbox per-app geometry rules or a
  seeded `~/.config` window state.

## Not a substitute for the acceptance test

These produce pictures, not verdicts. The gate is
[`../../tests/lane-a-smoke.sh`](../../tests/lane-a-smoke.sh).

`lane-a-fly.py` is likewise **demo code, not the flight stack** — Phase 1's offboard
controller belongs in `ros2_ws/src/control/` and talks **uXRCE-DDS**, not MAVLink. This
script exists because MAVLink was the fastest route to a takeoff for a video.
