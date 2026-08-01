# Reproducibility — Docker — backlog

**Area:** containerization, reproducible builds, one-command bring-up.
**Indexed from:** [`../drone-sim-todo.md`](../drone-sim-todo.md).

> **Project goal (added 2026-07-29):** *the whole setup must be easily reproducible as
> Docker.* Not "there are some Dockerfiles" — a fresh machine (or a fresh container on
> this one) must reach a working stack from the repo alone, with no undocumented manual
> steps.

**Definition of done — Lane A:** `docker compose up` brings the Lane A stack to the same
state `P0-07` proved by hand — headless SITL, `/fmu/out/*` populated, QGC-consumable
MAVLink — on a machine that has only Docker + an NVIDIA driver. **Met (`D-01`, `D-02`).**

**Definition of done — Lane C, added 2026-07-31 and NOT met.** Lane C is now the project's
primary stack (`../lane-c/todo.md`), so "reproducible as Docker" no longer means Lane A
alone. Lane C is the harder case in three specific ways, all discovered on 2026-07-31:

> ### The reproducibility goal has a hole in it, and it is worth stating plainly
>
> **The Lane A DoD says "a machine that has only Docker + an NVIDIA driver". Lane C cannot
> meet that as written.** Its engine base image, `ghcr.io/epicgames/unreal-engine`, is
> **credential-gated**: anonymous reads return HTTP 403, and pulling needs EpicGames GitHub
> **org membership plus a PAT with `read:packages`**. A clone of this repo plus a Dockerfile
> is **not sufficient** to build Lane C, and no amount of pinning fixes that.
>
> This is a genuine, permanent constraint from upstream licensing — not a gap to close.
> **So the goal has to be restated rather than quietly failed:** a fresh machine reaches a
> working stack from the repo alone **plus one documented credential step**, and that step
> is documented, scripted where possible, and named in the README rather than discovered.
>
> Anything less and the honest description of this project is "reproducible except for the
> primary simulator", which is not what the goal says.

Concretely, Lane C's DoD is:

1. `docker compose --profile lane-c up` brings up UE5.8 + Cosys-AirSim + PX4 + the ROS 2
   graph, with `/fmu/out/*` populated identically to Lane A (`lane-c-topic-parity`).
2. The **credential step is documented in `docker/README.md`** and fails with a clear,
   actionable message rather than a registry 403.
3. Disk is budgeted up front — see `D-04`.

---

## Why this is now urgent, not eventual

Phase 0 Lane A was installed **natively inside the `drone-sim` container**, on the
reasoning that Phase 0 proves components and Phase 1 containerizes them. That reasoning is
now superseded by this goal, and there is a **perishable asset**: the exact, working,
smoke-tested recipe is currently only in `versions.lock` and the worklogs. Every day it
goes uncaptured, the chance of it becoming irreproducible rises — apt archives move,
`latest` tags drift, and the deviations we discovered are exactly the kind of detail that
is expensive to rediscover.

**Three of today's findings are the sort a naive Dockerfile silently gets wrong:**

1. **The XRCE agent's pinned v2.4.2 cannot be built at all** — its Fast-DDS branch is
   deleted upstream, and the 2.12 line does not compile on GCC 13+. Must be **v2.4.3**
   built with `-DUAGENT_USE_SYSTEM_FASTDDS=ON`.
2. **`px4_ros_com` must be branch-matched** to `release/1.16`, which the plan's own setup
   snippet does not do.
3. **PX4 airframe targets are `gz_`-prefixed** — `gz_x500_lidar_2d`, not `x500_lidar_2d`.

A Dockerfile written from the reference docs rather than from today's evidence would
reproduce a **broken** stack.

---

## D-01 — Capture the working Lane A install as a Dockerfile

**Status:** ✅ **`done` (2026-07-29)** — the image is **native-equivalent** on a normal
container runtime. **Blocks:** D-02, D-03 (both now unblocked)

### Closing result — three-way comparison, identical harness and criteria

| Configuration | Aggregate RTF | Topic rate | Sensor TIMEOUTs | Instantaneous dips <0.95 |
|---|---|---|---|---|
| Native (no container runtime) | **1.0000** | 100.02 Hz | 1 in 3 runs | 1 of 8,791 |
| **Host podman** (no nesting) | **0.9967** | **99.74 Hz** | **0** | **0 of 2,930** |
| Nested Docker (this dev box) | 0.9767 | 97.2 Hz | 0 in 5 runs | 655 of 2,907 |

**The containerized stack reproduces native behaviour.** On host podman it runs at 99.67% of
real time — 0.33% off bare metal — with zero sensor TIMEOUTs, zero errors, 24 populated
`/fmu/out` topics and correct publish rates.

**The 2.3% deficit belongs to *this dev box's* nesting** (Docker inside rootless podman on
`fuse-overlayfs`), **not to containerization.** That distinction is the whole point for a
reproducibility goal: ship the image, run it on a normal host, get native performance. The
nested path remains fine for day-to-day work here — 0.9767 clears any sane floor — it is just
not the number to quote for the stack.

Full reasoning and every dead end:
[`../worklog/2026-07-29-d01-container-parity.md`](../worklog/2026-07-29-d01-container-parity.md).

### How to run it on the host (recipe, since each step has a trap)

```bash
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/host/run/user/1000/bus   # HOST bus, not the container's
EXT=/var/mnt/<uuid>/Developments/projects/drone-sim
host-spawn -no-pty /usr/bin/bash -c "/usr/bin/podman \
  --root $EXT/podman-store --runroot /tmp/pmrr \
  run --rm --shm-size=2g -e DURATION=300 -e OUTDIR=/out \
  -v $EXT/host-out:/out:z -v $EXT/smoke.sh:/smoke.sh:ro,z \
  docker.io/drone-sim/lane-a:v1.16.0 bash /smoke.sh"
```

| Trap | Why |
|---|---|
| `/run/host/run/user/1000/bus` | the container's own bus makes `host-spawn` a silent no-op (rc=0, nothing runs) |
| `--runroot /tmp/pmrr` | podman rejects runroot paths >50 chars (Unix socket limit) |
| `--root` on the external drive | **never** use the host's live store `~/.local/share/containers/storage` — it runs this distrobox and concurrent writes can corrupt it |
| `:z` on bind mounts | SELinux on Bazzite; without it `bash: /smoke.sh: Permission denied` (rc=126) |
| load the image **on the host** | nested rootless podman cannot map GID 42 — *"insufficient UIDs or GIDs available in user namespace"* |
| run host-spawn commands **synchronously** | a `nohup … &` child dies when host-spawn returns |

The isolated store already holds the image (12 GB), so no re-transfer is needed.

### What the investigation established (detail in the worklog)

The full story — every dead end, refuted hypothesis and measurement — is in
[`../worklog/2026-07-29-d01-container-parity.md`](../worklog/2026-07-29-d01-container-parity.md).
Only the **actionable** conclusions are kept here:

| Finding | Action it forces |
|---|---|
| **PX4 busy-spins its prompt** when stdin does not block (`pxh.cpp` clears `ICANON` without setting `VMIN`): ~1.45 M writes/s, **4.1 GB per 300 s run**, one CPU core consumed | **Launch under `screen` with `stty min 1 time 0`** — mandatory in every harness and compose service. Upstream defect; worth reporting. |
| Gazebo's **instantaneous** `real_time_factor` swings 0.14–1.01 while the true ratio is 0.977 | **Assert on AGGREGATE RTF** — `sim_time`/`real_time` over the run. Never the instantaneous field, never its minimum. A healthy *native* run has a lone 0.503 sample in 2,931. |
| Docker defaults `/dev/shm` to **64 MB**; Fast-DDS uses shared memory as its default transport | **`shm_size: 2gb`** in the compose service (`D-02`). Invisible in a Dockerfile. |
| tmpfs over PX4's `rootfs` | **Refuted and harmful** — 0/5 runs; it shadows the SITL filesystem and Gazebo never starts. Do not retry. |
| Killed runs leave orphaned `fastrtps_*` segments in `/dev/shm` (37 accumulated in one session) | **Sweep `/dev/shm` in the entrypoint**, so a cancelled CI job cannot poison the next build on that runner. |
| Harness-managed background jobs died at ~25 s; **detached** runs completed | Long regressions: `setsid nohup … &`. Write logs somewhere durable, never a tmpfs that can fill mid-run. |

**Void measurements — do not quote them.** Earlier pass rates of 40% and 60% were taken
while PX4 was spinning a core and writing ~4 GB per run through `fuse-overlayfs`, and were
scored against the noisy instantaneous metric. They measured the instrumentation, not Docker.

**Two Dockerfile bugs worth remembering:** `gz --version` is not a valid invocation (prints
usage, exits non-zero — fails the layer under `-o pipefail`), and the package is
`gz-sim8-cli`, not `gz-sim8`. Both were in verification lines, which is why the Dockerfile
now asserts on artifacts (`test -x`, SHA equality, `ldd | grep -c 'not found'`) rather than
on reaching the end of a script.

## D-02 — `docker compose` for the Lane A graph

**Status:** ✅ **`done` (2026-07-29)** — `docker compose up` reaches the `P0-07` result.

```bash
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml --profile test run --rm verify   # the gate
docker compose -f docker/compose.yaml down -v
```

**Verified:** full 300 s run against the composed stack — 24 `/fmu/out/*` topics, 0 sensor
TIMEOUTs, 0 ERROR lines, aggregate RTF **0.9733**, publish rate 98.05 Hz, clean teardown.
The `verify` service **attaches to the running stack** rather than starting its own PX4, so
it tests the deployment rather than a private copy of it. **Re-verified after the review
fixes below** (2026-07-30): same 0.9733 aggregate RTF, 24 topics, 0 TIMEOUTs, 97.89 Hz.

### Fixed by review before merge

| Finding | Why it mattered |
|---|---|
| **Ports published on `0.0.0.0`** | MAVLink is unauthenticated. On this box the offboard port 14540 was reachable at the LAN address *and* over the netbird overlay — anyone routable could arm and command the vehicle. Now `127.0.0.1` by default, `BIND_ADDR` to opt out. Confirmed at the socket level (`ss -lunt`). |
| `verify` waited only on `px4-sitl` | The gate could start before the bridge was up, see 0 topics and fail spuriously. It passed only because PX4's healthcheck happens to take longer. Now waits on `xrce-agent: service_healthy` too. |
| `xrce-agent` had no supervision | It is the entire PX4↔ROS 2 bridge and **has** crashed here (v2.4.2 segfaulted). A crash stopped every topic while the rest of the stack still reported healthy. Now has a healthcheck and `restart: unless-stopped`. |
| Two `out/` directories | Compose resolved `./out` → `docker/out/` while the README used the repo root. Both existed and diverged. Compose now writes `../out`; `.gitignore` anchors `/out/`. |
| `docker/README.md` said compose was missing | Stale in the same PR that added it. |
| Scope drift | `D-02` was marked done against a five-service definition; only three shipped. The two others are now explicit deferrals — see below. |

### Three bugs found by running it — none visible to `compose config`

| Bug | Symptom | Fix |
|---|---|---|
| Shared netns is not enough for DDS | `ros2 topic list` shows topics, `ros2 topic echo` returns **nothing** — Fast-DDS discovers over UDP but delivers over **shared memory**, and each container has its own `/dev/shm` | `ipc: "service:px4-sitl"` on every joining service |
| IPC donor must opt in | `failed to join IPC namespace: non-shareable IPC` | `ipc: shareable` on `px4-sitl` |
| `docker compose exec` bypasses the ENTRYPOINT | exec shells have no ROS env; `ros2 topic list` reports **0 topics on a healthy stack** — a false negative that looks exactly like a broken deployment | mount `docker/ros-env.sh` into `/etc/profile.d/`, and use `bash -lc` |

**Design constraint worth keeping in mind:** every service shares `px4-sitl`'s network *and*
IPC namespace, because PX4 dials the agent at **127.0.0.1**:8888 and Gazebo transport is
pinned to loopback. A conventional bridge network would break both. Consequence: only
`px4-sitl` may declare `ports:`.

### Delivered vs. defined — the two services deliberately left out

The original definition (below) named five services. **Three shipped**, plus the gate:

| Service | Status |
|---|---|
| `px4-sitl` | ✅ shipped |
| `xrce-agent` | ✅ shipped |
| `ros2-ws` → `ros2` | ✅ shipped (renamed; idles for `exec`, will host Phase 1 nodes) |
| `verify` | ✅ added — not in the original definition; it is the acceptance gate under `--profile test` |
| `qgc` | ✅ **shipped (2026-07-30)** — promoted out of `D-02b` because it turned out to be a **functional dependency of flight**, not a viewer: PX4 will not arm without a GCS datalink, and that check is deliberately left enforced. Runs headless on Xvfb via `docker/qgc.Dockerfile`. `D-02b` still owns making it *viewable*. |
| `recording` | ✅ **shipped (2026-07-30)** via `D-02c` — attaches to the running stack under `--profile record`. One known defect: the QGC pane records black (`D-02b`). |

**Ports:** all four are published (14550, 14540, 8888, 4560) but **bound to `127.0.0.1`**, not
`0.0.0.0` — MAVLink has no authentication, so a LAN-published 14540 lets anyone routable arm
and command the vehicle. Override with `BIND_ADDR=0.0.0.0` when a remote QGC is genuinely
wanted. This file is the template for Phase 4 hardware bring-up, where the same default
would point at a real Pixhawk.

**Volumes:** `/rosbags` (named volume) and `/scenarios` (read-only bind) both wired.

**Original definition:** ~~`todo` · Blocked by: D-01~~

**What.** Compose file with the services the plan names
(`02_development_plan.md:134`): `px4-sitl`, `xrce-agent`, `ros2-ws`, `qgc`, `recording`.
Ports 14550 (QGC), 14540 (offboard), 4560 (Gazebo↔PX4), 8888 (uXRCE-DDS); shared volumes
for `/rosbags`, `/scenarios`.

**Why.** "Easily reproducible" means one command, not a README of steps.

**Acceptance.** `docker compose up` on a clean machine reaches the `P0-07` result. A
second machine, or this one after `docker system prune`, is the real test.

**Trap.** Gazebo transport **must** stay on loopback (`PX4/PX4-Autopilot#24595`). Compose
networking makes it easy to accidentally expose it.

---

## D-02b — Live GUI access (x11vnc + noVNC), not just recordings

**Status:** `todo` · **Blocked by:** D-02

**What.** Expose the container's virtual display over the browser: `x11vnc` on the Xvfb
display plus `noVNC` on an HTTP port, as a compose service.

**Scope narrowed 2026-07-30:** the `qgc` service itself already shipped — it had to, because
PX4 will not arm without a GCS datalink, which made QGC a functional dependency of flight
rather than a viewer. It runs headless on Xvfb today. What is left here is making that
display **reachable and interactive**, i.e. attaching a viewer to the Xvfb that QGC is
already drawing on.

**Why.** Today the GUIs (Gazebo, QGroundControl) render to Xvfb and are only obtainable as
an `.mp4` — you cannot click anything. That is fine for CI evidence and useless for actually
driving a simulation. A browser-attachable display gives an operator the Gazebo view and QGC
without any host X11 setup, and works identically over SSH to `carbonite`, which is how this
box is normally used.

**Acceptance.** Browse to the mapped port, see the Gazebo GUI and QGC live, and interact with
them (orbit the camera, click Takeoff in QGC) while the smoke test's flight runs.

**Traps.**
- QGC **refuses to run as root** — the demo image already carries a `qgcuser` for this.
- **QGC's window will not tile** under openbox (`xdotool windowsize` does not stick after its
  first-run dialog); with a live viewer this matters more, so seed a window state into
  `~/.config` or add openbox per-app geometry rules.
- Xvfb needs `-ac +extension GLX +extension RANDR +render -noreset` or the Gazebo GUI dies
  mid-session.
- Software GL (`llvmpipe`) is the renderer; expect a slow but usable GUI, and do not confuse
  its frame rate with simulator real-time factor.

---

## D-02c — Fold the recording demo into compose as a `recording` service

**Status:** ✅ **`done` (2026-07-30)** — `docker compose --profile record run --rm recording`

**Verified by running it:** attaches to the live stack, builds `control` from the mounted
source, flies the real ROS 2 node (4/4 waypoints, landed, disarmed) and writes a
1920x1080 / 556-frame MP4. Exits non-zero when the flight fails.

**Design:** `qgc` owns the virtual display and the `xsock` volume shares it, so there is
ONE QGroundControl — the datalink — whose window the recorder captures, rather than a
second QGC fighting over PX4's learned remote address. That sharing is also the attachment
point for `D-02b`'s live viewer. The PX4 pane is a `tail -f` of the console log, because a
`screen` session cannot be attached across a container boundary.

**Three bugs it caught, all fixed:**

| Bug | Symptom |
|---|---|
| `-p takeoff_altitude:=10` is parsed as **INTEGER** against a DOUBLE default | `InvalidParameterTypeException`, node dead before it subscribed — and the recording captured an idle stack. Numeric parameters now use `dynamic_typing`. |
| Stale `mission-result.json` from a previous run | Reads `"outcome": "success"` and looks exactly like proof this recording flew. Artifacts are cleared at start. |
| `grep … \| tail \| sed \|\| echo` cannot detect failure | Pipeline exit status is `sed`'s, always 0 — the "no result" branch was unreachable. |

**Fixed 2026-07-30 — the QGC pane recorded black.** Two wrong diagnoses before the right
one, all of which *looked* like success:

| Attempt | Result |
|---|---|
| Window unmapped (`Map State: IsUnMapped`) | Real, but only half of it — `xdotool` moves and resizes unmapped windows happily and reports success. Mapping alone did not fix the black pane. |
| Window not tiling / wrong position | Refuted: `xdotool getwindowgeometry` showed exactly `960,0 960x540`. |
| **External resize kills QGC's painting** | **The actual cause.** Qt Quick's software backend gets no repaint trigger on a headless Xvfb with no compositor, so a resized window stays mapped, viewable, correctly placed — and blank. |

Fix: the `qgc` service seeds QGC's own `[MainWindowState]` so it **starts** at the target
geometry and is never resized; the recorder maps and raises it but no longer moves or sizes
it. Verified in the captured frame — QGC shows *Flying*, altitude, battery and live
telemetry. **Remaining, cosmetic:** QGC's first-run "Measurement Units" dialog overlays the
pane (`D-02b`).

**Original definition:** ~~`todo` · Blocked by: D-02 · Deferred from: D-02~~

**What.** The fifth service from the D-02 definition. The recorder already exists and works
(as `docker/demo/lane-a-record-quad.sh`, now deleted) but started **its own** PX4,
Gazebo and agent. As a compose service it must instead attach to the running stack — the same
change `verify` needed (`SMOKE_ATTACH=1`).

**Why.** Evidence capture should be one flag on the stack you are already running, not a
second stack with its own PX4 that only *resembles* the one under test. It is also what CI
would attach to for artifacts.

**Acceptance.** `docker compose --profile record up recording` writes an `.mp4` of the
**already-running** stack's flight, with no second `bin/px4` process anywhere.

**Traps.**
- The demo currently owns the display, the flight *and* the simulator; only the first two
  survive the split. The flight is now driven by the ROS 2 controller over uXRCE-DDS
  (`lane-a-fly.py` was removed): **only QGroundControl speaks MAVLink over IP.**
- Two recorders (or a recorder plus `verify`) both driving the same vehicle will fight over
  the offboard link. Make the profiles mutually exclusive, or document it.
- QGC's pane needs the same display work as `D-02b`; a recording service without it is just
  three panes.

---

## D-03 — GPU services and the two nested-Docker workarounds

**Status:** `todo` · **Blocked by:** D-02

**What.** GPU-consuming services (`vlm-server`, later perception) with correct device
pinning.

**Scope grew 2026-07-31:** Lane C's `sim` container is now the project's **primary** GPU
consumer — a UE5.8 renderer that must be pinned to **GPU 0 (the 3080)** while `vlm-server`
stays on **GPU 1 (the 5060 Ti)**. That makes the render/infer split a live compose concern
rather than a Phase 3 one, and it is exactly the case where the boundary-level pin below
matters: UE under `-RenderOffScreen` has historically ignored app-level GPU flags. See
`D-04`.

**Why / traps — these are this machine's specific hazards:**
- **`CUDA_VISIBLE_DEVICES=1` alone does not pin the 5060 Ti.** CUDA defaults to
  `FASTEST_FIRST` ordering, so with two dissimilar cards index 1 is not reliably the
  Blackwell card. **Always set `CUDA_DEVICE_ORDER=PCI_BUS_ID` as well**, and verify with
  `nvidia-smi`.
- **`--device nvidia.com/gpu=<n>` pins a GPU at the container boundary** — a more robust
  way to enforce the render/infer split than in-app flags. Verified with Isaac, which then
  enumerated the 3080 as its index 0.
- This project's Docker runs **nested inside rootless podman**; `fuse-overlayfs` and the
  `/etc/cdi-local` CDI spec are load-bearing (`docs/bench.md:123`). Any image that must
  build elsewhere should not depend on those quirks.

---

## D-04 — Lane C (UE5.8 + Cosys-AirSim) containers

**Status:** `todo` · **PROMOTED 2026-07-31 — no longer "eventual"** · **Blocked by:**
`C-02` · **Pairs with:** `D-06`, `D-03`

**Why it moved.** Lane C is the **primary stack** and Phase 2 is built there, so this is no
longer a late nice-to-have — it is on the critical path, and the reproducibility goal is not
met while the primary simulator is unbuildable from the repo.

**What.** Build on `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.0`, digest
`sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46`
(was `dev-slim-5.5.4`, superseded when the engine pin moved to UE5.8 — see
`versions.lock: lane_c.unreal_engine`).

### Three findings that change the shape of this task

**1. The base image is credential-gated.** EpicGames org membership **plus** a PAT with
`read:packages`. Anonymous pulls are HTTP 403. This breaks the area's Lane A DoD wording
and is why that DoD was restated at the top of this file. **Deliverables:** the credential
step documented in `docker/README.md`, a preflight check that fails with a readable message
instead of a registry 403, and the same credential wired into `D-05`'s CI.

> **Useful: inspection needs no `docker login` and no pull.** The `gh` CLI is already
> authenticated here, and its token exchanges for a short-lived read-only ghcr bearer, so
> the manifest and config blob can be read for ~17 KB with nothing written to disk. That is
> how the tag, the layer count, the 24 GB size and the Ubuntu 22.04 label below were all
> confirmed on 2026-07-31 *without* touching the 24 GB. Use this for the preflight check —
> it can verify the pin is reachable before a build commits to the download.

**2. The engine image is Ubuntu 22.04 (jammy), not 24.04.** **ROS 2 Jazzy has no jammy
packages**, so nothing Jazzy can be installed inside it. Lane C is therefore **at least two
containers, mandatorily** — an engine/`sim` container and a 24.04/Jazzy `ros2` container —
with the AirSim↔ROS 2 boundary staying the **RPC (TCP 41451) / MAVLink (TCP 4560)** socket.
This is not a packaging preference; a single-container Lane C is impossible. It also
strengthens `D-06`'s rejection of collapsing everything into one container, with a second
independent instance of the same constraint.

**3. Disk — and this is now the real blocker, not credentials.** Measured 2026-07-31 from
the registry manifest, without pulling:

| | |
|---|---|
| `dev-slim-5.8.0` compressed | **24.0 GB** across 30 layers |
| on disk after extraction | meaningfully more — and that is *before* the UE source build and Cosys-AirSim |
| Docker root dir | **`/var/lib/docker`** — on the **internal NVMe** |
| internal free | 272 GB (already holding 12.9 GB images + 17.4 GB build cache) |
| external free | 5.4 TB |

**The project rule is that large artifacts go on the 7 TB external drive, and Docker's
data-root currently does not.** A 24 GB pull plus a UE source build plus assets against the
constrained volume is exactly the case the rule exists for.

### DECIDED 2026-07-31 — Unreal stays on the internal NVMe

**Docker's `data-root` is not moved. The engine image, the UE source build and the live
working set all stay on the internal NVMe.**

**Why — and this corrects an assumption in the project rule rather than breaking it.**
Checked the hardware before deciding:

| Volume | Device | Type | Free |
|---|---|---|---|
| internal (`/`, holds `/var/lib/docker`) | Samsung 980 PRO 1TB | **NVMe SSD** (`rotational=0`) | 272 GB |
| external (`/var/mnt/<uuid>`) | Seagate `ST10000NE0008` | **7200 RPM SPINNING DISK** (`rotational=1`) | 5.4 TB |

The 7 TB drive is **mechanical**. UE5 shader compilation, asset streaming and Cesium tile
paging are random-I/O-heavy and latency-sensitive; running them off a spinning disk would be
slow in a way that shows up as poor simulator performance, not just a long build.

**The rule** — *"large datasets/rosbags/assets go on the 7 TB external drive"* — was written
for **archival, write-once, read-rarely** data. It was not written to cover a simulator's
**live working set**. The useful distinction, which the rule should be read with:

| Goes on the internal NVMe | Goes on the external HDD |
|---|---|
| Docker images and build cache | rosbags and MCAP archives |
| the UE5 engine image and source build | benchmark datasets (AerialVLN/OpenFly) |
| the live UE project and its working assets | recordings, MP4s, evidence artifacts |
| Cesium tile **cache** (latency-sensitive) | model weights not in active use |

**Budget check:** 24 GB compressed, call it 50–80 GB extracted plus build output, against
272 GB free. Comfortable. Reclaim the **17.4 GB build cache** first (`docker builder prune`)
for headroom, and note Isaac's images were already deleted to recover ~36 GB while Lane B
stays deferred.

**What this does not change:** unbounded, archival data still belongs on the external drive,
still under `/var/mnt/<uuid>/Developments/projects/drone-sim/` — never `~`, and never a
top-level directory on a drive we do not own.

**Watch item:** 272 GB is enough for Lane C today, not forever. If the internal volume drops
below ~100 GB free, revisit — the honest fix at that point is a second NVMe, not moving a
latency-sensitive working set onto a mechanical disk.

### Target topology (from `04_ue5_stack_architecture.md`, reconciled with `D-06`)

| Service | GPU | Base | Role |
|---|---|---|---|
| `sim` | **yes — pin GPU 0 (3080)** | Epic UE5.8 (22.04) | UE5 + Cosys-AirSim + Cesium, `-RenderOffScreen`. AirSim RPC on 41451, MAVLink sim on 4560 |
| `px4` | no | our Lane A image | PX4 SITL in lockstep, driven by `sim`; also runs `uxrce_dds_client` |
| `ros2` | later | our 24.04/Jazzy image | XRCE agent + the AirSim ROS 2 wrapper + our nodes |
| `vlm` | **yes — pin GPU 1 (5060 Ti)** | vLLM | Phase 3 |

**Acceptance.** `docker compose --profile lane-c up` reaches a spawned vehicle that arms,
with `/fmu/out/*` matching Lane A (`lane-c-topic-parity`), from a clone plus the documented
credential step — on this machine first, and stated honestly if it has not been tried
elsewhere.

**Traps.**
- **Headless Vulkan needs `-RenderOffScreen` explicitly**; without it UE silently falls back
  to OpenGL. Set `NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility` and mount the
  Vulkan/EGL ICD JSONs.
- **GPU selection under `-RenderOffScreen` has historically ignored app-level flags and
  defaulted to GPU 0.** Enforce the render/infer split **at the container boundary** with
  `--device nvidia.com/gpu=0`, the way the Isaac probe did — not with an in-app setting.
  See `D-03`.
- **The image sits on an NVIDIA CUDA base**; check its CUDA runtime against this bench's
  driver 610.43.03 at first pull, rather than after a failed build. Same class of mismatch
  that deferred Lane B.
- **Do not run a UE5 shader compile concurrently with other GPU work** — 64 GB will not
  comfortably hold it alongside a heavy sim (`03_hardware_assessment.md:66`).
- Pin the Cosys-AirSim SHA before investing in the build (`C-01`), and pin the
  three-component image tag plus digest — `dev-slim-5.8` is a moving alias.

---

## D-05 — CI builds the images

**Status:** `todo` · **Blocked by:** D-02

**What.** GitHub Actions builds `lane-a.Dockerfile` and runs the seeded SITL regression
inside it.

**Why.** An image that is only ever built by hand drifts. CI is what keeps "reproducible"
true rather than aspirational.

**Acceptance.** A red build when a pin breaks — e.g. re-introducing the agent v2.4.2 pin
must fail, since it is genuinely unbuildable.

**Added 2026-07-31 — CI needs its own Epic credential for Lane C.** Building
`lane-c.Dockerfile` requires a token with EpicGames org membership and `read:packages`
(`D-04`). That is a repository secret plus an org-membership dependency, so CI can build
Lane A from nothing but cannot build Lane C from nothing. Decide deliberately whether CI
builds Lane C at all, or whether Lane C images are built here and published — and write
the choice down rather than letting a red build discover it.

---

## Notes carried in from the native install

- **Do not** trust a build script's own success banner. One agent build printed `BUILD OK`
  while `make` had exited 2; `ldd` showing an unresolved library and a stale binary mtime
  is what caught it. Dockerfiles must assert on artifacts (`RUN … && test -x …`,
  `ldd | grep -c 'not found'`), not on reaching the end of a script.
- ROS 2's `setup.bash` is **not `set -u` clean** — entrypoint scripts must not use `set -u`.

---

## D-06 — Redraw the container boundaries to mirror the Phase 4 machines

**Status:** `todo` · **Raised:** 2026-07-31 · **Do it with:** `P1-04a` (which already forces
Gazebo into its own service) · **Related:** `D-03`

**What.** Regroup the services so each container corresponds to a machine that will actually
exist in Phase 4, and make the links between them swappable for their real transports.

| Phase 4 machine | Should be | Is today |
|---|---|---|
| Pixhawk 6C — PX4 firmware | `px4` | `px4-sitl`, which also runs the Gazebo server |
| — the simulator, no hardware analogue | `gazebo` (needed for `gz sim --seed`, `P1-04a`) | inside `px4-sitl` |
| Jetson Orin NX — companion: **XRCE agent + ROS 2 nodes** | one `companion` service | **split across `xrce-agent` and `ros2`** |
| Ground laptop — QGroundControl | `qgc` | `qgc` ✅ |

**Why.** The current split does **not** buy isolation, and it is worth being honest that it
never did: `px4-sitl`, `xrce-agent` and `ros2` share one network namespace *and* one
`/dev/shm`. That is one machine wearing three hats — they can see each other's loopback and
each other's shared memory, and the only real boundary left is the filesystem.

If the split is not buying isolation, the thing it *should* buy is **sim↔real rehearsal**:
the container boundary standing in for the machine boundary, so an accidental dependency on
co-location fails in sim rather than on the aircraft. Measured against that goal the current
map is drawn wrong in two places:

- **The XRCE agent runs on the companion computer on real hardware**, not on the flight
  controller — PX4 reaches it over a serial link. So the agent and the ROS 2 nodes are the
  *same* machine, and we have split them.
- **PX4 and the agent are different machines**, and we have given them a shared namespace.

**What it has cost so far** — all real, all from this week:

| Symptom | Cause |
|---|---|
| Every container `healthy`, `ros2 topic list` returns **0 topics** | recreating the netns donor alone; joiners left on a dead namespace |
| `shm_size` on three services doing nothing | joiners use the donor's `/dev/shm`; the declaration is inert |
| `non-shareable IPC` on startup | the donor must opt in with `ipc: shareable`, which is not discoverable |

**Explicitly REJECTED: collapsing everything into one container.** It is tempting — the
one-container mode already exists and works (`tests/lane-a-smoke.sh` runs PX4 and the agent
together, and it is the configuration that proved `D-01` parity). But it erases the sim↔real
rehearsal completely, and it cannot generalise: Lane B needs Isaac's Python 3.11 against
ROS 2 Jazzy's 3.12, which is an architectural split, not a packaging preference. Keep the
single-container path for the CI smoke gate; do not make it the deployment shape.

**Strengthened 2026-07-31 by a second, independent instance — and this one is not
hypothetical, because Lane C is the primary stack.** The Epic UE5.8 engine image is
**Ubuntu 22.04 (jammy)** and **ROS 2 Jazzy has no jammy packages**, so the renderer and the
ROS 2 graph *cannot* share a container no matter how the boundaries are drawn. Lane B's
Python split was a deferred lane's problem; this one is the active lane's, and it converts
the multi-container shape from a design preference into a hard constraint. It also fixes
the AirSim↔ROS 2 boundary as a **socket** (RPC 41451 / MAVLink 4560), which is the kind of
real link `D-06` wants standing in for a machine boundary. See `D-04`.

**Acceptance.**
- Containers map 1:1 onto Phase 4 machines, and `docs/lane-a/architecture.html` is redrawn
  to match.
- The PX4↔agent link is **configurable, not co-located** — i.e. the address is a parameter,
  so swapping UDP for a serial link in Phase 4 touches configuration and not the ROS graph.
- The `verify` gate still passes with unchanged numbers: 24 `/fmu/out/*` topics, 0 sensor
  TIMEOUTs, aggregate RTF within noise of 0.9733.
- A flight still succeeds end to end.

**Traps.**
- **PX4 dials `127.0.0.1:8888` today.** Moving the agent out of the shared namespace means
  that address must become a real one — PX4's `uxrce_dds_client` takes host/port parameters,
  so this is configuration, but it is *load-bearing* configuration.
- **Gazebo transport must stay on loopback** (`PX4/PX4-Autopilot#24595`) or the Accel/Mag
  TIMEOUTs come back. Splitting Gazebo out without keeping its transport pinned is how that
  regression returns.
- **Fast-DDS delivers over shared memory.** Any two services that must exchange ROS 2 topics
  at rate still need a shared `/dev/shm`, or they fall back to UDP with different
  performance. Measure the RTF and publish rate after the split rather than assuming.
- **Do not split and re-measure in one step.** Change the topology, re-run the gate, and
  compare against the recorded numbers — this area has produced several
  looks-fine-but-is-broken states already.

---

## D-07 — Automated flight gate (deferred)

**Status:** `todo` · **Deferred 2026-07-31** — running it locally is accepted instead
(`./scripts/run_local_ci.sh --gate`). **Related:** `../lane-a/todo.md` `P1-07`

**What.** Run the 10-seed SITL flight gate automatically, rather than when someone
remembers.

**Why it is not done.** Not effort — two blockers:

- **GitHub-hosted runners cannot do it.** 12.6 GB image against ~14 GB disk, 20–40 min to
  build, and **2 vCPU against an aggregate-RTF floor of 0.95**. The CPU limit is fatal on
  its own: the floor would fail on hardware, and the only fix would be lowering it, which
  removes the assertion that caught this box's nested-Docker deficit.
- **A self-hosted runner on a public repo lets fork PRs execute code on the machine** —
  the one holding SSH keys, the netbird tunnel and the 7 TB drive.

**The way in, when it is worth it.** Trigger on `push` to `main` plus a nightly schedule,
never on `pull_request`. The runner then never executes fork code, and PRs keep tier 1. If
PR gating is wanted later, "require approval for outside collaborators" layers on top
without redoing anything.

**Acceptance.** The gate runs unattended, uploads its MCAPs, and no workflow triggered by a
fork can execute on the runner. Prove the second part, do not assume it.

**Trap.** 19 minutes is fine nightly and painful per-push. Split the triggers deliberately
rather than discovering it through a queue of stacked runs.

**Also unverified:** GitHub's current defaults and setting names for fork-PR approval on
self-hosted runners were not checked against live documentation — confirm them in the
repository's Actions settings before wiring anything, rather than trusting a recollection.
