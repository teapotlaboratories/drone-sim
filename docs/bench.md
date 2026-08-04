# Workbench Briefing — `drone-sim` Container

> **Read this first.** This document describes the machine, the container and the tools the
> project is built and run on, so you know what you are working on before you touch
> anything. What the project *is* — a photoreal drone simulator: Unreal Engine 5.8 +
> Cosys-AirSim + PX4 SITL + ROS 2 Jazzy — is in the [README](../README.md), and how to run
> it is in [`quickstart.md`](quickstart.md).

_Network coordinates (hostname, LAN IP, NetBird management URL and overlay IP) are
redacted as `<placeholders>` because this repository is public; substitute your own._

_Last verified: 2026-07-28. If a version or IP below disagrees with what you observe, trust
what you observe and update this file._

---

## 1. What this environment is for

This is the development workbench for the simulator: **UE 5.8 + Cosys-AirSim + PX4 v1.16
SITL + ROS 2 Jazzy**, brought up with `./scripts/sim_up.sh` and flown over ROS 2.

**This box is not the deployment target.** Docker on a plain machine is
(`docs/docker/todo.md`, `D-08`) — the podman/distrobox nesting described below is how
`carbonite` happens to be set up, not something a fresh machine should reproduce. The
distinction matters because a few workarounds here exist only for this host and must not be
mistaken for general fixes.

The design docs that framed the project before it narrowed to one simulator are archived in
[`history/reference/`](./history/reference/):

| Doc | What it covers | Still current? |
|---|---|---|
| `history/reference/01_sim_stack_report.md` / `.html` | Simulator landscape (2026), PX4↔ROS 2 wiring | **Historical** — it argued for running several simulators; the project now runs one. |
| `history/reference/02_development_plan.md` / `.html` | Phased build plan, version-coupling landmines, CI, repo layout | **Partly** — the version-coupling analysis and the frozen-conventions rule ([`conventions.md`](conventions.md)) still hold; the phase plan does not. |
| `history/reference/03_hardware_assessment.md` / `.html` | Go/no-go on **this exact machine**, GPU work-assignment, VRAM budgets | **Yes, for the hardware.** Its GPU work-split is the rule enforced in §2. |
| `history/reference/04_ue5_stack_architecture.md` | The Unreal/Cosys-AirSim decision — simulator survey, container topology | **Yes in substance**, but it targets UE5.5; the engine pin moved to UE5.8. |

**The hardware assessment describes this machine.** Its recommendations (below) are not
hypothetical — they are about the two GPUs you actually have.

---

## 2. The host machine — `carbonite`

- **Hostname:** `<workstation>` — reachable at `<lan-ip>` (LAN) and over the netbird tunnel.
- **OS:** Fedora 44-based **ostree immutable** system (uBlue/Bazzite-family), kernel
  `7.1.3-ogc5.1.fc44`. The root filesystem is read-only/composefs; user work lives under
  `/home/deck`. Treat the host OS as immutable — install software **inside the container**,
  not on the host.
- **User:** `deck` (passwordless-sudo is NOT available on the host; sudo needs a password).
- **CPU/RAM:** Core i9 class, 64 GB RAM.
- **GPUs (both present, driver `610.43.03`, CUDA UMD 13.3):**

  | Index | GPU | Arch | VRAM | Assigned role (per hardware assessment) |
  |---|---|---|---|---|
  | 0 | RTX 3080 | Ampere `sm_86` | **10 GB** | **Renderer** — Unreal Engine, perception, display |
  | 1 | RTX 5060 Ti | Blackwell `sm_120` | **16 GB** | **Inference only** — model serving alongside a run |

- **Storage:**
  - Internal NVMe (`btrfs`): 788 GB total, ~279 GB free. Home + models live here, and so do
    Docker images and the UE5 working set — deliberately; see `docs/docker/todo.md` `D-04`.
  - External drive (`ext4`, `/var/mnt/…`): **7 TB, ~5.5 TB free** — use this for large
    datasets, rosbags and archived recordings. **It is a 7200 RPM mechanical disk**, so it is
    for write-once/read-rarely data, not for a latency-sensitive working set.
  - LLM weights already staged at `/home/deck/Developments/models` (~335 GB: Qwen3.x,
    gemma, medgemma GGUF/BF16).

### GPU assignment — the single most important rule

The RTX 5060 Ti is **Blackwell (sm_120)** and has documented crashes on too-new drivers with
GPU-heavy simulators. The RTX 3080 is a proven, stable renderer. Therefore:

- **Render on GPU 0 (RTX 3080).** `scripts/sim_up.sh` enforces this at the container
  boundary — `--gpus '"device=nvidia.com/gpu=0"'` — and that is the right place for it.
- **Infer on GPU 1 (RTX 5060 Ti).**
- **Pin at the container boundary, not in the application.** Two independent reasons:
  - `CUDA_VISIBLE_DEVICES` does **not** control a Vulkan RTX renderer at all — Unreal's and
    Isaac's renderers both ignore it.
  - Unreal under `-RenderOffScreen` has historically ignored app-level GPU flags and taken
    GPU 0 regardless. That happens to be the card we want, which is exactly why it is a trap:
    it works until the day the split is inverted.
  - GPU index ordering is not `nvidia-smi` ordering. CUDA defaults to `FASTEST_FIRST`, so
    with two dissimilar cards "index 1" is not reliably the Blackwell one — set
    `CUDA_DEVICE_ORDER=PCI_BUS_ID` for anything selecting a device from inside the process,
    and verify with `nvidia-smi`.
- **Never** span a single renderer across both dissimilar cards (documented corruption).
- **VRAM is the binding constraint.** The renderer has **10 GB** — cap scene complexity,
  camera count and capture resolution, and do not run a UE5 shader compile concurrently with
  other heavy GPU work. On the inference card, 16 GB does **not** fit a 30B-class VLM; serve
  2B/4B/8B or use a remote endpoint.

---

## 3. The container — `drone-sim`

A **distrobox** (rootless **podman 5.8.4**) container. It shares the `deck` home directory
with the host, so paths under `/home/deck/...` are identical inside and out.

| Property | Value |
|---|---|
| Name | `drone-sim` |
| Base image | **Ubuntu 24.04.4 LTS** (ROS 2 Jazzy's distro: Python 3.12) |
| Init | systemd running as PID 1 (`--init`) |
| Network | **Isolated namespace** (`--unshare-netns`) — its own stack, separate from the host |
| Extra caps/devices | `NET_ADMIN`, `/dev/net/tun` (for WireGuard) |
| GPU access | Host NVIDIA driver injected (`--nvidia`); both GPUs visible; CUDA verified working |

**Enter the container from the host:**
```bash
distrobox enter drone-sim
```

### Autostart

The container comes up automatically on host boot and needs no manual start:
`host boot → user lingering → systemd user service drone-sim.service → podman start drone-sim
→ in-container systemd → netbird + Docker`. Verified across a real reboot.

### The stack runs one level further down

Everything the project builds and flies lives inside **Docker running inside this
distrobox** — `sim-unreal`, `sim-px4`, `sim-xrce`, `sim-qgc`, `sim-ros2`. Two consequences
that are easy to get wrong:

- **`/var/lib/docker` is the distrobox's own storage**, and the daemon self-reports
  `Name=drone-sim.carbonite`. The stack's containers die with the distrobox.
- **The working tree is not nested.** `/home/deck` is the *host* home passed through by
  distrobox, so every bind mount in the bring-up (`sim/ue5/settings.json`, `vendor/`,
  `ros2_ws`, `out/`) resolves to a host-filesystem path. The processes are nested; the data
  is not.

---

## 4. Tools installed and how to use them

### netbird (mesh VPN)
- **v0.75.0**, managed as a systemd service, **auto-connects on boot**.
- Management URL: `<netbird-mgmt-url>`
- This node: **`<node>.<tailnet>.internal`**, NetBird IP **`<overlay-ip>/16`**,
  kernel WireGuard on `wt0`.
- The tunnel lives **only inside this container's netns** — the host and other apps do not
  route through it. Check with `sudo netbird status`.

### Docker (nested, inside the container)
- **Docker Engine v29.6.2**, rootful daemon via the container's systemd, **auto-starts on boot**.
- **No `sudo` needed** — the `deck` user is in the `docker` group. Just `docker …`.
- **Storage driver: `fuse-overlayfs`** (required — plain `overlay2` cannot nest in rootless
  podman; do not change it). Config in `/etc/docker/daemon.json`.
- **GPU works in Docker** via CDI:
  ```bash
  docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
  # or target one GPU:
  docker run --rm --device nvidia.com/gpu=1 ...     # 5060 Ti only, for inference
  ```

#### GPU-in-Docker: how it is wired (and its one failure mode)
This host is ostree-immutable and bind-mounts its own NVIDIA CDI spec (with Fedora
`/usr/lib64/...` paths) read-only over `/etc/cdi`, which is **wrong** for this Ubuntu
container. The fix in place:
- A correct CDI spec (with `/usr/lib/x86_64-linux-gnu/...` paths) is written to
  `/etc/cdi-local/nvidia.yaml`, and `daemon.json` sets `"cdi-spec-dirs":["/etc/cdi-local"]`.
- An in-container systemd unit **`nvidia-cdi-local.service`** regenerates that spec **before
  Docker starts on every boot**, so it self-heals across host driver updates.
- **Failure mode:** if `docker run --gpus all` ever errors with a missing
  `libEGL_nvidia.so.<ver>` / `/usr/lib64/...` path, the spec is stale. Regenerate:
  ```bash
  nvidia-ctk cdi generate | sudo tee /etc/cdi-local/nvidia.yaml >/dev/null
  sudo systemctl restart docker
  ```
- **The same Fedora/Ubuntu path mismatch reaches into an image.**
  `docker/unreal.Dockerfile` symlinks `/usr/lib64/libGLX_nvidia.so.0` for exactly this
  reason. That symlink is a **`carbonite`-only workaround**, harmless but not general — on a
  native Ubuntu host with `nvidia-container-toolkit` the multiarch path is already correct,
  and the symlink would mask a genuine ICD problem rather than fix one.

---

## 5. Where things live

```
/home/deck/Developments/
├─ models/                         # ~335 GB of local LLM/VLM weights (Qwen3.x, gemma, medgemma)
└─ projects/drone-sim/
   ├─ versions.lock                # every pin + the couplings CI asserts — the authority
   ├─ .repos                       # vcstool manifest for the vendored upstream trees
   ├─ docker/                      # px4 · unreal · ros2 · qgc · video · airsim-client images
   ├─ scripts/                     # sim_up.sh (the only supported bring-up), gate, harness
   ├─ ros2_ws/src/                 # the original glue: interfaces, bringup, control, …
   ├─ sim/ue5/                     # settings.json — which sensors exist and how they are tuned
   ├─ scenarios/                   # seeded scenario definitions
   ├─ patches/cosys-airsim/        # recorded deviations from the pristine vendored tree
   ├─ tests/                       # off-target tests (tier-1 CI)
   ├─ vendor/                      # pinned upstream checkouts (git-ignored; see .repos)
   └─ docs/
      ├─ bench.md                  # ← this file
      ├─ conventions.md            # the frozen ROS 2 graph spec
      ├─ quickstart.md             # how to run the simulator
      ├─ todo.md                   # the simulator backlog
      ├─ docker/todo.md            # the reproducibility backlog
      ├─ vendor/                   # vendoring notes per upstream component
      ├─ worklog/                  # dated record of each investigation, with evidence
      └─ history/                  # retired backlogs and design docs (Gazebo, Isaac, Phase 0)
```

Big data does **not** live here: rosbags, recordings and datasets go on the 7 TB external
drive, and on any non-repo drive only under
`<drive-root>/Developments/projects/drone-sim/` — never `~`, never a top-level directory on
a drive we do not own.

---

## 6. Ground rules for working here

1. **Install into the container, never the host.** The host OS is immutable; `sudo` on the
   host needs a password you do not have. Do package installs inside `drone-sim`.
2. **Respect the GPU split.** Render → GPU 0 (3080). Infer → GPU 1 (5060 Ti). Pin it at the
   container boundary.
3. **Do not change the Docker storage driver or the CDI setup** unless GPU-in-Docker is
   already broken — both are load-bearing workarounds, documented in §4.
4. **Big files go on the 7 TB external drive**, not the 279 GB internal NVMe — with the one
   documented exception that the UE5 working set stays on the NVMe because the external drive
   is mechanical (`docs/docker/todo.md` `D-04`).
5. **Version coupling is the dominant project risk** — `px4_msgs` branch-matched to the
   firmware, the engine image's Ubuntu 22.04 against ROS 2 Jazzy's 24.04. It is spelled out
   in [`../versions.lock`](../versions.lock) and in `history/reference/`.
6. **Trust observation over this doc.** Re-run the checks in §7 if anything looks off, and
   update the values here.

---

## 6a. Running commands on the host (verified 2026-07-29)

Normally you don't — install into the container. But when a genuine host-side test is needed
(and **approved**, per `.ai/AGENTS.md`), this is the working recipe. Every step below has a
trap that cost time to find.

```bash
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/host/run/user/1000/bus
host-spawn -no-pty /usr/bin/podman --version      # -> podman version 5.8.4
```

- **The bus path is the whole trick.** `distrobox-host-exec` delegates to `host-spawn`, which
  needs the **host's** session bus at `/run/host/run/user/1000/bus`. Pointed at the
  container's own `/run/user/1000/bus` it **silently no-ops** — rc=0, no output, nothing
  executes. `distrobox enter` sets this for interactive shells, so it works by hand and fails
  in non-interactive ones.
- **The host has `podman` 5.8.4, no `docker`.** Bazzite 44 (Kinoite).
- **The host's podman binary cannot be run from inside the container**
  (`libsubid.so.5: cannot open shared object file` — Fedora binary, Ubuntu userspace), and it
  would execute in the *container's* namespaces anyway.
- **`nohup … &` via host-spawn dies** when host-spawn returns. Run synchronously.
- **SELinux is enforcing.** Bind mounts into host podman containers need `:z`, or you get
  `Permission denied` (rc=126).
- **podman rejects `--runroot` paths longer than 50 characters** (Unix socket limit). Keep the
  runroot short (`/tmp/pmrr`); the `--root` store may be a long path.
- **Never write to the host's live podman store** at
  `~/.local/share/containers/storage` — it is shared via `/home/deck` and is what runs this
  distrobox. Concurrent writes from a second podman can corrupt it. Use an isolated
  `--root` (e.g. on the external drive).
- **Nested rootless `podman load` fails** with *"insufficient UIDs or GIDs available in user
  namespace (requested 0:42 for /etc/gshadow)"* — this container is already in a userns, so
  image loads must happen on the host.

Note also that `/tmp` inside the container is a **32 GB tmpfs distinct from the host's** — a
runaway log can fill it and break tooling (see the PX4 prompt-spin defect in
`docs/docker/todo.md`).

## 7. Quick self-check commands

```bash
# from the host:
distrobox enter drone-sim

# inside the container:
nvidia-smi -L                                   # both GPUs visible?
docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L   # GPU reaches Docker?
sudo netbird status | grep -E 'Management:|NetBird IP:' # tunnel up?
docker images | grep drone-sim/                 # the stack's images built?
```
