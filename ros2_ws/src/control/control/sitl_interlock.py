"""Proof that a SIMULATOR is on the other end of the graph, not a real aircraft. (SIM-45)

`CLAUDE.md` hard stop 1: anything that puts the real Pixhawk 6C in motion needs the
operator's explicit go-ahead for that specific run, every time. A web page with a TAKE OFF
button is the exact opposite of that, so hand-flying is gated on this check.

WHY A POSITIVE PROOF, NOT AN ABSENCE CHECK
------------------------------------------
The tempting version is "refuse if it looks like hardware" -- no `/dev/ttyACM0`, a
particular `SYS_AUTOSTART`, a hostname. Every one of those fails OPEN: an unrecognised
setup passes, so the check is weakest exactly where it is least understood. This asks the
opposite question. AirSim's msgpack-RPC answers `getServerVersion` on 127.0.0.1:41451, and
a companion computer bolted to a real airframe has no simulator behind it. No answer means
no flight.

WHY IT LIVES IN THE CONTROL NODE AND NOT IN THE TRANSPORT
---------------------------------------------------------
Put in the web layer, it would guard the browser and nothing else -- `ros2 topic pub` would
walk straight past it. At the node that actually sends VEHICLE_CMD_COMPONENT_ARM_DISARM it
guards every route to `/mission/command`.

WHY THIS IS NOT scripts/airsim_rpc_client.py
---------------------------------------------
That client is a HOST script. `sim_up.sh` copies only `ros2_ws/src/{interfaces,control,
bringup,webui}` into the container, so `scripts/` is not importable from a running node.
Duplicating ~20 lines of msgpack-RPC beats making the flight code depend on a path that is
not there -- and this needs one call, not a frame-capture client. Kept deliberately
minimal so there is nothing to keep in sync: if the wire protocol ever changes, both fail
loudly and identically.

THE ONE AirSim RPC CALL IN THE WHOLE WEB INTERFACE. It is read-only, it commands nothing,
and it is an interlock rather than a control or telemetry path -- SIM-45 asks for ROS 2
over RPC everywhere else, and everywhere else it is ROS 2.
"""

import socket

try:
    import msgpack
except ImportError:                                        # pragma: no cover
    msgpack = None

AIRSIM_RPC_HOST = "127.0.0.1"
AIRSIM_RPC_PORT = 41451


def simulator_present(host: str = AIRSIM_RPC_HOST, port: int = AIRSIM_RPC_PORT,
                      timeout: float = 2.0) -> tuple[bool, str]:
    """(True, version) if an AirSim RPC server answers; (False, why) otherwise.

    NEVER RAISES. It is called from a timer callback, and an exception escaping there takes
    the node down without publishing a result -- the failure mode `offboard_control` already
    documents twice. Every unexpected condition becomes a refusal with a legible reason,
    because the safe answer to "is this a simulator?" is no.

    The timeout is short and the socket is closed immediately: this runs once at start-up and
    once per TAKEOFF command, not per tick, so it must not be able to stall the state machine
    for longer than a couple of ticks even against a black-holed port.
    """
    if msgpack is None:
        return False, "python3-msgpack is not installed, so the interlock cannot be checked"
    try:
        with socket.create_connection((host, port), timeout) as s:
            s.sendall(msgpack.packb([0, 1, "getServerVersion", []], use_bin_type=True))
            try:
                unp = msgpack.Unpacker(raw=False, strict_map_key=False)
            except TypeError:                              # msgpack < 0.6
                unp = msgpack.Unpacker(raw=False)
            deadline_reads = 8      # bounded, so a chatty-but-wrong server cannot spin here
            for _ in range(deadline_reads):
                data = s.recv(1 << 16)
                if not data:
                    return False, f"{host}:{port} closed the connection without replying"
                unp.feed(data)
                for msg in unp:
                    # [1, msgid, error, result] -- msgid must match, for the same reason
                    # scripts/airsim_rpc_client.py matches it: a stale reply read as a fresh
                    # one is worse than no reply.
                    if isinstance(msg, (list, tuple)) and len(msg) == 4 and msg[0] == 1:
                        if msg[1] != 1:
                            continue
                        if msg[2] is not None:
                            return False, f"getServerVersion failed: {msg[2]}"
                        return True, f"AirSim RPC server version {msg[3]}"
            return False, f"{host}:{port} answered, but never with a reply to getServerVersion"
    except OSError as exc:
        return False, f"no AirSim RPC at {host}:{port} ({exc})"
    except Exception as exc:                               # pragma: no cover - see docstring
        return False, f"AirSim RPC check failed unexpectedly: {exc!r}"
