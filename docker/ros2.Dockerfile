# The ROS 2 / companion-computer image — the PX4 base plus what the AirSim wrapper needs.
#
# WHY THIS IS A SEPARATE IMAGE, AND NOT JUST THE BASE
# ---------------------------------------------------
# Role separation, and it is the same split the hardware will have: PX4 runs on the flight
# controller, the uXRCE-DDS agent and every ROS 2 node run on the companion computer. Here
# that is `sim-px4` against `sim-ros2`. Keeping them one artifact works right up until the
# Jetson, at which point the two halves have to be separable anyway.
#
# WHAT THIS IMAGE USED TO CARRY, AND WHY IT NO LONGER DOES
# --------------------------------------------------------
# It installed `ros-gz-bridge` to publish /clock from a Gazebo world. That bridge has no
# consumer now: /clock comes from the simulator itself, remapped in
# `ros2_ws/src/bringup/launch/perception.launch.py` (`/airsim_node/clock` -> `/clock`).
#
# The removal is worth more than the disk it frees. `ros-gz-bridge` pulls
# `gz_transport_vendor`, which installs its own libgz-transport13 and puts it on
# LD_LIBRARY_PATH whenever ROS is sourced, shadowing any system Gazebo and silently
# breaking the `gz` CLI for every process that sources ROS. That coupling is gone with it.
# The COMPANION COMPUTER image: ROS 2 Jazzy, the uXRCE-DDS agent, px4_msgs/px4_ros_com, and
# the AirSim wrapper's build dependencies.
#
# It used to be `FROM drone-sim/px4`, which made sense while the agent lived in the PX4 image.
# PR 35 moved the agent here -- it is companion plumbing, not an autopilot concern -- and
# SIM-19's audit then showed the PX4 image had no remaining use for ROS at all. So the
# inheritance is inverted rather than kept: ROS, the agent and px4_msgs all belong on this
# side, and px4 became a 466 MB SITL-only image.
#
# There is deliberately NO shared base image. The original SIM-19 sketch proposed one, on the
# premise that px4 and ros2 share ROS 2 and px4_msgs. After the agent moved, they share
# nothing -- px4 needs neither.
FROM ubuntu:24.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

ARG XRCE_TAG=v2.4.3
ARG XRCE_SHA=73622810d984349b80bbac0ef55fc0b694d62222
ARG PX4_MSGS_BRANCH=release/1.16
ARG PX4_MSGS_SHA=392e831c1f659429ca83902e66820d7094591410
ARG PX4_ROS_COM_BRANCH=release/1.16
ARG PX4_ROS_COM_SHA=86e9aeb20e55a4673fa8a9f1c29ea06a6c5ad1af
ARG ROS_DISTRO=jazzy

# Base tooling. `sudo` is not needed here (that was PX4's setup script); cmake/ninja/git are,
# because the XRCE agent is built from source below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg lsb-release locales \
        build-essential cmake ninja-build python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

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

ENV ROS_DISTRO=${ROS_DISTRO}

# --- the AirSim ROS 2 wrapper's build dependencies ------------------------------------

# The Cosys-AirSim ROS 2 wrapper's dependencies, baked in rather than apt-installed on
# every bring-up by scripts/build_airsim_wrapper.sh.
#
# Baking them in is not just speed: the bring-up script installs them INSIDE a running
# container, so the dependency lived only in that container's writable layer and vanished
# on every teardown — a network outage between two runs turned a working stack into a
# build failure. python3-msgpack is the RPC client's only dependency and is needed by the
# settle-wait even when the wrapper is never built.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-geographic-msgs \
        ros-${ROS_DISTRO}-mavros-msgs \
        ros-${ROS_DISTRO}-compressed-image-transport \
        python3-msgpack \
        patch \
    && rm -rf /var/lib/apt/lists/* \
    # Assert on the artifacts, not on apt's exit status: a package that installs but ships
    # no headers fails at the wrapper's colcon build, twenty minutes later and one layer up.
    && test -d /opt/ros/${ROS_DISTRO}/include/geographic_msgs \
    && test -d /opt/ros/${ROS_DISTRO}/include/mavros_msgs \
    && python3 -c "import msgpack" \
    && echo "airsim-wrapper deps (geographic_msgs mavros_msgs msgpack)" \
       >> /etc/drone-sim-versions

# compressed_image_transport, and an assertion that the PLUGIN REGISTERS rather than that apt
# was happy. Installed because raw imagery does not survive a WAN and JPEG does -- measured
# over a NetBird overlay (MTU 1280), same link, renderer verified alive throughout:
#
#   WAN, raw    ~10 Hz telemetry, ~0 images        (900 KB fragments into ~768 pieces)
#   WAN, JPEG   93.8 Hz telemetry, 16.4 Hz images, 1.7 Mbit/s
#
# Raw does not merely fail: the flood of unreassemblable fragments starves the small messages
# sharing the transport, so a remote client that subscribes to a camera loses 90% of its
# TELEMETRY and gets no pictures for it.
#
# This package IS the feature, not a helper for one. `airsim_node` publishes through
# image_transport, which advertises only the transports whose plugins it can load -- so with the
# package absent there is no `<base>/compressed` topic at all, and with it present every camera
# gains one automatically, lazily (nothing is encoded until something subscribes). Measured
# natively, no bridge process involved:
#
#   q95 (default)  15.13 Hz  32.9 KB  4.08 Mbit/s
#   q70            17.85 Hz  12.8 KB  1.87 Mbit/s      <- vs raw 900.0 KB, 111.90 Mbit/s
#
# Quality is a runtime parameter, so tuning it needs no rebuild:
#   ros2 param set /airsim_node \
#     airsim_node.PX4.front_center_Scene.image.compressed.jpeg_quality 70
#
# Without the package `ros2 run image_transport republish` also aborts with
# "image_transport/compressed_pub does not exist. Declared types are image_transport/raw_pub",
# which is a runtime failure a build-time apt check would not have caught -- the same shape as
# the QGC image passing `test -x` on a binary that could not link.
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && ros2 run image_transport list_transports 2>/dev/null | grep -q 'image_transport/compressed' \
    && echo "compressed_image_transport plugin registers" \
       >> /etc/drone-sim-versions

# A login shell sources ROS, so `docker exec sim-ros2 bash -lc 'ros2 topic list'` works
# without every caller repeating the source lines. `docker exec` without `-l` bypasses this
# and reports 0 topics on a perfectly healthy stack — which has cost real debugging time
# here, so the profile script is baked in rather than bind-mounted by a compose file that
# no longer exists.
COPY docker/ros-profile.sh /etc/profile.d/10-ros.sh
RUN chmod 0644 /etc/profile.d/10-ros.sh
