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
# WHY THIS IS SEPARATE FROM docker/video.Dockerfile
# ------------------------------------------------------------
# docker/video.Dockerfile adds ffmpeg, to re-encode renders after a flight. None of that is
# needed to hold a datalink. This image carries only what QGC needs to run, so the core stack
# does not depend on presentation tooling.
#
# QGC IS BAKED INTO THIS IMAGE, pinned and checksum-verified at build time.
#
# It used to be a 180 MB bind mount the user had to fetch first, on the reasoning that CI
# has no use for it. That reasoning was wrong: PX4 will not arm without the datalink, so
# QGC is a functional dependency of every flight, and a stack that cannot fly until someone
# runs a download script is not "reproducible from the repo alone".
#
# The pin still lives in versions.lock; the difference is that the build enforces it rather
# than a script the user has to remember. A checksum mismatch fails the BUILD, which is the
# earliest and loudest place to catch a swapped binary that decides whether the aircraft
# can arm.
FROM ubuntu:24.04

# NOT `FROM drone-sim/px4`. This image uses nothing from it -- no PX4, no ROS, no px4_msgs, no
# NuttX. It inherited them only because the PX4 image happened to be the house base, which cost
# it ~11 GB of unrelated content (SIM-19).
#
# Re-added below because they came free with the old base and are genuinely needed here: curl
# and ca-certificates for the AppImage download, and the provenance file every image appends to.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && : > /etc/drone-sim-versions

# Keep these in step with the qgroundcontrol entry in versions.lock.
ARG QGC_VERSION=5.0.8
ARG QGC_SHA256=06969c67ef58ea063def0a8271447a1cc385438c4a7df36813315b4475146737
ARG QGC_URL=https://github.com/mavlink/qgroundcontrol/releases/download/v5.0.8/QGroundControl-x86_64.AppImage

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

# Fetch QGC, VERIFY IT, and extract it once at build time.
#
# Extracting here rather than at every start is deliberate: --appimage-extract-and-run
# unpacks ~180 MB on each launch, which is wasted seconds on every single run and every
# recording. The compressed AppImage is deleted afterwards so only the extracted tree
# ships.
#
# The checksum is checked BEFORE extraction. A mismatch fails the build with the version
# and both hashes, because this binary is the arming datalink — a silent swap changes
# whether the vehicle can fly.
RUN curl -fL --retry 3 --retry-delay 2 -o /tmp/qgc.AppImage "$QGC_URL" \
    && actual="$(sha256sum /tmp/qgc.AppImage | cut -d" " -f1)" \
    && if [ "$actual" != "$QGC_SHA256" ]; then \
         echo "QGroundControl $QGC_VERSION checksum MISMATCH"; \
         echo "  expected $QGC_SHA256"; \
         echo "  actual   $actual"; \
         echo "  this binary is the arming datalink — refusing to build"; \
         exit 1; \
       fi \
    && chmod +x /tmp/qgc.AppImage \
    && mkdir -p /opt/qgc \
    && cd /opt/qgc && /tmp/qgc.AppImage --appimage-extract >/dev/null \
    && rm -f /tmp/qgc.AppImage \
    && test -x /opt/qgc/squashfs-root/AppRun \
    && echo "qgroundcontrol $QGC_VERSION $QGC_SHA256" >> /etc/drone-sim-versions

# QGC REFUSES TO RUN AS ROOT — it exits with a dialog. Discovered the hard way while
# recording the Phase 0 demo video.
RUN useradd -m -s /bin/bash qgcuser \
    && mkdir -p /home/qgcuser/tmp \
    && chown -R qgcuser:qgcuser /home/qgcuser /opt/qgc

COPY docker/qgc-entrypoint.sh /usr/local/bin/qgc-entrypoint.sh
RUN chmod +x /usr/local/bin/qgc-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/qgc-entrypoint.sh"]
