# Unreal Engine 5.8 + Cosys-AirSim build environment  (D-04, SIM-02)
#
# WHAT THIS IS: Epic's pre-built UE5.8 image plus the three tools Cosys-AirSim's build.sh
# needs and `dev-slim` does not ship, plus the three the chase-camera capture needs
# (SIM-29). Nothing more — the engine is 54 GB and this layer is 178 MB, measured.
#
# WHY IT EXISTS AT ALL: `./build.sh --ue-root ...` fails in one second on the stock image
# with an empty CMAKE variable, because `which cmake` finds nothing. Enumerated the rest up
# front rather than one crash at a time:
#
#   needed by build.sh/setup.sh : clang cmake gcc make rsync unzip wget zip
#   already in dev-slim         : make unzip zip gcc g++ git curl python3 tar patch pkg-config
#   MISSING, added here         : cmake rsync wget
#
# THE SECOND GROUP — xvfb, ffmpeg, x11-utils — exists for SIM-29, recording the chase
# camera. `ViewMode` defaults to `FlyWithMe`, so AirSim's chase view renders on every run;
# `-RenderOffScreen` is the only reason nobody can read it. Give the engine an Xvfb display
# and ffmpeg grabs the screen at ~31 fps at 1080p, against ~13-14 Hz for simGetImages, which
# cannot frame the aircraft at all because AirSimCameraDirector has no RPC binding.
#
# THEY GO IN *THIS* IMAGE, NOT A SIDECAR, because Xvfb is not separable: Unreal resolves
# DISPLAY at process start, so the X server has to be up in this container before the engine
# launches. ffmpeg could in principle live elsewhere and grab over a shared /tmp/.X11-unix
# socket or a TCP display, but a measured control run puts encoding at 1.3 fps of 32.0
# (4.2%), so there is no isolation to buy — only a second lifecycle to keep in step.
#
# TRIMMED from the throwaway probe that proved this: `xdotool` (found the window by title;
# the capture grabs the whole screen, so nothing needs to locate it) and
# `x11-xserver-utils` (xrandr/xset — Xvfb's geometry is fixed by -screen at start).
# `x11-utils` stays for `xdpyinfo`, which is how a caller waits for the display to be up
# instead of sleeping and hoping.
#
# NOT `drone-sim/video`. That image re-encodes finished renders offline and deliberately
# dropped its Xvfb/openbox ancestry when the Gazebo demo retired; it is a post-processing
# tool and is unaffected by this.
#
# clang is deliberately NOT installed. The engine ships its own toolchain
# (Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v26_clang-20.1.8-rockylinux8) and
# build.sh's --ue-root points CC/CXX at it. Installing a system clang would create exactly
# the ABI mismatch against UE's linker that this project already documented on the UE5.5
# line — build.sh even refuses --ue-root together with --gcc, because "Unreal Engine's
# bundled toolchain is Clang-only".
#
# PINNED BY DIGEST, not tag. `dev-slim-5.8` is a moving alias — the registry shows
# dev-slim-5.5 and dev-slim-5.5.4 sharing one digest, i.e. the two-component form tracked
# four patch releases. Same rule as `sha-not-branch`, applied to an image.
#
# ACCESS: needs EpicGames GitHub org membership plus a PAT with read:packages. That is a
# known, permanent gap against "a fresh machine reaches a working stack from the repo
# alone" — see docs/docker/todo.md D-04. Log in before building:
#   gh auth token | docker login ghcr.io -u <user> --password-stdin
FROM ghcr.io/epicgames/unreal-engine@sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46

# Late layer, and root only for the install. Grouping by rate of change rather than by
# subject is a lesson this repo already paid for: putting a cheap package near the top of
# px4.Dockerfile invalidated the 20-40 minute PX4 build below it.
USER root

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      cmake \
      rsync \
      wget \
      xvfb \
      ffmpeg \
      x11-utils \
 && rm -rf /var/lib/apt/lists/*

# Assert the ARTIFACTS, not that the script reached its end (D-01). A build script's own
# success banner has lied in this repo before: one printed "BUILD OK" while make had exited
# 2. Fail the layer here rather than one second into a plugin build.
RUN set -eux; \
    command -v cmake; command -v rsync; command -v wget; \
    test -x "$(command -v cmake)"; \
    command -v Xvfb; command -v ffmpeg; command -v xdpyinfo; \
    # Assert the ENCODER, not just the binary. An ffmpeg without libx264 installs happily and
    # then fails at the one job it is here for, at the end of a long flight. Written to a file
    # and grepped rather than piped into `grep -q`: grep exits on its first match, closes the
    # pipe, ffmpeg takes SIGPIPE, and under `-o pipefail` the layer fails with 141 — a build
    # that breaks precisely BECAUSE the assertion passed. docker/video.Dockerfile carries the
    # same warning; the trap is generic to asserting on a chatty command. \
    ffmpeg -hide_banner -encoders > /tmp/encoders.txt 2>/dev/null; \
    grep -q libx264 /tmp/encoders.txt; \
    rm -f /tmp/encoders.txt; \
    # Assert the X SERVER RUNS, not that its binary exists. `command -v Xvfb` would pass on an
    # install that cannot open a screen, and the failure would surface only at sim bring-up as
    # a renderer that never draws. Start one, connect to it, tear it down. \
    Xvfb :98 -screen 0 320x240x24 & \
    xvfb_pid=$!; \
    for i in $(seq 1 40); do DISPLAY=:98 xdpyinfo >/dev/null 2>&1 && break; sleep 0.25; done; \
    DISPLAY=:98 xdpyinfo | grep -q "dimensions:.*320x240"; \
    # WAIT for it, then clear its lock. Killing without reaping lets the layer commit while
    # Xvfb is still tearing down, baking /tmp/.X98-lock and /tmp/.X11-unix/X98 into the image
    # -- after which any run using DISPLAY_NUM=98 dies with "Server is already active for
    # display 98". docker/qgc-entrypoint.sh:28-34 documents that exact trap, reached from the
    # other direction (a restarting container), and works around it the same way. \
    kill "$xvfb_pid"; \
    wait "$xvfb_pid" 2>/dev/null || true; \
    rm -f /tmp/.X98-lock /tmp/.X11-unix/X98; \
    ls -d /home/ue4/UnrealEngine/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/*/ \
      | grep -q clang; \
    test -f /home/ue4/UnrealEngine/Engine/Build/Build.version

# Record what this image actually carries, so a silent upstream repackage shows up as a
# diff rather than as a mysterious build failure.
# NOTE the `set -eux` and the non-empty assertions. The first version of this layer used an
# inline python f-string containing escaped quotes, which is a SyntaxError on this
# interpreter - and because the redirect still created the file, the layer SUCCEEDED while
# recording `ue_engine=` empty. A version-recording step that silently records nothing is
# worse than none: it looks like evidence. Same class as the build script that printed
# "BUILD OK" after make exited 2.
RUN set -eux; \
    ue_engine="$(python3 -c "import json; d=json.load(open('/home/ue4/UnrealEngine/Engine/Build/Build.version')); print('%d.%d.%d' % (d['MajorVersion'], d['MinorVersion'], d['PatchVersion']))")"; \
    ue_toolchain="$(basename "$(ls -d /home/ue4/UnrealEngine/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/*/ | head -1)")"; \
    base_os="$(. /etc/os-release; echo "$VERSION_ID $VERSION_CODENAME")"; \
    cmake_v="$(cmake --version | head -1 | awk '{print $3}')"; \
    rsync_v="$(rsync --version | head -1 | awk '{print $3}')"; \
    ffmpeg_v="$(ffmpeg -version | head -1 | awk '{print $3}')"; \
    xvfb_v="$(Xvfb -help 2>&1 | grep -oE 'X.Org X Server [0-9.]+' | awk '{print $4}' || true)"; \
    test -n "$xvfb_v" || xvfb_v="$(dpkg-query -W -f='${Version}' xvfb)"; \
    for v in "$ue_engine" "$ue_toolchain" "$base_os" "$cmake_v" "$rsync_v" "$ffmpeg_v" "$xvfb_v"; do \
      test -n "$v" || { echo "FATAL: a version probe produced an empty value" >&2; exit 1; }; \
    done; \
    printf 'ue_engine=%s\nue_toolchain=%s\nbase_os=%s\ncmake=%s\nrsync=%s\nffmpeg=%s\nxvfb=%s\n' \
      "$ue_engine" "$ue_toolchain" "$base_os" "$cmake_v" "$rsync_v" "$ffmpeg_v" "$xvfb_v" \
      > /etc/drone-sim-versions

# ---------------------------------------------------------------------------------------
# VULKAN ICD PATH COMPAT — without this, UE's renderer cannot start at all.
#
# The host is Bazzite (Fedora-family), so its CDI spec injects an ICD that names the
# Fedora library path:
#
#   /etc/vulkan/icd.d/nvidia_icd.x86_64.json -> "library_path": "/usr/lib64/libGLX_nvidia.so.0"
#
# This container is Ubuntu, where that library lives under multiarch at
# /lib/x86_64-linux-gnu/. The loader therefore cannot open the driver:
#
#   ERROR: [Loader Message] /usr/lib64/libGLX_nvidia.so.0: cannot open shared object file
#   LogVulkanRHI: Error: vpCreateInstance(...) failed, VkResult=-9   (INCOMPATIBLE_DRIVER)
#   -> UnrealEditor segfaults, exit 139
#
# A symlink makes the Fedora path resolve. Verified with vulkaninfo: before, zero usable
# devices; after, "NVIDIA GeForce RTX 3080 / DISCRETE_GPU / 1.4.341 / driver 610.43.03".
#
# NOTE this is a PATH mismatch, NOT the too-new-driver incompatibility that deferred Isaac Sim.
# The 610.43.03 driver works fine here once the loader can find it.
#
# The link target is itself a symlink that CDI creates at RUN time, so this dangles at build
# time and resolves at run time. That is intended - do not "fix" it by pointing at the
# versioned .so.610.43.03, which would break on a host driver update.
RUN mkdir -p /usr/lib64 \
 && ln -sf /lib/x86_64-linux-gnu/libGLX_nvidia.so.0 /usr/lib64/libGLX_nvidia.so.0

USER ue4
WORKDIR /src

# Build the Cosys-AirSim plugin by mounting the vendored tree at /src:
#   docker run --rm -v "$PWD/vendor/Cosys-AirSim:/src" drone-sim/unreal:ue5.8 \
#     bash -lc './build.sh --ue-root /home/ue4/UnrealEngine'
CMD ["bash", "-lc", "cat /etc/drone-sim-versions"]
