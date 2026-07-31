from glob import glob
from setuptools import find_packages, setup


package_name = "bringup"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Aldwin Hermanudin",
    maintainer_email="aldwinakbar@gmail.com",
    description="Shared Drone Sim ROS 2 launch composition.",
    license="Apache-2.0",
)
