"""The depth camera, rendered for a browser.                                    (SIM-48)

    ros2 launch depth_view depth_view.launch.py near_m:=0.5 far_m:=40.0

RUNS ON THE SIMULATOR SIDE, in `sim-ros2`, beside `airsim_node`. The raw topic is 640x480x4 =
1.2 MB per frame (~157 Mbit/s at 16.6 Hz); this colourises it there and publishes kilobytes.

REQUIRES perception -- `ros2 launch bringup perception.launch.py` -- because `sim_up.sh` does
not start `airsim_node`. Without it the source topic does not exist, this node subscribes to
nothing, and its 10-second report says so rather than the page showing a blank pane with no
explanation.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument(
            "source_topic", default_value="/airsim_node/PX4/front_center_DepthPlanar/image",
            description="32FC1 metres. sim/ue5/settings.json sets ImageType 1 = DepthPlanar."),
        DeclareLaunchArgument(
            "near_m", default_value="0.5",
            description="Near end of the colour ramp. FIXED, not auto-scaled per frame: a "
                        "colour has to mean the same distance in every frame or two frames "
                        "cannot be compared."),
        DeclareLaunchArgument(
            "far_m", default_value="40.0",
            description="Far end. Beyond this is clamped -- still a real measurement, unlike "
                        "a no-return, which gets its own colour."),
        DeclareLaunchArgument("jpeg_quality", default_value="80"),
        DeclareLaunchArgument("max_hz", default_value="10.0"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
    ]
    return LaunchDescription(args + [
        Node(
            package="depth_view", executable="depth_view", name="depth_view", output="screen",
            parameters=[{
                "source_topic": LaunchConfiguration("source_topic"),
                "near_m": ParameterValue(LaunchConfiguration("near_m"), value_type=float),
                "far_m": ParameterValue(LaunchConfiguration("far_m"), value_type=float),
                "jpeg_quality": ParameterValue(LaunchConfiguration("jpeg_quality"), value_type=int),
                "max_hz": ParameterValue(LaunchConfiguration("max_hz"), value_type=float),
                "use_sim_time": ParameterValue(LaunchConfiguration("use_sim_time"), value_type=bool),
            }],
        ),
    ])
