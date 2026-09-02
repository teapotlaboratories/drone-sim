"""Cutting an MJPEG stream into frames, and finding the renderer's X display.     (SIM-45)

Off-target: no simulator, no ROS 2 runtime. `webui/chase_camera.py` imports rclpy at module
scope, so the two pieces worth testing were moved into `webui/mjpeg.py`, which imports
nothing but the standard library -- the same arrangement as `control/manual_policy.py`, and
for the same reason: a test that needs the target can only ever pass on the target.

The frame splitter is the reason this file exists. It is byte-level code whose failure mode
is SILENT: a frame cut one byte early renders as an image with a grey band, and an operator
surveying a site reads that as the world rather than as a bug.
"""
import importlib.util
import socket
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "mjpeg", REPO / "ros2_ws/src/chase_camera/chase_camera/mjpeg.py")
mjpeg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mjpeg)

SOI, EOI = mjpeg.SOI, mjpeg.EOI


def frame(payload=b"body"):
    return SOI + payload + EOI


# --- frame splitting -------------------------------------------------------------------

def test_one_whole_frame_comes_out_whole():
    buf = bytearray(frame())
    assert mjpeg.split_frames(buf) == [frame()]
    assert bytes(buf) == b""


def test_frames_include_their_own_markers():
    """web_video_server forwards these bytes to a browser untouched. A frame missing its SOI
    or its EOI is not a JPEG, and the browser shows nothing at all."""
    out = mjpeg.split_frames(bytearray(frame(b"xyz")))
    assert out[0].startswith(SOI)
    assert out[0].endswith(EOI)


def test_several_frames_in_one_read():
    """x11grab at 10 Hz into a 64 KB read routinely delivers more than one frame at a time.
    An earlier draft that returned only the first would have run at a fraction of the rate
    it reported, with the backlog growing silently."""
    buf = bytearray(frame(b"a") + frame(b"bb") + frame(b"ccc"))
    out = mjpeg.split_frames(buf)
    assert out == [frame(b"a"), frame(b"bb"), frame(b"ccc")]
    assert bytes(buf) == b""


def test_a_partial_trailing_frame_is_kept_not_emitted():
    """THE CENTRAL CASE. A pipe read ends wherever it ends, almost never on a frame
    boundary. Emitting the tail would publish a truncated image."""
    buf = bytearray(frame(b"whole") + SOI + b"still-arriv")
    out = mjpeg.split_frames(buf)
    assert out == [frame(b"whole")]
    assert bytes(buf) == SOI + b"still-arriv"


def test_the_partial_frame_completes_on_the_next_read():
    """Continuity across reads: the tail plus the next chunk must yield the frame intact."""
    buf = bytearray(SOI + b"half")
    assert mjpeg.split_frames(buf) == []
    buf += b"-and-half" + EOI
    assert mjpeg.split_frames(buf) == [SOI + b"half-and-half" + EOI]
    assert bytes(buf) == b""


def test_leading_noise_before_the_first_frame_is_discarded():
    """A stream joined mid-frame begins with the tail of a frame whose header was never
    seen. Publishing that would publish garbage as an image."""
    buf = bytearray(b"tail-of-a-frame-we-never-saw" + EOI + frame(b"good"))
    assert mjpeg.split_frames(buf) == [frame(b"good")]


def test_an_empty_read_yields_nothing_and_loses_nothing():
    buf = bytearray()
    assert mjpeg.split_frames(buf) == []
    assert bytes(buf) == b""


def test_a_stream_with_no_markers_does_not_accumulate():
    """If ffmpeg emits something that is not JPEG, the buffer must not grow without bound --
    the caller's MAX_BUFFER_BYTES is the backstop, but the splitter must not hoard either."""
    buf = bytearray(b"x" * 10000)
    assert mjpeg.split_frames(buf) == []
    assert len(buf) <= 2, "non-JPEG bytes were retained"


def test_a_trailing_ff_is_kept_because_it_may_start_the_next_marker():
    """0xFF alone is the first byte of SOI. Dropping it would cut the next frame's header in
    half and lose that frame -- once per read, forever, at a rate nothing would flag."""
    buf = bytearray(b"junk\xff")
    mjpeg.split_frames(buf)
    assert buf.endswith(b"\xff")
    buf += b"\xd8\xff" + b"payload" + EOI
    assert mjpeg.split_frames(buf) == [SOI + b"payload" + EOI]


def test_buffer_ceiling_is_bounded_and_sane():
    assert 1 << 20 <= mjpeg.MAX_BUFFER_BYTES <= 64 << 20


# --- the X display check ----------------------------------------------------------------

def test_absent_display_is_reported_not_raised():
    """A headless bring-up is the gate's NORMAL path, not an error. It must produce a
    readable reason, and the node must stay alive."""
    ok, why = mjpeg.x_display_reachable(":91", timeout=0.5)
    assert ok is False
    assert "sim_up.sh" in why or "--display" in why


def test_a_non_numeric_display_is_refused():
    """DISPLAY=host:0 is a TCP display; this check only understands local ones, and must say
    so rather than silently reporting no screen."""
    ok, why = mjpeg.x_display_reachable("somehost:0", timeout=0.5)
    assert ok is False
    assert "local display number" in why


def test_a_listening_abstract_socket_is_found():
    """The positive case, using the same abstract-socket namespace a real Xvfb binds -- so
    this proves the check looks in the right place, which is the whole point. The filesystem
    socket under /tmp/.X11-unix is invisible across containers; the abstract one is not."""
    display_num = "94"          # unlikely to collide with a real server on this machine
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(f"\0/tmp/.X11-unix/X{display_num}")
    except OSError:             # pragma: no cover - something already holds it
        srv.close()
        return
    srv.listen(1)
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    try:
        ok, why = mjpeg.x_display_reachable(f":{display_num}", timeout=1.0)
        assert ok is True, why
    finally:
        srv.close()


# --- fixes from the /review pass -------------------------------------------------------

def test_ffmpeg_stderr_is_drained():
    """stderr was a PIPE nothing read until after the loop. A pipe buffer is ~64 KiB; once
    ffmpeg fills it — about 800 warning lines — it BLOCKS on write, stops producing stdout,
    and the stdout read blocks forever. The node stays alive, the frame counter freezes, and
    the browser shows a frozen pane with no error anywhere."""
    src = (REPO / "ros2_ws/src/chase_camera/chase_camera/chase_camera.py").read_text()
    assert "_drain_stderr" in src
    assert src.count("threading.Thread(") >= 2, "stderr needs a reader of its own"
    # NOT merged into stdout: that stream carries JPEG bytes and text would corrupt frames.
    assert "stderr=subprocess.STDOUT" not in src
