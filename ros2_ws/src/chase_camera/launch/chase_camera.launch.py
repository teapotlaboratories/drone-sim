"""The chase view, published into the graph as a camera.              (SIM-45, SIM-46)

    ros2 launch chase_camera chase_camera.launch.py chase_display:=:77

RUNS ON THE SIMULATOR SIDE, in `sim-ros2`, and it is the one part of the web interface that
CANNOT move to the ground station -- for two reasons, the second of which is the real one:

  1. It reads the renderer's screen through an X ABSTRACT socket, which Linux scopes to the
     network namespace. The ground station would need the renderer's namespace to see it.
  2. **A real aircraft has no chase camera.** This is simulator scaffolding, in the same
     family as ground truth. A ground station that could produce it would be less faithful to
     the real thing, not more -- which is the whole point of SIM-46.

REQUIRES `./scripts/sim_up.sh --display`. Without a screen the node says so once and publishes
nothing, rather than publishing black frames: a black rectangle in a browser reads as "the
world is dark", which is a lie.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument(
            "chase_display", default_value=":77",
            description="The renderer's Xvfb display. Must match sim_up.sh's DISPLAY_NUM "
                        "(default 77). :99 is QGroundControl's and would film its map."),
        DeclareLaunchArgument("chase_fps", default_value="10.0"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
    ]
    return LaunchDescription(args + [
        Node(
            package="chase_camera", executable="chase_camera", name="chase_camera",
            output="screen",
            parameters=[{
                "display": LaunchConfiguration("chase_display"),
                "fps": ParameterValue(LaunchConfiguration("chase_fps"), value_type=float),
                "use_sim_time": ParameterValue(LaunchConfiguration("use_sim_time"),
                                               value_type=bool),
            }],
        ),
    ])
