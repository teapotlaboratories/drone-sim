#!/usr/bin/env bash
# Record AirSim's chase camera off the renderer's virtual screen.                    (SIM-29)
#
# SITL only. Read-only with respect to the aircraft — it grabs a screen and commands nothing.
#
# WHY THIS EXISTS
# ---------------
# Every camera in the ROS 2 graph is vehicle-mounted, so the one object you want to watch is the
# one object that can never be in frame. That gap cost real time during SIM-27: an argument about
# whether a landing "looked correct" could not be settled by looking.
#
# AirSim HAS a chase camera and it is already running -- `ViewMode` defaults to `FlyWithMe` for a
# multirotor. It cannot be fetched over RPC (AirSimCameraDirector has no binding, and
# simGetImages serves vehicle-mounted cameras only, capped near 13-14 Hz at ANY resolution by a
# blocking GPU->CPU readback). So it is read the only way it can be: off the screen the engine
# already drew. Measured ~31 fps at 1080p during flight.
#
# REQUIRES the stack to have been brought up with a screen:
#
#     ./scripts/sim_up.sh --display
#     ./scripts/record_chase.sh start
#     python3 scripts/run_scenario.py scenarios/square-10m.yaml --seed 1 --outdir out/x --no-restart
#     ./scripts/record_chase.sh stop
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM=${SIM:-sim-unreal}
DISPLAY_NUM=${DISPLAY_NUM:-99}
FPS=${FPS:-60}
CRF=${CRF:-23}
IN_CONTAINER_MP4=/tmp/chase.mp4
PROGRESS=/tmp/chase-progress.txt
STATE=${STATE:-$REPO/out/.chase-recording}
MAX_SECONDS=${MAX_SECONDS:-1800}

log()  { printf '\033[36m[chase]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[chase] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

# Last `frame=N` ffmpeg has written to its progress file, or empty if it has written none.
_progress_frames() {
  docker exec "$SIM" bash -lc \
    "grep -a '^frame=' $PROGRESS 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null | tr -d '\r '
}

# Leave nothing running behind a failed start. Without this the orphan blocks the next start
# with "ffmpeg is already running", and the real error is two layers back in the scrollback.
_abort_capture() {
  docker exec "$SIM" bash -lc "pkill -INT -x ffmpeg; sleep 1; rm -f $IN_CONTAINER_MP4 $PROGRESS" 2>/dev/null || true
}

usage() {
  cat <<'EOF'
usage: record_chase.sh start [--out PATH.mp4]
       record_chase.sh stop
       record_chase.sh status

  start   begin grabbing the renderer's screen. Default output out/chase-<UTC>.mp4.
  stop    finalise the recording, copy it out, and report BOTH grabbed and distinct frames.
  status  is a recording running, and is its file growing.

Environment: SIM, DISPLAY_NUM, FPS (60), CRF (23), MAX_SECONDS (1800).
EOF
}

require_display_mode() {
  docker inspect "$SIM" >/dev/null 2>&1 || die "container '$SIM' is not running — bring the stack up first"
  # Assert the SCREEN, not the container. A stack brought up WITHOUT --display runs the same
  # image and the same plugin; the only difference is whether anything was ever drawn to a
  # surface. Without this check the grab would succeed and record a black rectangle, which is
  # indistinguishable from a broken renderer at review time.
  docker exec "$SIM" bash -lc "DISPLAY=:$DISPLAY_NUM xdpyinfo >/dev/null 2>&1" \
    || die "no X display :$DISPLAY_NUM in '$SIM' — bring the stack up with: ./scripts/sim_up.sh --display"
}

cmd_start() {
  local out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --out)   out="${2:-}"; shift 2 ;;
      --out=*) out="${1#*=}"; shift ;;
      *)       usage >&2; die "unknown argument: $1" ;;
    esac
  done
  [ -n "$out" ] || out="$REPO/out/chase-$(date -u +%Y%m%dT%H%M%SZ).mp4"
  mkdir -p "$(dirname "$out")" "$REPO/out"

  require_display_mode
  [ -f "$STATE" ] && die "a recording is already running (state: $STATE) — stop it first"
  docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null' \
    && die "ffmpeg is already running inside '$SIM' — stop it before starting another"

  local geom
  geom=$(docker exec "$SIM" bash -lc \
    "DISPLAY=:$DISPLAY_NUM xdpyinfo | awk '/dimensions:/{print \$2}'" | tr -d '\r')
  [ -n "$geom" ] || die "could not read the screen geometry from :$DISPLAY_NUM"

  docker exec "$SIM" bash -lc "rm -f $IN_CONTAINER_MP4 $PROGRESS"
  docker exec -d "$SIM" bash -lc \
    "DISPLAY=:$DISPLAY_NUM ffmpeg -hide_banner -loglevel warning \
       -f x11grab -framerate $FPS -video_size $geom -i :$DISPLAY_NUM \
       -t $MAX_SECONDS -c:v libx264 -preset ultrafast -crf $CRF \
       -progress $PROGRESS -y $IN_CONTAINER_MP4 \
       > /tmp/chase-ffmpeg.log 2>&1"

  # "Armed" must mean FRAMES ARE BEING ENCODED, not that a command was issued -- a harness in
  # this repo has already reported a probe as running while it captured nothing.
  #
  # The obvious check, "is the output file growing", was tried first and is WRONG HERE. x264
  # buffers, and the pre-flight scene is a parked drone that compresses to almost nothing, so
  # the file sat at 48 bytes for four seconds and then jumped to 512 KB. A readiness check that
  # fails on a healthy capture is worse than none: it would have made display mode look broken
  # every time the drone happened to be still.
  #
  # ffmpeg's own -progress counter is the right signal. It advances per frame ingested,
  # regardless of how little the encoder chooses to write.
  local f1 f2
  sleep 2; f1=$(_progress_frames)
  sleep 2; f2=$(_progress_frames)
  if ! docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null'; then
    docker exec "$SIM" bash -lc 'cat /tmp/chase-ffmpeg.log' >&2 || true
    die "ffmpeg exited immediately — nothing is being recorded"
  fi
  if [ -z "$f2" ] || [ "$f2" -le "${f1:-0}" ] 2>/dev/null; then
    _abort_capture
    die "ffmpeg is running but no frames are being ingested (${f1:-none} -> ${f2:-none})"
  fi

  printf '%s\n' "$out" > "$STATE"
  log "recording $geom at ${FPS} fps -> $out"
}

cmd_stop() {
  [ -f "$STATE" ] || die "no recording in progress (no $STATE)"
  local out; out=$(cat "$STATE")
  require_display_mode

  # SIGINT, never SIGKILL. ffmpeg writes the moov atom on clean shutdown; killed, the mp4 is
  # unplayable and the whole flight is gone.
  docker exec "$SIM" bash -lc 'pkill -INT -x ffmpeg' || true
  local i
  for i in $(seq 1 60); do
    docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null' || break
    sleep 0.5
  done
  docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null' \
    && die "ffmpeg did not exit after SIGINT — refusing to copy a half-written file"

  # ANALYSE IN THE CONTAINER, ON THE CONTAINER'S COPY, BEFORE COPYING IT OUT.
  #
  # The first version did this backwards -- copied out, deleted the container's copy, then ran
  # ffprobe on the HOST path from INSIDE the container. The container cannot see a host path,
  # and the host has no ffprobe (deliberately: the video toolchain lives in images, not on the
  # workstation), so every number printed as "?" while the 63 MB recording was perfectly fine.
  # A reporting bug that looks like a capture failure is worth the comment.
  local grabbed dur distinct
  grabbed=$(docker exec "$SIM" bash -lc \
    "ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of csv=p=0 $IN_CONTAINER_MP4" \
    2>/dev/null | tr -d '\r' || true)
  dur=$(docker exec "$SIM" bash -lc \
    "ffprobe -v error -show_entries format=duration -of csv=p=0 $IN_CONTAINER_MP4" \
    2>/dev/null | tr -d '\r' || true)
  log "counting distinct frames (mpdecimate decodes the whole file; ~10 s per flight-length capture)"
  distinct=$(docker exec "$SIM" bash -lc \
    "ffmpeg -hide_banner -nostats -loglevel info -i $IN_CONTAINER_MP4 -vf mpdecimate -vsync vfr -an -f null - 2>&1 \
     | grep -o 'frame= *[0-9]*' | tail -1 | grep -o '[0-9]*'" 2>/dev/null | tr -d '\r' || true)

  docker cp "$SIM:$IN_CONTAINER_MP4" "$out" >/dev/null
  # SIM-26: artifacts written from inside a container land as root and the caller cannot read
  # their own results. Every writer into out/ has to do this.
  if [ "$(id -u)" != "0" ]; then chown "$(id -u):$(id -g)" "$out" 2>/dev/null || true; fi
  docker exec "$SIM" bash -lc "rm -f $IN_CONTAINER_MP4 $PROGRESS"
  rm -f "$STATE"

  log "saved $out"
  log "  duration        ${dur} s"
  log "  frames grabbed  ${grabbed}   <- x11grab's own clock; NOT the render rate"
  if [ -n "${distinct:-}" ] && [ "${dur%%.*}" -gt 0 ] 2>/dev/null; then
    log "  frames distinct ${distinct}   ($(awk -v n="$distinct" -v d="$dur" 'BEGIN{printf "%.1f", n/d}') fps averaged over the WHOLE file)"
    log "  note: idle time before and after the flight drags that average down — a parked"
    log "        drone does not change the screen. Judge the flight window, not the mean."
  fi
}

cmd_status() {
  if [ ! -f "$STATE" ]; then log "not recording"; return 0; fi
  local out; out=$(cat "$STATE")
  if docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null' 2>/dev/null; then
    # Frames, not bytes. A parked drone compresses to nothing, so byte count reads as a stall
    # on a perfectly healthy capture -- the same trap the readiness check above fell into.
    log "recording -> $out ($(_progress_frames) frames ingested)"
  else
    log "STALE: $STATE exists but ffmpeg is not running in '$SIM' — run 'stop' to clean up"
    return 1
  fi
}

case "${1:-}" in
  start)  shift; cmd_start "$@" ;;
  stop)   shift; cmd_stop ;;
  status) shift; cmd_status ;;
  -h|--help) usage ;;
  *)      usage >&2; die "expected: start | stop | status" ;;
esac
