# QGroundControl — the stack's ONLY MAVLink-over-IP client.
#
# ARCHITECTURE RULE
# -----------------
# QGC is the one component that speaks MAVLink over IP. Everything else — the offboard
# controller, the scenario runner, the eval harness — talks ROS 2 over uXRCE-DDS. That
# split is what keeps sim and real identical: on the aircraft the only MAVLink link is the
# telemetry radio to a ground station, and every other consumer is on the ROS graph.
#
# It is also load-bearing, not cosmetic: PX4 refuses to arm without a GCS datalink
# (NAV_DLL_ACT=2, set by the x500 airframe at 4001_gz_x500:51). That check is deliberately
# left ENFORCED, so QGC is a functional dependency of any flight, not an optional viewer.
#
# WHY THIS IS SEPARATE FROM docker/demo/lane-a-video.Dockerfile
# ------------------------------------------------------------
# The demo image adds ffmpeg, xterm, openbox and xdotool to record videos. None of that is
# needed to hold a datalink. This image carries only what QGC needs to run, so the core
# stack does not depend on demo tooling.
#
# QGC ITSELF IS NOT BAKED IN (180 MB). Bind-mount it:
#   -v ./vendor/tools/QGroundControl.AppImage:/qgc.AppImage:ro
FROM drone-sim/lane-a:v1.16.0

# xvfb        — a virtual display; QGC is a Qt GUI app and will not start without one,
#               even when nobody is looking at it. This service OWNS the display; the
#               `recording` service (D-02c) shares it through the `xsock` volume.
# openbox     — a window manager. Qt apps misbehave without one, and nothing can be
#               tiled for a recording.
# libfuse2t64 — AppImages are FUSE mounts. Without it the AppImage fails to self-mount;
#               we pass --appimage-extract-and-run to sidestep that, but the library is
#               still needed for the Qt platform bits.
# libxkbcommon-x11-0, libxcb-* — Qt's xcb platform plugin. Missing these produces
#               "could not load the Qt platform plugin xcb", which reads like a QGC bug.
# fonts-dejavu-core — without a font QGC renders blank boxes.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        xvfb libfuse2t64 fonts-dejavu-core openbox x11-utils \
        libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
        libxcb-xkb1 libpulse0 libspeechd2 \
    && rm -rf /var/lib/apt/lists/* \
    && echo "qgc runtime (xvfb + Qt xcb deps)" >> /etc/drone-sim-versions

# QGC REFUSES TO RUN AS ROOT — it exits with a dialog. Discovered the hard way while
# recording the Phase 0 demo video.
RUN useradd -m -s /bin/bash qgcuser \
    && mkdir -p /home/qgcuser/tmp \
    && chown -R qgcuser:qgcuser /home/qgcuser

COPY docker/qgc-entrypoint.sh /usr/local/bin/qgc-entrypoint.sh
RUN chmod +x /usr/local/bin/qgc-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/qgc-entrypoint.sh"]
