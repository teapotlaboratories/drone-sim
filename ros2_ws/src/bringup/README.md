# `bringup` — shared ROS 2 launch composition

The package owns the application-side launch boundary shared by simulation and real
flight. Topic names, QoS, frames, and nodes remain the same; only simulation time and the
transport-facing bridge change.

Lane A simulation:

```bash
ros2 launch bringup sim.launch.py
```

The launch starts the Gazebo clock bridge and the offboard controller with
`use_sim_time:=true`. The Gazebo transport topic defaults to
`/world/default/clock` and is remapped to the ROS-standard `/clock` topic.

Real-hardware composition:

```bash
ros2 launch bringup real.launch.py
```

The real launch starts the same controller with `use_sim_time:=false` and does not start
a simulator clock bridge. Running it against real hardware still requires explicit
per-run operator approval under the repository safety rules.
