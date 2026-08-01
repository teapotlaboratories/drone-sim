#!/usr/bin/env python3
"""Compose the C-09 before/after video from the two recorded flight runs.

Both source clips come from scripts/record_lane_c_flight.py and are already the same
geometry, so this is title cards plus a labelled banner over each segment -- no rescaling.

SITL only; neither source clip involves real hardware.

Usage:  compose_c09_beforeafter.py <before.mp4> <after.mp4> <out.mp4>
"""
import sys
import cv2
import numpy as np

FPS = 10
BANNER = 40          # label strip added above each segment
AFTER_SPEED = 2      # the success run is 88 s; every 2nd frame keeps it watchable

RED = (90, 90, 235)
GREEN = (120, 220, 140)
DIM = (170, 170, 170)


def text(img, s, xy, scale=0.6, colour=(240, 240, 240), thick=1, centre=False):
    if centre:
        (w, _), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        xy = (int(xy[0] - w / 2), xy[1])
    cv2.putText(img, s, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, s, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick, cv2.LINE_AA)


def card(W, H, lines, seconds=3.5):
    """lines: (text, scale, colour, y_offset_from_centre)"""
    base = np.zeros((H, W, 3), np.uint8)
    base[:] = (20, 18, 16)
    cy = H // 2
    for s, sc, col, dy in lines:
        text(base, s, (W // 2, cy + dy), sc, col, 2 if sc >= 0.8 else 1, centre=True)
    return [base] * int(FPS * seconds)


def segment(path, W, H, label, colour, stride=1, limit=None):
    cap = cv2.VideoCapture(path)
    out, i = [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % stride == 0:
            canvas = np.zeros((H, W, 3), np.uint8)
            canvas[:] = (20, 18, 16)
            canvas[BANNER:BANNER + fr.shape[0], :fr.shape[1]] = fr
            text(canvas, label, (14, 27), 0.62, colour, 2)
            out.append(canvas)
            if limit and len(out) >= limit:
                break
        i += 1
    cap.release()
    if not out:
        raise SystemExit(f"FATAL: no frames decoded from {path}")
    return out


def main():
    before_p, after_p, out_p = sys.argv[1], sys.argv[2], sys.argv[3]
    probe = cv2.VideoCapture(before_p)
    ok, fr = probe.read()
    probe.release()
    if not ok:
        raise SystemExit(f"FATAL: cannot read {before_p}")
    h, w = fr.shape[:2]
    W, H = w, h + BANNER

    frames = []
    frames += card(W, H, [
        ("C-09", 1.5, (240, 240, 240), -70),
        ("The vehicle arms, and never climbs", 0.8, DIM, -20),
        ("Lane A controller, unchanged, against Lane C", 0.55, DIM, 14),
        ("SITL only  -  nothing real armed or flown", 0.5, (120, 200, 255), 52),
    ], 4)

    # Trim the failure run: the whole point is that nothing changes, so 22 s carries it.
    frames += segment(before_p, W, H, "BEFORE  -  0/4 waypoints, timeout in state takeoff",
                      RED, stride=1, limit=FPS * 22)

    frames += card(W, H, [
        ("Not lockstep. Not the sensors.", 0.85, (240, 240, 240), -86),
        ("baro 122.883 m   and   GPS 123.280 m   AGREE", 0.55, DIM, -50),
        ("PX4's EKF origin was set before the vehicle settled,", 0.55, DIM, -18),
        ("and an EKF origin is set once.", 0.55, DIM, 8),
        ("ref_alt  88.113 m   ->   123.280 m", 0.7, GREEN, 48),
        ("restart PX4 after the vehicle settles  -  no config change", 0.5, (120, 200, 255), 82),
    ], 6)

    frames += segment(after_p, W, H, "AFTER  -  4/4 waypoints, max error 0.79 m  (2x)",
                      GREEN, stride=AFTER_SPEED)

    frames += card(W, H, [
        ("Lane C flies", 1.3, GREEN, -60),
        ("The controller was never patched -", 0.6, DIM, -10),
        ("byte-identical to the one that scores 10/10 in Gazebo.", 0.6, DIM, 18),
        ("Remaining: C-10, make the ordering deterministic.", 0.52, (120, 200, 255), 58),
    ], 5)

    vw = cv2.VideoWriter(out_p, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not vw.isOpened():
        raise SystemExit("FATAL: VideoWriter would not open")
    for f in frames:
        vw.write(f)
    vw.release()
    print(f"wrote {out_p}  frames={len(frames)}  {W}x{H} @ {FPS}fps  "
          f"= {len(frames)/FPS:.1f}s")


if __name__ == "__main__":
    main()
