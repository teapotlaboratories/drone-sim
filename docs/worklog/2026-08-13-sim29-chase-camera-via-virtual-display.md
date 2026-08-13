# 2026-08-13 — Recording the chase camera by giving the engine a screen

**`SIM-29`.** The question that started it: *"we can't record a chase camera in high res, is that
correct?"* — then, when the answer turned out to be about the readback path rather than the
camera: *"anyway to capture the AirSim chase? I'm surprised they don't have a headless option, if
not can you explore using virtual screen."*

**Headline: the chase camera was never missing. It has been running the whole time, rendered to
nothing.** Give the engine an Xvfb display instead of `-RenderOffScreen` and the view records at
~31 fps at 1080p — against ~13-14 Hz for `simGetImages`, which cannot frame the aircraft at all.

Nothing here is committed to the stack. This is a feasibility result produced by a throwaway
image built outside the repo, deliberately, so that "can we do this at all" was answered before
anything was proposed for `docker/`.

---

## Why the obvious path is a dead end

The instinct is to ask AirSim for the chase image over RPC. It cannot be done, for two
independent reasons, and it is worth writing both down because either alone would be enough.

**There is no RPC binding.** The chase camera is `AirSimCameraDirector`, whose modes live in
`ECameraDirectorMode` (`AirSimCameraDirector.h`). Nothing in `RpcLibServerBase.cpp` exposes it.
`simGetImages` serves *vehicle-mounted* cameras only — the ones declared in `settings.json` — and
the director's viewpoint is not one of them. It is a viewport concept, not a sensor.

**And the readback path is slow anyway.** `RenderRequest::getScreenshot` blocks on a GPU→CPU
readback measured at roughly 71 ms fixed plus ~5 ms/MB, which puts a ceiling near 13-14 Hz **at
any resolution** — the fixed term dominates, so asking for a smaller image does not help. That
same readback is what `SIM-23` was about; it stalls the render thread while it runs.

So the honest framing of the original question is not "why is the chase camera low-res". It is
**"the chase camera is not on the RPC surface, and the RPC surface would be too slow regardless"**.

## The thing that was hiding in plain sight

`ViewMode` is a `settings.json` key — `Fpv | GroundObserver | FlyWithMe | Manual | SpringArmChase
| Backup | NoDisplay | Front` — and for a multirotor it **defaults to `FlyWithMe`**.

The chase camera has therefore been active on every run this project has ever done. `sim_up.sh`
launches with `-RenderOffScreen`, so the engine renders that view to a surface nobody can read.
The capability was never absent; it was never *presented*.

That reframes the work entirely. Nothing needs to be added to Cosys-AirSim — no plugin change, no
new RPC, no patch. The engine needs a screen.

## What was built to test it

A throwaway image, `drone-sim/unreal-xvfb:probe`, `FROM drone-sim/unreal:ue5.8` plus `xvfb`,
`x11-utils`, `ffmpeg` and `xdotool` — all four absent from the engine image, which is the only
reason a new image was needed at all.

First probe: bare editor, no PX4, no ROS 2. `Xvfb :99 -screen 0 1920x1080x24`, then
`UnrealEditor Blocks.uproject -game -nosound -windowed` with `DISPLAY=:99`. The window appeared
(`xdotool search` found `Blocks Environment for Cosys-AirSim`) and `ffmpeg -f x11grab` captured
it. The extracted frame showed the Blocks world, the `Collision Count:0` HUD, and **the drone**.

Then the full stack, because a bare editor proves rendering and nothing about flying. `sim_up.sh`
has no display mode, so a copy in scratchpad carried two edits — the image, and
`-RenderOffScreen` replaced by an Xvfb launch at `-ResX=1920 -ResY=1080 -windowed`. The repo was
not touched. `run_scenario.py --no-restart` then flew against it.

Worth recording: **that bring-up hit `SIM-28` and repaired it** — `ref_alt 114.069 vs GPS 123.302,
9.233 m apart`, PX4 restarted, re-verified at `0.000 m`. The display path did not perturb the
known trap, and the known trap did not need special handling here.

## The measurement, and the trap inside it

The first number was wrong, and the way it was wrong is the useful part.

The static probe reported "600 frames in 10 s at 1080p, 60 fps". That is the **grab** rate.
`x11grab` samples the X server on its own clock and **re-emits the previous frame when the screen
has not changed**, so it will always report exactly the rate you asked for, whatever the engine is
doing. Against a parked drone — a scene that barely changes — it reported 60 fps while the true
distinct-frame rate was near zero.

Distinct frames have to be counted explicitly: `-vf mpdecimate -vsync vfr`, which drops
near-identical frames, and the surviving count is what the engine actually drew.

Flying `square-10m` seed 1 (`success, 4/4 waypoints, 106.6s`) while grabbing at 60 fps, 1080p:
7356 frames grabbed, **2954 distinct**. Per 10 s window:

| window (s) | distinct fps | what was happening |
|---|---|---|
| 0-10 | 0.1 | parked, pre-arm |
| 10-20 | 12.5 | takeoff beginning |
| 20-110 | **29.0 - 32.2** | the flight |
| 110-120 | 7.2 | landed, static |

The idle windows are not a defect — a parked drone genuinely does not change the screen. But they
mean **any average across a whole run understates the flight**, so the flight window is the only
honest figure.

## The control, which is the load-bearing part

~31 fps could be the engine's render rate, or it could be x264 stealing CPU from the renderer at
1080p60. Those have different consequences — one is a ceiling, the other is a configuration
mistake — so it was worth a second flight to separate them.

The control grabs identically but encodes nothing: `-vf mpdecimate -vsync vfr -f null -`, with
`-progress` giving a time series so the flight window could be isolated the same way. Same
scenario, same seed (`success, 4/4 waypoints, 106.1s`).

| 90 s flight window | distinct frames | fps |
|---|---|---|
| grabbing + x264 1080p60 | 2756 | **30.6** |
| grabbing, no encoder | 2877 | **32.0** |

**Encoding costs 1.3 fps — 4.2%.** So ~31 fps is the engine's own render rate under flight load,
and the capture is very nearly free. That is the answer to the question the first run left open.

Two caveats that belong next to the number: this is **Blocks on the 3080**, trivial geometry on a
GPU that is also running the sim, so it will move with world complexity and GPU load. And two
successful runs are not evidence that display mode leaves flight timing alone — that needs a
seeded comparison against headless before this becomes a default anything.

## Where it stands

| | `simGetImages` | Xvfb + x11grab |
|---|---|---|
| chase view / drone in frame | impossible | **yes** |
| rate under flight | ~13-14 Hz | **~31 fps** |
| cost to the simulator | blocking GPU→CPU readback, stalls the render thread | reads a screen already drawn |
| changes to Cosys-AirSim | — | **none** |

`SIM-29` carries the build: an opt-in display mode in `sim_up.sh` (headless stays the gate's
normal path), `xvfb` and `ffmpeg` into the engine image, and the grab started and stopped by
`run_scenario.py` inside the same `try/finally` that owns the bag — including the chown, because
`SIM-26` was exactly this bug for exactly this reason and a second writer into `out/` will
reproduce it.

The open question that most wants answering first is disk: 64 MB per 122 s at CRF 23 means a
40-seed gate writes ~2.5 GB. That is a decision to take before it lands, not after a gate fills
the NVMe.

## Dead ends and corrections, recorded

- **"We can't record the chase in high res"** — the premise was resolution; the actual constraint
  was that the chase view is not on the RPC surface at all.
- **"60 fps at 1080p"**, reported from the static probe, measured the grab clock rather than the
  engine. Corrected by `mpdecimate`. A capture tool that never reports less than you asked for is
  a tool that cannot tell you it is failing.
- **`/usr/bin/time` is absent** from the probe image, and **`-fps_mode` is not in this ffmpeg**
  (it wants `-vsync`). Both cost a round-trip; neither is interesting except as a reminder that
  the probe image is Ubuntu-minimal.
- **`REPO` in `sim_up.sh` derives from `${BASH_SOURCE[0]}`**, so a copy of the script in scratchpad
  looks for the repo beside itself and dies on a missing `settings.json`. Pinned explicitly in the
  copy.
