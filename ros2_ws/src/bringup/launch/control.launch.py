"""The offboard controller — the part that is IDENTICAL in sim and on the aircraft.

This is the shared include. `perception.launch.py` brings up the simulator-side pieces beside it
(currently the `/clock` bridge); a future `real.launch.py` will add the hardware transport
around the same node with nothing else changed.

That split is the point. `docs/conventions.md` freezes the graph so the same nodes
and topics reach the Pixhawk unchanged — if simulator-only machinery leaked in here, the
"only the transport is swapped" claim would quietly stop being true.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    args = [
        # Empty is PX4's single-vehicle default; a second aircraft becomes px4_ns:=/px4_1
        # with no code change (conventions §1).
        DeclareLaunchArgument("px4_ns", default_value=""),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("takeoff_altitude", default_value="10.0"),
        DeclareLaunchArgument("square_side", default_value="10.0"),
        DeclareLaunchArgument("accept_radius", default_value="1.0"),
        DeclareLaunchArgument("hold_seconds", default_value="2.0"),
        DeclareLaunchArgument("setpoint_rate_hz", default_value="20.0"),
        DeclareLaunchArgument("state_timeout_s", default_value="60.0"),
        DeclareLaunchArgument("result_path", default_value=""),
    ]

    controller = Node(
        package="control",
        executable="offboard_control",
        name="offboard_control",
        output="screen",
        # emulate_tty keeps the node's log ordering intact when launch captures stdout;
        # without it the state-machine transitions arrive batched and out of order, which
        # makes a failed run much harder to read after the fact.
        emulate_tty=True,
        parameters=[{
            "px4_ns": LaunchConfiguration("px4_ns"),
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "takeoff_altitude": LaunchConfiguration("takeoff_altitude"),
            "square_side": LaunchConfiguration("square_side"),
            "accept_radius": LaunchConfiguration("accept_radius"),
            "hold_seconds": LaunchConfiguration("hold_seconds"),
            "setpoint_rate_hz": LaunchConfiguration("setpoint_rate_hz"),
            "state_timeout_s": LaunchConfiguration("state_timeout_s"),
            "result_path": LaunchConfiguration("result_path"),
        }],
        # Bring the whole launch down when the controller exits.
        #
        # Without this, `ros2 launch` keeps running after the flight lands — the clock
        # bridge is still alive, so launch has no reason to stop. The command then never
        # returns, which makes it unusable from the scenario runner or any script that
        # needs an exit status. Observed as a 200 s timeout on a flight that had already
        # succeeded.
        on_exit=Shutdown(),
    )

    return LaunchDescription([*args, controller])
