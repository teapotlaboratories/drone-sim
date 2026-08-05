# Reproducibility — Docker — backlog

**Area:** containerization, reproducible builds, one-command bring-up.
**Indexed from:** [`../todo.md`](../todo.md) — the project backlog.

> **Project goal (added 2026-07-29):** *the whole setup must be easily reproducible as
> Docker.* Not "there are some Dockerfiles" — a fresh machine (or a fresh container on
> this one) must reach a working stack from the repo alone, with no undocumented manual
> steps.

**`D-NN` IDs are cross-cutting and stable.** They are not simulator tasks (`SIM-NN`, in
[`../todo.md`](../todo.md)) and they are not renumbered when the stack underneath them
changes — which it has.

---

## Restated 2026-08-04 — one stack, no compose

The repo builds and runs **one** stack: Unreal Engine 5.8 + Cosys-AirSim + PX4 v1.16 SITL +
ROS 2 Jazzy, brought up by [`../../scripts/sim_up.sh`](../../scripts/sim_up.sh). The Gazebo
baseline and Isaac Sim are retired; their backlogs are in [`../history/`](../history/).

**There is no compose file.** `docker/compose.yaml` described the Gazebo stack and was
deleted with it. The simulator has never used compose: every service joins the renderer's
network and IPC namespaces (which compose could express) *and* the bring-up has to sequence
a settle-then-verify step around PX4's EKF origin (which it cannot, cleanly). One correct
path beats two half-correct ones.

That deletion changes what several tasks below mean. Each is marked with what happened to
it, and none is silently dropped:

| ID | What it was | Disposition |
|---|---|---|
| `D-01` | Capture the working install as a Dockerfile | ✅ **done**, and still live as `docker/px4.Dockerfile` — updated, Gazebo removed |
| `D-02` | `docker compose` for the Gazebo graph | ✅ done 2026-07-29 → **superseded by deletion**; three of its findings carried over to `sim_up.sh` |
| `D-02b` | Live GUI access (x11vnc + noVNC) | **narrowed** — the Gazebo GUI it was half about no longer exists; QGC remains |
| `D-02c` | Recording as a compose service | ✅ done 2026-07-30 → **superseded by deletion**; replaced by `scripts/record_flight.py` |
| `D-03` | GPU services and device pinning | **partly done** — the renderer is pinned at the container boundary; the second consumer is not built |
| `D-04` | The Unreal engine container | **partly done** — it is built and it flies *here*; the reproducibility deliverables are outstanding. **The live constraint on the whole goal** |
| `D-05` | CI builds the images | `todo` — restated against the current Dockerfiles |
| `D-06` | Redraw container boundaries onto real machines | `todo` — the criticism got sharper, not weaker |
| `D-07` | Automated flight gate | `todo`, deferred — one of its two blockers changed |
| `D-08` | The runtime substrate is not captured | `todo` — the target is native Docker, not this box |

### Definition of done, for the one stack there is

On a machine with **native Docker and an NVIDIA driver**, a clone of this repo plus **one
documented credential step** reaches a flying stack:

1. the six images build from the repo (`docker/*.Dockerfile`);
2. `./scripts/sim_up.sh` prints `stack up and origin verified -- safe to fly`;
3. `./scripts/run_gate.py scenarios/square-10m.yaml` passes.

**Not met.** Two named gaps: the credential gate (`D-04`), and the fact that nothing here
has ever been built or run on a machine other than this one — which is a distrobox, not the
target substrate (`D-08`).

> ### The reproducibility goal has a hole in it, and it is worth stating plainly
>
> **The original wording was "a machine that has only Docker + an NVIDIA driver". The
> simulator cannot meet that as written.** Its engine base image,
> `ghcr.io/epicgames/unreal-engine`, is **credential-gated**: anonymous reads return HTTP
> 403, and pulling needs EpicGames GitHub **org membership plus a PAT with
> `read:packages`**. A clone of this repo plus a Dockerfile is **not sufficient**, and no
> amount of pinning fixes that.
>
> This is a genuine, permanent constraint from upstream licensing — not a gap to close.
> **So the goal has to be restated rather than quietly failed:** a fresh machine reaches a
> working stack from the repo alone **plus one documented credential step**, and that step
> is documented, scripted where possible, and named in the README rather than discovered.
>
> Anything less and the honest description of this project is "reproducible except for the
> simulator", which is not what the goal says.

---

## Why this was urgent, and what the urgency taught

The original Phase 0 install was done **natively inside the `drone-sim` container**, on the
reasoning that you prove components first and containerize them later. The reproducibility
goal superseded that reasoning, and the asset was **perishable**: the exact, working,
smoke-tested recipe lived only in `versions.lock` and the worklogs. Apt archives move,
`latest` tags drift, and the deviations discovered along the way are exactly the kind of
detail that is expensive to rediscover.

**Three findings from that day are the sort a naive Dockerfile silently gets wrong:**

1. **The XRCE agent's pinned v2.4.2 cannot be built at all** — its Fast-DDS branch is
   deleted upstream, and the 2.12 line does not compile on GCC 13+. Must be **v2.4.3**
   built with `-DUAGENT_USE_SYSTEM_FASTDDS=ON`. **Still live** — the agent is
   `sim-xrce` today.
2. **`px4_ros_com` must be branch-matched** to `release/1.16`, which the reference setup
   snippet does not do. **Still live.**
3. **PX4 airframe targets are `gz_`-prefixed** — `gz_x500_lidar_2d`, not `x500_lidar_2d`.
   **Retired with Gazebo**; the simulator uses airframe 10016 and the PX4 image no longer
   contains `gz` at all. Kept because it is the cleanest single example of the rule: a
   Dockerfile written from the reference docs rather than from evidence reproduces a
   **broken** stack.

---

## D-01 — Capture the working install as a Dockerfile

**Status:** ✅ **`done` (2026-07-29)** — the image is **native-equivalent** on a normal
container runtime. Now `docker/px4.Dockerfile`, and still the base of `drone-sim/ros2`,
`drone-sim/qgc` and `drone-sim/video`.

### Updated 2026-08-04 — the image no longer contains Gazebo

`Tools/setup/ubuntu.sh --no-sim-tools`, plus an explicit reinstall of the build dependencies
that flag drops which are *not* Gazebo (`bc`, `libeigen3-dev`, `protobuf-compiler`,
`pkg-config`, `libxml2-utils`). **MEASURED: 11.6 GB with Gazebo, 11.0 GB without**, and the
build then **asserts `gz` is absent** rather than trusting the flag — because a dependency
chain can pull Gazebo back in without anyone noticing, and an image that quietly regrows a
retired simulator is exactly what this file exists to prevent.

**NuttX is still installed on purpose.** Real Pixhawk 6C firmware is flashed from that same
tree, so dropping it would slim the image while silently removing a capability.

### Closing result — three-way comparison, identical harness and criteria

**Read this as evidence about *containerization*, not about today's stack.** It was measured
against PX4 + Gazebo SITL, which is retired. The conclusion it supports — a container costs
nothing on a normal runtime, and this box's nesting costs ~2.3% — is what carried forward.

| Configuration | Aggregate RTF | Topic rate | Sensor TIMEOUTs | Instantaneous dips <0.95 |
|---|---|---|---|---|
| Native (no container runtime) | **1.0000** | 100.02 Hz | 1 in 3 runs | 1 of 8,791 |
| **Host podman** (no nesting) | **0.9967** | **99.74 Hz** | **0** | **0 of 2,930** |
| Nested Docker (this dev box) | 0.9767 | 97.2 Hz | 0 in 5 runs | 655 of 2,907 |

**The containerized stack reproduced native behaviour.** On host podman it ran at 99.67% of
real time — 0.33% off bare metal — with zero sensor TIMEOUTs, zero errors, 24 populated
`/fmu/out` topics and correct publish rates.

**The 2.3% deficit belongs to *this dev box's* nesting** (Docker inside rootless podman on
`fuse-overlayfs`), **not to containerization.** That distinction is the whole point for a
reproducibility goal: ship the image, run it on a normal host, get native performance. The
nested path remains fine for day-to-day work here — it is just not the number to quote for
the stack.

**There is no equivalent number for the current simulator, and there cannot be one of this
kind.** Lockstep is dead code in Cosys-AirSim, so its timing is free-running and a
real-time factor from it is not a determinism claim (`../conventions.md` §4). The simulator
is gated on **success rate over seeded runs** instead (`scripts/run_gate.py`).

Full reasoning and every dead end:
[`../worklog/2026-07-29-d01-container-parity.md`](../worklog/2026-07-29-d01-container-parity.md).

### How to run an image on the host (recipe, since each step has a trap)

The script this was demonstrated with — the Gazebo acceptance gate — was deleted with that
stack. **The traps are the durable part** and apply to any host-side podman run.

```bash
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/host/run/user/1000/bus   # HOST bus, not the container's
EXT=/var/mnt/<uuid>/Developments/projects/drone-sim
host-spawn -no-pty /usr/bin/bash -c "/usr/bin/podman \
  --root $EXT/podman-store --runroot /tmp/pmrr \
  run --rm --shm-size=2g -e DURATION=300 -e OUTDIR=/out \
  -v $EXT/host-out:/out:z -v $EXT/probe.sh:/probe.sh:ro,z \
  docker.io/drone-sim/px4:v1.16.0 bash /probe.sh"
```

| Trap | Why |
|---|---|
| `/run/host/run/user/1000/bus` | the container's own bus makes `host-spawn` a silent no-op (rc=0, nothing runs) |
| `--runroot /tmp/pmrr` | podman rejects runroot paths >50 chars (Unix socket limit) |
| `--root` on the external drive | **never** use the host's live store `~/.local/share/containers/storage` — it runs this distrobox and concurrent writes can corrupt it |
| `:z` on bind mounts | SELinux on Bazzite; without it `bash: /probe.sh: Permission denied` (rc=126) |
| load the image **on the host** | nested rootless podman cannot map GID 42 — *"insufficient UIDs or GIDs available in user namespace"* |
| run host-spawn commands **synchronously** | a `nohup … &` child dies when host-spawn returns |

### What the investigation established (detail in the worklog)

Only the **actionable** conclusions are kept here:

| Finding | Action it forces | Still live? |
|---|---|---|
| **PX4 busy-spins its prompt** when stdin does not block (`pxh.cpp` clears `ICANON` without setting `VMIN`): ~1.45 M writes/s, **4.1 GB per 300 s run**, one CPU core consumed | **Launch under `screen` with `stty min 1 time 0`** — mandatory in every harness. Upstream defect; worth reporting. | **yes** — `sim-px4` still runs PX4 this way |
| Docker defaults `/dev/shm` to **64 MB**; Fast-DDS uses shared memory as its default transport | **`--shm-size=2g`** on the container that donates the IPC namespace. Invisible in a Dockerfile. | **yes** — on `sim-unreal` |
| Killed runs leave orphaned `fastrtps_*` segments in `/dev/shm` (37 accumulated in one session) | **Sweep `/dev/shm` at start**, so a cancelled run cannot poison the next one on that machine. | **yes** |
| Harness-managed background jobs died at ~25 s; **detached** runs completed | Long regressions: `setsid nohup … &`. Write logs somewhere durable, never a tmpfs that can fill mid-run. | **yes** |
| Gazebo's **instantaneous** `real_time_factor` swings 0.14–1.01 while the true ratio is 0.977 | **Assert on AGGREGATE RTF**, never the instantaneous field or its minimum. A healthy *native* run has a lone 0.503 sample in 2,931. | retired with Gazebo — but the shape of the mistake is not: **do not assert on a noisy instantaneous metric** |
| tmpfs over PX4's `rootfs` | **Refuted and harmful** — 0/5 runs; it shadowed the SITL filesystem and Gazebo never started. Do not retry. | historical |

**Void measurements — do not quote them.** Earlier pass rates of 40% and 60% were taken
while PX4 was spinning a core and writing ~4 GB per run through `fuse-overlayfs`, and were
scored against the noisy instantaneous metric. They measured the instrumentation, not Docker.

**Two Dockerfile bugs worth remembering:** `gz --version` is not a valid invocation (prints
usage, exits non-zero — fails the layer under `-o pipefail`), and the package was
`gz-sim8-cli`, not `gz-sim8`. Both were in *verification* lines. Neither can recur — Gazebo
is gone — but the lesson they forced is now the house style: **assert on artifacts**
(`test -x`, SHA equality, `ldd | grep -c 'not found'`, `! command -v gz`), never on reaching
the end of a script.

---

## D-02 — `docker compose` for the ROS 2 graph

**Status:** ✅ done 2026-07-29 → **SUPERSEDED BY DELETION (2026-08-04).** `docker/compose.yaml`
described the Gazebo stack and went with it. The simulator's bring-up is
`scripts/sim_up.sh`, raw `docker run`, and it never went through compose.

**Marked superseded rather than deleted because the findings outlived the file.** Three of
them are load-bearing in `sim_up.sh` today, and two are security decisions that must not be
re-litigated by accident.

### What carried over into `sim_up.sh`

| Finding, as originally recorded | Where it lives now |
|---|---|
| **Shared netns is not enough for DDS** — `ros2 topic list` shows topics, `ros2 topic echo` returns **nothing**, because Fast-DDS discovers over UDP but delivers over **shared memory**, and each container has its own `/dev/shm` | `--ipc container:sim-unreal` on every joiner |
| **The IPC donor must opt in** — otherwise `failed to join IPC namespace: non-shareable IPC` | `--ipc shareable` on `sim-unreal` |
| **`exec` bypasses the ENTRYPOINT** — exec shells have no ROS env, so `ros2 topic list` reports **0 topics on a healthy stack**, a false negative that looks exactly like a broken deployment | `docker/ros-profile.sh` is now **baked into `drone-sim/ros2`** at `/etc/profile.d/10-ros.sh` (it used to be bind-mounted), and callers use `bash -lc` |
| **Ports published on `0.0.0.0` are a real hazard** — MAVLink is unauthenticated; on this box offboard port 14540 was reachable at the LAN address *and* over the netbird overlay, so anyone routable could arm and command the vehicle. Confirmed at the socket level (`ss -lunt`) | **No ports are published at all.** Every service joins the renderer's netns, so there is nothing bound on the host; reach the stack with `docker exec`. If a port is ever published, this is why it must be `127.0.0.1` by default |
| **The agent needs supervision** — it is the entire PX4↔ROS 2 bridge and **has** crashed here (v2.4.2 segfaulted); a crash stopped every topic while everything else still reported healthy | **Regressed, deliberately noted:** `sim-xrce` has no healthcheck and no restart policy. `sim_up.sh` verifies the stack once at bring-up and `run_gate.py` VOIDs a run whose origin cannot be read, but nothing watches the agent mid-flight. **Open** — the cheapest version is a liveness check in the gate |

### What did not carry over

- The **`verify` service** (`--profile test`) that attached to the running stack rather than
  starting its own PX4. Its successor is `scripts/run_gate.py`, which does the same thing —
  test the deployment, not a private copy of it.
- The **`recording` service** — see `D-02c`.
- The measured result it was accepted on: a full 300 s run against the composed stack, 24
  `/fmu/out/*` topics, 0 sensor TIMEOUTs, 0 ERROR lines, aggregate RTF **0.9733**, publish
  rate 98.05 Hz, clean teardown; re-verified after review at 0.9733 / 24 topics / 0 TIMEOUTs
  / 97.89 Hz. **That is a Gazebo number and does not describe anything that runs today.**
- The **design constraint that only one service may declare `ports:`** — inherited by
  `sim_up.sh` in a stronger form, since none of them do.

**One structural note that survived intact:** every service sharing one network *and* one
IPC namespace is not isolation, it is one machine wearing five hats. That is `D-06`'s whole
argument, and the renderer donating the namespace makes it sharper than it was.

---

## D-02b — Live GUI access (x11vnc + noVNC), not just recordings

**Status:** `todo` · **Narrowed twice**

**What.** Expose the container's virtual display over the browser: `x11vnc` on the Xvfb
display plus `noVNC` on an HTTP port.

**Scope narrowed 2026-07-30:** the QGC service itself shipped — it had to, because PX4 will
not arm without a GCS datalink, which made QGC a functional dependency of flight rather than
a viewer. It runs headless on Xvfb. What was left was making that display **reachable and
interactive**.

**Narrowed again 2026-08-04:** half of the original justification was the **Gazebo GUI**,
which no longer exists. The remaining target is **QGC only**. The renderer has no window to
attach to — it runs `-RenderOffScreen` by design — and streaming its viewport live is a
different problem (`SIM-17`, Pixel Streaming), currently **blocked** on the driver:
[`../nvenc-driver-blocker.md`](../nvenc-driver-blocker.md).

**Why.** QGC renders to Xvfb and is only obtainable as a captured frame — you cannot click
anything. A browser-attachable display gives an operator QGC without any host X11 setup, and
works identically over SSH to `carbonite`, which is how this box is normally used.

**Acceptance.** Browse to the mapped port, see QGC live, and interact with it (click Takeoff)
while a flight runs.

**Traps.**
- QGC **refuses to run as root** — `drone-sim/qgc` already carries a `qgcuser` for this.
- **QGC's window will not tile** under openbox (`xdotool windowsize` does not stick after its
  first-run dialog); with a live viewer this matters more, so seed a window state into
  `~/.config` or add openbox per-app geometry rules. See `D-02c` for why resizing it at all
  is dangerous.
- Xvfb needs `-ac +extension GLX +extension RANDR +render -noreset`, or a GUI client dies
  mid-session.
- Software GL (`llvmpipe`) is the renderer for QGC; expect a slow but usable GUI, and do not
  confuse its frame rate with anything the simulator is doing.

---

## D-02c — Recording as a first-class part of the stack

**Status:** ✅ done 2026-07-30 as a compose service → **SUPERSEDED BY DELETION
(2026-08-04).** `docker/demo/` and the `recording` profile are gone. The simulator's
equivalent is `scripts/record_flight.py` plus `drone-sim/video:v1.16.0` — a thin ffmpeg
layer on the PX4 base, which no longer carries Xvfb, xterm, xdotool or openbox because those
existed to drive the four-pane Gazebo GUI capture.

**Kept in full because it caught four defects, and every one of them is a way of producing
convincing evidence of a flight that never happened.**

**Three bugs it caught, all fixed:**

| Bug | Symptom |
|---|---|
| `-p takeoff_altitude:=10` is parsed as **INTEGER** against a DOUBLE default | `InvalidParameterTypeException`, node dead before it subscribed — and the recording captured an idle stack. Numeric parameters now use `dynamic_typing`. |
| Stale `mission-result.json` from a previous run | Reads `"outcome": "success"` and looks exactly like proof this recording flew. Artifacts are cleared at start. |
| `grep … \| tail \| sed \|\| echo` cannot detect failure | Pipeline exit status is `sed`'s, always 0 — the "no result" branch was unreachable. |

**The fourth: the QGC pane recorded black.** Two wrong diagnoses before the right one, all of
which *looked* like success:

| Attempt | Result |
|---|---|
| Window unmapped (`Map State: IsUnMapped`) | Real, but only half of it — `xdotool` moves and resizes unmapped windows happily and reports success. Mapping alone did not fix the black pane. |
| Window not tiling / wrong position | Refuted: `xdotool getwindowgeometry` showed exactly `960,0 960x540`. |
| **External resize kills QGC's painting** | **The actual cause.** Qt Quick's software backend gets no repaint trigger on a headless Xvfb with no compositor, so a resized window stays mapped, viewable, correctly placed — and blank. |

Fix: the QGC image seeds QGC's own `[MainWindowState]` so it **starts** at the target
geometry and is never resized; the recorder maps and raises it but no longer moves or sizes
it. **That seeding is still in `docker/qgc.Dockerfile` and must not be removed as
dead-looking config.** Remaining and cosmetic: QGC's first-run "Measurement Units" dialog
overlays the window (`D-02b`).

**The design principle that carried over.** Recording **attaches to the running stack**; it
never starts a second simulator. Evidence capture should be one command against the stack
you are already testing, not a second stack that only *resembles* it. And it **exits
non-zero if no successful flight happened** — an early version cheerfully produced a
convincing 1080p video of a simulator that never armed, and reported success.

**Trap that still applies:** two things driving the same vehicle will fight over the offboard
link. A recording and a gate run are mutually exclusive.

---

## D-03 — GPU pinning, and the nested-Docker workarounds

**Status:** **partly done** · **Related:** `D-04`, `D-08`

**Done: the renderer is pinned at the container boundary.** `sim_up.sh` starts `sim-unreal`
with `--gpus '"device=nvidia.com/gpu=0"'`, which is the RTX 3080. That is the right layer —
see the traps below.

**Not done:** a second GPU consumer. There is no model-serving container in this repo, and
the render/infer split is only half-exercised until something actually runs on GPU 1.

**Why / traps — these are this machine's specific hazards:**
- **`CUDA_VISIBLE_DEVICES=1` alone does not pin the 5060 Ti.** CUDA defaults to
  `FASTEST_FIRST` ordering, so with two dissimilar cards index 1 is not reliably the
  Blackwell card. **Always set `CUDA_DEVICE_ORDER=PCI_BUS_ID` as well**, and verify with
  `nvidia-smi`.
- **`CUDA_VISIBLE_DEVICES` does not control a Vulkan renderer at all.** It is a CUDA
  variable; Unreal's RHI never reads it.
- **`--device nvidia.com/gpu=<n>` pins a GPU at the container boundary** — a more robust way
  to enforce the split than in-app flags. **UE under `-RenderOffScreen` has historically
  ignored app-level GPU flags and taken GPU 0 regardless**, which happens to be the card we
  want and is therefore a trap: it works until the day the split is inverted.
- This project's Docker runs **nested inside rootless podman**; `fuse-overlayfs` and the
  `/etc/cdi-local` CDI spec are load-bearing here (`../bench.md` §4). **Re-scope pending:**
  `D-08` decided native Docker is the target, so part of this task dissolves rather than
  needing a solution — but the CDI *device reference* (`nvidia.com/gpu=0`) still has to be
  confirmed on a native host, or fall back to `--gpus all`.

---

## D-04 — The Unreal engine container, and the credential gate

**Status:** **partly done** · **Pairs with:** `D-06`, `D-03`, `D-08`

**Built and flying here.** `docker/unreal.Dockerfile` produces `drone-sim/unreal:ue5.8` from
`ghcr.io/epicgames/unreal-engine` pinned **by digest**
(`sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46`, tag
`dev-slim-5.8.0`), and `sim_up.sh` flies against it.

**Outstanding — and this is what keeps the area's goal unmet:**

1. the credential step **documented** in `docker/README.md` (done) and enforced by a
   **preflight check that fails with a readable message rather than a registry 403** (not
   done);
2. the same credential wired into CI (`D-05`);
3. **it has never been built anywhere but this machine.** Nothing below has been tested from
   a clone on a second host, and that is the actual claim the goal makes.

### Three findings that shaped this task

**1. The base image is credential-gated.** EpicGames org membership **plus** a PAT with
`read:packages`. Anonymous pulls are HTTP 403. This is why the area's definition of done was
restated at the top of this file.

> **Useful: inspection needs no `docker login` and no pull.** The `gh` CLI is already
> authenticated here, and its token exchanges for a short-lived read-only ghcr bearer, so
> the manifest and config blob can be read for ~17 KB with nothing written to disk. That is
> how the tag, the layer count, the 24 GB compressed size and the Ubuntu 22.04 label were
> confirmed on 2026-07-31 *without* touching the 24 GB. **Use this for the preflight check**
> — it can verify the pin is reachable before a build commits to the download.

**2. The engine image is Ubuntu 22.04 (jammy), not 24.04.** **ROS 2 Jazzy has no jammy
packages**, so nothing Jazzy can be installed inside it. The stack is therefore **at least
two containers, mandatorily** — a renderer container and a 24.04/Jazzy ROS 2 container —
with the AirSim↔ROS 2 boundary staying the **RPC (TCP 41451) / MAVLink (TCP 4560)** socket.
This is not a packaging preference; a single-container simulator is impossible. It is also
the strongest argument in `D-06` against collapsing everything into one container.

**3. Disk.** Measured 2026-07-31 from the registry manifest, without pulling; the on-disk
figures were confirmed after the build:

| | |
|---|---|
| `dev-slim-5.8.0` compressed | **24.0 GB** across 30 layers |
| the engine image on disk | **57.4 GB** (measured 2026-08-04) |
| `drone-sim/unreal:ue5.8` on disk | **57.5 GB** — i.e. the plugin build adds ~0.1 GB on top |
| Docker root dir | **`/var/lib/docker`** — on the **internal NVMe** |
| internal free, 2026-07-31 | 272 GB |
| internal free, 2026-08-04 | **196 GB** |
| external free | 5.4 TB |

### DECIDED 2026-07-31 — Unreal stays on the internal NVMe

**Docker's `data-root` is not moved. The engine image, the UE build and the live working set
all stay on the internal NVMe.**

**Why — and this corrects an assumption in the project rule rather than breaking it.**
Checked the hardware before deciding:

| Volume | Device | Type | Free |
|---|---|---|---|
| internal (`/`, holds `/var/lib/docker`) | Samsung 980 PRO 1TB | **NVMe SSD** (`rotational=0`) | 272 GB |
| external (`/var/mnt/<uuid>`) | Seagate `ST10000NE0008` | **7200 RPM SPINNING DISK** (`rotational=1`) | 5.4 TB |

The 7 TB drive is **mechanical**. UE5 shader compilation, asset streaming and tile paging are
random-I/O-heavy and latency-sensitive; running them off a spinning disk would be slow in a
way that shows up as poor simulator performance, not just a long build.

**The rule** — *"large datasets/rosbags/assets go on the 7 TB external drive"* — was written
for **archival, write-once, read-rarely** data. It was not written to cover a simulator's
**live working set**. The useful distinction, which the rule should be read with:

| Goes on the internal NVMe | Goes on the external HDD |
|---|---|
| Docker images and build cache | rosbags and MCAP archives |
| the engine image and the plugin build | benchmark datasets |
| the live UE project and its working assets | recordings, MP4s, evidence artifacts |
| the derived-data cache (`sim-ddc`, latency-sensitive) | model weights not in active use |

**What this does not change:** unbounded, archival data still belongs on the external drive,
still under `/var/mnt/<uuid>/Developments/projects/drone-sim/` — never `~`, and never a
top-level directory on a drive we do not own.

**Watch item, and it moved.** The 2026-07-31 note said "revisit if the internal volume drops
below ~100 GB free". It was 272 GB then and is **196 GB** now — 76 GB consumed in four days,
most of it the engine image. The honest fix at the threshold is a second NVMe, not moving a
latency-sensitive working set onto a mechanical disk. **Reclaim the retired images first**:
the pre-rename image tags and the Gazebo-era layers are both still resident, and
`docker builder prune` has never been run since the engine build.

### The topology as built

| Container | GPU | Base | Role |
|---|---|---|---|
| `sim-unreal` | **yes — GPU 0 (3080)** | Epic UE5.8 (22.04) | UE5 + Cosys-AirSim, `-RenderOffScreen`. AirSim RPC on 41451, MAVLink sim on 4560. **Donates netns + IPC + `/dev/shm`** |
| `sim-px4` | no | `drone-sim/px4:v1.16.0` | PX4 SITL, airframe 10016, driven by the renderer; also runs `uxrce_dds_client` |
| `sim-xrce` | no | `drone-sim/px4:v1.16.0` | the uXRCE-DDS agent on UDP 8888 |
| `sim-qgc` | no | `drone-sim/qgc:v1.16.0` | the GCS datalink PX4 requires before it will arm |
| `sim-ros2` | later | `drone-sim/ros2:v1.16.0` | the AirSim ROS 2 wrapper + our nodes |

**Acceptance.** `./scripts/sim_up.sh` reaches a spawned vehicle that arms and flies, with
`/fmu/out/*` populated, **from a clone plus the documented credential step** — on this
machine first, and **stated honestly that it has not been tried elsewhere.**

**Traps.**
- **Headless Vulkan needs `-RenderOffScreen` explicitly**; without it UE silently falls back
  to OpenGL. Set `NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility` and mount the
  Vulkan/EGL ICD JSONs.
- **GPU selection under `-RenderOffScreen` has historically ignored app-level flags.**
  Enforce the split **at the container boundary** — see `D-03`.
- **The Vulkan ICD symlink in `docker/unreal.Dockerfile` is a `carbonite`-only workaround**
  and must be labelled as one — `D-08`.
- **The image sits on an NVIDIA CUDA base**; check its CUDA runtime against this bench's
  driver 610.43.03 at first pull rather than after a failed build. That class of mismatch is
  what cost this project Isaac Sim.
- **Do not run a UE5 shader compile concurrently with other GPU work** — 64 GB will not
  comfortably hold it alongside a heavy sim
  (`../history/reference/03_hardware_assessment.md:86`).
- Pin **by digest**, not by tag: `dev-slim-5.8` is a moving alias, exactly as a git branch is
  not a pin.

---

## D-05 — CI builds the images

**Status:** `todo`

**What.** GitHub Actions builds `docker/px4.Dockerfile` and runs a flight check inside it.

**Why.** An image that is only ever built by hand drifts. CI is what keeps "reproducible"
true rather than aspirational.

**Acceptance.** A red build when a pin breaks — e.g. re-introducing the agent v2.4.2 pin must
fail, since it is genuinely unbuildable.

**What tier-1 CI does today, and what it deliberately does not.** A new check,
`scripts/check_image_refs.py`, asserts that every `drone-sim/...` reference anywhere in the
tracked tree names an image declared under `images:` in `versions.lock`. It replaced the
`docker compose config` step that went with the compose file, and it catches the same *class*
of defect — a reference to something that does not exist — which did not go away when the
file did. **It does not build anything**, by design: it has to pass on a runner with no
images and no daemon.

**CI needs its own Epic credential (added 2026-07-31).** Building the renderer image requires
a token with EpicGames org membership and `read:packages` (`D-04`) — a repository secret plus
an org-membership dependency. So CI can build the PX4-side images from nothing but cannot
build the renderer from nothing. **Decide deliberately** whether CI builds it at all, or
whether it is built here and published, and write the choice down rather than letting a red
build discover it.

---

## D-06 — Redraw the container boundaries to mirror the real machines

**Status:** `todo` · **Raised:** 2026-07-31 · **Sharpened:** 2026-08-04 · **Related:** `D-03`

**What.** Regroup the containers so each corresponds to a machine that will actually exist
when this flies for real, and make the links between them swappable for their real
transports.

| Real machine | Should be | Is today |
|---|---|---|
| Pixhawk 6C — PX4 firmware | `sim-px4` | `sim-px4` ✅ |
| — the renderer, **no hardware analogue at all** | a container nothing else depends on | **the namespace donor the whole stack joins** |
| Jetson Orin NX — companion: **XRCE agent + ROS 2 nodes** | one `companion` container | **split across `sim-xrce` and `sim-ros2`** |
| Ground laptop — QGroundControl | `sim-qgc` | `sim-qgc` ✅ |

### The companion row is DONE — 2026-08-04. Agent merged into `sim-ros2`; 5 containers -> 4

Prompted by "can we merge the agent, PX4 and ROS 2 — in the real world that is one machine?"
**PX4 is not on that machine**: `versions.lock` `hardware:` puts PX4 on the Pixhawk 6C and the
agent + ROS 2 nodes on the Jetson Orin NX, joined by a UART. So the companion row is
agent + ROS 2, PX4 stays out — and that is what shipped.

**The honest case for it is simplicity, not fidelity, and the distinction is worth keeping.**
The agent is *plumbing*: it makes `/fmu/*` appear, nothing connects to it directly, and on the
Jetson it is a process beside the ROS 2 nodes rather than a machine. Its own container modelled
a boundary that exists nowhere in the real system. What the merge does **not** buy:

- **It moves zero packets.** `sim-xrce` and `sim-ros2` already shared one netns and one
  `/dev/shm` (`sim_up.sh:153-157`, open-coded again at the ros2 `docker run`). On the real
  Jetson, agent↔nodes is also loopback + shared memory. The link the merge touched was already
  faithful.
- **The unfaithful link is untouched.** Real hardware runs `uxrce_dds_client -t serial -d
  ${SERIAL_DEV}` (`module.yaml:1-14`); SITL hardcodes `-t udp -h 127.0.0.1` at
  `ROMFS/…/init.d-posix/rcS:317`. `PX4_UXRCE_DDS_PORT` and `_NS` are env-overridable; **the
  HOST is not.** This doc's trap note used to say "the address is a parameter, so this is
  configuration" — that was optimistic, and it is corrected above. Making it real needs a
  custom `-s` startup script or a vendor patch under the least-destructive rule.
- **The image boundary is leakier than the container boundary ever was.** `drone-sim/ros2` is
  `FROM drone-sim/px4`, so the "Jetson" container already ships the whole PX4 SITL build
  (~90 `px4-*` modules) and is amd64 where the Jetson is arm64.

**What it DID buy, and this is the part that justified doing it:** supervision the stack never
had. Three shapes were measured; two are silently wrong.

| Shape | What a crash does |
|---|---|
| `agent & … exec sleep infinity` | `exec` replaces the parent, nothing reaps — the dead agent becomes a **zombie** that `pgrep -f` still matches. Green healthcheck, dead bridge. `--init` does not fix it |
| `exec MicroXRCEAgent` as PID 1 | takes the workspace and any in-flight `docker exec` with it, **destroying the MCAP** |
| **supervising subshell** (shipped) | stays the parent, so it reaps and restarts |

Verified by killing it: agent SIGKILLed at pid 55, back at pid 1546 within ~2 s, container still
`Running`, **0 zombies**, `[xrce] agent exited … restarting` in `docker logs`. Before this, no
container in the stack had a restart policy at all — a regression from the retired compose stack
that this row closes for the agent.

**Two traps found while building it, both now in `docker/README.md`:**

1. **`MicroXRCEAgent` exits 0 on a bind failure**, so "it started and returned success" is
   compatible with no bridge at all.
2. **`pgrep -f MicroXRCEAgent` matches the supervising loop**, whose own command line contains
   the string — it reports a hit whether or not the agent is alive. Measured: `pgrep` returned
   the loop's pid, not the agent's.
   **So never health-check the agent by its process.** `wait_for_fmu` asserts the data — real
   `/fmu/out` topics with a finite EKF origin — which is what actually proves a bridge.
3. Diagnostic bug caught in review of the shipped loop: `$?` after `agent | sed` is **sed's**
   status, so a SIGKILLed agent logged `rc=0`. Uses `${PIPESTATUS[0]}` now; demonstrated —
   `false | sed` gives `$?=0` but `PIPESTATUS[0]=1`, and a SIGKILL gives `137`.

`sim-xrce` remains in both teardown lists (`sim_up.sh`, `run_park_tour.sh`) although nothing
creates it: a stale container from an older checkout would hold `udp/8888`, and per trap 1 the
new agent would exit 0 rather than complain.

**Still open on this task — the harder two rows.** The renderer is still the namespace donor the
whole stack joins, which this doc calls the worst of the three boundaries and which the
companion merge does not touch. Do not read this row's completion as `D-06` being done.

**Why.** The current split does **not** buy isolation, and it never did: every container
shares one network namespace *and* one `/dev/shm`. That is one machine wearing five hats —
they can see each other's loopback and each other's shared memory, and the only real boundary
left is the filesystem.

If the split is not buying isolation, the thing it *should* buy is **sim↔real rehearsal**:
the container boundary standing in for the machine boundary, so an accidental dependency on
co-location fails in sim rather than on the aircraft. Measured against that goal the map is
drawn wrong in three places:

- **The XRCE agent runs on the companion computer on real hardware**, not on the flight
  controller — PX4 reaches it over a serial link. So the agent and the ROS 2 nodes are the
  *same* machine, and we have split them.
- **PX4 and the agent are different machines**, and we have given them a shared namespace.
- **New, and the worst of the three: the renderer is the donor.** Every other container joins
  the network and IPC namespace of a process that **has no counterpart on the aircraft at
  all**. On real hardware there is nothing for them to join. That is the strongest possible
  form of the accidental-co-location dependency this task exists to flush out, and it is
  currently structural rather than accidental.

**What it has cost so far** — all real:

| Symptom | Cause |
|---|---|
| Every container up, `ros2 topic list` returns **0 topics** | recreating the netns donor alone; joiners left on a dead namespace |
| `shm_size` declared on joining services doing nothing | joiners use the donor's `/dev/shm`; the declaration is inert |
| `non-shareable IPC` on startup | the donor must opt in with `--ipc shareable`, which is not discoverable |

**Explicitly REJECTED: collapsing everything into one container.** It erases the sim↔real
rehearsal completely, and it **cannot be done**: the engine image is **Ubuntu 22.04 (jammy)**
and **ROS 2 Jazzy has no jammy packages**, so the renderer and the ROS 2 graph cannot share a
container no matter how the boundaries are drawn (`D-04`). That converts the multi-container
shape from a design preference into a hard constraint, and it fixes the AirSim↔ROS 2 boundary
as a **socket** (RPC 41451 / MAVLink 4560) — exactly the kind of real link this task wants
standing in for a machine boundary. (The first instance of this constraint was Isaac Sim's
Python 3.11 against Jazzy's 3.12. That stack is retired; the jammy/noble one is live.)

**Acceptance.**
- Containers map 1:1 onto the machines that will exist on the aircraft, and there is **an
  architecture diagram of the current stack** — there is none today. The Gazebo-era one is
  archived at [`../history/gazebo/architecture.html`](../history/gazebo/architecture.html)
  and describes a stack that no longer exists.
- The PX4↔agent link is **configurable, not co-located** — the address is a parameter, so
  swapping UDP for a serial link touches configuration and not the ROS graph.
- **The flight gate still passes with unchanged numbers** — `scripts/run_gate.py`, success
  rate over seeded runs, plus the sensor rates (imagery at 94% and LiDAR at 100% of the
  ceilings `perception.launch.py` sets). Record them **before** the change.
- A flight still succeeds end to end.

**Traps.**
- **PX4 dials `127.0.0.1:8888` today.** Moving the agent out of the shared namespace means
  that address must become a real one — PX4's `uxrce_dds_client` takes host/port parameters,
  so this is configuration, but it is *load-bearing* configuration.
- **Fast-DDS delivers over shared memory.** Any two containers exchanging ROS 2 topics at
  rate still need a shared `/dev/shm`, or they fall back to UDP with different performance.
  Measure the sensor rates after the split rather than assuming.
- **The EKF-origin settle-then-verify ordering must survive the change.** It is the reason
  there is no compose file, and it is what stands between the stack and a vehicle that
  reports 35 m of altitude while sitting on the ground.
- **Do not split and re-measure in one step.** Change the topology, re-run the gate, compare
  against the recorded numbers — this area has produced several looks-fine-but-is-broken
  states already.

---

## D-07 — Automated flight gate (deferred)

**Status:** `todo` · **Deferred 2026-07-31** — running it locally is accepted instead
(`./scripts/run_local_ci.sh --gate`). **The gate is now `scripts/run_gate.py` (`SIM-07`)**,
which scores success rate over seeded runs and VOIDs (rather than fails) a run whose EKF
origin was stale.

**What.** Run the seeded flight gate automatically, rather than when someone remembers.

**Why it is not done — and one of the two blockers changed:**

- **GitHub-hosted runners cannot do it, and the reason got simpler.** The original argument
  was disk (12.6 GB image against ~14 GB) and **2 vCPU against an aggregate-RTF floor of
  0.95**. That was about the Gazebo stack. The current one needs an **NVIDIA GPU for the
  renderer** and a **57 GB engine image**; hosted runners have neither. This is no longer a
  performance argument that could be won by lowering a threshold — the simulator simply
  cannot run there.
- **A self-hosted runner on a public repo lets fork PRs execute code on the machine** —
  the one holding SSH keys, the netbird tunnel and the 7 TB drive. **Unchanged.**

**The way in, when it is worth it.** Trigger on `push` to `main` plus a nightly schedule,
never on `pull_request`. The runner then never executes fork code, and PRs keep tier 1. If
PR gating is wanted later, "require approval for outside collaborators" layers on top without
redoing anything.

**Acceptance.** The gate runs unattended, uploads its MCAPs, and no workflow triggered by a
fork can execute on the runner. Prove the second part, do not assume it.

**Trap.** The recorded "19 minutes" is the **retired Gazebo gate's** wall time. The current
gate restarts the full renderer stack per seed and has not been timed for this file — measure
it before choosing a trigger, because a duration that is fine nightly is painful per-push.

**Also unverified:** GitHub's current defaults and setting names for fork-PR approval on
self-hosted runners were not checked against live documentation — confirm them in the
repository's Actions settings before wiring anything, rather than trusting a recollection.

---

## D-08 — The runtime substrate itself is not captured

**Status:** `todo` · **Raised:** 2026-08-01, from `SIM-10`.

**Directly against the project goal** that "a fresh machine must reach a working stack from the
repo alone." It does not, and the gap is a whole layer below anything currently written down.

**What is actually running here** — established by inspection, not assumption:

```
host (Bazzite, immutable)
└── podman 5.8.4, rootless               <- runs the distrobox
    └── distrobox "drone-sim"            <- ubuntu 24.04, hostname drone-sim.carbonite
        └── dockerd (PID 6033) + containerd
            └── sim-unreal / -px4 / -xrce / -qgc / -ros2
```

**Every image, container and flight test in this project lives at the bottom of that stack.**
`dockerd` runs *inside* the distrobox — `/var/lib/docker` is the distrobox's own storage, and
the daemon self-reports `Name=drone-sim.carbonite`. So the containers die with the distrobox,
and `scripts/sim_up.sh` assumes a daemon that nothing in the repo creates.

**What is missing:** how the `drone-sim` distrobox is created (image, flags, GPU passthrough),
and how Docker is installed and started inside it. Both were done by hand before any of this was
written down. `D-01` captured the *install* as a Dockerfile; nothing captured the thing the
Dockerfile is built by.

**Second, subtler gap — the data is NOT inside the distrobox.** `/home/deck` is the host home
(`/dev/nvme0n1p6[/home/deck]`, btrfs) passed through by distrobox's default home mount, so every
bind mount in the bring-up — `sim/ue5/settings.json`, `vendor/Cosys-AirSim`, `ros2_ws`, `out/` —
resolves to host-filesystem paths. The processes are nested; the working tree is not. Worth
stating because "it all runs in the container" is the natural assumption and is only half true.

### DECIDED 2026-08-01 by the owner: distrobox is NOT the target

**The target is Docker on a native machine.** The distrobox is scaffolding on this development
box only — an accident of how `carbonite` was set up, not the intended substrate.

That *shrinks* this task rather than growing it: there is no need to capture
`distrobox create`, because a fresh machine should never make one. What is needed instead is an
audit that nothing in the stack has quietly come to depend on the distrobox or on this host.

**Good news first:** `scripts/sim_up.sh` is plain `docker run` throughout and should port to
native Docker unchanged. The nesting was never load-bearing for the bring-up logic.

**The real risk is host-specific workarounds baked into portable-looking images:**

1. **The Vulkan ICD symlink in `docker/unreal.Dockerfile` is a Bazzite/Fedora workaround.** It
   exists solely because *this* host's CDI spec injects an ICD naming `/usr/lib64/…` into an
   Ubuntu container. On a native Ubuntu host with `nvidia-container-toolkit` the multiarch path
   is already correct and the symlink is dead weight — harmless, but it encodes a foreign host's
   layout into an image that is meant to be portable, and it would mask a genuine ICD problem on
   the target machine. **Audit it before it is mistaken for a general fix.**
2. **The CDI device reference `nvidia.com/gpu=0`** comes from this host's CDI spec. Confirm the
   native target uses CDI too, or fall back to `--gpus all`.
3. **`/home/deck/...` bind paths** are the host home passed through by distrobox. On the target
   these are ordinary paths, but nothing should assume that prefix.

**Do:**

1. **State the target substrate explicitly** — native Docker, no distrobox — wherever a fresh
   machine's setup is described, so nobody reproduces the nesting by imitation.
2. **Audit the three host-specific items above** and mark each as either genuinely required or a
   `carbonite`-only workaround, in the file where it lives.
3. **Re-scope `D-03`.** It was framed around "the two nested-Docker workarounds"; if nesting is
   not the target, some of it dissolves rather than needing a solution.

**Related housekeeping:** flight video is accumulating in `out/` on the host home. It is
gitignored, but the project rule keeps big data out of `~` and on the 7 TB drive — which is
also where it belongs on the SSD/HDD split in `D-04`, since recordings are archival. Small
now, grows with every run; decide a destination before it is a problem.

**Done when:** a fresh machine running **native Docker** reaches a flying stack from
documented steps alone, and no image carries a `carbonite`-specific workaround without it
being labelled as one.
