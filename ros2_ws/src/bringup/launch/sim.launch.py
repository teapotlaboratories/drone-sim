"""Launch the shared application graph against a simulator."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    controller_config = PathJoinSubstitution(
        [FindPackageShare("bringup"), "config", "lane_a.yaml"]
    )
    clock_topic = LaunchConfiguration("gz_clock_topic")

    return LaunchDescription(
        [
            DeclareLaunchArgument("px4_ns", default_value=""),
            DeclareLaunchArgument("gz_clock_topic", default_value="/world/default/clock"),
            DeclareLaunchArgument("launch_controller", default_value="true"),
            Node(
                package="ros_gz_bridge",
                executable="parameter_bridge",
                name="sim_clock_bridge",
                output="screen",
                arguments=[
                    [clock_topic, "@rosgraph_msgs/msg/Clock[gz.msgs.Clock"]
                ],
                remappings=[(clock_topic, "/clock")],
            ),
            Node(
                package="control",
                executable="offboard_control",
                name="offboard_control",
                output="screen",
                condition=IfCondition(LaunchConfiguration("launch_controller")),
                parameters=[
                    controller_config,
                    {
                        "px4_ns": LaunchConfiguration("px4_ns"),
                        "use_sim_time": True,
                    },
                ],
            ),
        ]
    )
