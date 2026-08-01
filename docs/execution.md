# Execution environments

This is the contributor-neutral authority for where Drone Sim can run. The original
contributor still has the workstation described in [`bench.md`](bench.md); access to that
machine is contributor-specific and must never be assumed for another contributor. Fern on
Runpod is the portable alternative for contributors without that access.

## Current execution model

| Work | Environment | GPU assumption |
|---|---|---|
| off-target tests, lint, workflow checks | any contributor host or GitHub-hosted runner | none |
| Lane A PX4 + Gazebo SITL | contributor workstation/container, or Runpod CPU Pod through Fern | none |
| Lane B/C or VLM GPU work | original contributor workstation when available to that contributor, or Runpod GPU Pod through Fern | measured per environment; never assumed repo-wide |

The original workstation and its hardware assessment remain valid evidence for that
contributor. New code, paths, capacity plans, and acceptance criteria must also state a
portable Fern/Runpod path when they would otherwise require access to that private machine.

## Lane A cloud contract

The Runpod path is deliberately a batch workload:

- one immutable OCI image contains PX4, Gazebo, the XRCE agent, ROS 2 packages, the smoke
  harness, and the runtime wrapper;
- Fern creates the billable Pod, with `--dry-run` required before `--yes`;
- no MAVLink, XRCE-DDS, Gazebo, health, or control port is mapped publicly;
- Fast DDS uses loopback UDP because Runpod does not expose Docker's `--shm-size` option;
- each run writes atomically under `/workspace/runs/<run-id>/`;
- the runner requests a Pod stop after terminal status and idles instead of rerunning if
  that stop cannot be accepted; Fern remains the external cleanup authority;
- credentials are environment-only and never enter request, status, logs, metrics, or
  image layers.

The durable run contract is:

```text
/workspace/runs/<run-id>/
├── request.json
├── status.json
├── ready
├── metrics.json
├── logs/
│   ├── preflight.log
│   ├── runtime-api.log
│   ├── smoke.log
│   ├── px4.log
│   ├── agent.log
│   └── rosbag.log
└── artifacts/
    ├── preflight.json
    ├── versions.txt
    └── lane-a-mcap/
```

`status.json` is the authority for lifecycle state. `ready` means ROS graph discovery and
moving PX4 telemetry passed; it does not mean the five-minute acceptance run has finished.
`metrics.json` and the MCAP artifact are written before the terminal `succeeded` status.

## Publish the image

Pushes to `main` run `.github/workflows/drone-sim-image.yml`; the temporary
`feat/image-builder` trigger validates the same build before merge. It publishes:

```text
ghcr.io/teapotlaboratories/drone-sim:stack-<main-sha>
ghcr.io/teapotlaboratories/drone-sim:stack-main
```

The workflow uploads `image-manifest.json` and prints the canonical digest reference. Fern
must use that digest, not the moving channel tag. The workflow authenticates with its
repository `GITHUB_TOKEN`; making the package public is a separate maintainer decision. If
the package remains private, configure the corresponding Runpod registry credential before
deploying it.

## Deploy through Fern

Install the released CLI, or run it from the sibling source checkout:

```bash
bun install --global @coolcmyk/fern
fern --help

# Source-checkout equivalent:
cargo run --manifest-path ../fern/Cargo.toml -- --help
```

Keep the Runpod credential outside the repository:

```bash
export RUNPOD_API_KEY='<runpod-api-key>'
fern config check
```

Use the digest emitted by the successful `Drone Sim stack image` workflow:

```bash
IMAGE='ghcr.io/teapotlaboratories/drone-sim@sha256:<digest>'

fern deploy --profile drone-sim-stack --image "$IMAGE" --duration 300 --dry-run
fern deploy --profile drone-sim-stack --image "$IMAGE" --duration 300 --yes
fern pod list --compute cpu
fern pod get <pod-id>
fern pod stop <pod-id>
```

The source-checkout form is identical after replacing `fern` with:

```bash
cargo run --manifest-path ../fern/Cargo.toml --
```

Fern currently provides deploy, list, get, and stop. Until wait/download/destroy land in
Fern, inspect terminal status and durable artifacts through the provider's persistent
workspace, and always issue `fern pod stop <pod-id>` as an external cleanup check.

## Preflight and cost safety

Before PX4 starts, the image records and validates:

- CPU count and total memory;
- writable `/workspace` plus free persistent storage;
- `/dev/shm` capacity appropriate to the selected DDS transport;
- every simulator/control port available on loopback;
- executable PX4 and discoverable XRCE agent binaries;
- GPU inventory when present, without requiring one for Lane A.

A failed preflight writes a terminal failure and never starts the simulator. Pod creation is
billable, so use `--dry-run`, record the Pod ID, set a cost alert in Runpod, and stop the Pod
from Fern even when the in-container self-stop reports success.

## Validation boundary

Off-target contract tests can run before merge. The publish workflow and a fresh Runpod
five-minute smoke cannot run against this image until the PR reaches `main`. A PR must say
