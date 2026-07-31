"""Launch the shared application graph against the real transport."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    controller_config = PathJoinSubstitution(
        [FindPackageShare("bringup"), "config", "lane_a.yaml"]
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument("px4_ns", default_value=""),
            Node(
                package="control",
                executable="offboard_control",
                name="offboard_control",
                output="screen",
                parameters=[
                    controller_config,
                    {
                        "px4_ns": LaunchConfiguration("px4_ns"),
                        "use_sim_time": False,
                    },
                ],
            ),
        ]
    )
