"""Publish the depth camera as something a browser can display.                (SIM-48)

SITL/simulator side, and READ-ONLY with respect to the aircraft: it subscribes to a camera and
publishes a picture. It commands nothing.

WHY A NODE AND NOT A URL
------------------------
The other two panes are pointed straight at `web_video_server`, because both are already JPEG.
Depth is not: `/airsim_node/PX4/front_center_DepthPlanar/image` is **`32FC1`, metres** --
`sim/ue5/settings.json` configures `ImageType: 1`, DepthPlanar, the Z-distance rather than ray
length. `web_video_server` knows `bgr8` and `bgra8` and nothing else; its streamers carry no
min/max or colormap parameters, checked against the shipped `.so`. Pointed at a float topic it
fails or renders garbage.

So depth is converted here and published as a `CompressedImage`, which is the same shape
`chase_camera` produces and which `ros_compressed` then forwards without transcoding.

WHY ON THE SIMULATOR SIDE AND NOT THE GROUND STATION
-----------------------------------------------------
Not tidiness -- bandwidth. The raw topic is 640x480x4 = **1.2 MB per frame**, about
157 Mbit/s at the measured 16.6 Hz. `docker/ros2.Dockerfile` already records the measurement
that raw imagery does not survive a WAN while JPEG does (raw: ~0 images and 90% of telemetry
lost; JPEG: 16.4 Hz at 1.7 Mbit/s). Colourise beside the source, ship kilobytes.

The decisions about RANGE and INVALID PIXELS are in `colorize.py`, which imports only numpy so
the tests can exercise them off-target.
"""

import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Float32MultiArray

import cv2

from depth_view.colorize import apply_mask, legend_stops, normalise


class DepthView(Node):
    def __init__(self) -> None:
        super().__init__("depth_view")

        self.declare_parameter("source_topic",
                               "/airsim_node/PX4/front_center_DepthPlanar/image")
        self.declare_parameter("topic", "/depth_view/image/compressed")
        self.declare_parameter("near_m", 0.5)
        self.declare_parameter("far_m", 40.0)
        self.declare_parameter("jpeg_quality", 80)
        # The source runs at ~16.6 Hz and every frame costs a colormap plus a JPEG encode on
        # the same machine that is rendering the world. 10 Hz is faster than an operator can
        # act on and leaves the GPU alone.
        self.declare_parameter("max_hz", 10.0)
        self.declare_parameter("frame_id", "depth_view")

        self.near = float(self.get_parameter("near_m").value)
        self.far = float(self.get_parameter("far_m").value)
        # VALIDATED HERE, NOT PER FRAME. `normalise` raises on far <= near, and without this
        # the node came up looking healthy, published a range the page's legend cheerfully
        # drew, and then threw an unhandled exception out of the subscription at 10 Hz.
        # Refusing to start is the honest failure. (review)
        if self.far <= self.near:
            raise ValueError(
                f"far_m ({self.far}) must be greater than near_m ({self.near}) -- "
                "refusing to start rather than publishing a legend for a colour map that "
                "cannot be computed")
        self.quality = int(self.get_parameter("jpeg_quality").value)
        self.frame_id = str(self.get_parameter("frame_id").value)
        self.min_period = 1.0 / max(float(self.get_parameter("max_hz").value), 0.1)
        src = str(self.get_parameter("source_topic").value)

        self.bridge = CvBridge()
        self.frames = 0
        self.dropped = 0
        self._last_stamp = 0.0

        # RELIABLE, depth 1 -- AND THE RELIABILITY IS NOT A PREFERENCE.
        #
        # `web_video_server` subscribes RELIABLE (image_transport's default), and a RELIABLE
        # subscriber matches NOTHING from a BEST_EFFORT publisher. That cost a bring-up during
        # SIM-45: the chase topic published at 9.99 Hz while the browser received 22 bytes,
        # with no warning anywhere. depth 1 still gives "newest wins" under load.
        self.pub = self.create_publisher(
            CompressedImage, str(self.get_parameter("topic").value),
            QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                       history=HistoryPolicy.KEEP_LAST, depth=1))

        # BEST_EFFORT on the way IN, matching how airsim_node publishes its raw images, and
        # correct for its own sake: a dropped depth frame is not worth retransmitting when
        # another arrives in 60 ms.
        self.create_subscription(
            Image, src, self._on_depth,
            QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                       history=HistoryPolicy.KEEP_LAST, depth=1))

        # THE RANGE IS PUBLISHED, so the page's legend cannot disagree with the colour map.
        #
        # Hard-coding 0.5 and 40 in the page would work until someone launched this node with
        # a different `far_m`, at which point the scale would confidently mislabel every
        # distance on screen -- a legend that is wrong is worse than no legend, because it
        # looks authoritative. TRANSIENT_LOCAL so a browser opened later still gets it.
        self.pub_range = self.create_publisher(
            Float32MultiArray, "/depth_view/range",
            QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL,
                       history=HistoryPolicy.KEEP_LAST, depth=1))
        rng = Float32MultiArray()
        rng.data = [float(self.near), float(self.far)]
        self.pub_range.publish(rng)

        self.get_logger().info(
            f"depth_view: {src} -> {self.pub.topic_name} "
            f"[{self.near}..{self.far} m, {self.get_parameter('max_hz').value} Hz max]")
        self.get_logger().info(f"depth_view: legend stops {legend_stops(self.near, self.far)} m")
        self.create_timer(10.0, self._report)

    def _on_depth(self, msg: Image) -> None:
        now = self.get_clock().now().nanoseconds * 1e-9
        if now - self._last_stamp < self.min_period:
            self.dropped += 1
            return
        self._last_stamp = now
        try:
            depth = self.bridge.imgmsg_to_cv2(msg, desired_encoding="passthrough")
        except Exception as exc:                      # pragma: no cover - needs a live stack
            self.get_logger().error(f"depth_view: cannot read frame ({exc})")
            return

        # ENCODING IS CHECKED, NOT ASSUMED. If airsim_node's settings ever change ImageType or
        # a different camera is wired to this node, the arithmetic below would silently produce
        # a plausible-looking picture of the wrong thing.
        if msg.encoding != "32FC1":
            self.get_logger().error(
                f"depth_view: expected 32FC1 metres, got {msg.encoding!r} -- refusing to "
                "render, because a wrong colour map looks exactly like a right one")
            return

        u8, valid = normalise(np.asarray(depth, dtype=np.float32), self.near, self.far)
        # TURBO where available: it is perceptually ordered, so "closer" reads as a direction
        # rather than as a set of unrelated hues. Older OpenCV builds lack it, and INFERNO is
        # the next best ordered map -- JET is deliberately not the fallback, it is famously
        # misleading about ordering.
        cmap = getattr(cv2, "COLORMAP_TURBO", getattr(cv2, "COLORMAP_INFERNO", cv2.COLORMAP_HOT))
        bgr = apply_mask(cv2.applyColorMap(u8, cmap), valid)

        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:                                    # pragma: no cover
            self.get_logger().error("depth_view: JPEG encode failed")
            return

        out = CompressedImage()
        out.header = msg.header
        out.header.frame_id = self.frame_id
        out.format = "jpeg"
        out.data = buf.tobytes()
        self.pub.publish(out)
        self.frames += 1

    def _report(self) -> None:
        """Proof of life, because a silent topic and a dead converter look identical from
        outside -- the same argument record_chase.sh makes for reading ffmpeg's frame counter
        rather than trusting that the process exists."""
        self.get_logger().info(
            f"depth_view: {self.frames} frames published, {self.dropped} dropped to the rate cap")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = DepthView()
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
