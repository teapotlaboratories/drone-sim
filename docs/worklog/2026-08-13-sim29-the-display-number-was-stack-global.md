# 2026-08-13 — The chase recorder captured QGroundControl

**`SIM-29`, third entry.** Wiring the chase capture into `run_scenario.py` was meant to be
plumbing: start it where the RPC video starts, stop it in the `finally` that already owns the bag
and the landing probe, chown it, name it by seed. That part took an hour and worked.

**Then the regression test — a headless stack with `SIM_CHASE_VIDEO=1`, which should record
nothing — produced an 8.2 MB mp4.**

**Headline: `DISPLAY=:99` inside the renderer resolved to QGroundControl's X server, and the
recorder saved QGC's map view as `<tag>-chase.mp4`.**

---

## Why a headless container had a display

Every container in this stack shares **one network namespace** — that is the whole point of the
`--ipc shareable` / `--network container:$SIM` design, and it is what makes `127.0.0.1` mean the
same thing to PX4, the agent and the renderer.

An X server binds **two** sockets: a filesystem one at `/tmp/.X11-unix/X<N>`, and an **abstract**
unix socket. Abstract sockets have no filesystem presence and are scoped to the **network
namespace**.

So on this stack a display number is **stack-global, not container-local**. `sim-unreal` had:

```
pgrep -a Xvfb            -> no Xvfb process
ls /tmp/.X11-unix/       -> empty
DISPLAY=:99 xdpyinfo     -> name of display: :99   vendor: The X.Org Foundation
```

No local server, no local socket, and a working display — because `docker/qgc-entrypoint.sh:9`
has read `DISPLAY_NUM="${DISPLAY_NUM:-:99}"` since the Gazebo era, and QGC's Xvfb was answering
across the shared netns.

I picked `:99` for the renderer without checking who already had it.

## Why this was worse than a black rectangle

The guard I wrote in the previous entry has this comment:

> Without this check the grab would succeed and record a black rectangle, which is
> indistinguishable from a broken renderer at review time.

That was the failure mode I imagined, and it is the *benign* one. What actually happened is the
dangerous one. The extracted frame shows QGroundControl mid-flight: **"Flying / Offboard", 20.0 m,
0.1 m/s, the vehicle icon tracking across a satellite map.** It is a recording of a real flight,
correctly timed, of entirely the wrong thing.

**A black frame reads as broken. A map reads as evidence.** Anyone opening
`square-10m-seed9-chase.mp4` during a post-mortem would have seen a flight and believed it — and
this project has already lost days to arguing from a video that did not show what it seemed to
(`SIM-27`).

## The fix, in two parts, because either alone leaves it armed

**1. The renderer moved to `:77`.** QGC owns `:99` by documented default; the collision was
avoidable and should never have been created.

**2. Detection now requires a LOCAL `Xvfb` process**, not merely that `xdpyinfo` answers:

```
docker exec "$SIM" bash -lc 'pgrep -x Xvfb >/dev/null'   # is the server OURS
docker exec "$SIM" bash -lc "DISPLAY=:$N xdpyinfo ..."   # is it on the number we expect
```

Renumbering alone would have fixed today's collision and left the trap for the next person who
adds a service with a display. **On a shared netns, "a display answers" never meant "the display
is ours"** — and only the second check encodes that.

`record_chase.sh` and `run_scenario.py` both carry it, and the refusal says why:

```
[chase] FATAL: no Xvfb running inside 'sim-unreal' — bring the stack up with ./scripts/sim_up.sh --display
       (a display may still be REACHABLE here via the shared network namespace, e.g. QGC's —
        recording it would capture the wrong window, so this refuses rather than guess)
```

## What the integration itself looks like

Unremarkable, which is the point — it follows the shape already in `run_scenario.py`:

- `SIM_CHASE_VIDEO=1` starts the grab before the flight, so the recording brackets takeoff rather
  than clipping it, for the same reason the bag does.
- It stops inside the **same `finally`** as the bag and the landing probe. A controller timeout
  must not leave a 60 fps screen grab running into the next seed — the exact reasoning the probe's
  comment already gives for itself.
- The mp4 lands as `out/<scenario>-seed<N>-chase.mp4` and is surfaced as `chase_video` in the run
  JSON. An artifact nobody can find is not evidence.
- Every call is **non-fatal**. A capture failure must not fail a flight that flew.

Two deliberate departures:

- **`sim_up.sh --display` now bind-mounts `out/` into the renderer**, so ffmpeg writes straight to
  the final path. Staging in the container's writable overlay and `docker cp`-ing afterwards works
  — it is what the previous entry shipped — but a long capture is GBs through the overlay. Headless
  renderers do not get the mount.
- **`stop --no-distinct` in the gate path.** The `mpdecimate` pass decodes the whole file, ~10 s
  per capture, ~7 minutes across 40 seeds, for a per-seed number nobody reads.

## Verification

Seven `square-10m` flights, all `success, 4/4 waypoints`. The three that matter here:

| stack | `SIM_CHASE_VIDEO` | expected | got |
|---|---|---|---|
| `--display` | `1` | chase mp4 beside the MCAP | seed 7 — 62 MB, owner `deck`, in the run JSON |
| `--display` | unset | nothing | seed 8 — no chase file, `chase_video: None` |
| headless | `1` | warn, skip, fly anyway | seed 12 — warned, no file, `success` |

The display-mode frame was **looked at**, not merely counted: Blocks world, drone in frame,
`Collision Count:0`. That check is the one that would have caught this bug a day earlier, and it
is now the reason the fix is trusted.

## The lesson worth carrying

Every check in this feature has now failed the same way once: `x11grab`'s frame count was too
generous, file size was too stingy, and `xdpyinfo` answered for someone else's server. **Three
proxies for "is the renderer drawing", none of which was that.**

The one thing that has never lied is opening the file and looking at it.
