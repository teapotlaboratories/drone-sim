from glob import glob

from setuptools import find_packages, setup

package_name = "depth_view"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Aldwin Hermanudin",
    maintainer_email="aldwinakbar@gmail.com",
    description="DepthPlanar 32FC1 -> colourised JPEG for the web interface. Simulator side.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "depth_view = depth_view.depth_view:main",
        ],
    },
)
