# Reproducibility — Docker — backlog

**Area:** containerization, reproducible builds, one-command bring-up.
**Indexed from:** [`../drone-sim-todo.md`](../drone-sim-todo.md).

> **Project goal (added 2026-07-29):** *the whole setup must be easily reproducible as
> Docker.* Not "there are some Dockerfiles" — a fresh machine (or a fresh container on
> this one) must reach a working stack from the repo alone, with no undocumented manual
> steps.

**Definition of done for this area:** `docker compose up` brings the Lane A stack to the
same state `P0-07` proved by hand — headless SITL, `/fmu/out/*` populated, QGC-consumable
MAVLink — on a machine that has only Docker + an NVIDIA driver.

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

**Status:** `todo` · **Blocked by:** D-01

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

## D-03 — GPU services and the two nested-Docker workarounds

**Status:** `todo` · **Blocked by:** D-02

**What.** GPU-consuming services (`vlm-server`, later perception) with correct device
pinning.

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

## D-04 — Lane C (UE5 + Cosys-AirSim) container

**Status:** `todo` · **Blocked by:** Lane C bring-up

**What.** Build on `ghcr.io/epicgames/unreal-engine:dev-slim-5.5.4` (EpicGames org access
confirmed 2026-07-28).

**Why.** Lane C is now the photorealistic-perception lane, and a UE5 source build is
precisely the kind of long, fragile, easy-to-get-slightly-different process that
containerization exists for.

**Trap.** Image size and build time are both large; pin a known-good Cosys-AirSim commit
before investing in the build (`02_development_plan.md:64`).

---

## D-05 — CI builds the images

**Status:** `todo` · **Blocked by:** D-02

**What.** GitHub Actions builds `lane-a.Dockerfile` and runs the seeded SITL regression
inside it.

**Why.** An image that is only ever built by hand drifts. CI is what keeps "reproducible"
true rather than aspirational.

**Acceptance.** A red build when a pin breaks — e.g. re-introducing the agent v2.4.2 pin
must fail, since it is genuinely unbuildable.

---

## Notes carried in from the native install

- **Do not** trust a build script's own success banner. One agent build printed `BUILD OK`
  while `make` had exited 2; `ldd` showing an unresolved library and a stale binary mtime
  is what caught it. Dockerfiles must assert on artifacts (`RUN … && test -x …`,
  `ldd | grep -c 'not found'`), not on reaching the end of a script.
- ROS 2's `setup.bash` is **not `set -u` clean** — entrypoint scripts must not use `set -u`.
