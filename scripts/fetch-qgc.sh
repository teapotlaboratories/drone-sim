#!/usr/bin/env bash
# Fetch the pinned QGroundControl AppImage into vendor/tools/.
#
# WHY THIS EXISTS
# ---------------
# QGroundControl is a FUNCTIONAL DEPENDENCY OF FLIGHT in this project, not an optional
# viewer: PX4 refuses to arm without a GCS datalink (NAV_DLL_ACT=2, set by the x500
# airframe), that check is deliberately left enforced, and QGC is the only component
# permitted to speak MAVLink over IP. Without this file the `qgc` compose service cannot
# start, so nothing can arm.
#
# It is 180 MB, so it is NOT committed — `vendor/` is git-ignored. That is fine for the
# reproducibility goal *provided the download is pinned*, which is what this script is for.
#
# WHY A VERSIONED URL AND NOT THE `latest` CHANNEL
# ------------------------------------------------
# versions.lock previously recorded the CloudFront **latest** channel:
#     https://d176tv9ibo4jno.cloudfront.net/latest/QGroundControl-x86_64.AppImage
# That is a moving target, not a pin — the same trap that made the XRCE agent's v2.4.2
# retroactively unbuildable when the branch it depended on was deleted. "A branch is not a
# pin", and neither is `latest`. This script pins an exact release and verifies its
# SHA256, so a fresh machine gets the *same* binary that was flight-tested, or a loud
# failure.
#
# Verified 2026-07-30: the pinned URL is byte-identical to the AppImage used for the
# Phase 1 flight tests (`cmp` clean, matching SHA256).
set -euo pipefail

QGC_VERSION="${QGC_VERSION:-5.0.8}"
QGC_SHA256="${QGC_SHA256:-06969c67ef58ea063def0a8271447a1cc385438c4a7df36813315b4475146737}"
QGC_URL="${QGC_URL:-https://github.com/mavlink/qgroundcontrol/releases/download/v${QGC_VERSION}/QGroundControl-x86_64.AppImage}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${DEST:-$REPO_ROOT/vendor/tools/QGroundControl.AppImage}"

verify() {
  # Compare against the pin. Returns non-zero on mismatch so callers can decide.
  local file="$1"
  local actual
  actual="$(sha256sum "$file" | cut -d' ' -f1)"
  [ "$actual" = "$QGC_SHA256" ]
}

# A DIRECTORY here is a real failure mode, not a hypothetical: if the compose bind mount
# runs while this file is missing, Docker helpfully creates a directory at this path, and
# every later run then fails confusingly. Clear it.
if [ -d "$DEST" ]; then
  echo "fetch-qgc: $DEST is a DIRECTORY (Docker creates one when a bind-mount source is"
  echo "fetch-qgc: missing). Removing it."
  rmdir "$DEST" 2>/dev/null || rm -rf "$DEST"
fi

if [ -f "$DEST" ]; then
  if verify "$DEST"; then
    echo "fetch-qgc: already present and matches the pin (QGC $QGC_VERSION) — nothing to do."
    exit 0
  fi
  echo "fetch-qgc: existing file does NOT match the pinned SHA256; re-downloading."
  echo "fetch-qgc:   expected $QGC_SHA256"
  echo "fetch-qgc:   actual   $(sha256sum "$DEST" | cut -d' ' -f1)"
fi

mkdir -p "$(dirname "$DEST")"
TMP="$(mktemp "${DEST}.XXXXXX.part")"
# Clean up the partial file on any failure, so a half-download is never mistaken for the
# real thing by a later run.
trap 'rm -f "$TMP"' EXIT

echo "fetch-qgc: downloading QGroundControl $QGC_VERSION (~180 MB)"
echo "fetch-qgc:   $QGC_URL"
curl -fL --retry 3 --retry-delay 2 --connect-timeout 30 -o "$TMP" "$QGC_URL"

if ! verify "$TMP"; then
  echo "fetch-qgc: SHA256 MISMATCH — refusing to install." >&2
  echo "fetch-qgc:   expected $QGC_SHA256" >&2
  echo "fetch-qgc:   actual   $(sha256sum "$TMP" | cut -d' ' -f1)" >&2
  echo "fetch-qgc: If you intended to move to a new QGC release, update QGC_VERSION and" >&2
  echo "fetch-qgc: QGC_SHA256 here AND in versions.lock, and re-run the flight test —" >&2
  echo "fetch-qgc: this binary is the arming datalink, so a silent swap changes flight." >&2
  exit 1
fi

# 0755 explicitly, not `chmod +x`: mktemp creates 0600, so `+x` yields 0711 — executable
# but NOT readable by other users. The qgc container copies this file before dropping to
# an unprivileged user, so 0711 happens to work, and would quietly stop working the moment
# anything reads it as non-root.
chmod 0755 "$TMP"
mv "$TMP" "$DEST"
trap - EXIT
echo "fetch-qgc: installed QGC $QGC_VERSION at $DEST"
echo "fetch-qgc: sha256 $QGC_SHA256 (verified)"
