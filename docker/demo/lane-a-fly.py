#!/usr/bin/env python3
"""Fly the SITL drone via MAVLink: act as a GCS, arm, take off, hover, land.

SITL ONLY — this talks to PX4 SITL over UDP 14550 on loopback. It never touches real
hardware.

Why a GCS heartbeat is needed: PX4's rcAndDataLinkCheck refuses to arm with
"Preflight Fail: No connection to the ground control station". Rather than disable a safety
check, this script *is* a minimal GCS — it sends HEARTBEAT at 1 Hz, which is what the check
is actually looking for.

Commands are sent as MAVLink (not typed into the pxh shell) so we get COMMAND_ACK results
and can read altitude back instead of guessing.
"""
import os, sys, time
from pymavlink import mavutil

ALT = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
HOVER = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

def log(*a): print("   [fly]", *a, flush=True)

# Port choice matters when QGC is also running: PX4 SITL streams GCS telemetry to 14550
# and onboard/offboard telemetry to 14540. Binding 14550 here STEALS the GCS link and QGC
# shows "Comms Lost". 14540 is the correct port for programmatic control, and it lets QGC
# and this script run side by side — which is also how the real vehicle is wired.
PORT = int(os.environ.get('MAVLINK_PORT', '14540'))
log(f"binding udpin:0.0.0.0:{PORT} (14540 = offboard API; 14550 is left for QGC)")
m = mavutil.mavlink_connection(f'udpin:0.0.0.0:{PORT}', source_system=255)
log("waiting for PX4 heartbeat...")
if not m.wait_heartbeat(timeout=60):
    log("FAIL: no heartbeat from PX4"); sys.exit(1)
log(f"connected: sys={m.target_system} comp={m.target_component}")

def beat():
    m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                         mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)

# Heartbeat for a while so the data-link check clears, and let EKF2 converge.
log("sending GCS heartbeats so the data-link arming check clears...")
t0 = time.time()
while time.time() - t0 < 20:
    beat(); m.recv_match(blocking=False); time.sleep(0.5)

def wait_ack(cmd, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        beat()
        msg = m.recv_match(type='COMMAND_ACK', blocking=True, timeout=1)
        if msg and msg.command == cmd:
            names = {0:'ACCEPTED',1:'TEMP_REJECTED',2:'DENIED',3:'UNSUPPORTED',4:'FAILED',5:'IN_PROGRESS'}
            return names.get(msg.result, str(msg.result))
    return 'NO_ACK'

def alt():
    msg = m.recv_match(type='LOCAL_POSITION_NED', blocking=True, timeout=3)
    return (-msg.z if msg else None)

log("arming...")
m.mav.command_long_send(m.target_system, m.target_component,
                        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)
r = wait_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
log(f"arm result: {r}")
if r != 'ACCEPTED':
    log("arming refused — dumping recent STATUSTEXT for the reason")
    t = time.time()
    while time.time() - t < 5:
        beat()
        s = m.recv_match(type='STATUSTEXT', blocking=True, timeout=1)
        if s: log("   px4:", s.text)
    sys.exit(2)

# MAV_CMD_NAV_TAKEOFF is ACKed by PX4 but does NOT by itself enter takeoff mode: the
# vehicle arms, sits there, and PX4 auto-disarms ("Disarmed by auto preflight disarming").
# The canonical PX4 sequence is: set MIS_TAKEOFF_ALT, then switch to AUTO.TAKEOFF via
# DO_SET_MODE with PX4's custom main/sub mode numbers.
PX4_MAIN_AUTO = 4
PX4_SUB_AUTO_TAKEOFF = 2

log(f"setting MIS_TAKEOFF_ALT = {ALT} m")
m.mav.param_set_send(m.target_system, m.target_component, b'MIS_TAKEOFF_ALT',
                     float(ALT), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
t0 = time.time()
while time.time() - t0 < 5:
    beat()
    pv = m.recv_match(type='PARAM_VALUE', blocking=True, timeout=1)
    if pv and pv.param_id.strip('\x00') == 'MIS_TAKEOFF_ALT':
        log(f"   confirmed MIS_TAKEOFF_ALT = {pv.param_value:.1f}")
        break

log("switching to AUTO.TAKEOFF")
m.mav.command_long_send(m.target_system, m.target_component,
                        mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
                        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                        PX4_MAIN_AUTO, PX4_SUB_AUTO_TAKEOFF, 0, 0, 0, 0)
log(f"set-mode result: {wait_ack(mavutil.mavlink.MAV_CMD_DO_SET_MODE)}")

log("climbing / hovering — reporting altitude:")
t0 = time.time(); peak = 0.0
while time.time() - t0 < HOVER:
    beat()
    a = alt()
    if a is not None:
        peak = max(peak, a)
        if int(time.time() - t0) % 4 == 0:
            log(f"   t={int(time.time()-t0):3d}s  alt={a:6.2f} m")
    time.sleep(1)
log(f"peak altitude: {peak:.2f} m")

log("landing...")
m.mav.command_long_send(m.target_system, m.target_component,
                        mavutil.mavlink.MAV_CMD_NAV_LAND, 0, 0, 0, 0, 0, 0, 0, 0)
log(f"land result: {wait_ack(mavutil.mavlink.MAV_CMD_NAV_LAND)}")
t0 = time.time()
while time.time() - t0 < 25:
    beat(); a = alt()
    if a is not None and a < 0.3:
        log(f"touched down at {a:.2f} m"); break
    time.sleep(1)

print(f"FLIGHT_PEAK_ALT={peak:.2f}", flush=True)
log("done")
