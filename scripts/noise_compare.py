#!/usr/bin/env python3
"""Locate and visualise the excess high-frequency noise in AirSim's capture vs Unreal's render.

Foliage is genuinely high-frequency in BOTH images, so raw sharpness says nothing -- a crop
picked by eye would just find leaves. What matters is where AirSim carries MORE high-frequency
energy than the native render of the identical view, which is the signature of temporal
accumulation failing to converge rather than of real detail.

So: high-pass both (residual against a median filter, which keeps edges and isolates speckle),
then pick the window maximising airsim_hf - native_hf and magnify it with nearest-neighbour so
individual pixels stay visible instead of being smoothed away by the resize.

Runs inside drone-sim/airsim-client.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path("/out")


def crop_letterbox(img, tol=10):
    g = img.max(axis=2)
    rows = np.where(g.max(axis=1) > tol)[0]
    cols = np.where(g.max(axis=0) > tol)[0]
    if rows.size == 0 or cols.size == 0:
        return img
    return img[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def highfreq(bgr):
    """Per-pixel high-frequency energy: |image - median3|, summed over colour.

    A median filter is used rather than a Gaussian because speckle is exactly what a median
    removes while leaving genuine edges intact, so the residual is dominated by the noise.
    """
    med = cv2.medianBlur(bgr, 3)
    return np.abs(bgr.astype(np.int16) - med.astype(np.int16)).sum(axis=2).astype(np.float32)


def best_window(diff, win, stride=16):
    """Window maximising mean excess high-frequency energy."""
    h, w = diff.shape
    wh, ww = win
    integral = cv2.integral(diff)
    best, best_xy = -1e18, (0, 0)
    for y in range(0, h - wh + 1, stride):
        for x in range(0, w - ww + 1, stride):
            s = (integral[y + wh, x + ww] - integral[y, x + ww]
                 - integral[y + wh, x] + integral[y, x])
            if s > best:
                best, best_xy = s, (x, y)
    return best_xy


def bar(w, text, sub, h=46):
    b = np.zeros((h, w, 3), np.uint8)
    cv2.putText(b, text, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(b, sub, (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (170, 210, 170), 1, cv2.LINE_AA)
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--crop-w", type=int, default=360)
    ap.add_argument("--crop-h", type=int, default=240)
    ap.add_argument("--zoom", type=int, default=3)
    args = ap.parse_args()

    a = cv2.imread(str(OUT / f"{args.scene}_airsim.png"))
    n = crop_letterbox(cv2.imread(str(OUT / f"{args.scene}_native.png")))
    if n.shape[:2] != a.shape[:2]:
        n = cv2.resize(n, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)

    ha, hn = highfreq(a), highfreq(n)
    excess = cv2.GaussianBlur(ha - hn, (0, 0), 9)
    x, y = best_window(excess, (args.crop_h, args.crop_w))

    ca = a[y:y + args.crop_h, x:x + args.crop_w]
    cn = n[y:y + args.crop_h, x:x + args.crop_w]
    za = cv2.resize(ca, None, fx=args.zoom, fy=args.zoom, interpolation=cv2.INTER_NEAREST)
    zn = cv2.resize(cn, None, fx=args.zoom, fy=args.zoom, interpolation=cv2.INTER_NEAREST)

    hf_a = float(ha[y:y + args.crop_h, x:x + args.crop_w].mean())
    hf_n = float(hn[y:y + args.crop_h, x:x + args.crop_w].mean())

    w = za.shape[1]
    sheet = np.hstack([
        np.vstack([bar(w, f"AirSim simGetImages  ({args.zoom}x, nearest)",
                       f"high-freq energy {hf_a:.2f}  <- speckle on foliage"), za]),
        np.full((za.shape[0] + 46, 8, 3), 255, np.uint8),
        np.vstack([bar(w, f"Unreal HighResShot  ({args.zoom}x, nearest)",
                       f"high-freq energy {hf_n:.2f}"), zn]),
    ])
    cv2.imwrite(str(OUT / f"noise_{args.scene}.png"), sheet)

    # Context shot: where in the frame the crop came from.
    ctx = a.copy()
    cv2.rectangle(ctx, (x, y), (x + args.crop_w, y + args.crop_h), (0, 0, 255), 3)
    cv2.imwrite(str(OUT / f"noise_{args.scene}_context.png"), ctx)

    print(json.dumps({
        "scene": args.scene, "crop_xy": [int(x), int(y)],
        "hf_airsim": round(hf_a, 3), "hf_native": round(hf_n, 3),
        "ratio": round(hf_a / hf_n, 3) if hf_n else None,
        "hf_airsim_full": round(float(ha.mean()), 3),
        "hf_native_full": round(float(hn.mean()), 3),
        "ratio_full": round(float(ha.mean() / hn.mean()), 3),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
