"""Lane A simulation bring-up: the `/clock` bridge plus the shared controller.

    ros2 launch bringup sim.launch.py
    ros2 launch bringup sim.launch.py takeoff_altitude:=5.0 use_sim_time:=true

WHAT IS SIMULATOR-ONLY, AND WHY IT LIVES HERE
---------------------------------------------
The `/clock` bridge exists only because Gazebo owns time. A real aircraft has no such
bridge — it runs on wall clock — so putting it in the shared `control.launch.py` would
break the property the whole graph is designed around: sim and real differ only in the
transport. Everything simulator-specific stays in this file.

`use_sim_time` DEFAULTS TO FALSE, DELIBERATELY
----------------------------------------------
Turning it on without a `/clock` publisher gives every node a clock frozen at zero: timers
never fire and the node hangs in a way that looks exactly like a deadlocked controller.
That was the state of the stack until now (`P1-03a`), and it is why the conventions
document recorded `use_sim_time` as specified-but-unreachable.

It is safe to enable here because this file starts the bridge. It still defaults to false
so that launching with `clock_bridge:=false` — or against a stack whose Gazebo is not
running — fails visibly rather than hanging.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument("px4_ns", default_value=""),
        DeclareLaunchArgument(
            "world", default_value="default",
            description="Gazebo world name. The clock topic is /world/<world>/clock, so a "
                        "different world means a different source topic — not a constant."),
        DeclareLaunchArgument("clock_bridge", default_value="true"),
        DeclareLaunchArgument(
            "use_sim_time", default_value="false",
            description="Requires the clock bridge. Without a /clock publisher every "
                        "timer stops and the node hangs like a deadlock."),
        DeclareLaunchArgument("takeoff_altitude", default_value="10.0"),
        DeclareLaunchArgument("square_side", default_value="10.0"),
        DeclareLaunchArgument("accept_radius", default_value="1.0"),
        DeclareLaunchArgument("hold_seconds", default_value="2.0"),
        DeclareLaunchArgument("setpoint_rate_hz", default_value="20.0"),
        DeclareLaunchArgument("state_timeout_s", default_value="60.0"),
        DeclareLaunchArgument("result_path", default_value=""),
    ]

    # Gazebo publishes /world/<world>/clock; ROS convention is /clock, hence the remap.
    # The bridge links VENDORED gz libraries rather than the system Harmonic ones — that
    # combination was tested rather than assumed, because a mismatch produces a bridge that
    # runs happily and carries nothing.
    clock_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="clock_bridge",
        output="screen",
        condition=IfCondition(LaunchConfiguration("clock_bridge")),
        arguments=[
            ["/world/", LaunchConfiguration("world"),
             "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"],
        ],
        remappings=[
            (["/world/", LaunchConfiguration("world"), "/clock"], "/clock"),
        ],
    )

    controller = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution(
            [FindPackageShare("bringup"), "launch", "control.launch.py"])),
        launch_arguments={
            "px4_ns": LaunchConfiguration("px4_ns"),
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "takeoff_altitude": LaunchConfiguration("takeoff_altitude"),
            "square_side": LaunchConfiguration("square_side"),
            "accept_radius": LaunchConfiguration("accept_radius"),
            "hold_seconds": LaunchConfiguration("hold_seconds"),
            "setpoint_rate_hz": LaunchConfiguration("setpoint_rate_hz"),
            "state_timeout_s": LaunchConfiguration("state_timeout_s"),
            "result_path": LaunchConfiguration("result_path"),
        }.items(),
    )

    return LaunchDescription([
        *args,
        LogInfo(msg="bringup: SITL only — no real aircraft is commanded by this launch."),
        clock_bridge,
        controller,
    ])
