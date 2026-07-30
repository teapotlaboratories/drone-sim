# syntax=docker/dockerfile:1
#
# Lane A + video capture — a THIN layer on the verified base image.
#
# Kept separate on purpose: the base `lane-a` image is what CI runs, and it should not carry
# Xvfb/ffmpeg/X11 just so a human can watch a flight. This derived image adds only what is
# needed to render the Gazebo GUI on a virtual display and screen-record it.
#
# Build:
#   docker build -f docker/demo/lane-a-video.Dockerfile -t drone-sim/lane-a-video:v1.16.0 .
#
# The base image's PX4/ROS/Gazebo layers are cached, so this builds in ~1 minute.

FROM drone-sim/lane-a:v1.16.0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

# xvfb        — virtual X display (no GPU/display needed; same trick that verified QGC)
# ffmpeg      — x11grab screen capture -> mp4
# x11-utils   — xdpyinfo, to assert the display is actually up before recording
# imagemagick — `import` for single-frame stills
# mesa-utils  — glxinfo, to confirm software GL is available to the Gazebo GUI
# xterm       — shows the PX4 pxh console and the MAVLink script output as visible windows
# xdotool     — position/resize windows (Qt apps ignore -geometry)
# openbox     — minimal WM; without one, Qt windows misbehave and cannot be tiled
# fonts-dejavu-core — xterm renders empty boxes without a usable font
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb ffmpeg x11-utils imagemagick mesa-utils \
        xterm xdotool openbox fonts-dejavu-core libfuse2t64 \
    && rm -rf /var/lib/apt/lists/* \
    && command -v Xvfb && command -v ffmpeg && command -v xdpyinfo \
    && command -v xterm && command -v xdotool && command -v openbox \
    && echo "video tooling OK (xvfb ffmpeg xterm xdotool openbox)" >> /etc/drone-sim-versions

# QGroundControl HARD-REFUSES to run as root ("You are running QGroundControl as root...
# QGroundControl will now exit") and the container is root. Create an unprivileged user to
# launch QGC as; everything else (PX4, Gazebo, ROS) keeps running as root.
RUN useradd -m -s /bin/bash qgcuser \
    && mkdir -p /home/qgcuser/.config /home/qgcuser/tmp \
    && chown -R qgcuser:qgcuser /home/qgcuser

# Software rendering for the Gazebo GUI: Xvfb has no hardware GL.
ENV LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    DISPLAY=:99
