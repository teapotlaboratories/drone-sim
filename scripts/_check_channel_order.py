#!/usr/bin/env python3
"""Verify that the raw uint8 frame from simGetImages really is BGR.

The whole washout investigation is being read off per-channel statistics, so a swapped red and
blue would not just mis-describe a colour cast -- it would invert it. This settles it against
AirSim's own PNG encoder rather than against an assumption: the SAME view is grabbed twice, once
as raw bytes (reshaped as BGR, the way _capture_client.py does it) and once as a compressed PNG
(encoded by AirSim, decoded by cv2 into known-good BGR). If the raw path is right the two agree.
"""
import sys

import cosysairsim as airsim
import cv2
import numpy as np

client = airsim.MultirotorClient()
client.confirmConnection()

pose = airsim.Pose(airsim.Vector3r(50.0, -30.0, -10.0),
                   airsim.euler_to_quaternion(0.0, 0.0, float(np.deg2rad(315.0))))
for _ in range(60):
    client.simSetVehiclePose(pose, True, "Drone")

raw, png = client.simGetImages([
    airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False),
    airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, True),
], "Drone")

a = np.frombuffer(raw.image_data_uint8, dtype=np.uint8).reshape(raw.height, raw.width, 3)
b = cv2.imdecode(np.frombuffer(png.image_data_uint8, dtype=np.uint8), cv2.IMREAD_COLOR)

print(f"raw  as-BGR  B={a[:,:,0].mean():7.2f} G={a[:,:,1].mean():7.2f} R={a[:,:,2].mean():7.2f}")
print(f"png  (BGR)   B={b[:,:,0].mean():7.2f} G={b[:,:,1].mean():7.2f} R={b[:,:,2].mean():7.2f}")

d_same = float(np.abs(a.astype(int) - b.astype(int)).mean())
d_swap = float(np.abs(a[:, :, ::-1].astype(int) - b.astype(int)).mean())
print(f"mean|raw - png|        = {d_same:6.2f}")
print(f"mean|raw_swapped - png|= {d_swap:6.2f}")
print("VERDICT:", "raw is BGR (as assumed)" if d_same < d_swap else "raw is RGB -- CLIENT BUG")
sys.exit(0)
