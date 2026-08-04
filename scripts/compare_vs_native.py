#!/usr/bin/env python3
"""Score the paired AirSim/native captures and build the side-by-side sheets.

Runs inside drone-sim/airsim-client (needs cv2). Two corrections have to happen before any
number means anything:

1. `HighResShot` renders at the VIEWPORT's aspect ratio and letterboxes to the requested size,
   so the native frame arrives with black bars. Left in, they drag the mean down and inflate
   the contrast range -- they would manufacture exactly the difference being measured.
2. The two frames can differ in size, so they are matched before any per-pixel comparison.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path("/out")


def crop_letterbox(img, tol=10):
    """Drop uniformly near-black rows/columns at the edges (HighResShot's letterbox bars)."""
    g = img.max(axis=2)
    rows = np.where(g.max(axis=1) > tol)[0]
    cols = np.where(g.max(axis=0) > tol)[0]
    if rows.size == 0 or cols.size == 0:
        return img
    return img[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def stats(bgr):
    rgb = bgr[:, :, ::-1].astype(np.float64)
    lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    p01, p99 = np.percentile(lum, 1), np.percentile(lum, 99)
    return {
        "mean": round(float(lum.mean()), 2),
        "std": round(float(lum.std()), 2),
        "p01": round(float(p01), 2),
        "p99": round(float(p99), 2),
        "range": round(float(p99 - p01), 2),
        "sat": round(float(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[:, :, 1].mean()), 2),
    }


def label_bar(w, lines, h=54):
    bar = np.zeros((h, w, 3), np.uint8)
    cv2.putText(bar, lines[0], (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(bar, lines[1], (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 210, 170), 1, cv2.LINE_AA)
    return bar


def main():
    scenes = sorted({p.name.rsplit("_", 1)[0] for p in OUT.glob("*_airsim.png")})
    order = ["treeline_close", "treeline_mid", "treeline_far", "park_high", "lakeside", "path_north"]
    scenes = [s for s in order if s in scenes] + [s for s in scenes if s not in order]

    rows, table = [], []
    for name in scenes:
        a = cv2.imread(str(OUT / f"{name}_airsim.png"))
        n = cv2.imread(str(OUT / f"{name}_native.png"))
        if a is None or n is None:
            continue
        n = crop_letterbox(n)
        if n.shape[:2] != a.shape[:2]:
            n = cv2.resize(n, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)

        sa, sn = stats(a), stats(n)
        table.append({"scene": name, "airsim": sa, "native": sn,
                      "d_mean": round(sa["mean"] - sn["mean"], 2),
                      "d_range": round(sa["range"] - sn["range"], 2),
                      "d_sat": round(sa["sat"] - sn["sat"], 2)})

        w = a.shape[1]
        la = label_bar(w, [f"AirSim simGetImages - {name}",
                           f"mean {sa['mean']}  contrast {sa['range']}  sat {sa['sat']}"])
        ln = label_bar(w, [f"Unreal HighResShot - {name}",
                           f"mean {sn['mean']}  contrast {sn['range']}  sat {sn['sat']}"])
        pair = np.hstack([np.vstack([la, a]),
                          np.full((a.shape[0] + la.shape[0], 6, 3), 255, np.uint8),
                          np.vstack([ln, n])])
        cv2.imwrite(str(OUT / f"pair_{name}.png"), pair)
        rows.append(pair)

    if rows:
        width = max(r.shape[1] for r in rows)
        padded = [np.hstack([r, np.zeros((r.shape[0], width - r.shape[1], 3), np.uint8)])
                  if r.shape[1] < width else r for r in rows]
        sheet = np.vstack([x for r in padded for x in (r, np.zeros((10, width, 3), np.uint8))])
        cv2.imwrite(str(OUT / "all_pairs.png"), sheet)

    (OUT / "comparison.json").write_text(json.dumps(table, indent=2))
    print(f"{'scene':<16} {'AirSim mean':>12} {'native mean':>12} {'d_mean':>8} "
          f"{'AS contrast':>12} {'nat contrast':>13} {'d_range':>9} {'d_sat':>7}")
    for t in table:
        print(f"{t['scene']:<16} {t['airsim']['mean']:>12.2f} {t['native']['mean']:>12.2f} "
              f"{t['d_mean']:>8.2f} {t['airsim']['range']:>12.2f} {t['native']['range']:>13.2f} "
              f"{t['d_range']:>9.2f} {t['d_sat']:>7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
