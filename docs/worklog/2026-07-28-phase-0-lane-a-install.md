# 2026-07-28 — Phase 0 Lane A install (PX4 v1.16 + Gazebo Harmonic + ROS 2 Jazzy)

**Task.** Execute the Lane A install sequence approved after the scaffold
([`2026-07-28-phase-0-scaffold.md`](2026-07-28-phase-0-scaffold.md)): `P0-01` → `P0-08`,
with `P0-13` (vLLM) available to run in parallel.

**Approach decisions carried in:** native-in-container (compose deferred to Phase 1);
`vendor/` on the internal NVMe (312 GB free at start).

**Status:** in progress. This file is updated as each task lands, not at the end.

---

## P0-01 — GPU baseline · **PASS**

Both GPUs are visible in the container *and* reach nested Docker through the CDI path.
No spec regeneration needed — the `nvidia-cdi-local.service` workaround is healthy.

```
$ nvidia-smi -L
GPU 0: NVIDIA GeForce RTX 3080 (UUID: GPU-a59afb24-83b3-dc09-b197-345d1157b5a1)
GPU 1: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-961e3133-170e-e898-1f77-0bd7e59fe168)

$ docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
GPU 0: NVIDIA GeForce RTX 3080 (UUID: GPU-a59afb24-83b3-dc09-b197-345d1157b5a1)
GPU 1: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-961e3133-170e-e898-1f77-0bd7e59fe168)
```

UUIDs recorded because they are stable identifiers — unlike the index, which is what
`nvidia-smi` orders and what Isaac Sim explicitly does *not* use (Isaac reads the
Omniverse `.log` `[gpu.foundation]` table).

## P0-02 — ROS 2 Jazzy · **PASS**

Preconditions confirmed before starting: passwordless `sudo` inside the container, GitHub
API reachable (HTTP 200), codename `noble`.

Apt source installed via the **`ros2-apt-source` .deb** method, not the retired apt-key
path:

- `ros-apt-source` release resolved to **`1.2.0`** → package `ros2-apt-source 1.2.0~noble`
- After `apt update`, `ros-jazzy-desktop` candidate is
  **`0.11.0-1noble.20260616.084553`** — record this in `versions.lock` as the Jazzy sync
  point, since "Jazzy" alone is not a reproducible pin.

`ros-jazzy-desktop` + `ros-dev-tools` installed — **0 apt errors** (`grep -c '^E: '` on the
install log). Acceptance met:

```
ROS_DISTRO=jazzy  ROS_VERSION=2  ROS_PYTHON_VERSION=3
ros-jazzy-desktop   0.11.0-1noble.20260616.084553
ros-jazzy-ros-base  0.11.0-1noble.20260616.084325
python3 -c "import rclpy"  → OK on 3.12.3
ros2 topic list            → /parameter_events, /rosout
colcon /usr/bin/colcon · vcs /usr/bin/vcs · rosdep /usr/bin/rosdep
```

**Pinned the dated sync stamp, not "jazzy".** The distro name is not a reproducible pin;
`0.11.0-1noble.20260616.084553` is. Recorded in `versions.lock`.

The `rclpy` import on **3.12.3** is the system-Jazzy half of the Isaac Python split. It is
correct here and must never be sourced into Isaac Sim's 3.11 interpreter — that is exactly
the `ModuleNotFoundError: rclpy._rclpy_pybind11` failure `P0-10` exists to avoid.

**Note:** `dpkg-preconfigure: unable to re-open stdin` appears twice in the apt output.
Benign — it is the noninteractive frontend with no TTY attached, not a failed
configuration step. Confirmed by the zero error count and a working install.

## P0-03 — PX4 v1.16.0 clone · **clone done**, build pending

`git clone --recursive --branch v1.16.0` into `vendor/PX4-Autopilot-v1.16` (the path
`.repos` declares). Ran concurrently with the apt install deliberately: the clone is
network/disk-bound and takes no dpkg lock, so the two did not contend. Exit 0.

**First real SHA resolved:**

```
$ git describe --tags   → v1.16.0
$ git rev-parse HEAD    → 6ea3539157ca358c70a515878b77077af7d4611d
$ git submodule status --recursive | wc -l → 35, none with the '-' uninitialized marker
$ du -sh .              → 2.6G
```

Written into `versions.lock` as `sha:` — but the entry is deliberately still
`status: pinned`, **not locked**, because this file's own rule (D2) is that a pin is not
locked until it also has a passing smoke test. `make px4_sitl gz_x500` has not run.

`Tools/setup/ubuntu.sh` **must wait** for the apt install to finish — it installs packages
itself and would block on the dpkg lock.

## P0-03 build + launch · **PASS — and the TIMEOUT risk did not reproduce**

`HEADLESS=1 GZ_IP=127.0.0.1 make px4_sitl gz_x500`. Built and launched to a `pxh>` shell.

```
Accel/Mag TIMEOUT failures : 0
ERROR [...] lines           : 0
gz sim                      : running headless, GZ_IP=127.0.0.1
uxrce_dds_client            : init UDP agent IP:127.0.0.1, port:8888
INFO [px4] Startup script returned successfully
```

The `Accel #0 fail: TIMEOUT!` / `MAG #0 failed: TIMEOUT!` failure tracked in
`PX4/PX4-Autopilot#25089`, `#24159`, `#24595`, `#26299` — listed in the risk register as
Med/Med for this exact Ubuntu 24.04 + Harmonic combination — **did not occur**.

All four documented mitigations were applied *up front* rather than after a failure:
single-command `make px4_sitl gz_x500` launch (not the split-terminal/standalone method),
`GZ_IP=127.0.0.1` to keep Gazebo transport on loopback, `HEADLESS=1`, and 24 cores of
headroom. The container's isolated netns likely helps too, since the documented root cause
in `#24595` is Gazebo multicast flooding the host network — and this container cannot
reach the host network to flood it.

**Not yet proven:** this was a short launch, not the 5-minute continuous run `P0-07`
requires, and the real-time factor has not been measured. A clean start is not a clean
run.

`INFO [commander] LED: open /dev/led0 failed (22)` is expected in SITL — no LED device.

## P0-05 — Micro-XRCE-DDS-Agent · **first attempt FAILED (upstream drift), retrying**

`git clone -b v2.4.2` → SHA `57d086216d01ec43121845d385894a25987f8a2c`. Clone fine; the
**superbuild** failed:

```
fatal: invalid reference: 2.12.x
CMake Error at .../fastdds-gitclone.cmake:49 (message):
  Failed to checkout tag: '2.12.x'
```

**Root cause — upstream branch deletion, not a local problem.** `CMakeLists.txt:99` pins
Fast-DDS by *branch*:

```cmake
set(_fastdds_version 2.12)
set(_fastdds_tag 2.12.x)
```

eProsima has since removed the `2.12.x` branch. Confirmed by querying the remote: live
branches are now `2.6.x` and `3.0.x`–`3.6.x`, with no 2.12 branch — though tags `v2.12.0`
… `v2.12.2` still exist.

**This is the failure mode `versions.lock` D2 was written for.** A pin expressed as a
*branch* is not a pin; upstream deleted it and a tagged release of the agent became
unbuildable retroactively. A SHA or tag could not have done this.

**Fix chosen: `-DUAGENT_USE_SYSTEM_FASTDDS=ON`** — a build-layer flag upstream already
exposes, which skips the clone and uses the Fast-DDS already on the box.

Options considered:

| Option | Verdict |
|---|---|
| `-DUAGENT_USE_SYSTEM_FASTDDS=ON` against Jazzy's Fast-DDS | **chosen** — no vendor edit at all; upstream's own `if(UAGENT_USE_SYSTEM_FASTDDS)` path sets `_fastdds_version 2`, i.e. upstream sanctions any 2.x. Also leaves exactly **one** Fast-DDS on the machine, which matters because the agent and the ROS 2 graph talk DDS *to each other*. |
| Edit `CMakeLists.txt:99` to tag `v2.12.2` | rejected — a source edit requiring a `LOCAL_PATCHES.md` entry, when a build-layer flag achieves the same end. Violates "push integration into the build layer, not the files". |
| Bump the agent to v3.x | rejected — silently abandons the `v2.4.2` pin the plan calls out; a version decision, not a build fix. |

Available from Jazzy, all three CMake configs present: `fastrtps 2.14.6-1noble.20260303.233638`,
`fastcdr 2.2.7-1noble.20260225.051855`, `foonathan_memory`.

**Consequence to record in `versions.lock`:** the agent is no longer built against
Fast-DDS 2.12 as upstream v2.4.2 intended, but against **2.14.6**. Upstream's system path
accepts any 2.x, so this is sanctioned — but it is a deviation from the nominal pin and
must be visible, not buried.

## P0-05 retry · **PASS**

Built with `-DUAGENT_USE_SYSTEM_FASTDDS=ON -DCMAKE_PREFIX_PATH=/opt/ros/jazzy`. Installed
to `/usr/local/bin/MicroXRCEAgent`.

Smoke test did better than "it starts" — the PX4 SITL still running from `P0-03` connected
to it immediately:

```
UDPv4AgentLinux.cpp | init              | running...          | port: 8888
Root.cpp            | create_client     | create              | client_key: 0x00000001
SessionManager.hpp  | establish_session | session established | address: 127.0.0.1:46248
ProxyClient.cpp     | create_participant| participant created | participant_id: 0x001(1)
ProxyClient.cpp     | create_topic      | topic created       | topic_id: 0x800(2)
```

That is the uXRCE-DDS transport working end to end, not merely a binary that launches.

**Observed, non-blocking:** the agent **dumped core when `timeout` sent SIGTERM**. Runtime
is clean; the crash is on the shutdown path. Noted rather than chased — but if `P0-07` or
CI ever needs a clean agent shutdown, this is the thing to look at first.

## P0-06 — px4_msgs / px4_ros_com · **build green, task NOT closed**

Both cloned on **`release/1.16`** and built:

```
px4_msgs     392e831c1f659429ca83902e66820d7094591410   (226 .msg files)
px4_ros_com  86e9aeb20e55a4673fa8a9f1c29ea06a6c5ad1af
Summary: 2 packages finished [1min 48s]
ros2 interface show px4_msgs/msg/VehicleLocalPosition → resolves, MESSAGE_VERSION = 0
```

`MESSAGE_VERSION = 0` is v1.16's uORB message versioning field — present, as expected for
this firmware.

**Deliberately still `status: pinned`, not locked.** A green colcon build is exactly the
evidence that does *not* prove a branch match: a mismatch produces topics that exist and
stay empty. Only `P0-07` observing `/fmu/out/*` carrying *moving* data closes this.

**Resolved a `TODO-verify`, and found an inconsistency in the plan.** Upstream publishes a
`release/1.16` branch for **both** repos. The development plan's setup snippet
(`02_development_plan.md:108`) clones `px4_msgs` with `-b release/1.16` but `px4_ros_com`
with **no branch at all** — i.e. `main`. Both are branch-matched here. Left as-is the
asymmetry is a latent version-skew waiting to happen.

**Workspace layout decision.** Third-party ROS packages stay in `vendor/` (per `.repos`)
and are built with `colcon build --base-paths ../vendor/px4_msgs ../vendor/px4_ros_com`
from `ros2_ws/`. No symlinks or copies land in `ros2_ws/src`, and `build/install/log` are
already git-ignored.

## Dead end — `set -u` in the P0-07 harness

First `P0-07` attempt produced exactly one line and exited:

```
/opt/ros/jazzy/setup.bash: line 8: AMENT_TRACE_SETUP_FILES: unbound variable
```

**The run never happened**; the shell reported exit 0 because the failure was swallowed by
a trailing `| tail`. ROS 2's `setup.bash` references unbound variables by design and dies
under `set -u`. Harness changed to `set +u` with a comment so it does not get "tidied"
back in.

Worth recording because the failure mode is silent-looking: a wrapper that exits 0 while
doing nothing is indistinguishable from a pass unless the output is actually read.

## P0-07 — 5-minute end-to-end run · **attempt 1 FAILED, attempt 2 PASSED**

### Attempt 1 — agent segfault, 0 topics

```
Segmentation fault (core dumped)  MicroXRCEAgent udp4 -p 8888
/fmu/out topics visible: count: 0
TIMEOUT failures: 0     ERROR lines: 53     RTF min/mean/max: 1.000/1.000/1.000
```

The 53 PX4 errors were all downstream of the crash — `create entities failed:
rt/fmu/out/<topic>` for every topic, then `session setup failed`.

**This corrected an earlier misread of mine.** After the first `P0-05` smoke I recorded the
agent core dump as "shutdown path, non-blocking" because `timeout` had just sent SIGTERM.
That was wrong: it was this same segfault, and calling it benign cost a full 5-minute run.
The crash sits immediately after `create_subscriber` — inside DDS entity creation, exactly
where a Fast-DDS version skew bites.

### Root cause — the pinned agent version is not buildable on this platform

Established by elimination:

| Route | Outcome |
|---|---|
| v2.4.2 superbuild (upstream default) | Dies at clone — Fast-DDS branch `2.12.x` deleted upstream |
| v2.4.2 + Fast-DDS tag `v2.12.2` | Dies at **compile**: `typesv1.cxx:61: error: 'uint8_t' in namespace 'std' does not name a type` — missing `<cstdint>`, the 2.12 line predates GCC 13 |
| v2.4.2 + system Fast-DDS 2.14.6 | Builds, then **segfaults** creating DDS entities → 0 topics |
| **v2.4.3 + system Fast-DDS 2.14.6** | **works** |

**Decision: bump the agent v2.4.2 → v2.4.3.** It is the next release and pins Fast-DDS
**2.14** — the generation Jazzy ships (2.14.6) and one that compiles on noble because
Ubuntu packages it. Needs **no source patch**; the vendored tree is byte-identical to
upstream. This deviates from `02_development_plan.md:33`, so it is recorded in
`docs/vendor/micro-xrce-dds-agent.md` with the full evidence and in
`versions.lock`.

**Caveat carried forward:** the binary links `/opt/ros/jazzy/lib/libfastrtps.so.2.14`, so
**ROS 2 must be sourced for the agent to run**. Fine for the current scripts; it would bite
if the agent is ever a bare systemd unit or lives in an image without Jazzy.

### A near-miss worth recording

The `v2.12.2` rebuild printed **`BUILD OK` on a build that had failed** — `set -e` did not
fire, so my own script reported success while `make` had exited 2 and `make install` never
ran. What caught it was `ldd` showing `libfastrtps.so.2.14 => not found` and the binary's
mtime being 16 minutes stale.

**Lesson applied:** subsequent builds check `$?` explicitly after each step and assert the
installed binary's mtime is current and that it has **zero** unresolved libraries. A
script's own success banner is not evidence.

### Attempt 2 — PASS

```
booted after ~14s
/fmu/out topics visible: count: 24
vehicle_local_position timestamp: 1785275929412801  →  1785275950356985   (moving)
average rate: 100.019 Hz    min 0.008s  max 0.012s  std dev 0.00198s
TIMEOUT failures: 0
ERROR lines:      0
RTF samples:      15        RTF min/mean/max: 1.000 / 1.000 / 1.000
still alive: px4=3 gz=1 agent=1
```

**Both remaining Phase 0 Lane A exit criteria met:** headless SITL with no Accel/Mag
TIMEOUT over 5 minutes, and `/fmu/out/*` present *and carrying moving data*.

**RTF floor for CI: propose 0.95.** Measured 1.000 flat across all 15 samples — the
machine has large headroom, so 0.95 trips on genuine desync without flagging jitter.

`P0-06` closes here too. A green colcon build never proved the branch match; 24 topics
publishing at a stable 100 Hz does.

- Disk at start of installs: **312 GB free** on `/`. Watch it — PX4 with recursive
  submodules plus a full Jazzy desktop is a meaningful bite, and the internal NVMe is the
  constrained volume (the 7 TB external is for data, not tooling).

---

## P0-08 — QGroundControl · **PASS (via a virtual display)**

The container has **no usable `DISPLAY`**. An `X0` socket exists, but X authorization
rejects us:

```
Authorization required, but no authorization protocol specified
fatal: This application failed to start because no Qt platform plugin could be initialized.
```

Wire-level evidence was easy — 28 distinct MAVLink message types / 3724 messages in 10 s
on UDP 14550, `ATTITUDE` + `GLOBAL_POSITION_INT` + `LOCAL_POSITION_NED` each ~50 Hz,
`HEARTBEAT sys=1 autopilot=12 (PX4)`. But that proves the *stream*, not that QGC connects.

**Solution: run QGC on a virtual X display and screenshot it.**

```bash
Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp &
DISPLAY=:99 QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb \
  ./vendor/tools/QGroundControl.AppImage &
DISPLAY=:99 import -window root shot.png
```

`QT_QUICK_BACKEND=software` is **required** — Xvfb has no hardware GL and QtQuick will not
initialise without it.

Result, captured at 20/40/60/80 s (evidence:
`assets/2026-07-28-p0-08-qgc-connected.png`): **"Ready To Fly"**, flight mode **Hold**,
battery **100%**, autopilot detected as **PX4**, live compass, map at the SITL default home
(Irchelpark, Zurich — 47.397 N, 8.545 E). QGC alive through all four captures; the
`Bus error` in the log is from the cleanup kill, not the run.

**This recipe generalises** — it is how any GUI in this container (rviz, Foxglove, the
Gazebo GUI) should be verified from now on. "No display" is no longer a reason to leave a
criterion unverified.

## P0-13 — vLLM installed · **partial** (no model served yet)

`vllm 0.26.0`, `torch 2.11.0+cu130`, CUDA 13.0, in a venv on the external drive.

**`sm_120` IS in torch's arch list** (`sm_75, sm_80, sm_86, sm_90, sm_100, sm_120`), so
Blackwell needs **no** cu128-nightly workaround — a risk `03_hardware_assessment.md:42`
raised that turns out not to apply at these versions.

### ⚠ `CUDA_VISIBLE_DEVICES=1` alone does not guarantee the 5060 Ti

vLLM warns on every import here:

> *"Detected different devices in the system: NVIDIA GeForce RTX 3080, NVIDIA GeForce
> RTX 5060 Ti. Please make sure to set `CUDA_DEVICE_ORDER=PCI_BUS_ID` to avoid unexpected
> behavior."*

CUDA's default ordering is **`FASTEST_FIRST`, not PCI bus order**. With two *dissimilar*
cards, index 1 is not reliably the 5060 Ti. The GPU work split is a hard project rule, and
the docs describe enforcing it with `CUDA_VISIBLE_DEVICES=1` alone — which is **not
sufficient**. Every vLLM launch must set **both** `CUDA_DEVICE_ORDER=PCI_BUS_ID` and
`CUDA_VISIBLE_DEVICES=1`, and confirm placement against `nvidia-smi`.

## P0-09 — Isaac Sim 5.1 on driver 610.43.03 · **CONFIRMED BROKEN**

The open question from the scaffold worklog now has an empirical answer.

**Test.** `nvcr.io/nvidia/isaac-sim:5.1.0` — **public, no NGC login required** (resolving a
`TODO-verify`; the image is 14.0 GB). Headless `SimulationApp({"headless": True})` probe,
with **only GPU 0 (RTX 3080) exposed** via `--device nvidia.com/gpu=0`.

**Result: SIGSEGV.**

```
docker rc=139
rtx.scenedb.plugin crash signature x5
[Fatal] librtx.scenedb.plugin.so!carbOnPluginStartup+0x3b4de
Segmentation fault (core dumped)
PROBE_APP_UP never printed — SimulationApp never came up
```

Verbatim the failure `03_hardware_assessment.md:41` documents for this driver situation.

**The disambiguation that matters.** The crash happened on the **Ampere RTX 3080**, with
the Blackwell 5060 Ti excluded from the container entirely. So this is **not** the
documented Blackwell/Isaac instability — it is purely the driver version. Reassigning GPU
roles cannot fix it. Exposing a single GPU was deliberate test design: with both cards
visible, a crash would have had two candidate causes.

**Useful by-product.** Isaac's own enumeration rendered before dying, confirming the
container boundary works as a GPU pin:

```
| Driver Version: 610.43.03     | Graphics API: Vulkan
| 0 | NVIDIA GeForce RTX 3080 | Yes: 0 | 10240 MB | UUID a59afb24.. |
```

`--device nvidia.com/gpu=0` makes the 3080 index 0 inside the container, so
render-on-the-3080 can be enforced at the container boundary instead of relying on
`--/renderer/activeGpu`.

**Fallback 1 (NGC container) is exhausted** — it bundles userspace but still talks to the
host kernel driver, which is the thing that is wrong.

**Now testing fallback 2:** Isaac Sim 6.0.0 (also public) on the same driver. If 6.0 runs,
the trade is not free — 6.0 needs Python 3.12 (rewriting the `P0-10` ROS 2 integration
path) and **breaks Pegasus v5.1.0**, which pins to Isaac 5.1.0 exactly. Fallback 3 (host
rebase to R580) stays owner-only.
