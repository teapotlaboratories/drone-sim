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
QGC_APPIMAGE="${QGC_APPIMAGE:-/qgc.AppImage}"

if [ ! -r "$QGC_APPIMAGE" ]; then
  echo "qgc: $QGC_APPIMAGE not found or unreadable." >&2
  echo "qgc: QGroundControl is 180 MB and is NOT baked into the image. Fetch it:" >&2
  echo "qgc:   ./scripts/fetch-qgc.sh      (pinned to versions.lock, SHA256-verified)" >&2
  echo "qgc: then bring the stack up again. Without it nothing can arm." >&2
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

# KNOWN ISSUE, not fixed here: QGC shows a first-run "Measurement Units" dialog centred
# over the flight view, and its window neither tiles nor stays where xdotool puts it.
# Tried and REJECTED: seeding `[AppSettings] firstRunPromptIdsShown=1` into
# QGroundControl.ini — a guess at QGC's internal key that did NOT suppress the dialog, so
# it is not left in pretending to work. A synthetic Return keypress does not dismiss it
# either (the dialog is a separate window). Fixing it properly means either finding the
# real settings key in QGC 5.0.8's source or seeding a full pre-answered profile; tracked
# with the tiling problem in D-02b.

# QGC refuses to run as root, so drop privileges. It also needs a writable HOME for its
# settings and a writable TMPDIR for --appimage-extract-and-run.
cp "$QGC_APPIMAGE" /home/qgcuser/qgc.AppImage
chmod +x /home/qgcuser/qgc.AppImage
chown qgcuser:qgcuser /home/qgcuser/qgc.AppImage

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
      /home/qgcuser/qgc.AppImage --appimage-extract-and-run
