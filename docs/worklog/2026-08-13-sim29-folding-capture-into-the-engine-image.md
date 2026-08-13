# 2026-08-13 — Folding the chase capture into the engine image

**`SIM-29`, second entry.** The first one proved the chase camera can be recorded off a virtual
screen, using a throwaway image built outside the repo. The question that produced this entry was
short: *"what if we remove the new container and just add the new packages to the unreal
container?"*

**That is the right call, and the probe was carrying two separate confusions.** It never needed
its own *container* — the capture already ran inside `sim-unreal` via `docker exec`; only the
first exploratory probe was standalone. And it needed its own *image* for exactly one reason:
five packages were missing.

**Headline: `xvfb`, `ffmpeg` and `x11-utils` now live in `docker/unreal.Dockerfile`. 178 MB on a
57.4 GB base — 0.3%. There is no extra image and no extra container.**

---

## Why the engine image and not a sidecar

**Xvfb is not separable.** Unreal resolves `DISPLAY` at process start, so the X server has to be
up *in this container* before the engine launches. No amount of sidecar arrangement changes that.

**`ffmpeg` is separable in principle** — a sidecar could grab over a shared `/tmp/.X11-unix` or a
TCP display. The reason not to is measured: the control run in the previous entry put encoding at
**1.3 fps of 32.0, 4.2%**. There is no isolation worth buying, and a sidecar costs a shared X
socket or an open TCP display plus a second lifecycle to keep in step with the renderer's.

The placement inside the Dockerfile matters as much as the choice. The existing apt block is a
**late, root-only layer**, positioned that way deliberately — the file's own comment records that
putting a cheap package high in `px4.Dockerfile` once invalidated a 20-40 minute PX4 build below
it. Adding three packages there invalidates that layer and the three cheap ones under it. The
54 GB pinned-by-digest base is untouched. Measured rebuild: **34 s**, nearly all of it exporting
layers.

**Trimmed from the probe's set:** `xdotool` (it found the window by title; the capture grabs the
whole screen, so nothing needs to locate anything) and `x11-xserver-utils` (`xrandr`/`xset` —
Xvfb's geometry is fixed by `-screen` at start). `x11-utils` stays, for `xdpyinfo`.

## Asserting the server, not the binary

`command -v Xvfb` passes on an install that cannot open a screen, and that failure would surface
at sim bring-up as a renderer that never draws — a long way from its cause. So the layer starts a
server, connects to it, checks the geometry, and tears it down:

```
Xvfb :98 -screen 0 320x240x24 &
for i in $(seq 1 40); do DISPLAY=:98 xdpyinfo >/dev/null 2>&1 && break; sleep 0.25; done
DISPLAY=:98 xdpyinfo | grep -q "dimensions:.*320x240"
```

This is the file's existing house rule — *assert the artifacts, not that the script reached its
end* — applied to a service rather than a binary.

**One hazard checked rather than assumed.** Those comments sit inside a `RUN` whose lines end in
`\`, and a `#` comment that swallowed the next line would silently delete the assertion while the
build still passed. Docker strips whole-line comments inside multi-line instructions, so it is
fine — but it was verified against the built image rather than trusted:

```
docker history --no-trunc ... | grep -o 'Xvfb :98[^;]*'   ->  Xvfb :98 -screen 0 320x240x24 & ...
docker history --no-trunc ... | grep -c 'Assert the ENCODER'  ->  0
```

The assertion is in the baked layer; the prose is not.

## Two bugs found by running it

Neither was in the feature. Both were in the harness written around it, and both had the same
shape: **a check that measured something adjacent to what it claimed to measure.**

### 1. "Is the file growing" is not a liveness signal

`record_chase.sh start` proved the capture was alive by sampling the output file twice, two
seconds apart, and requiring growth. It failed on a perfectly healthy capture:

```
[chase] FATAL: ffmpeg is running but the file is not growing (48B -> 48B)
```

48 bytes is an empty mp4 header. Three seconds later the same file was **524 KB**. x264 buffers,
and the pre-flight scene is a parked drone that compresses to almost nothing — so the encoder
genuinely had nothing to write yet.

A readiness check that fails whenever the drone happens to be still is worse than no check: it
would have made display mode look broken at random. **`ffmpeg -progress` is the right signal** —
its `frame=` counter advances per frame ingested, regardless of how little the encoder writes.

This is the same trap as the previous entry's, seen from the other side. There, x11grab's frame
count was *too generous* because it re-emits unchanged frames. Here, file size was *too stingy*
because the encoder elides them. **Both are proxies for "is the engine drawing", and neither is
that.**

### 2. Reporting that ran in the wrong filesystem

`stop` printed:

```
duration        ? s
frames grabbed  ?
```

while the recording itself was a perfectly good 63 MB, 7411-frame, 123.5 s h264 file. The stop
path copied the mp4 out, deleted the container's copy, and *then* ran `ffprobe` on the **host**
path from **inside** the container — which cannot see it. The host fallback found no `ffprobe`
either, because the video toolchain deliberately lives in images rather than on the workstation.

Fixed by analysing in the container, on the container's own copy, before copying anything out. It
is worth naming because **a reporting bug that prints `?` looks exactly like a capture failure**,
and the artifact it was describing was fine the whole time.

## What shipped

| | |
|---|---|
| `docker/unreal.Dockerfile` | `xvfb`, `ffmpeg`, `x11-utils` + a running-server assertion + versions recorded |
| `scripts/sim_up.sh` | `--display` / `DISPLAY_MODE`, waits on `xdpyinfo` rather than sleeping |
| `scripts/record_chase.sh` | `start` / `stop` / `status`, refuses a screenless stack, SIGINT not SIGKILL, chowns the artifact |

Verified by flying it, three times, on the display stack: `success, 4/4 waypoints` at 106.6,
106.1 and 105.9 s. The last recording: **6984 frames grabbed, 2946 distinct over 116.4 s**.

Both bring-ups hit `SIM-28`'s stale origin (9.118 m, 9.233 m) and repaired it. Display mode does
not perturb that trap.

## A correction to the previous entry's disk note

That entry warned a 40-seed gate would write ~2.5 GB of video, framed as a new cost. **It is
not.** `out/` already holds **2.0 GB** of per-seed mp4s from the existing `simGetImages` path,
measured with `du`. Chase video roughly doubles an existing cost rather than introducing one.
Worklogs are frozen, so the correction lives here and in the `SIM-29` entry.

## What is still open

- **`run_scenario.py` integration.** The bag and the video are driven separately today; the grab
  belongs in the same `try/finally` that owns the bag and the landing probe.
- **A seeded headless-vs-display timing comparison.** Three passing flights are not evidence that
  a windowed renderer leaves flight timing alone. Until that exists, display mode stays opt-in and
  the gate stays headless.
- **A world that is not Blocks.** ~31 fps is trivial geometry on a GPU that is also running the
  sim.
