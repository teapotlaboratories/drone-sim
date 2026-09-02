"""Turn a 32FC1 depth image in metres into something a browser can show.      (SIM-48)

PURE, and numpy-only on purpose: no rclpy, no cv_bridge, no ROS. `depth_view.py` cannot be
imported off-target, and this is the part with the decisions in it -- so it lives where
`tests/` can exercise the exact arithmetic the aircraft runs, the same arrangement as
`control/manual_policy.py` and `chase_camera/mjpeg.py`.

THE TWO DECISIONS
-----------------
**A FIXED RANGE, NOT PER-FRAME AUTO-SCALING.** Normalising each frame to its own min and max
is the obvious thing and it is wrong here. It makes the picture flicker as the aircraft moves,
and -- the real objection -- it makes a colour mean a different distance in every frame, so no
two frames can be compared. With `near`/`far` fixed, a colour IS a distance, which is what a
site survey is asking: *is that wall 5 m away or 20*.

**INVALID RETURNS GET THEIR OWN COLOUR.** AirSim reports sky as a very large value, and NaN and
inf are both possible. Folding those onto the far end of the ramp draws a confident distance
where there is no measurement. This project already has that rule for numbers on a page -- a
value that never arrived must not look like a measurement -- and it applies to pixels.
"""

import numpy as np

# Anything at or beyond this is treated as "no return" rather than as a distance. AirSim's sky
# comes back around 1e4-1e5 m depending on the scene; 1000 m is far beyond anything a survey
# cares about and well below the sky value, so it separates the two cleanly.
NO_RETURN_M = 1000.0

# The colour drawn where there is no measurement. Near-black, and deliberately OUTSIDE the
# colormap's own range so it cannot be mistaken for a reading at any distance.
NO_RETURN_BGR = (18, 18, 18)


def normalise(depth_m: np.ndarray, near: float, far: float):
    """(u8, valid) -- depth mapped to 0..255 across [near, far], and a validity mask.

    0 is `near`, 255 is `far`. Values outside the band are CLAMPED rather than marked
    invalid: a wall closer than `near` is still a wall, and saying "no data" there would hide
    the single most important thing in the frame. Only non-finite and no-return pixels are
    invalid.
    """
    d = np.asarray(depth_m, dtype=np.float32)
    if far <= near:
        raise ValueError(f"far ({far}) must be greater than near ({near})")

    valid = np.isfinite(d) & (d < NO_RETURN_M)
    # Fill invalid pixels with `near` BEFORE scaling: NaN propagates through arithmetic and
    # through the uint8 cast it becomes 0, which is indistinguishable from "very close".
    # The mask is what decides the output, but the arithmetic must not produce garbage first.
    filled = np.where(valid, d, near)
    scaled = (filled - near) / (far - near)
    return (np.clip(scaled, 0.0, 1.0) * 255.0).astype(np.uint8), valid


def apply_mask(bgr: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Paint the no-return colour wherever the mask is False."""
    out = np.array(bgr, copy=True)
    out[~valid] = NO_RETURN_BGR
    return out


def legend_stops(near: float, far: float, n: int = 5):
    """The metre labels for the scale drawn beside the pane.

    A colourised depth image without a legend is decoration -- it looks like data and cannot be
    read. Returned here rather than hard-coded in the page so the labels always match the
    `near`/`far` the node is actually running with.
    """
    return [round(near + (far - near) * i / (n - 1), 1) for i in range(n)]
