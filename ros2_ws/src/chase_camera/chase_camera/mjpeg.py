"""Cutting an MJPEG byte stream into frames, and asking whether an X display answers.

Both are pure, both are used by `chase_camera.py`, and NEITHER IMPORTS ROS -- which is the
point. `chase_camera.py` imports rclpy at module scope, so anything left inside it can only
be tested on the target, and CI here runs off-target. The frame splitter in particular is
exactly the kind of code that is wrong by one byte and looks fine: a truncated frame renders
as a grey band rather than an error, and the operator reads it as the world.
"""

import socket

SOI = b"\xff\xd8\xff"      # start of image (with the first marker byte, so it is 3 bytes)
EOI = b"\xff\xd9"          # end of image

# A 1920x1080 JPEG at ffmpeg -q:v 6 measures a few hundred KB. 8 MB is a hard ceiling on how
# much a stalled or non-JPEG producer can accumulate before the buffer is dropped. Without
# it, an ffmpeg emitting something other than JPEG grows this without bound until the OOM
# reaper takes the container -- which would present as the whole ROS 2 workspace dying, with
# nothing pointing at the camera.
MAX_BUFFER_BYTES = 8 << 20


def split_frames(buf: bytearray) -> list[bytes]:
    """Remove and return every COMPLETE JPEG in `buf`, leaving the partial tail behind.

    `buf` is modified in place: on return it holds only the bytes of a frame still arriving,
    so the caller can append the next read and call again.

    Bytes BEFORE the first SOI are discarded. ffmpeg's mjpeg muxer emits nothing else, but a
    stream joined mid-frame begins with the tail of a frame whose header was never seen, and
    publishing that as an image would be publishing garbage.

    The scan is on the markers rather than on any container framing, because the markers are
    what both ends actually agree on -- the multipart headers of `-f mpjpeg` would be one
    more thing to parse and one more thing to get wrong.
    """
    out = []
    while True:
        start = buf.find(SOI)
        if start < 0:
            # No frame has started. Anything held is pre-frame noise; a lone trailing 0xFF
            # could still be the first byte of the next SOI, so keep the last two bytes.
            if len(buf) > 2:
                del buf[:-2]
            return out
        end = buf.find(EOI, start + len(SOI))
        if end < 0:
            del buf[:start]          # drop noise before the frame in progress, keep the rest
            return out
        end += len(EOI)
        out.append(bytes(buf[start:end]))
        del buf[:end]


def x_display_reachable(display: str, timeout: float = 2.0) -> tuple[bool, str]:
    """(True, "") if an X server answers on `display`, else (False, why).

    CHECKS THE ABSTRACT SOCKET, which is the one that crosses a container boundary. An X
    server binds both `/tmp/.X11-unix/X<N>` (a filesystem socket, scoped to the mount
    namespace) and `@/tmp/.X11-unix/X<N>` (an abstract socket, scoped to the NETWORK
    namespace). `chase_camera` runs in `sim-ros2` and the renderer's Xvfb runs in
    `sim-unreal`; they share a network namespace and not a mount namespace, so the
    filesystem socket is invisible here and testing for it would report "no screen" against
    a perfectly healthy renderer.

    Measured 2026-09-01 across two containers sharing one netns:
        filesystem socket visible : False
        abstract socket connect   : CONNECTED

    This is the same fact `scripts/sim_up.sh:75` records from the other direction: display
    numbers are stack-global here, which is how `:99` once let the chase recorder film
    QGroundControl's map view.
    """
    num = display.lstrip(":").split(".")[0]
    if not num.isdigit():
        return False, f"DISPLAY {display!r} is not a local display number"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(f"\0/tmp/.X11-unix/X{num}")
        return True, ""
    except OSError as exc:
        return False, (f"no X server on :{num} (abstract socket @/tmp/.X11-unix/X{num}: "
                       f"{exc}) -- the stack was probably brought up without --display")
    finally:
        s.close()
