# `docker/demo/` — human-facing demos

Things you run to **look at** the simulator. None of this is needed by CI, and none of it
belongs in the shipped image — that is why it lives here rather than in
[`../`](../) (Dockerfile + entrypoint) or [`../../tests/`](../../tests/) (the acceptance
gate).

| File | What it does |
|---|---|
| `lane-a-video.Dockerfile` | Thin image on top of `drone-sim/lane-a` adding Xvfb, ffmpeg, xterm, openbox, and an unprivileged `qgcuser` |
| `record-attached.sh` | Records four panes of the **running compose stack**: Gazebo GUI · QGroundControl · PX4 console · ROS 2 controller |

## Build the demo image

```bash
docker build -f docker/demo/lane-a-video.Dockerfile -t drone-sim/lane-a-video:v1.16.0 .
```

Base layers are cached, so this takes ~1 minute rather than rebuilding PX4.

## Record a four-pane flight

Run it as a compose service against a stack that is already up:

```bash
docker compose -f docker/compose.yaml --profile record run --rm recording
```

**It attaches; it does not start its own simulator** (`D-02c`). The old recorders started a
private PX4, Gazebo and agent, so the video showed a stack that merely *resembled* the one
under test — and needed a MAVLink flight script to arm, which broke the rule that only
QGroundControl speaks MAVLink over IP. Both are deleted.

It exits **non-zero** if the flight did not succeed, and clears the previous run's
artifacts first: a stale `mission-result.json` reads `"outcome": "success"` and looks
exactly like proof that this recording flew.

Everything is headless — one Xvfb display with software GL (`llvmpipe`), openbox for window
management, `xdotool` for tiling, `ffmpeg x11grab` for capture. No GPU or physical display
needed.

## Things that cost time to discover

- **QGC is not in the image** (172 MB, and CI has no use for it) — bind-mount it from
  `vendor/tools/`.
- **QGC refuses to run as root** and exits with a dialog. The demo image adds `qgcuser` and
  drops privileges for that pane only.
- **Only QGroundControl speaks MAVLink over IP here.** The flight is driven by the ROS 2
  offboard controller over uXRCE-DDS, which is how the real vehicle is wired. The old
  `lane-a-fly.py` MAVLink script has been **removed** — it predated the controller and
  violated that split.
- **Xvfb needs `-ac +extension GLX +extension RANDR +render -noreset`**, or the Gazebo GUI
  dies mid-run with `XIO: fatal IO error 2 on X server :99`.
- **`MAV_CMD_NAV_TAKEOFF` alone does not take off.** PX4 ACKs it, then auto-disarms
  ("Disarmed by auto preflight disarming"). The working sequence is `MIS_TAKEOFF_ALT` +
  `DO_SET_MODE(AUTO.TAKEOFF)`.
- **Arming needs a GCS datalink.** `rcAndDataLinkCheck.cpp:81` refuses to arm whenever
  `NAV_DLL_ACT > 0`, and the x500 airframe sets it to 2. The check stays **enforced**;
  QGroundControl (the `qgc` compose service) supplies the link.
- **Never resize QGC's window externally.** This cost two rounds of debugging. `xdotool
  windowsize` leaves the window mapped, viewable and correctly positioned — and painting
  NOTHING: the Qt Quick software backend gets no repaint trigger on a headless Xvfb with no
  compositor, so the pane records as **solid black while every check reports success**
  (`xwininfo` said `IsViewable`, `xdotool getwindowgeometry` said `960,0 960x540`). The fix
  is to seed QGC's own `[MainWindowState]` so it *starts* at the right geometry and is never
  resized. The recorder still maps/raises it — its window comes up `IsUnMapped` — but does
  not move or size it.
- **QGC's first-run dialogs ARE suppressible — but the key is `firstRunPromptIdsShown`
  under `[General]`, not `[AppSettings]`.** An earlier attempt put it in the wrong section
  and silently did nothing. Found by dismissing the prompt with a synthetic click and
  diffing the ini, rather than guessing twice. It is a QUOTED, COMMA-SEPARATED LIST: there
  are at least two prompts (1 = Measurement Units, 2 = Vehicle Information), so suppressing
  only the first just reveals the second.
- **The Gazebo camera must be told to follow the vehicle**, or the GUI opens on a wide
  default view and the drone is a dot. `/gui/follow` + `/gui/follow/offset` — the same
  services PX4 itself uses (`px4-rc.gzsim:147`). **Both return `data: true` at any offset,
  including useless ones**, so the reply proves the call worked, not that the framing did.
  `-6,-6,3` still left a speck; `-3,-3,1.5` renders a recognisable aircraft.

## Not a substitute for the acceptance test

These produce pictures, not verdicts. The gate is
[`../../tests/lane-a-smoke.sh`](../../tests/lane-a-smoke.sh).

The flight itself is no longer demo code: these recorders now drive
`ros2_ws/src/control/`'s offboard controller over **uXRCE-DDS**, the same node the
acceptance gate uses.
