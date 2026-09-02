"""Turning 32FC1 depth into a picture, and the two decisions inside it.          (SIM-48)

Off-target: no simulator, no ROS 2. `depth_view.py` imports rclpy and cv_bridge, so the part
with the judgement in it lives in `depth_view/colorize.py`, which imports only numpy — the same
arrangement as `control/manual_policy.py` and `chase_camera/mjpeg.py`, and for the same reason:
a test that needs the target can only ever pass on the target.

The failure mode here is silent and confident. A wrong colour map does not error; it draws a
picture that looks exactly like a right one, and an operator judging "is that wall 5 m away or
20" reads it as fact.
"""
import importlib.util
import re
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "ros2_ws/src/depth_view/depth_view"
_spec = importlib.util.spec_from_file_location("colorize", PKG / "colorize.py")
cz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cz)


# --- the range is fixed, and that is the point ---------------------------------------

def test_near_and_far_map_to_the_ends():
    u8, _ = cz.normalise(np.array([[0.5, 40.0]], np.float32), 0.5, 40.0)
    assert u8[0, 0] == 0 and u8[0, 1] == 255


def test_the_same_distance_gives_the_same_colour_in_every_frame():
    """THE WHOLE REASON THE RANGE IS FIXED. Per-frame auto-scaling is the obvious
    implementation and it makes a colour mean a different distance in each frame, so two
    frames cannot be compared — which is exactly the question a site survey asks."""
    lonely = np.array([[10.0, 10.0]], np.float32)             # nothing else in view
    busy = np.array([[10.0, 0.6, 39.0, 25.0]], np.float32)    # a full range of returns
    a, _ = cz.normalise(lonely, 0.5, 40.0)
    b, _ = cz.normalise(busy, 0.5, 40.0)
    assert a[0, 0] == b[0, 0], "10 m rendered differently depending on the rest of the frame"


def test_midpoint_is_the_middle_of_the_ramp():
    u8, _ = cz.normalise(np.array([[20.25]], np.float32), 0.5, 40.0)
    assert 126 <= int(u8[0, 0]) <= 129


def test_beyond_the_band_is_clamped_but_still_valid():
    """A wall closer than `near` is still a wall. Marking it "no data" would hide the single
    most important thing in the frame."""
    u8, valid = cz.normalise(np.array([[0.1, 90.0]], np.float32), 0.5, 40.0)
    assert u8[0, 0] == 0 and u8[0, 1] == 255
    assert valid.all(), "a clamped measurement is still a measurement"


def test_far_must_exceed_near():
    with pytest.raises(ValueError):
        cz.normalise(np.array([[1.0]], np.float32), 10.0, 10.0)


# --- invalid returns are not distances ------------------------------------------------

def test_nan_inf_and_sky_are_invalid():
    """AirSim reports sky as a very large value. Folding that onto the far end of the ramp
    would draw a confident distance where there is no measurement."""
    d = np.array([[np.nan, np.inf, -np.inf, 1e5, cz.NO_RETURN_M]], np.float32)
    _, valid = cz.normalise(d, 0.5, 40.0)
    assert not valid.any()


def test_nan_does_not_leak_through_the_arithmetic():
    """NaN propagates, and through the uint8 cast it becomes 0 — indistinguishable from "very
    close". The mask decides the output, but the arithmetic must not produce garbage first."""
    u8, valid = cz.normalise(np.array([[np.nan]], np.float32), 0.5, 40.0)
    assert np.isfinite(u8).all()
    assert not valid[0, 0]


def test_invalid_pixels_are_painted_their_own_colour():
    bgr = np.full((1, 3, 3), 200, np.uint8)
    valid = np.array([[True, False, True]])
    out = cz.apply_mask(bgr, valid)
    assert tuple(out[0, 1]) == cz.NO_RETURN_BGR
    assert tuple(out[0, 0]) == (200, 200, 200)


def test_the_no_return_colour_is_not_on_the_ramp():
    """It has to be distinguishable from every real reading, or "no measurement" reads as a
    distance. Near-black sits outside a TURBO/INFERNO ramp at both ends."""
    assert max(cz.NO_RETURN_BGR) < 40


def test_apply_mask_does_not_mutate_its_input():
    bgr = np.full((1, 2, 3), 100, np.uint8)
    cz.apply_mask(bgr, np.array([[False, False]]))
    assert (bgr == 100).all()


# --- the legend must match the colour map ---------------------------------------------

def test_legend_spans_the_range_inclusively():
    stops = cz.legend_stops(0.5, 40.0, 5)
    assert stops[0] == 0.5 and stops[-1] == 40.0 and len(stops) == 5
    assert stops == sorted(stops)


def test_the_page_does_not_hard_code_the_range():
    """A legend that disagrees with the colour map is worse than no legend, because it looks
    authoritative. The page draws its labels from /depth_view/range."""
    app = (REPO / "ros2_ws/src/webui/webui/static/app.js").read_text()
    assert "/depth_view/range" in app
    assert "depth-marks" in app


def test_the_no_return_swatch_matches_the_code():
    """The legend's swatch is CSS and the pixel colour is Python; they must agree or the key
    labels a colour the image never contains."""
    css = (REPO / "ros2_ws/src/webui/webui/static/style.css").read_text()
    b, g, r = cz.NO_RETURN_BGR
    assert f"rgb({r},{g},{b})" in css.replace(" ", ""), \
        f"legend swatch does not match NO_RETURN_BGR={cz.NO_RETURN_BGR}"


# --- the wiring ------------------------------------------------------------------------

def test_depth_topic_is_readable_by_the_browser():
    allow = importlib.util.spec_from_file_location(
        "allowlist", REPO / "ros2_ws/src/webui/webui/allowlist.py")
    mod = importlib.util.module_from_spec(allow)
    allow.loader.exec_module(mod)
    assert "/depth_view/*" in mod.TOPICS_SUB
    assert not any("depth" in t for t in mod.TOPICS_PUB), "depth must stay read-only"


def test_the_node_refuses_an_unexpected_encoding():
    """If settings.json ever changes ImageType, or a different camera is wired in, the
    arithmetic would silently produce a plausible picture of the wrong thing."""
    src = (PKG / "depth_view.py").read_text()
    assert 'msg.encoding != "32FC1"' in src


def test_the_node_publishes_reliable():
    """web_video_server subscribes RELIABLE; a BEST_EFFORT publisher matches nothing while
    looking perfectly healthy. That cost a bring-up during SIM-45."""
    src = (PKG / "depth_view.py").read_text()
    pub = src[src.index("self.pub = self.create_publisher"):]
    assert "ReliabilityPolicy.RELIABLE" in pub[:400]


def test_pkill_patterns_cannot_match_the_entrypoint():
    """`depth_view` is now a package name inside sim_up.sh's sim-ros2 entrypoint copy loop, so
    a bare `pkill -f depth_view` would match PID 44 — the shell supervising the uXRCE-DDS
    agent. Same trap as SIM-46, one ticket later."""
    sim_up = (REPO / "scripts/sim_up.sh").read_text()
    start = sim_up.index("docker run -d --name sim-ros2")
    entry = sim_up[start:sim_up.index("exec sleep infinity'", start)]
    assert "depth_view" in entry, "the copy loop should mention it — that is the hazard"
    web_ui = (REPO / "scripts/web_ui.sh").read_text()
    for pat in re.findall(r'"(lib/[a-z_]+/[a-z_]+)"', web_ui) + \
               re.findall(r'(\w+\.launch\.py)', web_ui):
        assert pat not in entry, f"pkill pattern {pat!r} can match the sim-ros2 entrypoint"


# --- the spotlight layout ---------------------------------------------------------- (SIM-49)

def test_spotlight_never_touches_src_or_moves_the_dom():
    """A swap must be a re-layout only. Reassigning `src` or moving the <img> tears down the
    multipart stream and re-opens it, so the view just asked for goes blank for about a second
    — at exactly the moment someone wanted to look at it."""
    app = (REPO / "ros2_ws/src/webui/webui/static/app.js").read_text()
    fn = app[app.index("function setSpotlight("):]
    fn = fn[:fn.index("\n}")]
    for forbidden in (".src", "appendChild", "insertBefore", "prepend(", "replaceChild"):
        assert forbidden not in fn, f"setSpotlight uses {forbidden} — that restarts the stream"
    assert "classList.toggle('spot'" in fn


def test_spotlight_keys_do_not_collide_with_flight_keys():
    """W A S D, R F and Q E fly the aircraft. A layout shortcut that overlapped them would
    move the vehicle on a mis-hit."""
    app = (REPO / "ros2_ws/src/webui/webui/static/app.js").read_text()
    flight = set("wasdrfqe")
    spot = re.search(r"\['1', '2', '3'\]\.indexOf", app)
    assert spot, "the spotlight shortcuts should be digits"
    assert not (flight & set("123"))


def test_spotlight_choice_survives_a_reload_but_never_breaks_the_page():
    """localStorage throws outright in a private window or with site data blocked, and the
    page must still render."""
    app = (REPO / "ros2_ws/src/webui/webui/static/app.js").read_text()
    assert app.count("localStorage") == 2
    for m in re.finditer(r"localStorage", app):
        window = app[max(0, m.start() - 220):m.start() + 120]
        assert "try {" in window, "every localStorage access must be guarded"


def test_an_impossible_range_refuses_to_start():
    """`normalise` raises on far <= near. Without validation the node came up healthy,
    published a range the legend drew, then threw out of the subscription at 10 Hz."""
    src = (PKG / "depth_view.py").read_text()
    init = src[src.index("def __init__"):src.index("def _on_depth")]
    assert "if self.far <= self.near:" in init and "raise ValueError" in init


def test_missing_telemetry_never_renders_as_the_string_null():
    """`${fmt(a)} / ${fmt(b)}` interpolates a null as "null", and because that is a non-null
    string put() treated it as a measurement and did not mark it stale."""
    app = (REPO / "ros2_ws/src/webui/webui/static/app.js").read_text()
    assert "const join = (parts" in app
    for bad in ("`${fmt(m.vx", "map((v) => fmt(v, 2)).join("):
        assert bad not in app, f"a null can still reach a template literal via {bad!r}"


def test_the_vendored_control_library_is_tracked_by_git():
    """It was untracked and not ignored — simply never added. A fresh clone would build a
    sim-webui whose page loads with no ROSLIB, so every button silently does nothing: exactly
    the failure vendoring exists to prevent, and against CLAUDE.md's reproducibility goal."""
    import subprocess
    lib = "ros2_ws/src/webui/webui/static/vendor/roslib.min.js"
    r = subprocess.run(["git", "ls-files", "--error-unmatch", lib],
                       cwd=REPO, capture_output=True)
    assert r.returncode == 0, f"{lib} is not tracked by git — a fresh clone would ship no ROSLIB"
