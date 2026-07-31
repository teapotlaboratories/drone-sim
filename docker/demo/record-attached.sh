#!/usr/bin/env bash
# Record the RUNNING compose stack flying — four panes on one screen (D-02c).
#
#   top-left      Gazebo GUI        — `gz sim -g` attached to the running server
#   top-right     QGroundControl    — the `qgc` service's window, on the shared display
#   bottom-left   PX4 console       — live tail of the SITL console log
#   bottom-right  ROS 2 controller  — `ros2 run control offboard_control`
#
# WHAT CHANGED FROM THE OLD RECORDER, AND WHY
# -------------------------------------------
# The previous version started its OWN PX4, Gazebo and XRCE agent. That meant the video
# showed a second, private stack that merely resembled the one under test — and it needed
# its own MAVLink flight script to arm, which violated the project rule that ONLY
# QGroundControl speaks MAVLink over IP.
#
# This attaches instead, exactly as the `verify` service does:
#   * Gazebo    — runs only the GUI client (`-g`); the server is px4-sitl's.
#   * QGC       — NOT started here. The `qgc` service already runs it, and that instance is
#                 the datalink PX4 requires to arm. Starting a second QGC would fight over
#                 PX4's learned remote address. We share its DISPLAY and just place its
#                 window.
#   * PX4 CLI   — a live `tail -f` of /out/px4.log rather than `screen -r`, because the
#                 console lives in another container and a screen session cannot be
#                 attached across that boundary. Same output, no interactivity.
#   * Flight    — the same ROS 2 node the acceptance gate runs, over uXRCE-DDS.
#
# SITL ONLY. No real hardware is involved at any point.
set +u
OUT=${OUTDIR:-/out}; mkdir -p "$OUT"
RES=${RES:-1920x1080}
W=${RES%x*}; H=${RES#*x}
HW=$((W/2)); HH=$((H/2))
FPS=${FPS:-15}
ALT=${ALT:-10}
export DISPLAY=${DISPLAY:-:99}

say(){ echo "### $*"; }
cleanup(){
  # Kill ONLY what this container started. The stack — PX4, Gazebo server, agent, QGC —
  # belongs to compose and must survive; tearing it down here would make the recording a
  # destructive operation.
  pkill -f '[f]fmpeg' 2>/dev/null
  pkill -f '[x]term' 2>/dev/null
  pkill -f '[g]z sim -g' 2>/dev/null
  sleep 2
}
trap cleanup EXIT

# place <window-name-regex> <x> <y> <w> <h>
place(){
  local pat="$1" x="$2" y="$3" w="$4" h="$5" id
  for i in $(seq 1 40); do
    id=$(xdotool search --name "$pat" 2>/dev/null | tail -1)
    if [ -n "$id" ]; then
      # windowmap FIRST. QGC's window belongs to the qgc container and comes up
      # `Map State: IsUnMapped`; xdotool will happily move and resize an unmapped window,
      # report success, and the pane records as SOLID BLACK. Verified with xwininfo.
      xdotool windowmap "$id" 2>/dev/null
      sleep 1
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

# Clear THIS run's artifacts first. A leftover mission-result.json from an earlier run
# reads "outcome": "success" and looks exactly like proof that this recording flew — which
# is precisely how a recording of an idle stack gets reported as a successful flight.
rm -f "$OUT/fly.log" "$OUT/mission-result.json" "$OUT/quad-flight.mp4" "$OUT/quad-frame.png"

say "waiting for the shared display (owned by the qgc service)"
for i in $(seq 1 60); do xdpyinfo >/dev/null 2>&1 && break; sleep 1; done
xdpyinfo >/dev/null 2>&1 || { echo "FAIL: no display on $DISPLAY — is the qgc service up?"; exit 1; }
echo "  display: $(xdpyinfo | awk '/dimensions/{print $2}')"

say "waiting for the running PX4 (attach mode — not starting one)"
for i in $(seq 1 90); do
  grep -qa 'Startup script returned successfully' "$OUT/px4.log" 2>/dev/null && break; sleep 2
done
grep -qa 'Startup script returned successfully' "$OUT/px4.log" 2>/dev/null \
  || { echo "FAIL: no booted PX4 found in $OUT/px4.log — is the stack up?"; exit 1; }
echo "  found a booted PX4"

say "building the control package from the mounted source"
mkdir -p /ros2_ws/src && rm -rf /ros2_ws/src/control
cp -r /ros2_ws_src/src/control /ros2_ws/src/control
( cd /ros2_ws && colcon build --packages-select control --symlink-install ) >"$OUT/colcon.log" 2>&1 \
  && echo "  control built" || { echo "FAIL: colcon build"; tail -15 "$OUT/colcon.log"; exit 1; }

say "pane 1/4 — Gazebo GUI client (top-left)"
# -g is the whole point: GUI only, attaching to px4-sitl's server over the shared netns
# and /dev/shm. Without it this would start a second simulation.
gz sim -g > "$OUT/gzgui.log" 2>&1 &
place "Gazebo Sim" 0 0 "$HW" "$HH"

# QGC's window belongs to the qgc container and needs more than a one-shot placement:
# it comes up `Map State: IsUnMapped`, and mapping it ONCE early does not stick — the
# pane still recorded solid black. Map/activate/raise/place, verify with xwininfo, and
# retry; then do it again right before recording starts.
ensure_qgc(){
  local id
  for i in $(seq 1 "${1:-10}"); do
    id=$(xdotool search --name "^QGroundControl$" 2>/dev/null | tail -1)
    [ -z "$id" ] && id=$(xdotool search --name "QGroundControl" 2>/dev/null | tail -1)
    if [ -n "$id" ]; then
      xdotool windowmap "$id" 2>/dev/null
      xdotool windowactivate "$id" 2>/dev/null      # also de-iconifies
      xdotool windowraise "$id" 2>/dev/null
      # DELIBERATELY no windowmove/windowsize here. Resizing QGC externally leaves it
      # mapped, viewable and correctly positioned while painting NOTHING — the Qt Quick
      # software backend gets no repaint trigger on a headless Xvfb, so the pane records
      # solid black and every check still reports success. The qgc service seeds QGC's
      # own [MainWindowState] instead, so it starts at the right geometry.
      # Trust xwininfo, not xdotool's exit status: xdotool happily moves and resizes an
      # UNMAPPED window and reports success, which is exactly how this pane recorded black.
      if xwininfo -id "$id" 2>/dev/null | grep -q 'Map State: IsViewable'; then
        echo "   QGC mapped and placed (attempt $i)"
        return 0
      fi
    fi
    sleep 2
  done
  echo "   WARN: QGC window never became viewable — its pane will record black"
  return 1
}

say "pane 2/4 — QGroundControl (top-right), owned by the qgc service"
ensure_qgc 15
# QGC shows a first-run dialog centred over the flight view. Returning to it does not
# reliably dismiss it (separate window) — known issue, tracked in D-02b.
QID=$(xdotool search --name "QGroundControl" 2>/dev/null | tail -1)
[ -n "$QID" ] && xdotool key --window "$QID" Return 2>/dev/null

say "pane 3/4 — PX4 console (bottom-left)"
xterm -title "PX4-Console" -fa DejaVuSansMono -fs 10 -bg black -fg green \
  -e "bash -c 'tail -f $OUT/px4.log'" > "$OUT/xterm1.log" 2>&1 &
place "PX4-Console" 0 "$HH" "$HW" "$HH"

say "pane 4/4 — ROS 2 offboard controller (bottom-right)"
xterm -title "ROS2-Controller" -fa DejaVuSansMono -fs 10 -bg '#101820' -fg '#8FD3DA' \
  -e "bash -c 'echo \"ROS 2 offboard controller over uXRCE-DDS — SITL only\"; sleep 6; source /ros2_ws/install/setup.bash; ros2 run control offboard_control --ros-args -p takeoff_altitude:=$ALT -p result_path:=$OUT/mission-result.json 2>&1 | tee $OUT/fly.log; echo; echo DONE; sleep 600'" \
  > "$OUT/xterm2.log" 2>&1 &
place "ROS2-Controller" "$HW" "$HH" "$HW" "$HH"

say "letting the GUIs settle (software GL is slow)"
sleep 20

# QGC repaints and re-places itself after its dialog settles, so re-assert immediately
# before the capture starts rather than trusting the earlier placement.
say "re-asserting QGC visibility just before capture"
ensure_qgc 8

say "recording ${RES} @ ${FPS}fps"
ffmpeg -nostdin -loglevel error -f x11grab -video_size "$RES" -framerate "$FPS" -i "$DISPLAY" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -y "$OUT/quad-flight.mp4" \
  > "$OUT/ffmpeg.log" 2>&1 &
sleep 3
pgrep -f '[f]fmpeg' >/dev/null && echo "  recording" || { echo "FAIL: no ffmpeg"; tail -5 "$OUT/ffmpeg.log"; }

say "waiting for the flight to finish (pane 4)"
# The node writes a JSON result and logs `result:` when it terminates, either way.
for i in $(seq 1 150); do
  grep -qa '"outcome"' "$OUT/fly.log" 2>/dev/null && break
  sleep 2
done
# `grep ... | tail | sed || echo` CANNOT detect failure: the pipeline exit status is
# sed's, which is always 0. Test the file explicitly.
FLIGHT_OK=0
if grep -qa '"outcome": "success"' "$OUT/mission-result.json" 2>/dev/null; then
  FLIGHT_OK=1
  grep -a 'result:' "$OUT/fly.log" 2>/dev/null | tail -1 | sed 's/^/  /'
else
  echo "  FLIGHT DID NOT SUCCEED — the video shows a stack that did not fly."
  echo "  --- last lines of the controller output ---"
  tail -15 "$OUT/fly.log" 2>/dev/null | sed 's/^/    /'
fi
sleep 6

say "stop recording"
pkill -INT -f '[f]fmpeg' 2>/dev/null; sleep 4

say "result"
if [ -s "$OUT/quad-flight.mp4" ]; then
  echo "  $OUT/quad-flight.mp4  ($(du -h "$OUT/quad-flight.mp4" | cut -f1))"
  ffprobe -v error -show_entries format=duration -show_entries stream=width,height,nb_frames \
    -of default=noprint_wrappers=1 "$OUT/quad-flight.mp4" 2>/dev/null | sed 's/^/  /'
  # Sanity: a dead display or unrendered panes give a near-uniform frame. stddev near 0
  # means the video is technically valid and visually empty.
  ffmpeg -nostdin -loglevel error -ss 10 -i "$OUT/quad-flight.mp4" -frames:v 1 -y "$OUT/quad-frame.png" 2>/dev/null
  identify -format "  frame: %wx%h mean=%[fx:int(255*mean)] stddev=%[fx:int(255*standard_deviation)]\n" "$OUT/quad-frame.png" 2>/dev/null
else
  echo "  NO VIDEO"; tail -10 "$OUT/ffmpeg.log" 2>/dev/null
fi

say "windows present at end"
xdotool search --name "." getwindowname %@ 2>/dev/null | sort -u | grep -viE '^(openbox|desktop)$' | sed 's/^/  /'
