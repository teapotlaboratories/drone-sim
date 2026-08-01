# syntax=docker/dockerfile:1
#
# Lane A — PX4 v1.16 + Gazebo Harmonic + ROS 2 Jazzy + uXRCE-DDS
#
# Reproduces EXACTLY the stack that was installed natively and smoke-tested on
# 2026-07-28 (see docs/worklog/2026-07-28-phase-0-lane-a-install.md). Every pin below is a
# SHA that was actually built and passed a smoke test — not a value copied from
# documentation.
#
# THREE OF THESE PINS DEVIATE FROM THE REFERENCE DOCS. A Dockerfile written from
# docs/reference/02_development_plan.md instead of from evidence produces a BROKEN image:
#
#   1. Micro-XRCE-DDS-Agent is v2.4.3, NOT the plan's v2.4.2. v2.4.2 cannot be built at
#      all here: its superbuild pins Fast-DDS by BRANCH (2.12.x) which eProsima deleted,
#      and the 2.12 line does not compile on GCC 13+ ("'uint8_t' in namespace 'std' does
#      not name a type"). Built against system Fast-DDS, v2.4.2 segfaults during DDS
#      entity creation. See vendor/Micro-XRCE-DDS-Agent/LOCAL_PATCHES.md.
#   2. px4_ros_com is branch-matched to release/1.16. The plan's setup snippet clones it
#      with no branch (i.e. main) while branch-matching px4_msgs — a latent version skew.
#   3. PX4 airframe targets are gz_-prefixed (gz_x500_lidar_2d, not x500_lidar_2d).
#
# Build:  docker build -f docker/lane-a.Dockerfile -t drone-sim/lane-a:v1.16.0 .
# Run:    docker run --rm -it drone-sim/lane-a:v1.16.0
#
# No GPU required — Gazebo runs headless (server-only) and PX4 SITL is CPU-bound.

FROM ubuntu:24.04

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
        build-essential cmake ninja-build python3 python3-pip python3-venv tini \
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
        ros-${ROS_DISTRO}-ros-gz-bridge \
        ros-${ROS_DISTRO}-rosbag2-storage-mcap \
    && rm -f /tmp/ros2-apt-source.deb \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /opt/ros/${ROS_DISTRO}/setup.bash

# Record what apt actually resolved, so the image can be compared against the native run.
RUN dpkg -s ros-${ROS_DISTRO}-desktop | awk '/^Version:/{print "ros-desktop " $2}' > /etc/drone-sim-versions \
    && dpkg -s ros-${ROS_DISTRO}-ros-gz-bridge | awk '/^Version:/{print "ros-gz-bridge " $2}' >> /etc/drone-sim-versions \
    && dpkg -s ros-${ROS_DISTRO}-rosbag2-storage-mcap | awk '/^Version:/{print "rosbag2-storage-mcap " $2}' >> /etc/drone-sim-versions \
    && source /opt/ros/${ROS_DISTRO}/setup.bash \
    && python3 -c "import rclpy" \
    && echo "rclpy import OK on $(python3 --version)"

# --- PX4 v1.16.0 + Gazebo Harmonic ---------------------------------------------------
# Tools/setup/ubuntu.sh installs Gazebo Harmonic and the NuttX toolchain. NuttX is kept
# deliberately: Phase 4 flashes real Pixhawk 6C firmware from this same tree. Adding
# --no-nuttx would slim the image but silently remove that capability.
WORKDIR /opt/px4
RUN git clone --recursive --branch ${PX4_TAG} https://github.com/PX4/PX4-Autopilot.git . \
    && test "$(git rev-parse HEAD)" = "${PX4_SHA}" \
    && echo "PX4 SHA verified: ${PX4_SHA}" \
    && echo "px4 ${PX4_TAG} ${PX4_SHA}" >> /etc/drone-sim-versions

# NOTE: assert on the package, not on `gz --version` — that is not a valid invocation,
# it prints usage and exits non-zero, which fails the layer under `-o pipefail`.
RUN bash Tools/setup/ubuntu.sh \
    && dpkg -s gz-harmonic  | awk '/^Version:/{print "gz-harmonic " $2}'  >> /etc/drone-sim-versions \
    && dpkg -s gz-sim8-cli  | awk '/^Version:/{print "gz-sim8-cli " $2}'  >> /etc/drone-sim-versions \
    && rm -rf /var/lib/apt/lists/* \
    && grep -q gz-harmonic /etc/drone-sim-versions

# Build the SITL target. Do NOT append _default to the make target.
RUN make px4_sitl_default \
    && test -x build/px4_sitl_default/bin/px4 \
    && echo "px4_sitl built"

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

# --- repository-owned ROS 2 graph ----------------------------------------------------
# Build the application packages into the image so Runpod never depends on bind mounts.
COPY ros2_ws/src/control src/control
COPY ros2_ws/src/bringup src/bringup
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && source /ros2_ws/install/setup.bash \
    && colcon build --packages-select control bringup --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && test -f install/bringup/share/bringup/launch/sim.launch.py \
    && test -x install/control/lib/control/offboard_control \
    && rm -rf build log

# --- runtime -------------------------------------------------------------------------
# GZ_IP pins Gazebo transport to loopback — multicast flooding the host network is a
# documented root cause of the Accel/Mag TIMEOUT failures (PX4/PX4-Autopilot#24595).
ENV HEADLESS=1 \
    GZ_IP=127.0.0.1 \
    PX4_DIR=/opt/px4 \
    ROS_DISTRO=${ROS_DISTRO}

# No simulation port is declared for automatic publication. Compose maps loopback ports
# explicitly; the Runpod image maps none, so MAVLink, XRCE-DDS, and Gazebo remain local.
COPY docker/lane-a-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /opt/px4
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
