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
        # Launch files must be INSTALLED, not merely present in the source tree —
        # `ros2 launch bringup sim.launch.py` resolves through the install space.
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Aldwin Hermanudin",
    maintainer_email="aldwinakbar@gmail.com",
    description="Launch composition for Lane A.",
    license="Apache-2.0",
    entry_points={"console_scripts": []},
)
