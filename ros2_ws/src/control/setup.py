from setuptools import find_packages, setup

package_name = "control"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Aldwin Hermanudin",
    maintainer_email="aldwinakbar@gmail.com",
    description="PX4 offboard setpoint node for Lane A.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "offboard_control = control.offboard_control:main",
            "park_tour = control.park_tour:main",
        ],
    },
)
