from glob import glob

from setuptools import find_packages, setup

package_name = "chase_camera"

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
    description="AirSim's chase view as a ROS 2 camera topic. Simulator side only.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "chase_camera = chase_camera.chase_camera:main",
        ],
    },
)
