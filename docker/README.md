# `docker/` — per-service Dockerfiles + compose

**Layout — core vs test vs demo:**

| Path | Contents | Used by |
|---|---|---|
| `docker/lane-a.Dockerfile` | the shippable Lane A image, all pins SHA-verified at build | CI, everyone |
| `docker/lane-a-entrypoint.sh` | the only `COPY`'d script — sources ROS 2 + workspace | the image |
| [`../tests/`](../tests/) | `lane-a-smoke.sh` — the acceptance gate | CI |
| [`demo/`](demo/) | video/flight demos + the Xvfb-enabled derived image | humans |

**Two files reproduce the whole Lane A stack**: the Dockerfile and the entrypoint. Test and
demo scripts are **bind-mounted at run time**, not baked in, so editing one does not trigger
an 11.6 GB rebuild.

**Still missing: `docker-compose.yml`** (`D-02`). Until it exists, bring-up is a long
`docker run` with `--shm-size=2g` and several bind mounts — exactly the undocumented-flags
problem the reproducibility goal exists to remove.

Planned services (`docs/reference/02_development_plan.md:134`):

| Service | Purpose |
|---|---|
| `px4-sitl` | PX4 v1.16.x, Lane A |
| `px4-sitl-mavlink` | PX4 v1.14.3 for Pegasus, Lane B |
| `xrce-agent` | Micro XRCE-DDS Agent, UDP 8888 |
| `gazebo` | Gazebo Harmonic |
| `isaac-sim` | GPU, Python 3.11 ROS workspace |
| `ros2-ws` | Python 3.12 application nodes |
| `vlm-server` | vLLM/SGLang, GPU 1 |
| `qgc`, `foxglove`/`rviz`, `recording` | tooling |

Ports: 14550 (QGC), 14540 (offboard), 4560 (Gazebo↔PX4), 8888 (uXRCE-DDS). Shared
volumes: `/rosbags`, `/scenarios`, `/models`. Prefer `HEADLESS=1`.

**This box runs Docker nested inside a rootless-podman distrobox.** The
`fuse-overlayfs` storage driver and the `/etc/cdi-local` CDI spec are load-bearing
workarounds — do not change them unless GPU-in-Docker is already broken
(`docs/bench.md:123`).
