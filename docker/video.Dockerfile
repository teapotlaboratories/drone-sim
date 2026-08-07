# syntax=docker/dockerfile:1
#
# ffmpeg — a THIN layer on the verified PX4 base, used only to re-encode renders.
#
# Kept separate on purpose: the base image is what the stack flies on, and it should not
# carry a video toolchain just so a human can watch a flight afterwards.
#
# Build:
#   docker build -f docker/video.Dockerfile -t drone-sim/video:v1.16.0 .
#
# The base image's PX4/ROS layers are cached, so this builds in well under a minute.
#
# WHAT THIS DELIBERATELY NO LONGER CARRIES. Its ancestor also installed Xvfb, xterm,
# xdotool, openbox, imagemagick and mesa-utils, because the retired Gazebo demo rendered
# four GUI panes onto a virtual display and screen-recorded them. Nothing does that now:
# frames come from the simulator's own capture and from recorded MCAP bags, so the render
# path is offline and needs an encoder, not a window manager.

FROM ubuntu:24.04

# NOT `FROM drone-sim/px4`. ffmpeg needs nothing from the autopilot image; inheriting it cost
# ~11 GB for a codec (SIM-19).
RUN : > /etc/drone-sim-versions

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && command -v ffmpeg \
    # Assert the ENCODER, not just the binary. An ffmpeg without libx264 installs happily
    # and then fails at the one job this image exists for, at the end of a long render.
    #
    # Written to a file and grepped, NOT piped into `grep -q`. `grep -q` exits on its first
    # match and closes the pipe, ffmpeg takes SIGPIPE, and `-o pipefail` fails the layer
    # with exit 141 — a build that breaks precisely BECAUSE the assertion passed. This
    # image's ancestor carried the same warning about `gz --version`; the trap is generic
    # to asserting on a chatty command under pipefail.
    && ffmpeg -hide_banner -encoders > /tmp/encoders.txt 2>/dev/null \
    && grep -q libx264 /tmp/encoders.txt \
    && rm -f /tmp/encoders.txt \
    && echo "ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}') (libx264)" \
       >> /etc/drone-sim-versions
