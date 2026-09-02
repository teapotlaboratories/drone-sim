import os
from glob import glob

from setuptools import find_packages, setup

package_name = "webui"


def _static_files():
    """Install the page verbatim, preserving its directory layout.

    `webui.launch.py` serves `share/webui/static` with `python3 -m http.server`, so the
    tree here is exactly the tree the browser sees -- `static/vendor/roslib.min.js` has to
    land at that path or the page loads with no control library and buttons that do
    nothing. Enumerated per directory because data_files does not recurse.
    """
    out = []
    for d, _dirs, files in os.walk(os.path.join(package_name, "static")):
        if not files:
            continue
        rel = os.path.relpath(d, package_name)
        out.append((os.path.join("share", package_name, rel),
                    [os.path.join(d, f) for f in files]))
    return out


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ] + _static_files(),
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Aldwin Hermanudin",
    maintainer_email="aldwinakbar@gmail.com",
    description="Hand-flying web interface (SIM-45). SITL only.",
    license="Apache-2.0",
    # NO console_scripts, DELIBERATELY.                                        (SIM-46)
    #
    # This package is the ground station: a static page, a launch file that configures two
    # upstream nodes, and the allowlist those nodes are configured with. It runs no code of
    # its own. `chase_camera` used to live here and moved to its own package, because it reads
    # the renderer's screen and a real aircraft has no chase camera.
    #
    # The absence is asserted by tests/test_web_ui_probes.py. A node added here would be built
    # into an image with no ffmpeg, no msgpack and no AirSim -- so it would fail at run time,
    # inside a container, rather than at the point someone wrote it.
    entry_points={},
)
