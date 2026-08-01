# Fern/Runpod full-stack image and GHCR publisher

**Date:** 2026-08-01
**Branch:** `feat/image-builder`
**Scope:** Drone Sim issues #5 and #8–#14
**Safety boundary:** SITL and off-target checks only; no real aircraft or hardware transport

## Goal

Provide an optional Fern/Runpod Lane A path for contributors without access to a suitable
workstation. The original contributor retains the documented `carbonite` environment; this
work does not replace or invalidate that path. It adds a contributor-portable OCI runtime,
durable evidence contract, preflight, shared ROS bringup, and a main-branch GHCR publisher.

The requested delivery boundary is an open pull request. Nothing is merged automatically.

## Context checked before implementation

The sibling Fern repository was inspected rather than guessed from its README alone:

- `fern deploy --profile drone-sim-stack --image <ref> --duration 300 --dry-run` prints the
  billable request;
- `--yes` creates the Pod;
- the built-in Lane A profile is CPU-only, requests 8 vCPUs, maps no ports, mounts
  `/workspace`, and accepts an image override;
- current Fern lifecycle commands are deploy, list, get, and stop; wait, artifact download,
  and guarded destroy remain planned;
- Fern's earlier image wrapped an upstream Drone Sim image and forced Fast DDS to `UDPv4`
  because Runpod does not expose Docker's `--shm-size` control.

That led to two design decisions: move the wrapper contract into Drone Sim so Fern can pull
one repository-owned image, and do not claim that the still-missing Fern PR gate exists.

## Implemented topology

```text
push to main
    │
    ▼
GitHub Actions ──build/push──► GHCR digest
                                  │
                     fern deploy --image <digest>
                                  │
                                  ▼
                    Runpod CPU Pod, no mapped ports
                    ├─ PX4 + Gazebo
                    ├─ Micro XRCE-DDS Agent
                    ├─ ROS 2 graph / MCAP
                    ├─ loopback health API
                    └─ /workspace/runs/<run-id>/
```

The Runpod image derives from the verified Lane A base. It uses `tini`, copies the smoke
harness and runtime utilities, forces DDS to loopback UDP, declares no `EXPOSE`, and starts a
single fail-closed runner.

## Runtime contract

Each run creates `/workspace/runs/<run-id>/` with atomic `request.json` and `status.json`,
logs, `metrics.json`, preflight evidence, pinned versions, and a declared-topic MCAP. Secrets
are not copied into the request or status schema. A readiness marker is created only after at
least 24 PX4 output topics exist and two telemetry samples move.

Preflight checks CPU, memory, persistent disk, writable workspace, shared memory, selected
DDS transport, loopback port availability, PX4/XRCE executables, and optional GPU inventory.
A failed preflight records a terminal failure and does not start PX4.

The runner records success/failure before cleanup. On Runpod it requests a self-stop through
`runpodctl` or the provider REST API when credentials are available, then idles instead of
exiting into a billable restart loop. Fern's external `pod stop` remains the cleanup check.

## Shared ROS bringup

`ros2_ws/src/bringup` is now an installable `ament_python` package with:

- `sim.launch.py`, which bridges `/world/default/clock` to ROS `/clock`, enables
  `use_sim_time`, and launches the shared controller;
- `real.launch.py`, which launches the same node set with wall time;
- one Lane A parameter file installed with the package.

The base image installs `ros_gz_bridge` and `rosbag2-storage-mcap`, records the resolved apt
versions, and builds repository-owned `control` and `bringup` packages into the immutable
image rather than depending on runtime bind mounts.

## GHCR workflow

`.github/workflows/drone-sim-image.yml` runs on pushes to `main` and remains manually
dispatchable for pre-merge validation. It uses pinned actions, repository `GITHUB_TOKEN`
package permission, commit-SHA base/runner tags, a human-facing `stack-main` channel, and an
uploaded `image-manifest.json` containing the canonical digest reference. Fern must use the
digest, not the moving channel tag.

The base now starts from the official ROS Jazzy `ros-base` image pinned by digest instead of
reinstalling the ROS apt source and base packages from Ubuntu on every cold build. PX4,
Gazebo, Micro XRCE-DDS Agent, QGC, repository ROS packages, and the runtime wrapper remain
versioned in this repository. BuildKit's GitHub Actions cache is separated into base and
stack scopes, and the workflow reads the pushed digest from BuildKit metadata without
pulling the large image back onto the hosted runner.

Package visibility is not changed automatically. The workflow authenticates its own push
and pull; public visibility or a Runpod registry credential remains a maintainer deployment
choice.

## Multi-contributor correction

The initial issue wording treated the original workstation as unavailable. The owner
clarified that the original contributor still has it; only other contributors may lack
access. Issues #5, #6, #8, and #15 were revised in English. A full re-scan of #5–#27 found no
remaining `unavailable`, `not available`, Fern-only, or replacement language.

Repository documentation now scopes `bench.md`, its storage rules, driver result, and GPU
capacity to the original contributor. `docs/execution.md` presents Fern/Runpod as an option,
not a replacement.

## Review findings fixed during implementation

1. Preflight called `statvfs` before creating/checking `/workspace`; a missing mount path
   crashed instead of producing a structured diagnostic. Writability now runs first and has
   a regression test.
2. Runtime binaries were checked only for file existence. PX4 must also be executable.
3. Unknown port protocols silently became TCP. They now fail explicitly and are tested.
4. The runner initially omitted `set -e`, so artifact/status infrastructure failures could
   continue into simulation. It now uses an ERR trap and fail-closed lifecycle while still
   capturing the smoke pipeline's real exit status.
5. Digest selection initially trusted the first rendered `RepoDigest`. It now reconstructs
   the canonical repository digest explicitly.
6. Automatically making the GHCR package public was rejected as an unapproved visibility
   change. That mutation is not in the workflow.

7. Rebuilding ROS Jazzy from a bare Ubuntu image made every publisher run pay for stable
   upstream layers. The base now reuses the digest-pinned official ROS image and persistent
   BuildKit caches while retaining the repository-owned PX4/Gazebo/XRCE/QGC stack.
## Verification completed in this PR

```text
PYTHONPATH=ros2_ws/src/control PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
..........................................                               [100%]
42 passed in 0.15s
```

The first complete publisher run also passed:

```text
Workflow run: 30709124212
Commit:       7ded0741a9c8c7cb1c53057935cb65f3332faa6b
Result:       success
Elapsed:      22m42s
Output:       stack-7ded0741a9c8c7cb1c53057935cb65f3332faa6b
```

That run proved the flattened PX4 + Gazebo + XRCE + ROS 2 + QGC stack can build and push to
the organization's private GHCR namespace. It preceded the official-ROS-base/cache
optimization; a manually dispatched run validates that optimization before the PR opens.
