#!/usr/bin/env python3
"""Connect, read sensors and a camera image, take off, fly a leg, land.

SITL ONLY. This arms and flies a SIMULATED vehicle. It must never be pointed at real hardware.

The smallest complete thing: one file that does all three of the things people actually want to
do first -- subscribe to telemetry, receive an IMAGE, and command the aircraft -- with nothing
imported from this repo. Copy it, change the waypoint, and you have your own client.

RUN IT (the stack must already be up, and this must run INSIDE sim-ros2, which is where ROS 2
and px4_msgs live):

    ./scripts/sim_up.sh
    ./scripts/build_airsim_wrapper.sh          # only if you want the camera; see below
    docker cp examples/hello_drone.py sim-ros2:/tmp/
    docker exec -it sim-ros2 bash -lc \\
      'source /opt/ros/jazzy/setup.bash; source /ros2_ws/install/setup.bash; \\
       python3 /tmp/hello_drone.py'

THE FOUR THINGS THAT SILENTLY GIVE YOU NOTHING
----------------------------------------------
1. QoS. `/fmu/out/*` publishers are BEST_EFFORT + TRANSIENT_LOCAL and `/fmu/in/*` subscribers
   are BEST_EFFORT + VOLATILE. rclpy's DEFAULT is RELIABLE, which matches NEITHER -- so a
   default subscription reads as silence and a default publisher is dropped, on a perfectly
   healthy stack. This is the single most common way to conclude the simulator is broken when
   it is not.
2. Offboard mode needs setpoints ALREADY FLOWING before you ask for it. PX4 rejects the mode
   switch if it is not receiving a stream, so the loop below publishes for a second first.
3. Camera topics only exist if `airsim_node` is running, and `sim_up.sh` does NOT start it --
   it is built and launched by `scripts/build_airsim_wrapper.sh`. Without that, `/fmu/*` works
   and every `/airsim_node/*` topic is simply absent. This script says so rather than hanging.
4. Frames. Setpoints here are NED and Z is NEGATIVE UP: -5.0 means five metres above home, and
   +5.0 flies into the ground.
"""
import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy)

from px4_msgs.msg import (OffboardControlMode, TrajectorySetpoint, VehicleCommand,
                          VehicleLocalPosition, VehicleStatus)
from sensor_msgs.msg import Image

TAKEOFF_ALT_M = 5.0        # NED: negative is up, applied below
LEG_NORTH_M = 5.0
ACCEPT_M = 1.0

# /fmu/out/* -- what PX4 publishes. TRANSIENT_LOCAL so a late subscriber still gets the last
# sample rather than waiting for the next one.
QOS_OUT = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     history=HistoryPolicy.KEEP_LAST)
# /fmu/in/* -- what PX4 subscribes to. VOLATILE here, not TRANSIENT_LOCAL.
QOS_IN = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT,
                    durability=DurabilityPolicy.VOLATILE,
                    history=HistoryPolicy.KEEP_LAST)
# Camera topics come from airsim_node, a normal ROS 2 publisher: default RELIABLE is correct
# here, and using the PX4 profile would match nothing. Two different worlds on one graph.
QOS_IMG = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.VOLATILE, history=HistoryPolicy.KEEP_LAST)

# Raw, because this script runs on the same machine as the simulator and raw costs nothing over
# shared memory. FROM ANOTHER MACHINE, SUBSCRIBE TO `<base>/compressed` INSTEAD -- raw imagery
# does not survive a WAN and takes the telemetry down with it (94 Hz -> ~10 Hz while delivering
# no pictures). `airsim_node` advertises the JPEG topic automatically; nothing to start. See
# docs/quickstart.md, "Imagery over that link".
IMAGE_TOPIC = "/airsim_node/PX4/front_center_Scene/image"


class HelloDrone(Node):
    def __init__(self) -> None:
        super().__init__("hello_drone")
        self.pos: VehicleLocalPosition | None = None
        self.status: VehicleStatus | None = None
        self.image: Image | None = None

        self.create_subscription(VehicleLocalPosition, "/fmu/out/vehicle_local_position",
                                 self._on_pos, QOS_OUT)
        self.create_subscription(VehicleStatus, "/fmu/out/vehicle_status_v1",
                                 self._on_status, QOS_OUT)
        self.create_subscription(Image, IMAGE_TOPIC, self._on_image, QOS_IMG)

        self.pub_mode = self.create_publisher(OffboardControlMode,
                                              "/fmu/in/offboard_control_mode", QOS_IN)
        self.pub_sp = self.create_publisher(TrajectorySetpoint,
                                            "/fmu/in/trajectory_setpoint", QOS_IN)
        self.pub_cmd = self.create_publisher(VehicleCommand, "/fmu/in/vehicle_command", QOS_IN)

    def _on_pos(self, m): self.pos = m
    def _on_status(self, m): self.status = m
    def _on_image(self, m): self.image = m

    # --- helpers -------------------------------------------------------------------------
    def _stamp(self) -> int:
        return int(self.get_clock().now().nanoseconds / 1000)   # PX4 wants MICROseconds

    def spin(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.02)

    def hold(self, north: float, east: float, down: float) -> None:
        """One heartbeat + one setpoint. Must be called at >2 Hz for PX4 to accept OFFBOARD."""
        mode = OffboardControlMode()
        mode.timestamp = self._stamp()
        mode.position = True
        self.pub_mode.publish(mode)

        sp = TrajectorySetpoint()
        sp.timestamp = self._stamp()
        sp.position = [float(north), float(east), float(down)]   # NED, down is +ve
        sp.yaw = 0.0
        self.pub_sp.publish(sp)

    def send(self, command: int, p1: float = 0.0, p2: float = 0.0) -> None:
        c = VehicleCommand()
        c.timestamp = self._stamp()
        c.command = command
        c.param1, c.param2 = p1, p2
        c.target_system, c.target_component = 1, 1
        c.source_system, c.source_component = 255, 190
        c.from_external = True
        self.pub_cmd.publish(c)

    def armed(self) -> bool:
        return bool(self.status and self.status.arming_state == VehicleStatus.ARMING_STATE_ARMED)

    def dist_to(self, north: float, east: float) -> float:
        if self.pos is None:
            return float("inf")
        return math.dist((self.pos.x, self.pos.y), (north, east))


def main() -> int:
    rclpy.init()
    n = HelloDrone()
    print("connecting...", flush=True)

    # --- 1. telemetry --------------------------------------------------------------------
    n.spin(5.0)
    if n.pos is None:
        print("FAIL: no /fmu/out/vehicle_local_position after 5 s.\n"
              "      The stack is not up, or the QoS is wrong (BEST_EFFORT + TRANSIENT_LOCAL).",
              file=sys.stderr)
        return 1
    print(f"telemetry : x={n.pos.x:+.2f} y={n.pos.y:+.2f} z={n.pos.z:+.2f} m  "
          f"(NED, z negative is up)")
    print(f"            xy_valid={n.pos.xy_valid}  ref_alt={n.pos.ref_alt:.2f} m")

    # --- 2. an image ---------------------------------------------------------------------
    n.spin(3.0)
    if n.image is None:
        print(f"image     : NONE on {IMAGE_TOPIC}\n"
              f"            airsim_node is not running -- sim_up.sh does not start it.\n"
              f"            Run ./scripts/build_airsim_wrapper.sh for camera topics.")
    else:
        i = n.image
        print(f"image     : {i.width}x{i.height} {i.encoding}, {len(i.data)} bytes, "
              f"frame_id={i.header.frame_id}")

    # --- 3. fly it -----------------------------------------------------------------------
    print("arming and taking off (SITL)...", flush=True)
    down = -abs(TAKEOFF_ALT_M)          # NED

    # Setpoints must ALREADY be streaming before OFFBOARD is requested.
    for _ in range(50):
        n.hold(0.0, 0.0, down)
        n.spin(0.02)

    n.send(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, 1.0, 6.0)   # 6 = OFFBOARD
    n.send(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 1.0)

    deadline = time.time() + 30
    while time.time() < deadline and abs((n.pos.z if n.pos else 0.0) - down) > 0.5:
        n.hold(0.0, 0.0, down)
        n.spin(0.05)
    if not n.armed():
        print("FAIL: never armed. QGC provides the datalink PX4 requires -- is sim-qgc up?",
              file=sys.stderr)
        return 1
    print(f"takeoff   : z={n.pos.z:+.2f} m")

    print(f"flying {LEG_NORTH_M} m north...", flush=True)
    deadline = time.time() + 40
    while time.time() < deadline and n.dist_to(LEG_NORTH_M, 0.0) > ACCEPT_M:
        n.hold(LEG_NORTH_M, 0.0, down)
        n.spin(0.05)
    print(f"arrived   : error {n.dist_to(LEG_NORTH_M, 0.0):.2f} m")

    print("landing...", flush=True)
    n.send(VehicleCommand.VEHICLE_CMD_NAV_LAND)
    deadline = time.time() + 60
    while time.time() < deadline and n.armed():
        n.spin(0.1)
    print(f"landed    : armed={n.armed()}  z={n.pos.z:+.2f} m")

    n.destroy_node()
    rclpy.shutdown()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
