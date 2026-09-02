"""THE GROUND STATION: rosbridge, web_video_server, and the page. (SIM-45, SIM-46)

    ros2 launch webui webui.launch.py
    ros2 launch webui webui.launch.py bind_address:=127.0.0.1

Started by `scripts/web_ui.sh`, which resolves `bind_address` from the stack's actual network
mode.

RUNS IN `sim-webui`, ITS OWN CONTAINER, and that is the point of SIM-46. On real hardware the
ROS 2 graph runs on the companion computer and a ground-station web page does not -- it runs on
someone's laptop. `sim-qgc` has modelled that same boundary since the Gazebo era.

NOTHING HERE TOUCHES THE SIMULATOR. This package contains no Python nodes at all, and
`docker/webui.Dockerfile` installs no ffmpeg, no AirSim client and no msgpack -- so "the ground
station has no path to the renderer" is a property a `docker run` can check rather than a claim
in a comment. The chase camera, which DOES read the renderer's screen, stays on the simulator
side in the `chase_camera` package: a real aircraft has no chase camera.

**SITL ONLY.** This is a control surface with a button that arms an aircraft. See hard stop 1
in `CLAUDE.md`: commanding the real Pixhawk needs the operator's explicit go-ahead for that
specific run, every time, and a browser button is the opposite of that. The interlock that
enforces it is NOT here -- it is in `control/sitl_interlock.py`, checked by the node that
actually sends the arm command, so that `ros2 topic pub` cannot walk around it either.

WHAT THIS FILE IS FOR: making a deliberately GENERAL bridge obey a narrow rule
-----------------------------------------------------------------------------
`rosbridge_suite` exists to expose a whole ROS graph to a browser. That is the right tool by
this project's reuse rule and it is exactly the wrong default for a page with a TAKE OFF
button, so every glob below is load-bearing. Three traps, all read out of rosbridge 2.7.0's
own source before being relied on -- not from documentation:

  1. The globs are STRINGS CONTAINING A LIST LITERAL, not string arrays.
     `rosbridge_websocket.py` declares them as `str` and parses with `parse_glob_string`.
     Passing a real list here yields a parameter-type error at best and a silently
     unfiltered bridge at worst.
  2. AN UNSET GLOB MEANS NO CHECKING AT ALL. `parse_glob_string("")` returns None, and every
     capability treats None as "do not check". Security here is opt-in, so leaving a glob
     off does not narrow the bridge, it opens it.
  3. `topics_pub_glob` and `topics_sub_glob` are SEPARATE. Setting only the legacy
     `topics_glob` would apply one list to both directions -- and the read set has to be
     wide (all of /fmu/out) while the write set must be exactly one topic.

Enforcement point, verified by reading it: `rosbridge_library/capabilities/publish.py`
checks `topics_pub_glob` and returns BEFORE creating the topic registration, so a publish to
a non-matching topic never reaches the graph at all.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

# The lists themselves are in `webui/allowlist.py`, which imports nothing -- so the
# off-target tests can assert the exact values this launch passes to rosbridge, rather than a
# copy of them. See that module's docstring for the three rosbridge properties they depend on.
from webui import allowlist

def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument(
            "bind_address", default_value="127.0.0.1",
            description="Address rosbridge and web_video_server bind. THE DEFAULT IS THE "
                        "SAFE ONE. Under NET_MODE=host the container's namespace IS the "
                        "host's, so this is the only thing keeping an arm button off every "
                        "interface this machine has -- the exposure sim_up.sh:104 records "
                        "for MAVLink port 14540. scripts/web_ui.sh widens it to 0.0.0.0 "
                        "only for NET_MODE=shared, where the namespace is private and a "
                        "published port is the sole way in."),
        DeclareLaunchArgument("rosbridge_port", default_value="9090"),
        DeclareLaunchArgument("video_port", default_value="8181"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("page_port", default_value="8080"),
    ]

    rosbridge = Node(
        package="rosbridge_server",
        executable="rosbridge_websocket",
        name="rosbridge_websocket",
        output="screen",
        parameters=[{
            "port": ParameterValue(LaunchConfiguration("rosbridge_port"), value_type=int),
            "address": LaunchConfiguration("bind_address"),
            # THE FOUR LINES THAT MAKE A GENERAL BRIDGE NARROW. See the module docstring.
            "topics_pub_glob": allowlist.render(allowlist.TOPICS_PUB),
            "topics_sub_glob": allowlist.render(allowlist.TOPICS_SUB),
            # "[]" is an EMPTY allowlist, which is not the same as "" (no checking).
            # Without this the page could call any node's set_parameters and retune
            # takeoff_altitude mid-flight. rosbridge appends '/rosapi/*' to a non-None
            # services glob by itself, so roslibjs can still resolve topic types.
            "services_glob": allowlist.SERVICES_GLOB,
            "actions_glob": allowlist.ACTIONS_GLOB,
            "use_sim_time": ParameterValue(LaunchConfiguration("use_sim_time"),
                                           value_type=bool),
        }],
    )

    # Serves any image topic as MJPEG over HTTP, including /chase/image/compressed. It
    # transcodes nothing when the source is already a CompressedImage in jpeg, which both
    # the chase camera and airsim_node's compressed topics are.
    video = Node(
        package="web_video_server",
        executable="web_video_server",
        name="web_video_server",
        output="screen",
        parameters=[{
            "port": ParameterValue(LaunchConfiguration("video_port"), value_type=int),
            "address": LaunchConfiguration("bind_address"),
            # ONE THREAD PER CONCURRENT STREAM, PLUS HEADROOM. The default is 2, and since
            # SIM-48 the page holds THREE streams open at once (chase, onboard, depth) --
            # the spotlight layout shrinks a view, it does not disconnect it. A starved
            # request does not error: web_video_server answers HTTP 200, writes the
            # multipart boundary and closes, which a browser shows as an empty pane. That
            # failure shape already cost a long hunt in SIM-46; two spare threads is a
            # cheap way to keep it off the table.
            "ros_threads": 6,
            "use_sim_time": ParameterValue(LaunchConfiguration("use_sim_time"),
                                           value_type=bool),
        }],
    )

    # THE PAGE ITSELF, over python3 -m http.server.
    #
    # rosbridge serves a websocket and web_video_server serves video; neither serves a file,
    # and the page has to come from somewhere. The stdlib server is the whole of it -- the
    # page is three static files, one of them the vendored roslibjs, and there is no
    # server-side logic to write. Adding a web framework to serve three files would be a
    # dependency, a pin and a rebuild bought for nothing.
    #
    # It is READ-ONLY and serves ONE directory. Note that it binds the same address as the
    # two nodes above, so the safe default applies to it too.
    static_dir = os.path.join(get_package_share_directory("webui"), "static")
    page = ExecuteProcess(
        cmd=["python3", "-u", "-m", "http.server",
             LaunchConfiguration("page_port"),
             "--bind", LaunchConfiguration("bind_address"),
             "--directory", static_dir],
        output="screen",
        name="webui_page",
    )

    return LaunchDescription(args + [
        LogInfo(msg=["webui: rosbridge publish allowlist is ", str(allowlist.TOPICS_PUB),
                     " -- every other topic is read-only to the browser. "
                     "SITL ONLY; the interlock is in control/sitl_interlock.py."]),
        rosbridge, video, page,
    ])
