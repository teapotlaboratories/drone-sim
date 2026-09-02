"""Publish AirSim's chase view into the ROS 2 graph as a camera.                (SIM-45)

SITL only, and READ-ONLY with respect to the aircraft: it grabs a screen and commands
nothing.

WHY THE CHASE VIEW NEEDS A NODE AT ALL
--------------------------------------
Every camera in this graph is vehicle-mounted, so the one object an operator wants to watch
is the one object that can never be in frame. `scripts/record_chase.sh` documents the rest:
AirSim HAS a chase camera and it is already rendering -- `ViewMode` defaults to `FlyWithMe`
for a multirotor -- but it cannot be fetched over RPC. `AirSimCameraDirector` has no binding
and `simGetImages` serves vehicle-mounted cameras only. The only way to read it is off the
screen the engine already drew.

`record_chase.sh` does that and writes an mp4. This does it and publishes a topic, which is
what makes the chase view reachable from a browser through `web_video_server` like any other
camera -- so the web interface talks to ROS 2 and not to a side channel. The two are
independent; neither is built on the other, and `record_chase.sh` is deliberately untouched
because the gate depends on it.

HOW IT REACHES A SCREEN IN ANOTHER CONTAINER
--------------------------------------------
The engine's Xvfb runs in `sim-unreal`; this node runs in `sim-ros2`. An X server binds an
*abstract* unix socket, and Linux scopes abstract sockets to the NETWORK namespace -- which
every container in this stack shares (`sim_up.sh` netns_args). So `DISPLAY=:77` here reaches
the renderer's server. Verified 2026-09-01 with two containers sharing one netns:

    filesystem socket /tmp/.X11-unix/X77 visible from the second container : False
    abstract socket   @/tmp/.X11-unix/X77 from the second container        : CONNECTED
    ffmpeg -f x11grab -i :77 -frames:v 5 -f mpjpeg                         : 5 JPEG frames

This is the same fact `sim_up.sh:75` records from the other direction: display numbers are
stack-global here, which is how `:99` once let the chase recorder film QGroundControl's map
view and write it out as evidence.

REQUIRES `./scripts/sim_up.sh --display`. A headless bring-up has no X server at all, and
this node then says so once and publishes nothing, rather than publishing black frames --
a black rectangle in a browser reads as "the world is dark", which is a lie.

WHY ffmpeg AND NOT A PYTHON SCREEN GRAB
---------------------------------------
It is already the proven path in this repo, it does the JPEG encoding in C, and it is one
subprocess whose death is observable. A pure-Python grab would put a full-resolution
RGB->JPEG encode on the same interpreter that runs the timer, at 1920x1080.
"""

import collections
import os
import shutil
import subprocess
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage

# The frame splitter and the display check live in `webui/mjpeg.py`, which imports no ROS --
# so CI can test them. This module cannot be imported off-target because of rclpy above, and
# a test that needs the target can only ever pass on the target.
from chase_camera.mjpeg import MAX_BUFFER_BYTES, split_frames, x_display_reachable


class ChaseCamera(Node):
    def __init__(self) -> None:
        super().__init__("chase_camera")

        self.declare_parameter("display", ":77")       # matches sim_up.sh DISPLAY_NUM default
        self.declare_parameter("fps", 10.0)
        self.declare_parameter("quality", 6)           # ffmpeg -q:v, 2 (best) .. 31 (worst)
        self.declare_parameter("width", 0)             # 0 = the screen's own size
        self.declare_parameter("height", 0)
        self.declare_parameter("topic", "/chase/image/compressed")
        self.declare_parameter("frame_id", "chase")

        self.display = str(self.get_parameter("display").value)
        if not self.display.startswith(":"):
            self.display = ":" + self.display
        self.fps = float(self.get_parameter("fps").value)
        self.quality = int(self.get_parameter("quality").value)
        self.frame_id = str(self.get_parameter("frame_id").value)
        topic = str(self.get_parameter("topic").value)

        # RELIABLE, depth 1 -- AND THE RELIABILITY IS NOT A PREFERENCE.
        #
        # The first cut was BEST_EFFORT, reasoning that for video the newest frame is the only
        # interesting one. That reasoning is fine and the setting was still wrong, because the
        # CONSUMER decides: `web_video_server` subscribes RELIABLE (image_transport's default),
        # and a RELIABLE subscriber matches NOTHING from a BEST_EFFORT publisher. Measured on
        # a live stack -- the topic published at 9.99 Hz while the HTTP stream returned 22
        # bytes, the multipart boundary and not one frame.
        #
        # THE FAILURE MODE IS WHY THIS COMMENT IS LONG: an unmatched QoS pair is not an error
        # anywhere. No node warns, `ros2 topic hz` shows a healthy publisher, and the browser
        # shows an empty pane that reads as "the camera is broken" or "the world is dark".
        #
        # This is the MIRROR of the trap docs/quickstart.md already documents for `/fmu/out/*`,
        # where PX4 publishes BEST_EFFORT and a default RELIABLE subscription sees silence
        # against a completely healthy stack. Same incompatibility, opposite direction.
        # `airsim_node` publishes its compressed images RELIABLE for the same reason.
        #
        # depth 1 still gives "newest wins": under load the queue holds one frame, so a slow
        # consumer gets the latest rather than a backlog of where the aircraft used to be.
        self.pub = self.create_publisher(
            CompressedImage, topic,
            QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                       history=HistoryPolicy.KEEP_LAST, depth=1))

        self.frames = 0
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None

        why = self._unavailable()
        if why:
            # SAID ONCE, LOUDLY, AND THEN NOTHING. The common case is a headless bring-up,
            # which is not an error -- it is the gate's normal path. The node stays alive so
            # the launch file does not go down with it, and publishes nothing at all, so
            # web_video_server reports no such stream rather than serving black.
            self.get_logger().warning(
                f"chase camera NOT running: {why}. Bring the stack up with "
                "`./scripts/sim_up.sh --display` to record the chase view.")
            return

        self._start()

    # -- availability ----------------------------------------------------------------

    def _unavailable(self) -> str:
        """Why this cannot run, or "" if it can."""
        if shutil.which("ffmpeg") is None:
            return "ffmpeg is not installed in this container"
        ok, why = x_display_reachable(self.display)
        if not ok:
            return why
        return ""

    # -- the grab --------------------------------------------------------------------

    def _start(self) -> None:
        w = int(self.get_parameter("width").value)
        h = int(self.get_parameter("height").value)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-f", "x11grab", "-framerate", str(self.fps)]
        if w > 0 and h > 0:
            cmd += ["-video_size", f"{w}x{h}"]
        cmd += ["-i", self.display,
                # mjpeg, not mpjpeg: the multipart wrapper adds headers this node would only
                # have to skip, and the frames are cut on the JPEG markers regardless.
                "-c:v", "mjpeg", "-q:v", str(self.quality), "-f", "mjpeg", "pipe:1"]

        env = dict(os.environ, DISPLAY=self.display)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.get_logger().info(
            f"chase camera: x11grab {self.display} at {self.fps} Hz q{self.quality} "
            f"-> {self.pub.topic_name}")

        # A THREAD, NOT A TIMER. The read blocks on a pipe, and doing that on the executor
        # would stall every other callback in this process for as long as the renderer takes
        # to draw a frame.
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

        # AND A SECOND THREAD FOR STDERR, WHICH IS NOT HOUSEKEEPING.
        #
        # stderr was a PIPE that nothing read until after the loop exited. A pipe buffer is
        # ~64 KiB; once ffmpeg fills it -- about 800 warning lines, reachable in a couple of
        # minutes if x11grab starts complaining about a flaky X connection -- ffmpeg BLOCKS on
        # write, stops producing stdout, and `self.proc.stdout.read()` blocks forever. The node
        # stays alive, the frame counter freezes, and the browser shows a frozen pane with no
        # error anywhere. Found in review.
        #
        # NOT `stderr=STDOUT`: this stdout is a JPEG byte stream, and merging text into it
        # would corrupt frames. NOT DEVNULL either -- the last few lines are what explains an
        # exit. Drained into a small ring buffer instead.
        self.errlines: collections.deque = collections.deque(maxlen=40)
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.create_timer(10.0, self._report)

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        buf = bytearray()
        while rclpy.ok():
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_BUFFER_BYTES:
                # Not a JPEG stream, or a producer that stopped mid-frame. Drop rather than
                # grow: see MAX_BUFFER_BYTES.
                self.get_logger().error(
                    f"chase camera: {len(buf)} bytes buffered without a complete frame -- "
                    "discarding; is ffmpeg emitting JPEG?")
                buf.clear()
                continue
            # Emits every COMPLETE frame and leaves the partial tail in `buf`.
            for frame in split_frames(buf):
                self._publish(frame)

        rc = self.proc.poll()
        # Whatever the drain thread last saw -- it is already read, so this cannot block.
        err = " | ".join(list(self.errlines)[-5:])
        # rclpy.ok() false means WE are shutting down, which is not a fault.
        if rclpy.ok():
            self.get_logger().error(
                f"chase camera: ffmpeg exited (rc={rc}) after {self.frames} frames"
                + (f" -- {err}" if err else ""))

    def _drain_stderr(self) -> None:
        """Keep ffmpeg's stderr pipe empty so it can never block on a write."""
        assert self.proc is not None and self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.errlines.append(line)

    def _publish(self, jpeg: bytes) -> None:
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.format = "jpeg"
        msg.data = jpeg
        self.pub.publish(msg)
        self.frames += 1

    def _report(self) -> None:
        """Proof of life in the log, because a silent topic and a dead grabber look identical
        from outside. Same argument as record_chase.sh reading ffmpeg's frame counter rather
        than trusting that the process exists."""
        self.get_logger().info(f"chase camera: {self.frames} frames published")

    def destroy_node(self) -> bool:
        # SIGTERM then wait, so the grab stops when the node does. A daemon thread would let
        # the interpreter exit with ffmpeg still holding the X connection, and the next start
        # would find a second grabber already running.
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ChaseCamera()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
