#!/usr/bin/env bash
# Run QGroundControl headless, as the stack's MAVLink datalink.
#
# QGC auto-discovers the vehicle: it needs no manual UDP link configuration against this
# stack, verified 2026-07-30 (PX4 reported gcs_connection_lost=false ~60 s after start,
# with nothing else speaking MAVLink).
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-:99}"
# 1920x1080 by default: this display is SHARED with the `recording` service (D-02c), and
# it is what gets captured to video. A smaller default would silently cap recording
# resolution.
RES="${RES:-1920x1080}"
# Baked into the image at build time, pinned and checksum-verified. QGC_APPRUN can point
# elsewhere to try a different build without rebuilding — the pinned one stays the default
# so a stack that comes up can always fly.
QGC_APPRUN="${QGC_APPRUN:-/opt/qgc/squashfs-root/AppRun}"

if [ ! -x "$QGC_APPRUN" ]; then
  echo "qgc: $QGC_APPRUN missing or not executable." >&2
  echo "qgc: QGroundControl is baked into drone-sim/qgc at build time, so this normally" >&2
  echo "qgc: cannot happen — rebuild the image:" >&2
  echo "qgc:   docker build -f docker/qgc.Dockerfile -t drone-sim/qgc:v1.16.0 ." >&2
  echo "qgc: Without it PX4 has no datalink and nothing can arm." >&2
  exit 1
fi

# Clear a stale X lock before starting. `restart: unless-stopped` means this entrypoint
# can rerun in a container whose /tmp still holds /tmp/.X99-lock from the previous attempt,
# and Xvfb then refuses with "Server is already active for display 99" — turning ONE
# failure into a permanent restart loop that outlives the original cause. Observed while
# testing a missing-AppImage start.
rm -f "/tmp/.X${DISPLAY_NUM#:}-lock" 2>/dev/null || true
rm -f "/tmp/.X11-unix/X${DISPLAY_NUM#:}" 2>/dev/null || true

# -ac and the extensions are NOT optional: without them the Qt/GL app dies mid-session
# with "XIO: fatal IO error 2 on X server". Cost an afternoon during the Phase 0 demo.
Xvfb "$DISPLAY_NUM" -screen 0 "${RES}x24" \
  -ac +extension GLX +extension RANDR +render -noreset -nolisten tcp \
  > /tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 3

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
  echo "qgc: Xvfb failed to start" >&2
  cat /tmp/xvfb.log >&2
  exit 1
fi

# Seed QGC's own saved window geometry so it STARTS at the size it should be.
#
# QGC persists [MainWindowState] and restores it on launch. That matters because resizing
# the window externally (xdotool) leaves it mapped, viewable and correctly positioned —
# and PAINTING NOTHING: the Qt Quick software backend gets no repaint trigger on a
# headless Xvfb with no compositor, so the pane captures as solid black while every check
# reports success. Verified: window at 960,0 / 960x540, Map State IsViewable, recorded
# black. Starting at the right geometry avoids the resize entirely.
QGC_CONF=/home/qgcuser/.config/QGroundControl
mkdir -p "$QGC_CONF"
python3 - "$QGC_CONF/QGroundControl.ini" "${QGC_X:-960}" "${QGC_Y:-0}" "${QGC_W:-960}" "${QGC_H:-540}" <<'PYEOF'
import configparser, sys
path, x, y, w, h = sys.argv[1], *sys.argv[2:6]
cp = configparser.ConfigParser()
cp.optionxform = str
cp.read(path)
if not cp.has_section("MainWindowState"):
    cp.add_section("MainWindowState")
cp["MainWindowState"].update(
    {"x": x, "y": y, "width": w, "height": h, "visibility": "2"})
with open(path, "w") as fh:
    cp.write(fh, space_around_delimiters=False)
print(f"qgc: seeded window geometry {w}x{h}+{x}+{y}")
PYEOF
chown -R qgcuser:qgcuser "$QGC_CONF"

# Suppress QGC's first-run "Measurement Units" dialog, which otherwise sits centred over
# the flight view and covers exactly the part worth looking at.
#
# The key is `firstRunPromptIdsShown` under **[General]** — NOT [AppSettings], which is
# where an earlier attempt put it, and why that attempt silently did nothing. Found by
# dismissing the prompts with a synthetic click and diffing the ini, rather than guessing
# at QGC's internals a second time.
#
# It is a QUOTED, COMMA-SEPARATED LIST, not a single value: there are at least two prompts
# (1 = Measurement Units, 2 = Vehicle Information), and suppressing only the first just
# reveals the second. The extra ids are harmless padding — QGC only asks whether an id has
# already been shown — and they cover prompts a later QGC release might add.
QGC_CONF=/home/qgcuser/.config/QGroundControl
mkdir -p "$QGC_CONF"
INI="$QGC_CONF/QGroundControl.ini"
PROMPTS='firstRunPromptIdsShown="1,2,3,4,5,6,7,8"'
if grep -q '^firstRunPromptIdsShown' "$INI" 2>/dev/null; then
  sed -i "s|^firstRunPromptIdsShown.*|$PROMPTS|" "$INI"
elif grep -q '^\[General\]' "$INI" 2>/dev/null; then
  sed -i "0,/^\[General\]/s||[General]\n$PROMPTS|" "$INI"
else
  printf '[General]\n%s\n' "$PROMPTS" >> "$INI"
fi
chown -R qgcuser:qgcuser "$QGC_CONF"

# QGC refuses to run as root, so privileges are dropped at exec below. Nothing is copied
# or unpacked here any more: the image already holds the extracted tree at $QGC_APPRUN.

# A window manager is required or Qt apps misbehave (and nothing can be tiled later).
# Started here rather than in `recording` because this service owns the display.
if command -v openbox >/dev/null 2>&1; then
  openbox > /tmp/openbox.log 2>&1 &
  sleep 1
fi

echo "qgc: starting QGroundControl on $DISPLAY_NUM (headless datalink)"
exec setpriv --reuid=qgcuser --regid=qgcuser --clear-groups \
  env HOME=/home/qgcuser TMPDIR=/home/qgcuser/tmp DISPLAY="$DISPLAY_NUM" \
      QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 \
      "$QGC_APPRUN"
