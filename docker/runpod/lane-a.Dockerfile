# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ARG VCS_REF=unknown
ARG IMAGE_VERSION=stack
ARG IMAGE_REFERENCE=unknown
ARG QGC_VERSION=5.0.8
ARG QGC_SHA256=06969c67ef58ea063def0a8271447a1cc385438c4a7df36813315b4475146737
ARG QGC_URL=https://github.com/mavlink/qgroundcontrol/releases/download/v5.0.8/QGroundControl-x86_64.AppImage

LABEL org.opencontainers.image.source="https://github.com/teapotlaboratories/drone-sim" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.title="Drone Sim full-stack Runpod runtime"

ENV DURATION=300 \
    FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
    FERN_PROFILE=drone-sim-stack \
    FERN_WORKSPACE=/workspace \
    DRONE_SIM_REVISION=${VCS_REF} \
    DRONE_SIM_IMAGE=${IMAGE_REFERENCE}

# QGC is the enforced GCS datalink required by any full flight. Bake the pinned binary and
# its headless runtime into this single-container image; Runpod cannot supply bind mounts.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        xvfb libfuse2t64 fonts-dejavu-core openbox x11-utils \
        libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
        libxcb-xkb1 libpulse0 libspeechd2 \
    && curl -fL --retry 3 --connect-timeout 30 -o /qgc.AppImage "${QGC_URL}" \
    && echo "${QGC_SHA256}  /qgc.AppImage" | sha256sum --check - \
    && chmod 0755 /qgc.AppImage \
    && mkdir -p /opt/qgc \
    && cd /opt/qgc \
    && /qgc.AppImage --appimage-extract >/dev/null \
    && rm -f /qgc.AppImage \
    && test -x /opt/qgc/squashfs-root/AppRun \
    && useradd -m -s /bin/bash qgcuser \
    && mkdir -p /home/qgcuser/tmp \
    && chown -R qgcuser:qgcuser /home/qgcuser /opt/qgc \
    && echo "qgroundcontrol ${QGC_VERSION} ${QGC_SHA256}" >> /etc/drone-sim-versions \
    && rm -rf /var/lib/apt/lists/*

COPY tests/lane-a-smoke.sh /usr/local/bin/drone-sim-lane-a-smoke
COPY docker/runpod/artifacts.py /usr/local/lib/drone-sim/artifacts.py
COPY docker/runpod/preflight.py /usr/local/lib/drone-sim/preflight.py
COPY docker/runpod/runtime_api.py /usr/local/lib/drone-sim/runtime_api.py
COPY docker/runpod/run-lane-a.sh /usr/local/bin/run-lane-a
COPY docker/runpod/request-stop.sh /usr/local/bin/request-runpod-stop
COPY docker/qgc-entrypoint.sh /usr/local/bin/qgc-entrypoint.sh

RUN chmod 0755 \
      /usr/local/bin/drone-sim-lane-a-smoke \
      /usr/local/bin/run-lane-a \
      /usr/local/bin/request-runpod-stop \
      /usr/local/bin/qgc-entrypoint.sh \
      /usr/local/lib/drone-sim/*.py

# Health/status is intentionally loopback-only by default and Fern exposes no port for
# this profile. The image flattens the default Compose services and verification profile
# into one Pod; all simulator UDP stays inside the Pod network namespace.
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["/usr/local/bin/run-lane-a"]
