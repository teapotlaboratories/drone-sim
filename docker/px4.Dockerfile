# syntax=docker/dockerfile:1
#
# PX4 v1.16 SITL + ROS 2 Jazzy + uXRCE-DDS — the autopilot half of the simulator.
#
# The renderer runs Unreal; PX4 runs here. They meet over the Simulator MAVLink API on TCP
# 4560, and autonomy reaches PX4 as `/fmu/*` over uXRCE-DDS — the same surface a real
# Pixhawk 6C exposes, which is what makes the transport swap the only sim-to-real boundary.
#
# This image is also the BASE of docker/qgc.Dockerfile and docker/ros2.Dockerfile, and it
# supplies both the PX4 SITL container and the uXRCE-DDS agent container in
# scripts/sim_up.sh. Retagging it is never a one-file change.
#
# Reproduces EXACTLY the stack that was installed natively and smoke-tested on
# 2026-07-28 (see docs/worklog/2026-07-28-phase-0-lane-a-install.md). Every pin below
# is a SHA that was actually built and passed a smoke test — not a value copied from
# documentation.
#
# THREE OF THESE PINS DEVIATE FROM THE REFERENCE DOCS. A Dockerfile written from
# docs/history/reference/02_development_plan.md instead of from evidence produces a BROKEN
# image:
#
#   1. Micro-XRCE-DDS-Agent is v2.4.3, NOT the plan's v2.4.2. v2.4.2 cannot be built at
#      all here: its superbuild pins Fast-DDS by BRANCH (2.12.x) which eProsima deleted,
#      and the 2.12 line does not compile on GCC 13+ ("'uint8_t' in namespace 'std' does
#      not name a type"). Built against system Fast-DDS, v2.4.2 segfaults during DDS
#      entity creation. See vendor/Micro-XRCE-DDS-Agent/LOCAL_PATCHES.md.
#   2. px4_ros_com is branch-matched to release/1.16. The plan's setup snippet clones it
#      with no branch (i.e. main) while branch-matching px4_msgs — a latent version skew.
#   3. `make px4_sitl_default`, never `make px4_sitl <target>_default`.
#
# Build:  docker build -f docker/px4.Dockerfile -t drone-sim/px4:v1.16.0 .
# Run:    docker run --rm -it drone-sim/px4:v1.16.0
#
# No GPU required — PX4 SITL is CPU-bound and headless.

FROM ubuntu:24.04 AS base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

# --- pins (all verified 2026-07-28) --------------------------------------------------
ARG PX4_TAG=v1.16.0
ARG PX4_SHA=6ea3539157ca358c70a515878b77077af7d4611d
ARG XRCE_TAG=v2.4.3
ARG XRCE_SHA=73622810d984349b80bbac0ef55fc0b694d62222
ARG PX4_MSGS_BRANCH=release/1.16
ARG PX4_MSGS_SHA=392e831c1f659429ca83902e66820d7094591410
ARG PX4_ROS_COM_BRANCH=release/1.16
ARG PX4_ROS_COM_SHA=86e9aeb20e55a4673fa8a9f1c29ea06a6c5ad1af
ARG ROS_DISTRO=jazzy

# --- base tooling --------------------------------------------------------------------
# `sudo` is required because PX4's Tools/setup/ubuntu.sh calls it unconditionally; as root
# it is a no-op passthrough, but the binary must exist.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git sudo gnupg lsb-release locales \
        build-essential cmake ninja-build python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# --- ROS 2 Jazzy via the ros2-apt-source .deb ----------------------------------------
# The old apt-key method was retired 2025-06-01 and will fail.
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository -y universe \
    && ROS_APT_SOURCE_VERSION="$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
         | grep -F '"tag_name"' | awk -F\" '{print $4}')" \
    && echo "ros-apt-source ${ROS_APT_SOURCE_VERSION}" \
    && curl -fsSL -o /tmp/ros2-apt-source.deb \
         "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo $VERSION_CODENAME)_all.deb" \
    && apt-get install -y /tmp/ros2-apt-source.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-desktop ros-dev-tools \
    && rm -f /tmp/ros2-apt-source.deb \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /opt/ros/${ROS_DISTRO}/setup.bash

# Record what apt actually resolved, so the image can be compared against the native run.
RUN dpkg -s ros-${ROS_DISTRO}-desktop | awk '/^Version:/{print "ros-desktop " $2}' > /etc/drone-sim-versions \
    && source /opt/ros/${ROS_DISTRO}/setup.bash \
    && python3 -c "import rclpy" \
    && echo "rclpy import OK on $(python3 --version)"

# --- PX4 v1.16.0 ----------------------------------------------------------------------
# Tools/setup/ubuntu.sh installs the NuttX toolchain and, by default, Gazebo Harmonic.
#
# NuttX is kept deliberately: real Pixhawk 6C firmware is flashed from this same tree, and
# --no-nuttx would slim the image while silently removing that capability.
#
# --no-sim-tools drops Gazebo. The renderer is Unreal, reached over the Simulator MAVLink
# API, so nothing here ever loads a gz world. PX4's gz consumers are all
# `if(gz-transport_FOUND)`-guarded while AirSim's transport (simulator_mavlink) is selected
# unconditionally, so px4_sitl_default configures and links without them.
#
# --no-sim-tools ALSO drops a handful of packages that are not Gazebo — bc, libeigen3-dev,
# protobuf-compiler, pkg-config, libxml2-utils. They are reinstated explicitly below rather
# than left to chance: they are cheap, several are genuine build inputs, and discovering
# which ones matter from a failed 30-minute build is the expensive way to find out.
# Deliberately NOT reinstated: gz-harmonic, gstreamer*, libopencv-dev, libunwind-dev,
# cppzmq-dev, dmidecode, libimage-exiftool-perl — the Gazebo rendering/video stack.
# =======================================================================================
# STAGE: firmware -- the FULL PX4 tree plus the NuttX/ARM toolchain.
#
# This stage exists so real Pixhawk 6C firmware can still be built from the same tree the
# simulator flies. It is NOT what runs: `docker build --target firmware` gets you a
# flashing-capable image, while the default build copies only the SITL output out of it.
#
# MEASURED, and the whole reason for the split: the toolchain and source tree are ~5.4 GB
# that no simulator container ever reads.
#   /usr/lib/arm-none-eabi   2.4 GB      libstdc++-arm-none-eabi-newlib  2014 MB
#   /opt/px4 source          2.5 GB      (2.9 GB total, of which build/ is only 377 MB)
# (openjdk-21, 286 MB, is NOT in that list: it comes from ROS in the base stage, not from
#  PX4 -- see the note by the runtime assertion below.)
# =======================================================================================
FROM base AS firmware

WORKDIR /opt/px4
RUN git clone --recursive --branch ${PX4_TAG} https://github.com/PX4/PX4-Autopilot.git . \
    && test "$(git rev-parse HEAD)" = "${PX4_SHA}" \
    && echo "PX4 SHA verified: ${PX4_SHA}" \
    && echo "px4 ${PX4_TAG} ${PX4_SHA}" >> /etc/drone-sim-versions

RUN bash Tools/setup/ubuntu.sh --no-sim-tools \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bc libeigen3-dev protobuf-compiler pkg-config libxml2-utils \
    && rm -rf /var/lib/apt/lists/* \
    && echo "px4-deps no-sim-tools (no gazebo)" >> /etc/drone-sim-versions

# ASSERT THE STRIP, do not assume it. `apt-get install` of a metapackage elsewhere in the
# chain could pull Gazebo back in without anyone noticing, and an image that quietly regrows
# a rendering stack it does not use is the reproducibility failure this project keeps
# re-learning. MEASURED: 11.6 GB with Gazebo, 11.0 GB without.
RUN ! dpkg -s gz-harmonic >/dev/null 2>&1 \
    && ! command -v gz >/dev/null 2>&1 \
    && echo "gazebo confirmed absent"

# Build the SITL target. Do NOT append _default to the make target.
RUN make px4_sitl_default \
    && test -x build/px4_sitl_default/bin/px4 \
    && echo "px4_sitl built"

# =======================================================================================
# STAGE: runtime -- what actually ships. Back to `base`, so the toolchain, the source tree
# and openjdk are all left behind in `firmware` rather than merely deleted in a later layer
# (a later `apt purge` reclaims NOTHING: the bytes still sit in the earlier layer).
#
# ONLY build/ is copied. Verified two ways: `ldd` on the px4 binary resolves to libc,
# libstdc++, libgcc and libm alone -- all present in the base -- and the SITL runtime reads
# only paths under build/px4_sitl_default (bin/, etc/, ROMFS/), which is what
# `cd /opt/px4/build/px4_sitl_default && ./bin/px4 -s etc/init.d-posix/rcS` in sim_up.sh uses.
# =======================================================================================
FROM base AS runtime

# Carry the provenance record forward -- the firmware stage appended the PX4 SHA to it, and
# losing that would make the shipped image unable to state what it was built from.
COPY --from=firmware /etc/drone-sim-versions /etc/drone-sim-versions
COPY --from=firmware /opt/px4/build /opt/px4/build

# The bytes are gone only if nothing here re-adds them. Assert rather than trust.
RUN test -x /opt/px4/build/px4_sitl_default/bin/px4 \
    && test -f /opt/px4/build/px4_sitl_default/etc/init.d-posix/rcS \
    && ! test -d /opt/px4/src \
    && ! test -d /opt/px4/.git \
    && ! test -d /usr/lib/arm-none-eabi \
    && ! command -v arm-none-eabi-gcc >/dev/null 2>&1 \
    && echo "runtime stage: SITL present, toolchain/source/.git absent"

# openjdk is deliberately NOT asserted absent, and SIM-19's entry was wrong about it.
# MEASURED: openjdk-21-jre-headless is present in the BASE stage, before PX4 is cloned --
# it arrives via ROS (default-jre-headless <- default-jdk-headless), not via
# Tools/setup/ubuntu.sh. A stage split therefore cannot remove it, and removing it means
# answering what in the ROS toolchain actually needs a JRE. Left in place, recorded rather
# than quietly dropped from the estimate.

# --- Micro-XRCE-DDS-Agent v2.4.3 -----------------------------------------------------
# -DUAGENT_USE_SYSTEM_FASTDDS=ON uses Jazzy's Fast-DDS 2.14.6 (which is what v2.4.3
# targets) instead of superbuilding a private copy. Consequence: the binary links
# /opt/ros/jazzy/lib/libfastrtps.so.2.14, so ROS 2 MUST be sourced to run the agent —
# the entrypoint does this.
WORKDIR /opt/xrce
RUN git clone --branch ${XRCE_TAG} https://github.com/eProsima/Micro-XRCE-DDS-Agent.git . \
    && test "$(git rev-parse HEAD)" = "${XRCE_SHA}" \
    && source /opt/ros/${ROS_DISTRO}/setup.bash \
    && cmake -B build -DUAGENT_USE_SYSTEM_FASTDDS=ON -DCMAKE_PREFIX_PATH=/opt/ros/${ROS_DISTRO} \
    && cmake --build build -j"$(nproc)" \
    && cmake --install build \
    && ldconfig /usr/local/lib/ \
    && test -x /usr/local/bin/MicroXRCEAgent \
    && echo "xrce-agent ${XRCE_TAG} ${XRCE_SHA}" >> /etc/drone-sim-versions

# Assert the binary actually links — a build that "succeeds" and leaves an unresolved
# library is a real failure mode we hit natively and only caught with ldd.
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && test "$(ldd /usr/local/bin/MicroXRCEAgent | grep -c 'not found')" = "0" \
    && echo "agent linkage OK"

# --- px4_msgs + px4_ros_com, BOTH branch-matched to the firmware ---------------------
WORKDIR /ros2_ws
RUN mkdir -p src \
    && git clone --branch ${PX4_MSGS_BRANCH} https://github.com/PX4/px4_msgs.git src/px4_msgs \
    && git -C src/px4_msgs checkout ${PX4_MSGS_SHA} \
    && git clone --branch ${PX4_ROS_COM_BRANCH} https://github.com/PX4/px4_ros_com.git src/px4_ros_com \
    && git -C src/px4_ros_com checkout ${PX4_ROS_COM_SHA} \
    && echo "px4_msgs ${PX4_MSGS_BRANCH} ${PX4_MSGS_SHA}" >> /etc/drone-sim-versions \
    && echo "px4_ros_com ${PX4_ROS_COM_BRANCH} ${PX4_ROS_COM_SHA}" >> /etc/drone-sim-versions

RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && test -f install/setup.bash \
    && rm -rf build log

# --- runtime -------------------------------------------------------------------------
ENV HEADLESS=1 \
    PX4_DIR=/opt/px4 \
    ROS_DISTRO=${ROS_DISTRO}

# 18570 GCS datalink (PX4's actual GCS port) · 14550 conventional GCS listen port
# 14540 offboard · 4560 renderer<->PX4 (Simulator MAVLink API) · 8888 uXRCE-DDS
EXPOSE 18570/udp 14550/udp 14540/udp 4560/tcp 8888/udp

COPY docker/px4-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /opt/px4
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
