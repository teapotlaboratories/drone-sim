# Workbench Briefing — `drone-sim` Container

> **Read this first.** You are an AI agent working inside (or against) the `drone-sim`
> container on the `carbonite` workstation. This document describes the machine, the
> container, and the tools you have, so you know what you are working on before you touch
> anything. Project background and design decisions live in [`reference/`](./reference/).

_Network coordinates (hostname, LAN IP, NetBird management URL and overlay IP) are
redacted as `<placeholders>` because this repository is public; substitute your own._

_Last verified: 2026-07-28. If a version or IP below disagrees with what you observe, trust
what you observe and update this file._

---

## 1. What this environment is for

This is the development workbench for a **triple-lane drone simulation framework** —
PX4 + ROS 2 Jazzy + Isaac Sim + Gazebo + Unreal/AirSim, driving toward VLM-based
sim-to-real drone navigation. The full plan, sim-stack rationale, and hardware assessment
are in `reference/`:

| Doc | What it covers |
|---|---|
| `reference/01_sim_stack_report.md` / `.html` | Simulator landscape (2026), why dual-sim, PX4↔ROS 2 wiring, the three target papers (SPF, Fly0, OnFly) |
| `reference/02_development_plan.md` / `.html` | Phased, executable build plan (Phase 0→4), version-coupling landmines, CI, repo layout |
| `reference/03_hardware_assessment.md` / `.html` | Go/no-go on **this exact machine**, GPU work-assignment, VRAM budgets |

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
  | 0 | RTX 3080 | Ampere `sm_86` | **10 GB** | **Renderer** — Isaac Sim, Unreal, Isaac ROS perception, display |
  | 1 | RTX 5060 Ti | Blackwell `sm_120` | **16 GB** | **Inference only** — vLLM / VLM serving |

- **Storage:**
  - Internal NVMe (`btrfs`): 788 GB total, ~279 GB free. Home + models live here.
  - External drive (`ext4`, `/var/mnt/…`): **7 TB, ~5.5 TB free** — use this for large
    datasets, rosbags, UE5 projects, and Isaac assets.
  - LLM weights already staged at `/home/deck/Developments/models` (~335 GB: Qwen3.x,
    gemma, medgemma GGUF/BF16).

### GPU assignment — the single most important rule

The RTX 5060 Ti is **Blackwell (sm_120)** and has documented Isaac Sim startup crashes on
too-new drivers. The RTX 3080 is a proven, stable renderer. Therefore:

- **Render on GPU 0 (RTX 3080).** Pin Isaac Sim with kit flags —
  `--/renderer/activeGpu=0 --/physics/cudaDevice=0` (GPU index comes from the Omniverse
  `.log` `[gpu.foundation]` table, **not** `nvidia-smi` ordering). `CUDA_VISIBLE_DEVICES`
  does **not** control the Vulkan RTX renderer.
- **Infer on GPU 1 (RTX 5060 Ti).** vLLM honours `CUDA_VISIBLE_DEVICES=1`.
- **Never** span a single Isaac Sim render across both dissimilar cards (documented corruption).
- **VRAM is the binding constraint.** 10 GB is below Isaac Sim 5.1/6.0's stated 16 GB
  minimum — it runs, but cap scene complexity, RTX-sensor count, and resolution.
  Qwen3-VL-30B-A3B does **not** fit locally; serve 2B/4B/8B or use a remote endpoint.

---

## 3. The container — `drone-sim`

A **distrobox** (rootless **podman 5.8.4**) container. It shares the `deck` home directory
with the host, so paths under `/home/deck/...` are identical inside and out.

| Property | Value |
|---|---|
| Name | `drone-sim` |
| Base image | **Ubuntu 24.04.4 LTS** (matches Isaac Sim 5.1 / ROS 2 Jazzy target: Python 3.11/3.12) |
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

---

## 4. Tools installed and how to use them

### Claude Code CLI
- **`claude` v2.1.220**, at `~/.local/bin/claude` (on `PATH`). Run `claude` inside the box.

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

---

## 5. Where things live

```
/home/deck/Developments/
├─ models/                         # ~335 GB of local LLM/VLM weights (Qwen3.x, gemma, medgemma)
└─ projects/drone-sim/
   └─ docs/
      ├─ bench.md                   # ← this file
      └─ reference/                 # project design docs (sim stack, dev plan, hardware)
```

The intended monorepo layout for the actual build (from the development plan) is `docker/`,
`ros2_ws/src/{perception,planning,vlm_client,...}`, `sim/{gazebo,isaac,ue5}`, `vlm/`, plus a
`versions.lock`. That code does not exist yet — this workbench is currently at **Phase 0
(environment & version lock)** of the plan.

---

## 6. Ground rules for an agent working here

1. **Install into the container, never the host.** The host OS is immutable; `sudo` on the
   host needs a password you do not have. Do package installs inside `drone-sim`.
2. **Respect the GPU split.** Render → GPU 0 (3080). Infer → GPU 1 (5060 Ti). Do not put the
   Isaac Sim renderer on the Blackwell card.
3. **Do not change the Docker storage driver or the CDI setup** unless GPU-in-Docker is
   already broken — both are load-bearing workarounds, documented in §4.
4. **Big files go on the 7 TB external drive**, not the 279 GB internal NVMe.
5. **Read `reference/` before making architecture decisions.** Version coupling
   (Pegasus↔PX4, Isaac↔ROS 2 Python, px4_msgs↔firmware) is the dominant project risk and is
   spelled out there.
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
claude --version                                # agent CLI present?
```
