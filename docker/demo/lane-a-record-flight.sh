#!/usr/bin/env bash
# Record a video of the drone flying in the containerized Lane A.
#
# Runs entirely headless: Gazebo's GUI client renders onto a virtual X display (Xvfb) with
# software GL, and ffmpeg screen-records it. Same virtual-display trick that verified QGC.
#
# The flight itself is commanded through PX4's own shell via `screen -X stuff`, which works
# because the SITL console is launched under screen (and needs `stty min 1` — see the
# prompt-spin defect in docs/docker/todo.md).
#
# Usage (image: drone-sim/lane-a-video:v1.16.0):
#   docker run --rm --shm-size=2g -e OUTDIR=/out -v "$PWD/out:/out" \
#     drone-sim/lane-a-video:v1.16.0 bash /record.sh
#
# Env: RES (default 1280x720), FPS (20), HOVER_S (25) — seconds to hover before landing.
set +u
OUT=${OUTDIR:-/out}; mkdir -p "$OUT"
RES=${RES:-1280x720}
FPS=${FPS:-20}
HOVER_S=${HOVER_S:-25}
PX4=${PX4_DIR:-/opt/px4}
export DISPLAY=:99

say(){ echo "### $*"; }
cleanup(){
  pkill -f '[f]fmpeg' 2>/dev/null
  screen -S px4sitl -X quit 2>/dev/null
  pkill -f '[g]z sim' 2>/dev/null; pkill -f '[b]in/px4' 2>/dev/null
  pkill -f '[M]icroXRCEAgent' 2>/dev/null; pkill -f '[X]vfb :99' 2>/dev/null
  sleep 2
}
trap cleanup EXIT

say "virtual display"
# -ac +extension GLX +render -noreset are REQUIRED: without them the Gazebo GUI dies with
# "XIO: fatal IO error 2 on X server :99" mid-run (verified 2026-07-29).
Xvfb :99 -screen 0 "${RES}x24" -ac +extension GLX +extension RANDR +render -noreset \
  -nolisten tcp > "$OUT/xvfb.log" 2>&1 &
for i in $(seq 1 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 1; done
xdpyinfo >/dev/null 2>&1 || { echo "FAIL: no display"; exit 1; }
echo "  display :99 up at $(xdpyinfo | awk '/dimensions/{print $2}')"

say "XRCE agent (so /fmu/out is available for telemetry checks)"
MicroXRCEAgent udp4 -p 8888 > "$OUT/agent.log" 2>&1 &

say "PX4 SITL + Gazebo server (headless server; GUI attaches separately)"
# stty min 1 is REQUIRED — without it PX4's prompt busy-spins at 1.45M writes/sec.
screen -dmS px4sitl -L -Logfile "$OUT/px4.log" \
  bash -c "stty min 1 time 0; cd $PX4 && HEADLESS=1 GZ_IP=127.0.0.1 make px4_sitl gz_x500"
for i in $(seq 1 90); do
  grep -qa 'Startup script returned successfully' "$OUT/px4.log" 2>/dev/null && break
  sleep 2
done
grep -qa 'Startup script returned successfully' "$OUT/px4.log" || { echo "FAIL: PX4 no boot"; tail -15 "$OUT/px4.log"; exit 1; }
echo "  PX4 booted after ~$((i*2))s"

say "Gazebo GUI client on the virtual display (software GL)"
gz sim -g > "$OUT/gzgui.log" 2>&1 &
sleep 25   # software-GL GUI startup is slow; give it time to render the world
echo "  gui process: $(pgrep -cf '[g]z sim' ) gz processes"

say "start recording (${RES} @ ${FPS}fps)"
ffmpeg -nostdin -loglevel error -f x11grab -video_size "$RES" -framerate "$FPS" -i :99 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -y "$OUT/flight.mp4" \
  > "$OUT/ffmpeg.log" 2>&1 &
sleep 3
pgrep -f '[f]fmpeg' >/dev/null && echo "  recording" || { echo "FAIL: ffmpeg not running"; tail -5 "$OUT/ffmpeg.log"; }

say "FLY via MAVLink (SITL only — no real hardware involved)"
# Typing "commander takeoff" into the pxh shell FAILED: PX4 refuses to arm with
# "Preflight Fail: No connection to the ground control station" (rcAndDataLinkCheck).
# lane-a-fly.py acts as a real minimal GCS (1 Hz HEARTBEAT) so the check clears legitimately,
# then arms/takes off/lands over MAVLink and reads altitude back — with COMMAND_ACK results
# instead of guesswork.
python3 /fly.py "${ALT:-5}" "$HOVER_S" 2>&1 | tee "$OUT/fly.log"

say "stop recording"
pkill -INT -f '[f]fmpeg' 2>/dev/null
sleep 4

say "result"
if [ -s "$OUT/flight.mp4" ]; then
  echo "  file : $OUT/flight.mp4  ($(du -h "$OUT/flight.mp4" | cut -f1))"
  ffprobe -v error -show_entries format=duration,size -show_entries stream=width,height,nb_frames \
    -of default=noprint_wrappers=1 "$OUT/flight.mp4" 2>/dev/null | sed 's/^/  /'
else
  echo "  NO VIDEO produced"; tail -10 "$OUT/ffmpeg.log" 2>/dev/null
fi
say "verify the video actually shows the world (not a blank/dead display)"
ffmpeg -nostdin -loglevel error -ss 12 -i "$OUT/flight.mp4" -frames:v 1 -y "$OUT/frame.png" 2>/dev/null
if [ -s "$OUT/frame.png" ]; then
  # A dead X server or unrendered GUI gives a near-uniform image. Report the spread.
  echo "  frame.png: $(stat -c%s "$OUT/frame.png") bytes"
  identify -format "  dimensions %wx%h  mean=%[fx:int(255*mean)]  stddev=%[fx:int(255*standard_deviation)]\n" "$OUT/frame.png" 2>/dev/null
  echo "  (stddev near 0 = blank screen; a rendered 3D scene should be well above 10)"
fi
say "gz GUI still alive at end? $(pgrep -cf '[g]z sim') gz processes"
say "flight log excerpts"
grep -aiE 'takeoff|landing|Land detected|armed|disarm' "$OUT/px4.log" 2>/dev/null \
  | sed 's/\x1b\[[0-9;]*m//g' | tail -8 | sed 's/^/  /'
