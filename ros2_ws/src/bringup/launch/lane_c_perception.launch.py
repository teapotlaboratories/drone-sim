"""Lane C perception bring-up: `airsim_node`, with the two fixes it cannot run correctly without.

    ros2 launch bringup lane_c_perception.launch.py
    ros2 launch bringup lane_c_perception.launch.py use_sim_time:=true

REQUIRES the Cosys-AirSim wrapper to be built and sourced — it is not part of this workspace:

    ./scripts/lane_c_up.sh && ./scripts/build_airsim_wrapper.sh
    . /airsim_root/ros2/install/setup.bash

WHY THIS FILE EXISTS AT ALL
---------------------------
`ros2 run airsim_ros_pkgs airsim_node` starts and looks healthy, and its clock is silently
dead. Two separate defects stack up (both measured on 2026-08-02, `C-04`):

  1. `publish_clock` defaults to FALSE (airsim_ros_wrapper.cpp:52) — so nothing is published.
  2. Even when enabled, it publishes to `"~/clock"` (:412), which resolves to
     `/airsim_node/clock` on a node named `airsim_node`. **NOT `/clock`.**

So `use_sim_time:=true` against a bare `airsim_node` gives every node in the graph a clock
frozen at zero: timers never fire, and it presents exactly as a deadlocked controller.

**Lane A already paid for this failure shape once, as `P1-03a`.** The cause there was Gazebo
owning time with no bridge running; here it is a parameter default plus a topic name. Same
symptom, same cost, different root — which is precisely why it is worth encoding in a launch
file rather than leaving in someone's shell history.

`publish_clock` DEFAULTS TO TRUE HERE, unlike upstream
------------------------------------------------------
In Lane C the simulator owns time, so a Lane C graph essentially always wants the clock. The
remap below is unconditional: harmless when nothing publishes, and it means there is no
configuration in which the node publishes a clock that nobody can find.

`use_sim_time` STILL DEFAULTS TO FALSE
--------------------------------------
Same reasoning as `sim.launch.py`. Enabling it is only safe because this file guarantees a
`/clock` publisher — but leaving the default false means that launching against a stack that
is not actually running fails visibly rather than hanging.

A NOTE ON FRAMES, SO NOBODY IS SURPRISED
-----------------------------------------
Everything `airsim_node` publishes is **NWU**, not ENU — `convert_tf_msg_to_enu()` exists at
airsim_ros_wrapper.cpp:1600 and is never called; all four call sites use
`convert_tf_msg_to_ros()`, which negates only y and z. Measured yaw missed ENU by 97.3 deg and
NWU by 7.3 deg. This launch does NOT convert it. Anything consuming these topics against
REP-103 or `px4_ros_com`'s ENU assumption will be yaw-rotated 90 deg until the conversion lands
in one tested place, per docs/lane-a/conventions.md.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument(
            "host_ip", default_value="127.0.0.1",
            description="AirSim RPC host. 127.0.0.1 because the lane-c containers share the "
                        "simulator's network namespace."),
        DeclareLaunchArgument(
            "publish_clock", default_value="true",
            description="Upstream defaults this to false. Lane C's simulator owns time, so a "
                        "Lane C graph wants it on."),
        DeclareLaunchArgument(
            "use_sim_time", default_value="false",
            description="Safe to enable here because this file guarantees a /clock publisher. "
                        "Left false by default so a dead stack fails visibly, not silently."),
        DeclareLaunchArgument("vehicle_name", default_value="PX4"),
    ]

    airsim_node = Node(
        package="airsim_ros_pkgs",
        executable="airsim_node",
        name="airsim_node",
        output="screen",
        parameters=[{
            "host_ip": LaunchConfiguration("host_ip"),
            "publish_clock": LaunchConfiguration("publish_clock"),
            "use_sim_time": LaunchConfiguration("use_sim_time"),
        }],
        # THE LOAD-BEARING LINE. Without it the clock lands on /airsim_node/clock, where
        # nothing looks for it, and every use_sim_time consumer freezes at zero.
        remappings=[("/airsim_node/clock", "/clock")],
    )

    return LaunchDescription(args + [
        LogInfo(msg=["Lane C perception: airsim_node, clock remapped to /clock. ",
                     "Topics are NWU, not ENU - see this file's docstring."]),
        airsim_node,
    ])
