#!/usr/bin/env bash
# Record ALL FOUR interfaces on one screen while the drone takes off:
#
#   top-left      Gazebo GUI          — the drone in the world
#   top-right     QGroundControl      — operator GCS view (MAVLink UDP 14550)
#   bottom-left   PX4 CLI (pxh>)      — attached to the real SITL console via screen
#   bottom-right  MAVLink script      — lane-a-fly.py arming/taking off, with ACKs
#
# All headless: one Xvfb display, openbox for window management, xdotool for tiling,
# ffmpeg x11grab for capture.
#
# QGC is NOT in the image (172 MB, and CI does not need it) — bind-mount it:
#   -v "$PWD/vendor/tools/QGroundControl.AppImage:/qgc.AppImage:ro"
#
# SITL ONLY. No real hardware is involved at any point.
set +u
OUT=${OUTDIR:-/out}; mkdir -p "$OUT"
RES=${RES:-1920x1080}
W=${RES%x*}; H=${RES#*x}
HW=$((W/2)); HH=$((H/2))
FPS=${FPS:-15}
ALT=${ALT:-8}
HOVER_S=${HOVER_S:-40}
PX4=${PX4_DIR:-/opt/px4}
export DISPLAY=:99

say(){ echo "### $*"; }
cleanup(){
  pkill -f '[f]fmpeg' 2>/dev/null; pkill -f '[Q]GroundControl' 2>/dev/null
  pkill -f '[x]term' 2>/dev/null; pkill -f '[o]penbox' 2>/dev/null
  screen -S px4sitl -X quit 2>/dev/null
  pkill -f '[g]z sim' 2>/dev/null; pkill -f '[b]in/px4' 2>/dev/null
  pkill -f '[M]icroXRCEAgent' 2>/dev/null; pkill -f '[X]vfb :99' 2>/dev/null
  sleep 2
}
trap cleanup EXIT

# place <window-name-regex> <x> <y> <w> <h>
place(){
  local pat="$1" x="$2" y="$3" w="$4" h="$5"
  for i in $(seq 1 40); do
    local id
    id=$(xdotool search --name "$pat" 2>/dev/null | tail -1)
    if [ -n "$id" ]; then
      xdotool windowmove "$id" "$x" "$y" 2>/dev/null
      xdotool windowsize "$id" "$w" "$h" 2>/dev/null
      echo "   placed '$pat' -> ${w}x${h}+${x}+${y}"
      return 0
    fi
    sleep 1
  done
  echo "   WARN: window '$pat' never appeared"
  return 1
}

say "virtual display ${RES}"
# GLX/RANDR/render + -noreset are required or the Gazebo GUI dies mid-run with
# "XIO: fatal IO error 2 on X server :99".
Xvfb :99 -screen 0 "${RES}x24" -ac +extension GLX +extension RANDR +render -noreset \
  -nolisten tcp > "$OUT/xvfb.log" 2>&1 &
for i in $(seq 1 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 1; done
xdpyinfo >/dev/null 2>&1 || { echo "FAIL: no display"; exit 1; }
echo "  display up: $(xdpyinfo | awk '/dimensions/{print $2}')"

say "window manager (Qt apps misbehave without one)"
openbox > "$OUT/openbox.log" 2>&1 &
sleep 2

say "XRCE agent + PX4 SITL"
MicroXRCEAgent udp4 -p 8888 > "$OUT/agent.log" 2>&1 &
# stty min 1 REQUIRED: PX4's pxh busy-spins its prompt otherwise (4.1 GB/300s).
screen -dmS px4sitl -L -Logfile "$OUT/px4.log" \
  bash -c "stty min 1 time 0; cd $PX4 && HEADLESS=1 GZ_IP=127.0.0.1 make px4_sitl gz_x500"
for i in $(seq 1 90); do
  grep -qa 'Startup script returned successfully' "$OUT/px4.log" 2>/dev/null && break; sleep 2
done
grep -qa 'Startup script returned successfully' "$OUT/px4.log" || { echo "FAIL: PX4 no boot"; tail -15 "$OUT/px4.log"; exit 1; }
echo "  PX4 booted after ~$((i*2))s"

say "pane 1/4 — Gazebo GUI (top-left)"
gz sim -g > "$OUT/gzgui.log" 2>&1 &
place "Gazebo Sim" 0 0 "$HW" "$HH"

say "pane 2/4 — QGroundControl (top-right)"
if [ -x /qgc.AppImage ]; then
  # QGC refuses to run as root, so drop privileges for this pane only. Xvfb runs with -ac
  # (no host-based access control), so an unprivileged client can still reach :99.
  cp /qgc.AppImage /home/qgcuser/qgc.AppImage 2>/dev/null
  chmod +x /home/qgcuser/qgc.AppImage 2>/dev/null
  chown qgcuser:qgcuser /home/qgcuser/qgc.AppImage 2>/dev/null
  setpriv --reuid=qgcuser --regid=qgcuser --clear-groups \
    env HOME=/home/qgcuser TMPDIR=/home/qgcuser/tmp DISPLAY=:99 \
        QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb \
        /home/qgcuser/qgc.AppImage --appimage-extract-and-run > "$OUT/qgc.log" 2>&1 &
  place "QGroundControl" "$HW" 0 "$HW" "$HH"
  # QGC shows a first-run "Measurement Units" dialog that covers the flight view. Dismiss it.
  sleep 8
  QID=$(xdotool search --name "QGroundControl" 2>/dev/null | tail -1)
  [ -n "$QID" ] && { xdotool windowactivate "$QID" 2>/dev/null; xdotool key --window "$QID" Return 2>/dev/null; echo "   dismissed first-run dialog"; }
  # QGC resizes ITSELF after the dialog closes, collapsing to a title bar and undoing the
  # earlier placement — so place it again once it has settled.
  sleep 6
  place "QGroundControl" "$HW" 0 "$HW" "$HH"
  sleep 2
  place "QGroundControl" "$HW" 0 "$HW" "$HH"
else
  echo "   QGC not mounted at /qgc.AppImage — pane will be empty"
fi

say "pane 3/4 — PX4 CLI attached to the live SITL console (bottom-left)"
xterm -title "PX4-CLI" -fa DejaVuSansMono -fs 10 -bg black -fg green \
  -e "screen -r px4sitl" > "$OUT/xterm1.log" 2>&1 &
place "PX4-CLI" 0 "$HH" "$HW" "$HH"

say "pane 4/4 — MAVLink script (bottom-right)"
xterm -title "MAVLink-Script" -fa DejaVuSansMono -fs 10 -bg '#101820' -fg '#8FD3DA' \
  -e "bash -c 'echo \"MAVLink flight script — SITL only\"; sleep 6; python3 /fly.py $ALT $HOVER_S 2>&1 | tee $OUT/fly.log; echo; echo DONE; sleep 600'" \
  > "$OUT/xterm2.log" 2>&1 &
place "MAVLink-Script" "$HW" "$HH" "$HW" "$HH"

say "letting the GUIs settle (software GL is slow)"
sleep 20

say "recording ${RES} @ ${FPS}fps"
ffmpeg -nostdin -loglevel error -f x11grab -video_size "$RES" -framerate "$FPS" -i :99 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -y "$OUT/quad-flight.mp4" \
  > "$OUT/ffmpeg.log" 2>&1 &
sleep 3
pgrep -f '[f]fmpeg' >/dev/null && echo "  recording" || { echo "FAIL: no ffmpeg"; tail -5 "$OUT/ffmpeg.log"; }

say "waiting for the flight to complete (script runs in pane 4)"
for i in $(seq 1 120); do
  grep -qa 'FLIGHT_PEAK_ALT' "$OUT/fly.log" 2>/dev/null && break
  sleep 2
done
grep -a 'FLIGHT_PEAK_ALT' "$OUT/fly.log" 2>/dev/null | sed 's/^/  /' || echo "  WARN: flight did not report a peak altitude"
sleep 6

say "stop recording"
pkill -INT -f '[f]fmpeg' 2>/dev/null; sleep 4

say "result"
if [ -s "$OUT/quad-flight.mp4" ]; then
  echo "  $OUT/quad-flight.mp4  ($(du -h "$OUT/quad-flight.mp4" | cut -f1))"
  ffprobe -v error -show_entries format=duration -show_entries stream=width,height,nb_frames \
    -of default=noprint_wrappers=1 "$OUT/quad-flight.mp4" 2>/dev/null | sed 's/^/  /'
  # Sanity: a dead display or unrendered panes give a near-uniform frame.
  ffmpeg -nostdin -loglevel error -ss 10 -i "$OUT/quad-flight.mp4" -frames:v 1 -y "$OUT/quad-frame.png" 2>/dev/null
  identify -format "  frame: %wx%h mean=%[fx:int(255*mean)] stddev=%[fx:int(255*standard_deviation)]\n" "$OUT/quad-frame.png" 2>/dev/null
else
  echo "  NO VIDEO"; tail -10 "$OUT/ffmpeg.log" 2>/dev/null
fi
say "windows present at end"
xdotool search --name "." getwindowname %@ 2>/dev/null | sort -u | grep -viE '^(openbox|desktop)$' | sed 's/^/  /'
say "flight log"
sed 's/\x1b\[[0-9;]*m//g; s/\[2K//g' "$OUT/px4.log" 2>/dev/null \
  | grep -aiE 'takeoff|Takeoff detected|Landing detected|armed|disarm' | tail -8 | sed 's/^/  /'
