# Local patches — Micro-XRCE-DDS-Agent

Upstream: https://github.com/eProsima/Micro-XRCE-DDS-Agent
**Pinned: tag `v2.4.3` · SHA `73622810d984349b80bbac0ef55fc0b694d62222`**
Built with: `-DUAGENT_USE_SYSTEM_FASTDDS=ON -DCMAKE_PREFIX_PATH=/opt/ros/jazzy`

**Source patches: none.** The vendored tree is byte-identical to upstream `v2.4.3`. The
only deviations are a version bump and build flags, both recorded below.

---

## 1. Version bump: `v2.4.2` → `v2.4.3`

**Date:** 2026-07-28 · **Deviates from:** `docs/reference/02_development_plan.md:33`, which
pins the agent at v2.4.2.

**Why: v2.4.2 cannot be built on this platform, by either available route.**

**Route A — upstream's superbuild (the default).** Fails immediately. v2.4.2 pins its
Fast-DDS dependency by *branch* (`CMakeLists.txt:99`, `set(_fastdds_tag 2.12.x)`) and
eProsima has deleted that branch:

```
fatal: invalid reference: 2.12.x
CMake Error at .../fastdds-gitclone.cmake:49: Failed to checkout tag: '2.12.x'
```

Repointing it at the real tag `v2.12.2` (last of the 2.12 line) gets further, then fails
to **compile** on Ubuntu 24.04's GCC:

```
fastdds/src/cpp/statistics/types/typesv1.cxx:61:23:
  error: 'uint8_t' in namespace 'std' does not name a type; did you mean 'wint_t'?
```

That is the well-known missing-`<cstdint>` breakage in code predating GCC 13. Fast-DDS
2.12 is from before this toolchain, so the whole 2.12 line is a dead end on 24.04.

**Route B — v2.4.2 against system Fast-DDS 2.14.6** (`-DUAGENT_USE_SYSTEM_FASTDDS=ON`).
Builds cleanly, then **segfaults at runtime** during DDS entity creation:

```
agent: participant created → topic created → subscriber created → Segmentation fault
PX4:   ERROR [uxrce_dds_client] create entities failed: rt/fmu/out/vehicle_local_position …
       ERROR [uxrce_dds_client] session setup failed
```

Net result: zero `/fmu/out/*` topics over a full 5-minute run. Upstream's system path
nominally accepts "any 2.x" (`set(_fastdds_version 2)`), but 2.14.6 does not actually work
with the v2.4.2 agent.

**v2.4.3 is the minimal fix.** It is the very next release, and it pins Fast-DDS **2.14**
(`set(_fastdds_version 2.14)` / `set(_fastdds_tag 2.14.x)`) — the same generation ROS 2
Jazzy ships (2.14.6) and a generation that demonstrably compiles on noble, since Ubuntu
packages it. Both the "branch deleted" and the "GCC too new" problems disappear, and no
source patch is required.

## 2. Build flag: `UAGENT_USE_SYSTEM_FASTDDS=ON`

Uses ROS 2 Jazzy's `ros-jazzy-fastrtps 2.14.6` instead of superbuilding a private Fast-DDS.
Chosen because v2.4.3 targets 2.14 anyway, so the versions agree — and because it leaves
exactly **one** Fast-DDS on the machine, shared with the ROS 2 graph the agent talks DDS
to. It also avoids compiling Fast-DDS from source entirely.

**Consequence:** the binary links `/opt/ros/jazzy/lib/libfastrtps.so.2.14`, so **ROS 2 must
be sourced for the agent to run.** Fine for the current workflow (the launch scripts source
Jazzy first), but if the agent is ever run as a bare systemd unit or in an image without
Jazzy, either source the setup file in the unit or switch to the superbuild.

## Rebase notes

- If the agent is bumped to `v3.x`, it moves to Fast-DDS 3.x (`_fastdds_tag 3.x`) and the
  package name changes from `fastrtps` to `fastdds` — that is a larger change than a bump,
  and would need re-checking against whatever Fast-DDS the ROS distro ships.
- Nothing here is a source edit, so an upstream rebase carries no conflicts.
