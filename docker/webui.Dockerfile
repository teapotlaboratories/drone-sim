# THE GROUND STATION image — rosbridge, web_video_server, and the page.  (SIM-45, SIM-46)
#
# WHY THIS IS A SEPARATE IMAGE AND A SEPARATE CONTAINER
# -----------------------------------------------------
# `SIM-45` put all of this inside `sim-ros2`. That was right for a SITL-only survey tool and
# wrong for what this repo claims: on real hardware the ROS 2 graph runs on the companion
# computer and a ground-station web page does not — it runs on someone's laptop. `sim-qgc` has
# modelled that same boundary since the Gazebo era, and this is its sibling.
#
# It is also a safety improvement, not only a fidelity one. `rosbridge_suite` is a GENERAL
# ROS-to-websocket bridge; hosting one on the companion computer is exactly what hard stop 1 in
# CLAUDE.md is about. Here it sits on the far side of a boundary, with a publish allowlist of
# one topic (ros2_ws/src/webui/webui/allowlist.py).
#
# WHAT IS DELIBERATELY ABSENT, AND WHAT IS NOT
# ---------------------------------------------
# No AirSim RPC client, no msgpack, no Cosys-AirSim wrapper, and none of our own nodes. The
# chase camera — the one part of the web interface that reads the renderer's screen — stays on
# the simulator side in the `chase_camera` package, because a real aircraft has no chase camera.
#
# **ffmpeg IS PRESENT, and that is not a mistake — it is upstream's.** `ros-jazzy-web-video-server`
# carries a hard `Depends: ffmpeg`, so it cannot be removed without removing the server this
# image exists to run. The first draft asserted its absence and the build refused, correctly.
#
# The boundary therefore does NOT rest on ffmpeg being missing. It rests on there being no node
# here that opens a display, and no client that can dial the AirSim RPC — which is what the
# assertion below actually checks. A claim narrowed to what is true is worth more than a
# stronger one that has to be forced.
#
# ros-base, NOT desktop. The GCS needs rclpy, the message runtime and the two upstream nodes; it
# does not need RViz, Gazebo tooling or the demo packages `desktop` drags in.
#
# IT DOES NEED A COMPILER, though, which ros-base does not carry. `drone_interfaces` is a
# rosidl package built from the repo at bring-up -- the same source both sides build, so the
# interface contract cannot drift between the companion and the ground station -- and the first
# cut of this image had neither gcc nor make. colcon reported
#
#     CMake Error: CMAKE_C_COMPILER not set, after EnableLanguage
#
# and the container came up with px4_msgs working (24 /fmu/out topics visible) and
# MissionCommand missing, so the browser could have read everything and published nothing.
FROM ubuntu:24.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

ARG ROS_DISTRO=jazzy

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release locales software-properties-common \
    && add-apt-repository -y universe \
    && ROS_APT_SOURCE_VERSION="$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
         | grep -F '"tag_name"' | awk -F\" '{print $4}')" \
    && curl -fsSL -o /tmp/ros2-apt-source.deb \
         "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo $VERSION_CODENAME)_all.deb" \
    && apt-get install -y /tmp/ros2-apt-source.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-ros-base \
        ros-${ROS_DISTRO}-rosbridge-suite \
        ros-${ROS_DISTRO}-web-video-server \
        python3-colcon-common-extensions python3-colcon-mixin \
        build-essential cmake \
        ros-${ROS_DISTRO}-rosidl-default-generators \
    && rm -f /tmp/ros2-apt-source.deb \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /opt/ros/${ROS_DISTRO}/setup.bash

# ASSERT ON ARTIFACTS, NOT ON apt's EXIT STATUS — the rule the other images here follow.
#
# `source` BEFORE `ldd`. A RUN gets `bash -o pipefail -c`, not a login shell, so /etc/profile.d
# never runs and ROS is not on LD_LIBRARY_PATH; without it ldd reports three ROS libraries "not
# found" on a perfectly healthy binary. That cost a build during SIM-45, and the reason it was
# missed is that the by-hand check used `docker exec … bash -lc`, which DOES source ROS.
# Verify in the shell the thing actually runs in.
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && test -x /opt/ros/${ROS_DISTRO}/lib/rosbridge_server/rosbridge_websocket.py \
    && test -x /opt/ros/${ROS_DISTRO}/lib/web_video_server/web_video_server \
    && test "$(ldd /opt/ros/${ROS_DISTRO}/lib/web_video_server/web_video_server \
                 | grep -c 'not found')" = "0" \
    # THE PUBLISH ALLOWLIST IS THE SAFETY BOUNDARY, so assert the mechanism exists in the
    # version actually installed. `webui.launch.py` sets `topics_pub_glob`; a future rosbridge
    # that renamed or dropped it would keep launching — with an unrecognised parameter and a
    # bridge that lets a browser publish anywhere, `/fmu/in/vehicle_command` included. Silent,
    # and exactly backwards from the intent.
    && grep -q "topics_pub_glob" \
         /opt/ros/${ROS_DISTRO}/lib/python3.12/site-packages/rosbridge_library/capabilities/publish.py \
    && grep -q "topics_pub_glob" /opt/ros/${ROS_DISTRO}/lib/rosbridge_server/rosbridge_websocket.py \
    && dpkg-query -W -f='ros-base ${Version}\n' ros-${ROS_DISTRO}-ros-base > /etc/drone-sim-versions \
    && dpkg-query -W -f='rosbridge-suite ${Version}\n' ros-${ROS_DISTRO}-rosbridge-suite \
         >> /etc/drone-sim-versions \
    && dpkg-query -W -f='web-video-server ${Version}\n' ros-${ROS_DISTRO}-web-video-server \
         >> /etc/drone-sim-versions

# --- px4_msgs: THE SAME BUILT ARTIFACT, not merely the same pinned SHA ------------------
#
# The ground station has to resolve `/fmu/out/*` types to serve them to a browser, so it needs
# px4_msgs — branch-matched to the firmware, like everything else here.
#
# COPIED FROM THE COMPANION IMAGE rather than rebuilt. `versions.lock` calls version coupling
# "the architecture, not a detail": two independent builds of the same SHA satisfy that
# nominally, ONE build shared by both satisfies it literally — the GCS cannot drift from the
# companion because there is nothing to drift. It also saves a second ~15-minute message build.
#
# Multi-stage `COPY --from` is already house style (docker/px4.Dockerfile is built that way).
# The cost is a BUILD ORDER: drone-sim/ros2 must exist first. docker/README.md records it.
COPY --from=drone-sim/ros2:v1.16.0 /ros2_ws/install /ros2_ws/install

# Assert the copy is the tree we meant, and that it LOADS — a message package that unpacks but
# whose Python bindings will not import fails later, in a node, as a bare ImportError.
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && source /ros2_ws/install/setup.bash \
    && python3 -c "from px4_msgs.msg import VehicleLocalPosition, VehicleCommand; print('px4_msgs imports')" \
    && echo "px4_msgs (copied from drone-sim/ros2:v1.16.0)" >> /etc/drone-sim-versions

# THE GROUND STATION MUST NOT BE ABLE TO REACH THE SIMULATOR. Asserted, because it is the
# claim this whole image exists to make and it would rot silently otherwise.
#
# NOT asserted: the absence of ffmpeg. See the header — it is a hard Depends of
# web_video_server. What IS asserted is everything the boundary actually rests on: no AirSim
# RPC client (msgpack is that client's only dependency), no vendored Cosys-AirSim tree, and no
# chase camera. Those are ours to control, and each one is a real path if it existed.
RUN ! python3 -c "import msgpack" 2>/dev/null \
    && test ! -d /airsim_root \
    && test ! -e /opt/ros/${ROS_DISTRO}/lib/chase_camera \
    && echo "no simulator path present (no msgpack/AirSim RPC, no Cosys-AirSim, no chase camera)" \
       >> /etc/drone-sim-versions

# A login shell sources ROS and the message workspace, so `docker exec sim-webui bash -lc ...`
# works without every caller repeating the source lines. Same reasoning as the companion image:
# `docker exec` without -l bypasses this and reports 0 topics on a healthy stack.
COPY docker/webui-profile.sh /etc/profile.d/10-ros.sh
RUN chmod 0644 /etc/profile.d/10-ros.sh

WORKDIR /ros2_ws
