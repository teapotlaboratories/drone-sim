#!/usr/bin/env bash
# Re-encode a render to phone-playable H.264.                                     (C-16)
#
# WHY THIS EXISTS. cv2.VideoWriter with the "mp4v" fourcc writes MPEG-4 Part 2, which desktop
# players tolerate and most phones do NOT -- the file opens and shows a blank frame. Three
# things are needed for broad playback and all three are easy to miss:
#   * H.264 (libx264) rather than MPEG-4 Part 2
#   * yuv420p -- many decoders reject yuv444p/other chroma
#   * +faststart -- moves the moov atom to the front so playback can begin before the whole
#     file is fetched; without it a remote/streamed file often shows nothing
#
# Output fps is forced to a standard rate by DUPLICATING frames, never by resampling time: the
# real-time duration is preserved. A 4.69 fps file is real-time-correct but some players will
# not handle such a low rate.
set -euo pipefail
IN="${1:?usage: encode_h264.sh <in.mp4> <out.mp4> [max_width] [fps]}"
OUT="${2:?}"; MAXW="${3:-1920}"; FPS="${4:-30}"
IMG=drone-sim/lane-a-video:v1.16.0
ind="$(cd "$(dirname "$IN")" && pwd)"; outd="$(cd "$(dirname "$OUT")" && pwd)"
docker run --rm -v "$ind:/i" -v "$outd:/o" --entrypoint ffmpeg "$IMG" \
  -hide_banner -loglevel error -y -i "/i/$(basename "$IN")" \
  -vf "scale='min($MAXW,iw)':-2" \
  -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p -r "$FPS" \
  -movflags +faststart "/o/$(basename "$OUT")"
echo "  wrote $OUT ($(du -h "$OUT" | cut -f1))"
