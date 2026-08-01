# Lane C — Unreal Engine 5.8 + Cosys-AirSim build environment  (D-04, C-02)
#
# WHAT THIS IS: Epic's pre-built UE5.8 image plus the three tools Cosys-AirSim's build.sh
# needs and `dev-slim` does not ship. Nothing more — the engine is 54 GB and this layer is
# a few tens of MB.
#
# WHY IT EXISTS AT ALL: `./build.sh --ue-root ...` fails in one second on the stock image
# with an empty CMAKE variable, because `which cmake` finds nothing. Enumerated the rest up
# front rather than one crash at a time:
#
#   needed by build.sh/setup.sh : clang cmake gcc make rsync unzip wget zip
#   already in dev-slim         : make unzip zip gcc g++ git curl python3 tar patch pkg-config
#   MISSING, added here         : cmake rsync wget
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
# four patch releases. Same rule as `lane-c-sha-not-branch`, applied to an image.
#
# ACCESS: needs EpicGames GitHub org membership plus a PAT with read:packages. That is a
# known, permanent gap against "a fresh machine reaches a working stack from the repo
# alone" — see docs/docker/todo.md D-04. Log in before building:
#   gh auth token | docker login ghcr.io -u <user> --password-stdin
FROM ghcr.io/epicgames/unreal-engine@sha256:daac02628ea880513e18ccd1364b1cac949d40609b24c040d73872d8214a0c46

# Late layer, and root only for the install. Grouping by rate of change rather than by
# subject is a lesson this repo already paid for: putting a cheap package near the top of
# lane-a.Dockerfile invalidated the 20-40 minute PX4 build below it.
USER root

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      cmake \
      rsync \
      wget \
 && rm -rf /var/lib/apt/lists/*

# Assert the ARTIFACTS, not that the script reached its end (D-01). A build script's own
# success banner has lied in this repo before: one printed "BUILD OK" while make had exited
# 2. Fail the layer here rather than one second into a plugin build.
RUN set -eux; \
    command -v cmake; command -v rsync; command -v wget; \
    test -x "$(command -v cmake)"; \
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
    for v in "$ue_engine" "$ue_toolchain" "$base_os" "$cmake_v" "$rsync_v"; do \
      test -n "$v" || { echo "FATAL: a version probe produced an empty value" >&2; exit 1; }; \
    done; \
    printf 'ue_engine=%s\nue_toolchain=%s\nbase_os=%s\ncmake=%s\nrsync=%s\n' \
      "$ue_engine" "$ue_toolchain" "$base_os" "$cmake_v" "$rsync_v" \
      > /etc/drone-sim-lane-c-versions

USER ue4
WORKDIR /src

# Build the Cosys-AirSim plugin by mounting the vendored tree at /src:
#   docker run --rm -v "$PWD/vendor/Cosys-AirSim:/src" drone-sim/lane-c:ue5.8 \
#     bash -lc './build.sh --ue-root /home/ue4/UnrealEngine'
CMD ["bash", "-lc", "cat /etc/drone-sim-lane-c-versions"]
