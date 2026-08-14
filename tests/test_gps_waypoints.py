"""The GPS -> local conversion, tested without a simulator.                       (SIM-31)

`gps_to_enu` and `gps_to_home_enu` are pure decisions, and this repo's convention is that pure
decisions are testable without bringing a stack up (see check_ekf_origin.origin_is_sane and
tests/test_gate_checks.py). A north/east swap or a sign flip would otherwise be caught only by
a flight — expensive, and only on a stack whose home happens to make it visible.
"""
import importlib.util
import math
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "run_scenario", Path(__file__).resolve().parent.parent / "scripts" / "run_scenario.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

# A reference with the vehicle AT the origin (x = y = 0), so origin-relative and home-relative
# coincide and each function can be checked on its own.
REF_AT_ORIGIN = {"ref_lat": 37.4123278, "ref_lon": -121.9948484, "ref_alt": 51.28,
                 "x": 0.0, "y": 0.0}


def test_origin_maps_to_zero():
    e, n, u = rs.gps_to_enu(REF_AT_ORIGIN["ref_lat"], REF_AT_ORIGIN["ref_lon"],
                            REF_AT_ORIGIN["ref_alt"], REF_AT_ORIGIN)
    assert abs(e) < 1e-6 and abs(n) < 1e-6 and abs(u) < 1e-6


def test_latitude_moves_north_not_east():
    """0.0001 deg of latitude is ~11.12 m north and nothing east.

    This is the assertion that catches a north/east swap, which is otherwise invisible on a
    square mission -- a swapped square is still a square.
    """
    e, n, _ = rs.gps_to_enu(REF_AT_ORIGIN["ref_lat"] + 0.0001, REF_AT_ORIGIN["ref_lon"],
                            REF_AT_ORIGIN["ref_alt"], REF_AT_ORIGIN)
    assert abs(e) < 0.01
    assert n == pytest.approx(11.12, abs=0.05)


def test_longitude_moves_east_and_shrinks_with_latitude():
    e, n, _ = rs.gps_to_enu(REF_AT_ORIGIN["ref_lat"], REF_AT_ORIGIN["ref_lon"] + 0.0001,
                            REF_AT_ORIGIN["ref_alt"], REF_AT_ORIGIN)
    assert abs(n) < 0.01
    # cos(37.41 deg) shrinks a degree of longitude; a flat 111320 would give 11.13 and fail.
    assert e == pytest.approx(111320 * 0.0001 * math.cos(math.radians(37.4123278)), abs=0.05)


def test_signs_are_not_flipped():
    south_e, south_n, _ = rs.gps_to_enu(REF_AT_ORIGIN["ref_lat"] - 0.0001,
                                        REF_AT_ORIGIN["ref_lon"] - 0.0001,
                                        REF_AT_ORIGIN["ref_alt"], REF_AT_ORIGIN)
    assert south_n < 0 and south_e < 0


def test_altitude_is_relative_to_ref_alt():
    _, _, u = rs.gps_to_enu(REF_AT_ORIGIN["ref_lat"], REF_AT_ORIGIN["ref_lon"],
                            REF_AT_ORIGIN["ref_alt"] + 20.0, REF_AT_ORIGIN)
    assert u == pytest.approx(20.0, abs=1e-6)


def test_home_relative_subtracts_the_vehicle_position():
    """THE BUG THIS FILE EXISTS FOR.

    offboard_control._build_square returns (x0 + x, y0 + y, z): waypoints are HOME-relative and
    the controller adds the vehicle's position back. A GPS point is absolute, so it must be
    offset by home first. The original code skipped that and flew correctly only because a cold
    start puts home near (0, 0) -- under --no-restart or `run_gate --reuse` it would have flown
    to home + (E, N) and reported small errors against the shifted target.
    """
    moved = dict(REF_AT_ORIGIN, x=30.0, y=40.0)     # NED: x north, y east
    lat, lon = REF_AT_ORIGIN["ref_lat"] + 0.0001, REF_AT_ORIGIN["ref_lon"]

    absolute = rs.gps_to_enu(lat, lon, REF_AT_ORIGIN["ref_alt"], moved)
    relative = rs.gps_to_home_enu(lat, lon, REF_AT_ORIGIN["ref_alt"], moved)

    # home_enu = (E, N) = (y, x) = (40, 30)
    assert relative[0] == pytest.approx(absolute[0] - 40.0, abs=1e-6)
    assert relative[1] == pytest.approx(absolute[1] - 30.0, abs=1e-6)
    # and the controller adding home back lands on the absolute point
    assert relative[0] + 40.0 == pytest.approx(absolute[0], abs=1e-6)
    assert relative[1] + 30.0 == pytest.approx(absolute[1], abs=1e-6)


def test_round_trip_reproduces_a_known_square():
    """The PR's own evidence, checked in rather than quoted."""
    R = rs.EARTH_RADIUS_M
    ref = REF_AT_ORIGIN

    def enu_to_gps(e, n, u):
        c = math.hypot(n, e) / R
        if c < 1e-12:
            return ref["ref_lat"], ref["ref_lon"], ref["ref_alt"] + u
        rl, rn = math.radians(ref["ref_lat"]), math.radians(ref["ref_lon"])
        lat = math.asin(math.cos(c) * math.sin(rl) + (n / (c * R)) * math.sin(c) * math.cos(rl))
        lon = rn + math.atan2(e * math.sin(c),
                              c * R * math.cos(rl) * math.cos(c) - n * math.sin(c) * math.sin(rl))
        return math.degrees(lat), math.degrees(lon), ref["ref_alt"] + u

    square = [(10.0, 0.0, 20.0), (10.0, 10.0, 20.0), (0.0, 10.0, 20.0), (0.0, 0.0, 20.0)]
    for want in square:
        lat, lon, alt = enu_to_gps(*want)
        got = rs.gps_to_enu(lat, lon, alt, ref)
        assert got == pytest.approx(want, abs=0.005)
