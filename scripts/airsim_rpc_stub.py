#!/usr/bin/env python3
"""A minimal msgpack-RPC stub that impersonates a Cosys-AirSim server.

WHY THIS EXISTS. Lane C's ROS 2 wrapper builds (`C-06`) but cannot be exercised without a
running Cosys-AirSim server, which needs a 24 GB engine image and a multi-hour UE build
(`C-02`). Three questions do not actually need the engine, because they are answered
entirely on the WRAPPER side:

  1. What topics does the wrapper really publish?  (`C-04` plans against this)
  2. Does `/clock` land on `/airsim_node/clock` instead of `/clock`?
  3. Are poses NWU or ENU? Upstream's docs claim "the ROS standard" while the code negates
     only y and z. Feeding a KNOWN pose in and reading the output settles it.

WHAT THIS IS NOT. It is a test fixture, not a simulator. Every number it returns is
fabricated by this file. It proves how the wrapper TRANSFORMS input, never how AirSim
behaves. Do not cite it as evidence about the simulator, and do not let it stand in for
`C-02`.

The AirSim client speaks standard msgpack-RPC over TCP (rpclib):
    request  [0, msgid, method, params]
    response [1, msgid, error, result]

Usage:
    ./scripts/airsim_rpc_stub.py --port 41455 [--pose X Y Z]

The default pose is deliberately asymmetric so an axis swap or sign flip is unmistakable.
"""

from __future__ import annotations

import argparse
import json
import socketserver
import struct
import sys
import threading

import msgpack

ARGS = None
CALLS: dict[str, int] = {}
_LOCK = threading.Lock()

# One multirotor, one of each cheap sensor. No cameras: simGetImages would need real
# image buffers and none of the three questions above depend on them.
SETTINGS = {
    "SeeDocsAt": "stub",
    "SettingsVersion": 2.0,
    "SimMode": "Multirotor",
    "ClockType": "SteppableClock",
    "Vehicles": {
        "Drone1": {
            "VehicleType": "SimpleFlight",
            "AutoCreate": True,
            "Sensors": {
                "imu":          {"SensorType": 2, "Enabled": True},
                "gps":          {"SensorType": 3, "Enabled": True},
                "magnetometer": {"SensorType": 4, "Enabled": True},
                "barometer":    {"SensorType": 1, "Enabled": True},
            },
            "Cameras": {},
        }
    },
}


def kin(pose):
    """AirSim KinematicsState. Position in NED, as the real server would send."""
    x, y, z = pose
    return {
        "position":    {"x_val": x, "y_val": y, "z_val": z},
        "orientation": {"w_val": 1.0, "x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
        "linear_velocity":      {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
        "angular_velocity":     {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
        "linear_acceleration":  {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
        "angular_acceleration": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
    }


def handlers(method, params):
    pose = ARGS.pose
    t = 1_700_000_000_000_000_000

    if method == "getServerVersion":            return 4
    if method == "getMinRequiredClientVersion": return 1
    if method == "ping":                        return True
    if method == "getSettingsString":           return json.dumps(SETTINGS)
    if method == "listVehicles":                return ["Drone1"]
    if method in ("enableApiControl", "isApiControlEnabled", "armDisarm",
                  "simSetVehiclePose", "reset", "simRunConsoleCommand"):
        return True
    if method in ("isRecording", "simIsPaused"):  return False

    if method == "getMultirotorState":
        # MSGPACK_DEFINE_MAP(collision, kinematics_estimated, gps_location, timestamp,
        # landed_state, rc_data) - MultirotorRpcLibAdaptors.hpp:116. EVERY nested struct
        # must be complete too; an empty {} throws std::bad_cast, not a default value.
        return {
            "collision": {                      # RpcLibAdaptorsBase.hpp:113
                "has_collided": False, "penetration_depth": 0.0, "time_stamp": t,
                "normal":       {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "impact_point": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "position":     {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "object_name": "", "object_id": -1,
            },
            "kinematics_estimated": kin(pose),
            "gps_location": {"latitude": 47.641468, "longitude": -122.140165,
                             "altitude": 122.0},
            "timestamp": t,
            "landed_state": 0,
            "rc_data": {                        # RpcLibAdaptorsBase.hpp:230
                "timestamp": t, "pitch": 0.0, "roll": 0.0, "throttle": 0.0, "yaw": 0.0,
                "left_z": 0.0, "right_z": 0.0, "switches": 0, "vendor_id": "",
                "is_initialized": False, "is_valid": False,
            },
        }

    if method in ("simGetVehiclePose", "simGetObjectPose"):
        x, y, z = pose
        return {"position": {"x_val": x, "y_val": y, "z_val": z},
                "orientation": {"w_val": 1.0, "x_val": 0.0, "y_val": 0.0, "z_val": 0.0}}

    if method == "simGetGroundTruthEnvironment":
        # MSGPACK_DEFINE_MAP(position, geo_point, gravity, air_pressure, temperature,
        # air_density) - RpcLibAdaptorsBase.hpp:487. Field names and types must match or
        # the client throws std::bad_cast.
        x, y, z = pose
        return {"position": {"x_val": x, "y_val": y, "z_val": z},
                "geo_point": {"latitude": 47.641468, "longitude": -122.140165,
                              "altitude": 122.0},
                "gravity": {"x_val": 0.0, "y_val": 0.0, "z_val": 9.80665},
                "air_pressure": 101325.0, "temperature": 288.15, "air_density": 1.225}
    if method == "simGetGroundTruthKinematics":
        return kin(pose)
    if method == "getHomeGeoPoint":
        return {"latitude": 47.641468, "longitude": -122.140165, "altitude": 122.0}
    if method == "getImuData":
        return {"time_stamp": t,
                "orientation": {"w_val": 1.0, "x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "angular_velocity":    {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "linear_acceleration": {"x_val": 0.0, "y_val": 0.0, "z_val": 9.80665}}
    if method == "getGpsData":
        return {"time_stamp": t,
                "gnss": {"time_utc": t,
                         "geo_point": {"latitude": 47.641468, "longitude": -122.140165,
                                       "altitude": 122.0},
                         "eph": 0.3, "epv": 0.4,
                         "velocity": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                         "fix_type": 3},
                "is_valid": True}
    if method == "getBarometerData":
        return {"time_stamp": t, "altitude": 122.0, "pressure": 101325.0, "qnh": 1013.25}
    if method == "getMagnetometerData":
        return {"time_stamp": t,
                "magnetic_field_body": {"x_val": 0.2, "y_val": 0.0, "z_val": 0.4},
                "magnetic_field_covariance": []}
    if method in ("getLidarData", "getGPULidarData"):
        return {"time_stamp": t, "point_cloud": [], "pose": {}, "segmentation": [],
                "groundtruth": []}
    if method == "getEchoData":
        return {"time_stamp": t, "point_cloud": [], "pose": {}, "groundtruth": [],
                "passive_beacons_point_cloud": [], "passive_beacons_groundtruth": []}
    if method == "simGetCameraInfo":
        return {"pose": {}, "fov": 90.0, "proj_mat": {"matrix": [[0.0] * 4] * 4}}

    # Anything the client deserialises into a std::vector must be an ARRAY, never null -
    # a null hits `.as<vector<T>>()` and throws clmdep_msgpack type_error / std::bad_cast,
    # which is how this list was discovered: one crash at a time.
    if method in (
        "simGetImages",
        "simListInstanceSegmentationObjects",
        "simListInstanceSegmentationPoses",
        "simGetInstanceSegmentationColorMap",
        "simListSceneObjects",
        "simListAssets",
        "simSwapTextures",
        "simGetMeshPositionVertexBuffers",
        "simGetDetections",
    ):
        return []

    return None  # unknown -> null; the wrapper decides whether it cares


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
        while True:
            try:
                data = self.request.recv(65536)
            except OSError:
                return
            if not data:
                return
            unpacker.feed(data)
            for msg in unpacker:
                if not isinstance(msg, (list, tuple)) or len(msg) < 3:
                    continue
                _, msgid, method = msg[0], msg[1], msg[2]
                params = msg[3] if len(msg) > 3 else []
                with _LOCK:
                    CALLS[method] = CALLS.get(method, 0) + 1
                if ARGS.verbose:
                    print(f"  <- {method}", flush=True)
                try:
                    result = handlers(method, params)
                    resp = [1, msgid, None, result]
                except Exception as exc:                       # noqa: BLE001
                    resp = [1, msgid, str(exc), None]
                try:
                    self.request.sendall(msgpack.packb(resp, use_bin_type=True))
                except OSError:
                    return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=41455)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--pose", type=float, nargs=3, metavar=("X", "Y", "Z"),
                    default=[1.0, 2.0, -3.0],
                    help="vehicle position in AirSim NED. Default is asymmetric on "
                         "purpose so an axis swap or sign flip cannot hide.")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = run until killed")
    ap.add_argument("--verbose", action="store_true")
    ARGS = ap.parse_args()

    srv = Server((ARGS.host, ARGS.port), Handler)
    print(f"AirSim RPC STUB on {ARGS.host}:{ARGS.port}")
    print(f"  NED pose served: x={ARGS.pose[0]} y={ARGS.pose[1]} z={ARGS.pose[2]}")
    print("  NOT a simulator - every value here is fabricated by this file.", flush=True)

    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        if ARGS.seconds > 0:
            t.join(ARGS.seconds)
        else:
            t.join()
    except KeyboardInterrupt:
        pass
    srv.shutdown()

    print("\nRPC methods the wrapper actually called:")
    for m, n in sorted(CALLS.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
