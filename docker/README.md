# `docker/` — per-service Dockerfiles + compose

**Layout — core vs test vs demo:**

| Path | Contents | Used by |
|---|---|---|
| `docker/compose.yaml` | the Lane A stack — PX4+Gazebo, XRCE agent, ROS 2, and the gate under `--profile test` | CI, everyone |
| `docker/lane-a.Dockerfile` | the shippable Lane A image, all pins SHA-verified at build | CI, everyone |
| `docker/lane-a-entrypoint.sh` | the only `COPY`'d script — sources ROS 2 + workspace | the image |
| `docker/ros-env.sh` | mounted to `/etc/profile.d/` so `compose exec` shells get a ROS env | `compose exec` |
| `docker/qgc.Dockerfile` + `qgc-entrypoint.sh` | QGroundControl headless — the datalink PX4 requires before it will arm | `qgc` |
| [`../tests/`](../tests/) | `lane-a-smoke.sh` — the acceptance gate | CI |
| [`demo/`](demo/) | video/flight demos + the Xvfb-enabled derived image | humans |

**Two files reproduce the whole Lane A stack**: the Dockerfile and the entrypoint. Test and
demo scripts are **bind-mounted at run time**, not baked in, so editing one does not trigger
an 11.6 GB rebuild.

## Bring-up

```bash
docker compose -f docker/compose.yaml up -d                          # the stack
docker compose -f docker/compose.yaml --profile test run --rm verify # the gate
docker compose -f docker/compose.yaml down -v
```

### Services today (`D-02`)

| Service | Purpose |
|---|---|
| `px4-sitl` | PX4 v1.16.0 + Gazebo Harmonic. **Owns the network and IPC namespaces** the rest join |
| `xrce-agent` | Micro XRCE-DDS Agent on UDP 8888 — the PX4↔ROS 2 bridge |
| `recording` | records the running stack flying, under `--profile record` (`D-02c`) |
| `qgc` | QGroundControl, headless on Xvfb — the **only** component that speaks MAVLink over IP, and the datalink PX4 requires before it will arm. Owns the shared display the `recording` service draws on |
| `ros2` | idles; `exec` into it, and where Phase 1 nodes will run |
| `verify` | the acceptance gate, `--profile test`; **attaches** to the running stack |

**Three constraints the compose file exists to encode** — each cost a debugging session:

- **`shm_size: 2gb` on `px4-sitl`.** Docker defaults `/dev/shm` to 64 MB; Fast-DDS delivers
  over shared memory and starves there. The joining services inherit it via the IPC namespace,
  so declaring it on them is inert.
- **`ipc: "service:px4-sitl"` on every joiner.** Sharing the *network* namespace alone gets
  you topics in `ros2 topic list` and **nothing** from `ros2 topic echo`.
- **`bash -lc` for `exec`.** `compose exec` bypasses the ENTRYPOINT, so a plain
  `exec ros2 ros2 topic list` reports 0 topics on a perfectly healthy stack.

**Ports are bound to `127.0.0.1`** — 18570 (**PX4's real GCS port**, `px4-rc.mavlink:11`),
14550 (the port a GCS conventionally listens on), 14540 (offboard), 8888 (uXRCE-DDS),
4560 (Gazebo↔PX4). MAVLink is unauthenticated; publishing 14540 on the LAN lets anyone
routable arm the vehicle. Opt in with `BIND_ADDR=0.0.0.0` if you really want that.

Volumes: `../out` → `/out` (run artifacts, at the repo root), `/scenarios` read-only,
`/rosbags` as a named volume.

### Services still planned (`docs/reference/02_development_plan.md:134`)

| Service | Purpose | Backlog |
|---|---|---|
| `qgc` | QGroundControl — needs a reachable display first | `D-02b` |
| `vlm-server` | vLLM/SGLang, GPU 1 | `D-03` |
| `isaac-sim` | GPU, Python 3.11 ROS workspace (Lane B, deferred) | — |
| `px4-sitl-mavlink` | PX4 v1.14.3 for Pegasus, Lane B | — |
| `foxglove`/`rviz` | tooling | `D-02b` |

**This box runs Docker nested inside a rootless-podman distrobox.** The
`fuse-overlayfs` storage driver and the `/etc/cdi-local` CDI spec are load-bearing
workarounds — do not change them unless GPU-in-Docker is already broken
(`docs/bench.md:123`).
