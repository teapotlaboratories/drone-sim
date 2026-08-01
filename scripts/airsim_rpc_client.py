#!/usr/bin/env python3
"""Minimal msgpack-RPC client for Cosys-AirSim + frame capture.

Written rather than pip-installed because msgpack-rpc-python pins tornado 4.x, which does
not import on Python 3.12. The wire protocol is plain msgpack-RPC over TCP:
    request  = [0, msgid, method, params]
    response = [1, msgid, error, result]

ImageRequest is MSGPACK_DEFINE_MAP(camera_name, image_type, pixels_as_float, compress,
annotation_name) in THIS tree -- five fields, the last being a Cosys-AirSim addition -- and
simGetImages binds (requests, vehicle_name), two args, not the three-arg form some upstream
docs show. Both read from the vendored source, not from memory.
"""
import socket, sys, msgpack

HOST, PORT = "127.0.0.1", 41451


class Rpc:
    def __init__(self, host=HOST, port=PORT, timeout=30):
        self.s = socket.create_connection((host, port), timeout)
        # msgpack 0.5.6 (this image) predates strict_map_key; newer versions need it for
        # the map keys AirSim sends. Support both rather than pinning either.
        try:
            self.unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        except TypeError:
            self.unp = msgpack.Unpacker(raw=False)
        self.msgid = 0

    def call(self, method, *params):
        self.msgid += 1
        self.s.sendall(msgpack.packb([0, self.msgid, method, list(params)],
                                     use_bin_type=True))
        while True:
            for msg in self.unp:
                if msg[0] == 1:
                    if msg[2] is not None:
                        raise RuntimeError(f"{method}: {msg[2]}")
                    return msg[3]
            data = self.s.recv(1 << 20)
            if not data:
                raise ConnectionError("server closed")
            self.unp.feed(data)

    def images(self, cams, vehicle="PX4", image_type=0):
        reqs = [{"camera_name": c, "image_type": image_type,
                 "pixels_as_float": False, "compress": False,
                 "annotation_name": ""} for c in cams]
        return self.call("simGetImages", reqs, vehicle)


if __name__ == "__main__":
    r = Rpc()
    print("server version :", r.call("getServerVersion"))
    print("vehicles       :", r.call("listVehicles"))
    # Which camera names actually return pixels? Probe the documented defaults.
    for cam in ["0", "front_center", "bottom_center", "front_left", "back_center"]:
        try:
            resp = r.images([cam])
            n = len(resp[0].get("image_data_uint8") or b"")
            print(f"  cam {cam:<14} {resp[0].get('width')}x{resp[0].get('height')}  bytes={n}")
        except Exception as e:
            print(f"  cam {cam:<14} FAILED: {str(e)[:60]}")
