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

## Live Runpod checkpoint — 2026-08-02

The post-rebase image build succeeded at commit `ad80a13` and published
`ghcr.io/teapotlaboratories/drone-sim@sha256:cfdaa0b6a6c561edbe5bbee993fb138e27b29fc189f146034fca7aefb7e11500`.
After the package became public, anonymous manifest inspection passed and Fern created
GPU Pod `5hpckmhamepd5g` on one RTX 2000 Ada at $0.24/hour.

Runpod's system log proved that the image pull and container start both succeeded. Terminal
cleanup then exposed two lifecycle defects:

```text
[WARN tini] Tini is not running as PID 1 and isn't registered as a child subreaper.
Error: unknown command "pod" for "runpodctl"
curl: (22) The requested URL returned error: 403
runner: WARNING automatic stop failed for 5hpckmhamepd5g
```

Runpod injected the legacy verb-first CLI, so `runpodctl pod stop` was not accepted. Its
Pod-scoped API key also cannot call the account REST stop endpoint. The runner's final
`sleep infinity` guard worked: it did not restart the simulation or overwrite evidence.
External `fern pod stop 5hpckmhamepd5g` succeeded and ended GPU billing while preserving
`/workspace`.

The visible tail proves terminal cleanup was reached, but does not prove whether the smoke
passed; `status.json` and `metrics.json` still need retrieval. No VNC port exists: QGC
runs under Xvfb and the profile intentionally exposes no ports.

Runpod's scheduled `stopAfter` field is currently on its single-GPU GraphQL create path,
while Fern's ordered GPU fallback uses REST. This patch therefore fixes CLI compatibility
and Tini directly; provider deadline unification remains part of the larger Fern lifecycle
gate rather than being claimed here.

### Second live Pod — self-stop verified

Workflow `30750015788` rebuilt commit `de1597a` in 19m42s and published public digest
`sha256:dfb35e321ceb2ad4501fe635a57d094ddb626adf3f81c634797b5d197c4a30d6`.
Fern deployed that exact digest as Pod `8nw3t2zr6444ft` with the ordered GPU fallback
request. Runpod selected a $0.24/hour host, pulled the image, started its container, and
then recorded the Pod as `EXITED` without an external stop command.

The terminal provider log was:

```text
No screen session found.
Runpod config file not found, please run `runpodctl config` to create it
pod "8nw3t2zr6444ft" stopped
runner: Runpod stop accepted for 8nw3t2zr6444ft
```

The final two lines verify the new self-stop path. The first line is an expected second
cleanup after the smoke trap already closed `px4sitl`; GNU Screen writes that result to
stdout. The config line is also non-fatal: Runpod injects the Pod credential through the
environment, and the following provider result proves the stop succeeded. D-08b makes the
double cleanup quiet and filters only that exact config warning while preserving all other
CLI output and the real exit status.

This provider tail verifies lifecycle cleanup, not the flight result. The persistent
`/workspace/runs/<run-id>/` status and logs still require inspection before claiming that
the smoke passed.

### Retained evidence — deterministic preflight self-conflict

The clean-log image at commit `e29bc6b` rebuilt successfully in workflow `30751308051`
and published digest
`sha256:c87cfb0fd0bb02eb3e8c596ad258e36d381b9729e10cd51aee4b4257ddf98854`.
Fern Pod `1e86xizphmyrtp` pulled that exact digest and failed closed in preflight. To avoid
guessing from the provider tail, the stopped Pod was temporarily updated to Runpod's
documented SSH-capable diagnostic image. Runpod preserved the 20 GB `/workspace` volume,
and Fern stopped the diagnostic container after evidence retrieval.

The authoritative status was `failed`, exit code `2`, message `preflight failed`. The run
started at `14:37:16.547848Z` and finished at `14:37:16.831373Z`. CPU, memory, workspace,
shared-memory UDP fallback, required executables, GPU inventory, and every other declared
port passed. Only this listener failed:

```text
tcp:8080  [Errno 98] Address already in use
```

No `qgc.log`, `smoke.log`, or metrics existed, proving PX4/Gazebo/XRCE/QGC never started.
The runner itself created the conflict: it started `runtime_api.py` on loopback TCP 8080,
then asked preflight to prove TCP 8080 was unused. D-08c moves runtime API startup into the
successful preflight branch. A static ordering regression requires:

```text
preflight invocation < runtime API startup < running status
```

This fixes the deterministic self-conflict but does not claim the five-minute smoke; the
next immutable Fern run remains the authority.

### Retained evidence — QGC runtime and restart-loop gate

Workflow `30752872373` published commit `e7e25bd` as digest
`sha256:7e60b23713875d0b8abc6980fb82765c2b1a74d7803ec12703a3549ce244d6d5`.
Fern Pod `x1ulxxt7vet93w` pulled that exact digest and passed every preflight check, but
then created 24 failed run directories at roughly 17-second intervals. Each terminal
status recorded exit code `1` and `runner infrastructure failed`; each `qgc.log` reported
that `/opt/qgc/squashfs-root/AppRun` was missing.

The flattened Runpod Dockerfile downloaded `/qgc.AppImage`, while `qgc-entrypoint.sh`
correctly requires the AppImage's extracted `AppRun`. The QGC liveness check consequently
failed, and the ERR trap exited directly before the Runpod self-stop/idle finalizer. The
provider restarted the container and multiplied the failure.

D-08d extracts the checksum-verified AppImage during the build and proves `AppRun` is
executable. Preflight now validates that same path. Infrastructure failures also flow
through the shared finalizer, which writes terminal evidence, requests Runpod stop, and
idles instead of exiting. Forty-eight off-target tests pass; a fresh immutable image and
Fern Pod remain required before claiming the QGC or five-minute smoke gate.

### Retained evidence — completed sample, blocked finalization

Workflow `30754388107` built commit `ac37526` successfully in 19m24s and published
digest `sha256:81bfdfc7d7ff8b81b974ba0844d5f3941e9f03e2323dde8654040b9743c83c1d`.
The first allocation, Pod `bpvxj1wjtz8169`, never became container-ready and was stopped
after 15m51s with zero uptime. A fresh Fern allocation, Pod `z1va6emzqg0t12`, pulled the
same digest and reached the full simulation gate.

The second Pod passed every preflight check on an RTX 2000 Ada, including the extracted
QGC `AppRun`. PX4 booted in roughly 22 seconds, ROS discovered 24 `/fmu/out` topics,
vehicle-local-position telemetry moved at about 100.2 Hz, QGC stayed alive, and declared
topics produced a 30.6 MB MCAP. The prior QGC restart loop did not recur.

After the requested 300-second Gazebo sample, `stats.raw` reached roughly 499 KB but the
smoke log never advanced beyond `sampling real-time factor for 300s`. The retained status
remained `running` and no `metrics.json` existed. That localized the block to two adjacent
unbounded waits: the subscriber's post-TERM behavior and the immediately following MCAP
recorder shutdown. The Pod required bounded external Fern cleanup; its persistent volume
was inspected through the stopped Pod's diagnostic image and stopped again.

D-08e adds `--kill-after=5s` to preserve the full requested sample while bounding the
subscriber half of that shutdown boundary. Twelve focused tests, shell parsing, and a live
local TERM-ignoring-process proof pass.

### Retained evidence — MCAP recorder shutdown remains unbounded

Workflow `30756647362` built commit `31b2cb8` successfully in 19m04s and published
digest `sha256:4a6cf6bb64463404c1fc88fd2059359aec5395579d325e13c351b3cbe03d5c73`.
Fern selected an RTX 4000 Ada for Pod `nmijzwcbots6jw` at $0.28/hour. The Pod again passed
preflight, booted PX4 in roughly 22 seconds, discovered 24 topics, moved telemetry at
about 100.2 Hz, retained a 25.5 MB MCAP, and wrote roughly 498 KB of Gazebo stats.

The sampler now had a hard kill deadline, but the run still ended before result parsing:
`status.json` remained `running`, `metrics.json` was absent, and the recorder log had no
completed-shutdown record. The next call after sampling was `stop_recorder`, which sent INT
and then performed an unbounded `wait` on `ros2 bag record`. D-08f bounds graceful MCAP
flush to 15 seconds, escalates to TERM for five seconds, then uses KILL only as a last
resort and always reaps the child before parsing results.

The retained PX4 log again contains eight `vehicle_command_ack lost` lines associated
with QGC startup. Their effect on the strict zero-error gate remains visible and will be
evaluated once the recorder can return control to metrics generation.



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

The runner records success/failure before cleanup. On Runpod it probes for the current
noun-first or legacy verb-first `runpodctl` syntax and requests a self-stop without reading
the Pod-scoped API key. Known missing-local-config noise is filtered only after retaining
the provider command's output and exit status. It then idles instead of exiting into a
billable restart loop. Fern's external `pod stop` remains the cleanup check.

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

After the live lifecycle failure, D-08a added Tini subreaper mode and current/legacy
`runpodctl` compatibility. Its off-target verification passed:

```text
PYTHONPATH=ros2_ws/src/control PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
...............................................                          [100%]
47 passed in 0.16s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./scripts/run_local_ci.sh
RESULT: PASS
```

The local gate covered off-target tests, shell/Python parsing, Compose validation, worklog
renders, lock consistency, and attribution checks. The rebuilt image and second live Pod
verified self-stop; artifact inspection remains pending.

D-08b then made successful cleanup output unambiguous:

```text
PYTHONPATH=ros2_ws/src/control PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
................................................                         [100%]
48 passed in 0.32s
```

The added failure-path test proves the helper returns the provider's non-zero status.

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
