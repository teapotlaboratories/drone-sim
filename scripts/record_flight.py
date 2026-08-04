#!/usr/bin/env python3
"""Record the flight test to mp4, with live telemetry burned in.

SITL only. Nothing real is armed or flown.

Two camera feeds side by side (front_center, bottom_center) plus a telemetry band showing
the thing this run is about: AirSim's GROUND TRUTH altitude next to what PX4's EKF BELIEVES.
They disagree by ~35 m, which is why the vehicle never takes off.

cv2 does the encoding -- there is no ffmpeg in any of these containers.
"""
import os, subprocess, sys, time
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airsim_rpc_client import Rpc

CAMS = ["front_center", "bottom_center"]
SCALE = 3                 # 256x144 native is too small to read; upscale for legibility
BAND = 118                # telemetry band height
FPS = 10
EKF_Z = "/tmp/ekf_z.txt"  # background ros2 echo writes here
OUT = "/tmp/sim-flight.mp4"


def frame_of(resp):
    """ImageResponse -> BGR ndarray. image_data_uint8 is raw HxWx3."""
    buf = resp.get("image_data_uint8") or b""
    h, w = resp.get("height") or 0, resp.get("width") or 0
    if not buf or not h or not w:
        return np.zeros((144, 256, 3), np.uint8)
    a = np.frombuffer(buf, np.uint8)
    if a.size < h * w * 3:
        return np.zeros((h or 144, w or 256, 3), np.uint8)
    return a[: h * w * 3].reshape(h, w, 3)


def last_ekf_z():
    # `ros2 topic echo` interleaves "---" separators between samples. A leading "-" is NOT
    # enough to identify a number -- "---" passes that test and then float() raises, which
    # (when the try wrapped the whole loop) silently yielded None and printed "..." for the
    # entire first recording. Parse per line and keep looking.
    try:
        with open(EKF_Z) as f:
            for line in reversed(f.read().strip().splitlines()):
                try:
                    return float(line.strip())
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def put(img, text, xy, scale=0.45, colour=(235, 235, 235), thick=1):
    cv2.putText(img, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2,
                cv2.LINE_AA)
    cv2.putText(img, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick, cv2.LINE_AA)


def main():
    rpc = Rpc()
    probe = rpc.images(CAMS)
    h, w = probe[0]["height"], probe[0]["width"]
    tile_w, tile_h = w * SCALE, h * SCALE
    W, H = tile_w * len(CAMS), tile_h + BAND

    vw = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not vw.isOpened():
        print("FATAL: VideoWriter would not open", file=sys.stderr)
        return 1

    # Start the controller. Its stdout is the phase narration on the band.
    env = dict(os.environ)
    ctl = subprocess.Popen(
        ["bash", "-lc",
         "source /opt/ros/jazzy/setup.bash; source /ros2_ws/install/setup.bash 2>/dev/null; "
         "source /ros2_ws_src/install/setup.bash 2>/dev/null; "
         "exec ros2 run control offboard_control --ros-args "
         "-p result_path:=/tmp/res.json -p state_timeout_s:=60.0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)

    phase, outcome, n = "starting", None, 0
    t0 = time.time()
    import threading

    def pump():
        nonlocal phase, outcome
        for line in ctl.stdout:
            line = line.strip()
            if "state:" in line:
                phase = line.split("state:")[-1].strip()
            elif "armed" in line and "Disarm" not in line:
                phase = "ARMED"
            elif "FAILED" in line:
                outcome = line.split("]")[-1].strip()
            print(line, flush=True)

    threading.Thread(target=pump, daemon=True).start()

    while ctl.poll() is None and time.time() - t0 < 240:
        loop = time.time()
        try:
            resp = rpc.images(CAMS)
            tiles = [cv2.resize(frame_of(r), (tile_w, tile_h),
                                interpolation=cv2.INTER_NEAREST) for r in resp]
            gt = rpc.call("simGetGroundTruthKinematics", "PX4")["position"]["z_val"]
        except Exception as e:
            print(f"capture hiccup: {str(e)[:70]}", flush=True)
            time.sleep(0.2)
            continue

        canvas = np.zeros((H, W, 3), np.uint8)
        canvas[:tile_h] = np.hstack(tiles)
        canvas[tile_h:] = (22, 20, 18)
        for i, c in enumerate(CAMS):
            put(canvas, c, (i * tile_w + 10, 22), 0.5)

        ekf = last_ekf_z()
        y = tile_h
        put(canvas, "SIMULATOR FLIGHT TEST  ~  SITL ONLY, NOTHING REAL ARMED OR FLOWN",
            (12, y + 22), 0.5, (120, 200, 255))
        put(canvas, f"t+{time.time()-t0:5.1f}s   phase: {phase}", (12, y + 46), 0.5)

        # The whole point of the video: truth vs belief, side by side.
        # Derive the label; an earlier version hardcoded "(on the ground)", which stayed on
        # screen through a 10 m hover and read as a false statement in the success recording.
        where = "on the ground" if abs(gt) < 1.0 else f"{abs(gt):.1f} m up"
        put(canvas, f"AirSim TRUTH  z = {gt:+7.3f} m   ({where})",
            (12, y + 70), 0.5, (140, 255, 140))
        if ekf is None:
            put(canvas, "PX4 EKF       z =    ...", (12, y + 92), 0.5, (140, 160, 255))
        else:
            put(canvas, f"PX4 EKF       z = {ekf:+7.3f} m   -> believes it is "
                        f"{abs(ekf):.1f} m UP   (off by {abs(ekf - gt):.1f} m)",
                (12, y + 92), 0.5, (140, 160, 255))
        if outcome:
            put(canvas, outcome[:74], (W - 470, y + 22), 0.46, (110, 110, 255))

        vw.write(canvas)
        n += 1
        time.sleep(max(0.0, 1.0 / FPS - (time.time() - loop)))

    # Hold the final frame so the outcome is readable rather than a flash.
    for _ in range(FPS * 3):
        vw.write(canvas)
        n += 1
    vw.release()
    try:
        ctl.kill()
    except Exception:
        pass
    print(f"\nwrote {OUT}  frames={n}  {W}x{H} @ {FPS}fps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
