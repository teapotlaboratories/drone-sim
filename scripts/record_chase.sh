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
# Accept BOTH `99` and `:99`. docker/qgc-entrypoint.sh:9 reads this same variable name as
# `${DISPLAY_NUM:-:99}` -- with the colon -- so an operator following the repo's existing
# script would otherwise produce `Xvfb ::99` and a bring-up that fails 20 s later. Normalise
# once, here, and prepend the colon at every use site. (review, PR 49)
DISPLAY_NUM=${DISPLAY_NUM:-77}
DISPLAY_NUM=${DISPLAY_NUM#:}
FPS=${FPS:-60}
CRF=${CRF:-23}
# Post-capture re-encode, separate from CRF above -- that one governs the LIVE capture and is kept
# cheap on purpose. Measurement table is at the compression step in cmd_stop.
COMPRESS_CRF=${COMPRESS_CRF:-28}
COMPRESS_PRESET=${COMPRESS_PRESET:-veryfast}
# Fallback container-side path, used when the renderer has no /out mount (a stack brought up
# by something other than `sim_up.sh --display`). _resolve_target below prefers /out.
FALLBACK_MP4=/tmp/chase.mp4
PROGRESS=/tmp/chase-progress.txt
STATE=${STATE:-$REPO/out/.chase-recording}
MAX_SECONDS=${MAX_SECONDS:-1800}

log()  { printf '\033[36m[chase]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[chase] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }
# Non-fatal, because the compression step's whole contract is "fall back, do not lose the file".
# It was missing, so the fallback branch hit `warn: command not found` and `set -e` killed the
# script BEFORE publishing -- an error handler that destroyed the outcome it was protecting.
# Caught by testing the failure path, which is the only way this class shows up.
warn() { printf '\033[33m[chase] WARN:\033[0m %s\n' "$*" >&2; }

# Where the renderer should WRITE, given where the caller wants the file.
#
# `sim_up.sh --display` bind-mounts <repo>/out into the renderer, so a target under out/ can be
# written straight to its final location: no staging in the container's writable overlay and no
# `docker cp` of the whole file afterwards. That matters for long captures, which are GBs.
#
# Everything else falls back to /tmp + copy, so this still works against a stack brought up
# some other way.
_resolve_target() {
  local host=$1 rel
  rel=${host#$REPO/out/}
  # `test -d /out` was NOT enough: any /out directory that exists in the image, or one an
  # earlier `mkdir` left behind, would select direct-write against a stack brought up WITHOUT
  # the mount -- ffmpeg would then write into the container's writable overlay and `stop` would
  # fail with "does not exist on the host", losing the recording. Ask the DAEMON whether /out
  # is really a bind mount.                                                    (review, PR 50)
  if [ "$rel" != "$host" ] \
     && docker inspect -f '{{range .Mounts}}{{println .Destination}}{{end}}' "$SIM" 2>/dev/null \
        | grep -qx '/out'; then
    # `basename` here FLATTENED subdirectories: --out out/sub/x.mp4 became /out/x.mp4, the file
    # landed at out/x.mp4, and `stop` then died on the mismatch WITHOUT clearing the state file
    # -- the permanent wedge this tool already has a comment about. Keep the path relative to
    # out/ instead.                                                            (review, PR 50)
    printf '/out/%s\n' "$rel"
  else
    printf '%s\n' "$FALLBACK_MP4"
  fi
}

# Where ffmpeg WRITES while running, as opposed to where the artifact ends up.
#
# With the /out bind mount the final path IS on the host, so writing straight to it made the
# deliverable the live output -- and every guard protecting it became decorative. When `stop`
# refused to copy a half-written file, there was no copy to refuse: a truncated, moov-less mp4
# was already sitting at out/<tag>-chase.mp4, and run_scenario.py's `chase_mp4.exists()` then
# advertised it in the run JSON. This repo's own words: "a video that cannot be opened is worse
# than no video -- it looks like evidence."                                    (review, PR 50)
# THE EXTENSION MUST SURVIVE. ffmpeg picks its muxer from the output file's extension, so a
# partial named `.x.mp4.partial` fails instantly with "Unable to find a suitable output format"
# -- the first draft of this did exactly that and recorded nothing. Insert the marker BEFORE
# the extension: out/x.mp4 -> out/.x.partial.mp4. Hidden, unmistakable, still an mp4.
_partial_of() {
  local d b stem ext
  d=$(dirname "$1"); b=$(basename "$1")
  case "$b" in
    *.*) stem=${b%.*}; ext=${b##*.}; printf '%s/.%s.partial.%s\n' "$d" "$stem" "$ext" ;;
    *)   printf '%s/.%s.partial\n' "$d" "$b" ;;
  esac
}

# `docker exec -u`, so ffmpeg writes as the INVOKING user rather than the image's `ue4`.
# Without it the bind-mounted out/ is written by whatever uid `ue4` happens to be, which
# matches the host user on this machine by luck and on a fresh one by nothing at all -- and
# rule 6 says a fresh machine must reach a working stack from the repo alone.  (review, PR 50)
_as_caller() { printf '%s\n' -u "$(id -u):$(id -g)"; }

# Last `frame=N` ffmpeg has written to its probe file, or empty if it has written none.
_progress_frames() {
  docker exec -e P="$PROGRESS" "$SIM" bash -lc \
    'grep -a "^frame=" "$P" 2>/dev/null | tail -1 | cut -d= -f2' 2>/dev/null | tr -d '\r '
}

# Leave nothing running behind a failed start. Without this the orphan blocks the next start
# with "ffmpeg is already running", and the real error is two layers back in the scrollback.
_abort_capture() {
  local t=${1:-$FALLBACK_MP4}
  docker exec -e T="$(_partial_of "$t")" -e P="$PROGRESS" "$SIM" \
    bash -lc 'pkill -INT -x ffmpeg; sleep 1; rm -f "$T" "$P"' 2>/dev/null || true
}

usage() {
  cat <<'EOF'
usage: record_chase.sh start [--out PATH.mp4]
       record_chase.sh stop [--no-distinct]
       record_chase.sh status

  start   begin grabbing the renderer's screen. Default output out/chase-<UTC>.mp4.
  stop    finalise the recording, deliver it, and report BOTH grabbed and distinct frames.
          --no-distinct skips the mpdecimate pass (~10 s per flight-length capture).
          --no-compress skips the post-capture re-encode (~43 s, ~11x smaller).
  status  is a recording running, and how many frames it has ingested.

Environment: SIM, DISPLAY_NUM, FPS (60), CRF (23), MAX_SECONDS (1800).
EOF
}

# `docker inspect` SUCCEEDS for a stopped container, so it cannot answer "is it running".
# Getting this wrong sent a dead renderer down the display check below, which then blamed the
# missing screen and told the operator to re-run with --display -- a diagnostic naming the
# wrong cause, which this repo treats as a bug in its own right. (review, PR 49)
require_container() {
  local running
  running=$(docker inspect -f '{{.State.Running}}' "$SIM" 2>/dev/null || echo "absent")
  [ "$running" = "true" ] \
    || die "container '$SIM' is $running — bring the stack up first (./scripts/sim_up.sh --display)"
}

# Only STARTING needs a screen. A stack brought up WITHOUT --display runs the same image and
# the same plugin; the only difference is whether anything was ever drawn to a surface, and
# without this check the grab succeeds and records a black rectangle -- indistinguishable
# from a broken renderer at review time.
#
# STOPPING deliberately does NOT call this. Finalising a recording that is already complete
# must not require a live X screen: if Xvfb or the engine dies after the flight, ffmpeg exits
# on the read error and still writes the trailer, and demanding a display here would strand a
# finished mp4 inside the container. (review, PR 49)
require_display() {
  require_container
  # THE DISPLAY NUMBER MUST BE TIED TO A *LOCAL* SERVER, and that takes a check neither
  # `pgrep` nor `xdpyinfo` can make alone.                                           (SIM-29)
  #
  # The first version of this fix ran both: `pgrep -x Xvfb` (is there a server here) and
  # `DISPLAY=:$N xdpyinfo` (does anyone answer on N). Review found the hole -- NEITHER ties
  # them together. On a healthy stack with the renderer on :77, `DISPLAY_NUM=99` passes check
  # one (the renderer's own :77 Xvfb) AND check two (QGC's :99, over the shared netns), and the
  # recorder captures QGroundControl exactly as before. One stale export reopens the bug.
  #
  # X binds TWO sockets: an ABSTRACT one, scoped to the NETWORK namespace and therefore shared
  # across every container here, and a FILESYSTEM one at /tmp/.X11-unix/X<N>, which is scoped
  # to this container's filesystem. The filesystem socket is the only container-local evidence
  # in the system -- and the bug report's own diagnostic showed it: `ls /tmp/.X11-unix/` was
  # EMPTY in the renderer while `xdpyinfo :99` answered happily.
  docker exec "$SIM" bash -lc "test -S /tmp/.X11-unix/X$DISPLAY_NUM" \
    || die "no LOCAL X server on :$DISPLAY_NUM in '$SIM' (no /tmp/.X11-unix/X$DISPLAY_NUM socket).
       A display of that number may still ANSWER here via the shared network namespace — QGC
       serves :99 — but it belongs to another container, and recording it would capture the
       wrong window. Bring the stack up with: ./scripts/sim_up.sh --display"
  # Belt and braces: the local server must be Xvfb, on this number. Cheap, and it catches a
  # socket left behind by a dead server.
  docker exec "$SIM" bash -lc "pgrep -x -a Xvfb | grep -qE ' :$DISPLAY_NUM( |\$)'" \
    || die "'$SIM' has a socket for :$DISPLAY_NUM but no Xvfb serving that number — stale socket?"
  docker exec "$SIM" bash -lc "DISPLAY=:$DISPLAY_NUM xdpyinfo >/dev/null 2>&1" \
    || die "'$SIM' has a local Xvfb on :$DISPLAY_NUM but it does not answer — check /tmp/xvfb.log"
}

cmd_start() {
  local out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      # `shift 2` with only one argument left returns 1, and under `set -e` the script exits
      # having printed NOTHING -- no usage, no message. Guard the arity first. (review, PR 49)
      --out)   [ $# -ge 2 ] || { usage >&2; die "--out needs a value"; }
               out="$2"; shift 2 ;;
      --out=*) out="${1#*=}"; shift ;;
      *)       usage >&2; die "unknown argument: $1" ;;
    esac
  done
  [ -n "$out" ] || out="$REPO/out/chase-$(date -u +%Y%m%dT%H%M%SZ).mp4"
  mkdir -p "$(dirname "$out")" "$REPO/out"

  require_display
  [ -f "$STATE" ] && die "a recording is already running (state: $STATE) — stop it first"
  docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null' \
    && die "ffmpeg is already running inside '$SIM' — stop it before starting another"

  local geom target
  geom=$(docker exec "$SIM" bash -lc \
    "DISPLAY=:$DISPLAY_NUM xdpyinfo | awk '/dimensions:/{print \$2}'" | tr -d '\r')
  [ -n "$geom" ] || die "could not read the screen geometry from :$DISPLAY_NUM"
  target=$(_resolve_target "$out")

  local partial; partial=$(_partial_of "$target")
  local -a asme; mapfile -t asme < <(_as_caller)

  # PATHS AND SIZES GO IN AS ENVIRONMENT, never interpolated into the shell string. $target
  # derives from --out, and from run_scenario.py that is built from the scenario YAML's `name`
  # -- so a scenario called `square 10m` would have made this `rm -f /out/square 10m-chase.mp4`,
  # and one containing a backtick would have executed in the renderer. run_scenario.py:378
  # already passes TAG through `docker exec -e` for exactly this reason: the shell expands
  # VALUES, it never parses scenario text as code.                             (review, PR 50)
  docker exec "${asme[@]}" -e T="$partial" -e P="$PROGRESS" "$SIM" \
    bash -lc 'rm -f "$T" "$P"'
  docker exec -d "${asme[@]}" \
    -e T="$partial" -e P="$PROGRESS" -e G="$geom" -e D=":$DISPLAY_NUM" \
    -e FPS="$FPS" -e CRF="$CRF" -e SECS="$MAX_SECONDS" "$SIM" \
    bash -lc 'DISPLAY="$D" ffmpeg -hide_banner -loglevel warning \
       -f x11grab -framerate "$FPS" -video_size "$G" -i "$D" \
       -t "$SECS" -c:v libx264 -preset ultrafast -crf "$CRF" \
       -progress "$P" -y "$T" > /tmp/chase-ffmpeg.log 2>&1' 

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
  # Validate that these are NUMBERS rather than redirecting the error away. `[ "$f2" -le 0 ]`
  # on non-numeric input exits 2, which reads as FALSE -- i.e. "frames are advancing" -- so a
  # garbled counter would PASS the readiness gate. `_progress_frames` runs `bash -l` in the
  # container, so any profile or motd output lands on stdout ahead of the number and produces
  # exactly that. (review, PR 49)
  if ! [[ "$f1" =~ ^[0-9]+$ ]] || ! [[ "$f2" =~ ^[0-9]+$ ]]; then
    _abort_capture "$target"
    die "could not read ffmpeg's frame counter (got '${f1:-}' then '${f2:-}') — nothing verified"
  fi
  if [ "$f2" -le "$f1" ]; then
    _abort_capture "$target"
    die "ffmpeg is running but no frames are being ingested ($f1 -> $f2)"
  fi

  printf '%s\n%s\n' "$out" "$target" > "$STATE"
  if [ "${target#/out/}" != "$target" ]; then
    log "recording $geom at ${FPS} fps -> $out (written directly, no copy)"
  else
    log "recording $geom at ${FPS} fps -> $out (staged in $SIM:$target)"
  fi
}

cmd_stop() {
  local no_distinct="" no_compress="" raw_b enc_b enc_dur
  while [ $# -gt 0 ]; do
    case "$1" in
      # Skip the mpdecimate pass. It decodes the whole file (~10 s per flight-length capture),
      # which is right interactively and wrong in a 40-seed gate -- ~7 minutes of pure
      # post-processing for a per-seed number nobody reads.                          (SIM-29)
      --no-distinct) no_distinct=1; shift ;;
      --no-compress) no_compress=1; shift ;;
      *)             usage >&2; die "unknown argument: $1" ;;
    esac
  done
  [ -f "$STATE" ] || die "no recording in progress (no $STATE)"
  local out target
  out=$(sed -n '1p' "$STATE")
  target=$(sed -n '2p' "$STATE")
  # State files written before the target was recorded carry only the host path.
  [ -n "$target" ] || target=$FALLBACK_MP4
  # Container, NOT display: see require_display's comment. A finished recording must be
  # retrievable even if the screen has gone.
  require_container

  # A recording whose container was torn down and re-upped leaves the state file pointing at
  # an mp4 that no longer exists. Without this branch `docker cp` fails, `set -e` exits BEFORE
  # the state file is removed, and the tool wedges permanently: every later `start` refuses
  # with "a recording is already running" and every later `stop` fails the same way -- while
  # `status` tells the operator to "run stop to clean up", which is the one path that cannot
  # work. Detect it, say so, and clear the state. (review, PR 49)
  local partial; partial=$(_partial_of "$target")
  if ! docker exec -e T="$partial" "$SIM" bash -lc 'test -f "$T"' 2>/dev/null; then
    rm -f "$STATE"
    docker exec -e P="$PROGRESS" "$SIM" bash -lc 'rm -f "$P"' 2>/dev/null || true
    die "no recording found at $partial in '$SIM' — the stack was probably restarted mid-recording. State cleared; '$out' was never written."
  fi

  # SIGINT, never SIGKILL. ffmpeg writes the moov atom on clean shutdown; killed, the mp4 is
  # unplayable and the whole flight is gone.
  docker exec "$SIM" bash -lc 'pkill -INT -x ffmpeg' || true
  local i
  for i in $(seq 1 60); do
    docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null' || break
    sleep 0.5
  done
  if docker exec "$SIM" bash -lc 'pgrep -x ffmpeg >/dev/null'; then
    # The partial stays where it is, under a dot name, and the FINAL path is never created.
    # Clearing the state file is what stops this wedging the tool.
    rm -f "$STATE"
    die "ffmpeg did not exit after SIGINT — the recording is left at $partial in '$SIM' and was NOT published to $out (an unplayable mp4 at the expected path would read as evidence). State cleared."
  fi

  # ANALYSE IN THE CONTAINER, ON THE CONTAINER'S COPY, BEFORE COPYING IT OUT.
  #
  # The first version did this backwards -- copied out, deleted the container's copy, then ran
  # ffprobe on the HOST path from INSIDE the container. The container cannot see a host path,
  # and the host has no ffprobe (deliberately: the video toolchain lives in images, not on the
  # workstation), so every number printed as "?" while the 63 MB recording was perfectly fine.
  # A reporting bug that looks like a capture failure is worth the comment.
  local grabbed dur distinct
  grabbed=$(docker exec -e T="$partial" "$SIM" bash -lc \
    'ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of csv=p=0 "$T"' \
    2>/dev/null | tr -d '\r' || true)
  dur=$(docker exec -e T="$partial" "$SIM" bash -lc \
    'ffprobe -v error -show_entries format=duration -of csv=p=0 "$T"' \
    2>/dev/null | tr -d '\r' || true)
  if [ -n "$no_distinct" ]; then
    distinct=""
  else
  log "counting distinct frames (mpdecimate decodes the whole file; ~10 s per flight-length capture)"
  distinct=$(docker exec -e T="$partial" "$SIM" bash -lc \
    'ffmpeg -hide_banner -nostats -loglevel info -i "$T" -vf mpdecimate -vsync vfr -an -f null - 2>&1 \
     | grep -o "frame= *[0-9]*" | tail -1 | grep -o "[0-9]*"' 2>/dev/null | tr -d '\r' || true)
  fi

  # RE-ENCODE AFTER THE FLIGHT, NEVER DURING IT.                                  (SIM-29)
  #
  # Capture uses `-preset ultrafast` on purpose: encoding competes with the renderer for CPU,
  # and this repo does not accept "two passing flights" as evidence that capture leaves flight
  # timing alone. So the live path stays cheap and the file stays fat -- 200 MB for a 232 s
  # CitySample flight -- and the squeeze happens here, after ffmpeg has exited and nothing is
  # flying.
  #
  # MEASURED on that 232 s 1080p capture, not assumed:
  #
  #     preset veryfast   17.5 MB   43 s      <- chosen
  #     preset faster     21.3 MB   46 s
  #     preset fast       23.8 MB   51 s
  #     preset medium     24.3 MB   58 s
  #     preset slow       25.2 MB   78 s
  #     (original)       200.4 MB
  #
  # veryfast is both the FASTEST and the SMALLEST, which is the opposite of the usual x264
  # trade. The source is ultrafast-encoded and carries blocking artifacts; the slower presets
  # reproduce those artifacts faithfully and spend bits doing it, while veryfast smooths them.
  # Choosing `slow` on instinct would have cost 80% more time for a 44% bigger file.
  #
  # THE ORIGINAL IS NOT DISCARDED UNTIL THE RE-ENCODE IS VERIFIED. A shorter or unreadable
  # output is thrown away and the capture published untouched: losing a flight video to a
  # compression step is strictly worse than storing a large one.
  if [ -z "$no_compress" ]; then
    raw_b=$(docker exec -e T="$partial" "$SIM" bash -lc 'stat -c%s "$T"' 2>/dev/null | tr -d '\r' || echo 0)
    log "compressing (x264 crf $COMPRESS_CRF preset $COMPRESS_PRESET; ~43 s per flight-length capture, --no-compress to skip)"
    if docker exec -e T="$partial" -e CRF="$COMPRESS_CRF" -e PRE="$COMPRESS_PRESET" "$SIM" bash -lc \
         'ffmpeg -v error -y -i "$T" -c:v libx264 -preset "$PRE" -crf "$CRF" -an "$T.enc.mp4"' 2>/dev/null
    then
      enc_dur=$(docker exec -e T="$partial" "$SIM" bash -lc \
        'ffprobe -v error -show_entries format=duration -of csv=p=0 "$T.enc.mp4"' 2>/dev/null | tr -d '\r' || true)
      if [ -n "$enc_dur" ] && [ -n "${dur:-}" ] \
         && awk -v a="$enc_dur" -v b="$dur" 'BEGIN{exit !(a>0 && (a-b<1) && (b-a<1))}'; then
        enc_b=$(docker exec -e T="$partial" "$SIM" bash -lc 'stat -c%s "$T.enc.mp4"' 2>/dev/null | tr -d '\r' || echo 0)
        docker exec -e T="$partial" "$SIM" bash -lc 'mv -f "$T.enc.mp4" "$T"'
        log "  compressed      $(awk -v r="$raw_b" -v e="$enc_b" 'BEGIN{printf "%.1f MB -> %.1f MB (%.1fx smaller)", r/1048576, e/1048576, (e>0? r/e : 0)}')"
      else
        docker exec -e T="$partial" "$SIM" bash -lc 'rm -f "$T.enc.mp4"' 2>/dev/null || true
        warn "re-encode duration '${enc_dur:-none}' does not match the capture's '${dur:-unknown}' --
       discarding it and publishing the original. The capture is intact."
      fi
    else
      docker exec -e T="$partial" "$SIM" bash -lc 'rm -f "$T.enc.mp4"' 2>/dev/null || true
      warn "re-encode failed; publishing the original capture untouched."
    fi
  fi

  # When the renderer wrote straight into the /out bind mount, the file is ALREADY on the host
  # at its final path -- copying it would be copying it onto itself. Only the staged path needs
  # the copy.
  # PUBLISH LAST, and only now that ffmpeg has exited cleanly and the file has been probed.
  # Until this line the final path does not exist, so nothing can mistake a partial for the
  # deliverable.
  if [ "${target#/out/}" != "$target" ]; then
    docker exec -e T="$partial" -e F="$target" "$SIM" bash -lc 'mv -f "$T" "$F"'
    [ -f "$out" ] || die "renderer moved $partial to $target but $out does not exist on the host"
  else
    docker cp "$SIM:$partial" "$out" >/dev/null
    docker exec -e T="$partial" "$SIM" bash -lc 'rm -f "$T"'
  fi
  # SIM-26: artifacts written from inside a container can land owned by someone the caller is
  # not. `docker exec -u` above makes that the normal case rather than the lucky one, and this
  # stays as the belt to its braces.
  if [ "$(id -u)" != "0" ]; then chown "$(id -u):$(id -g)" "$out" 2>/dev/null || true; fi
  docker exec -e P="$PROGRESS" "$SIM" bash -lc 'rm -f "$P"'
  rm -f "$STATE"

  log "saved $out"
  log "  duration        ${dur} s"
  # ffmpeg's own -t cap ends the capture without telling anyone: `stop` then finds no process,
  # takes the `|| true` path, and copies a truncated file whose reported duration reads as the
  # whole session. A 40-minute flight would silently lose its tail. (review, PR 49)
  # Guarded on dur being NUMERIC. When the ffprobe call above was broken, `dur` held an error
  # message, and this fired "the recording is TRUNCATED" on a healthy 6-second clip -- a false
  # alarm about evidence being incomplete is its own kind of bad evidence.      (review, PR 50)
  if [[ "${dur:-}" =~ ^[0-9]+(\.[0-9]+)?$ ]] \
     && awk -v d="$dur" -v m="$MAX_SECONDS" 'BEGIN{exit !(d >= m - 2)}'; then
    log "  WARNING: this hit the MAX_SECONDS=${MAX_SECONDS}s cap — the recording is TRUNCATED."
    log "           Re-run with a larger MAX_SECONDS to capture a session this long."
  fi
  log "  frames grabbed  ${grabbed}   <- x11grab's own clock; NOT the render rate"
  if [ -n "${distinct:-}" ] && [ "${dur%%.*}" -gt 0 ] 2>/dev/null; then
    log "  frames distinct ${distinct}   ($(awk -v n="$distinct" -v d="$dur" 'BEGIN{printf "%.1f", n/d}') fps averaged over the WHOLE file)"
    log "  note: idle time before and after the flight drags that average down — a parked"
    log "        drone does not change the screen. Judge the flight window, not the mean."
  fi
}

cmd_status() {
  if [ ! -f "$STATE" ]; then log "not recording"; return 0; fi
  local out; out=$(sed -n '1p' "$STATE")
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
  stop)   shift; cmd_stop "$@" ;;
  status) shift; cmd_status ;;
  -h|--help) usage ;;
  *)      usage >&2; die "expected: start | stop | status" ;;
esac
