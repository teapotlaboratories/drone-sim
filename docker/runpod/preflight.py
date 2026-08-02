#!/usr/bin/env python3
"""Validate a Pod before starting the Lane A workload."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
from pathlib import Path

from artifacts import atomic_write_json, utc_now

GIB = 1024**3
MIB = 1024**2


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


def memory_bytes() -> int:
    with open("/proc/meminfo", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return 0


def capacity(path: Path) -> tuple[int, int]:
    stats = os.statvfs(path)
    return stats.f_frsize * stats.f_blocks, stats.f_frsize * stats.f_bavail


def check_ports(specification: str) -> tuple[bool, list[dict]]:
    results = []
    ok = True
    for item in filter(None, (part.strip() for part in specification.split(","))):
        protocol, rendered_port = item.split(":", 1)
        if protocol not in {"tcp", "udp"}:
            raise ValueError(f"unsupported port protocol {protocol!r}")
        port = int(rendered_port)
        kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
        sock = socket.socket(socket.AF_INET, kind)
        try:
            sock.bind(("127.0.0.1", port))
            available = True
            detail = "available on loopback"
        except OSError as error:
            available = False
            detail = str(error)
            ok = False
        finally:
            sock.close()
        results.append(
            {
                "protocol": protocol,
                "port": port,
                "available": available,
                "detail": detail,
            }
        )
    return ok, results


def gpu_info() -> dict:
    command = shutil.which("nvidia-smi")
    if command is None:
        return {"present": False, "devices": []}
    result = subprocess.run(
        [
            command,
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    devices = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) == 3:
                devices.append(
                    {
                        "name": fields[0],
                        "driver_version": fields[1],
                        "memory_mib": int(fields[2]),
                    }
                )
    return {
        "present": bool(devices),
        "devices": devices,
        "error": "" if result.returncode == 0 else result.stderr.strip()[-500:],
    }


def writable_workspace(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".preflight-{os.getpid()}"
        with open(probe, "x", encoding="utf-8") as stream:
            stream.write("ok\n")
            stream.flush()
            os.fsync(stream.fileno())
        probe.unlink()
        return True, "writable"
    except OSError as error:
        return False, str(error)


def run(args: argparse.Namespace) -> dict:
    transport = os.getenv("FASTDDS_BUILTIN_TRANSPORTS", "DEFAULT")
    cpu_count = os.cpu_count() or 0
    ram = memory_bytes()
    workspace_ok, workspace_detail = writable_workspace(args.workspace)
    shm_total, shm_free = capacity(args.shm_path)
    if workspace_ok:
        disk_total, disk_free = capacity(args.workspace)
    else:
        disk_total, disk_free = 0, 0
    ports_ok, ports = check_ports(args.ports)

    min_cpus = env_int("DRONE_SIM_MIN_CPUS", 4)
    min_memory = env_int("DRONE_SIM_MIN_MEMORY_BYTES", 6 * GIB)
    min_disk = env_int("DRONE_SIM_MIN_WORKSPACE_FREE_BYTES", 1 * GIB)
    default_shm = 64 * MIB if transport.upper() == "UDPV4" else 2 * GIB
    min_shm = env_int("DRONE_SIM_MIN_SHM_BYTES", default_shm)

    px4_binary = Path(
        os.getenv("DRONE_SIM_PX4_BINARY", "/opt/px4/build/px4_sitl_default/bin/px4")
    )
    xrce_command = os.getenv("DRONE_SIM_XRCE_COMMAND", "MicroXRCEAgent")
    xrce_binary = shutil.which(xrce_command)

    qgc_apprun = Path(
        os.getenv("DRONE_SIM_QGC_APPRUN", "/opt/qgc/squashfs-root/AppRun")
    )
    checks = {
        "cpu": {
            "ok": cpu_count >= min_cpus,
            "observed": cpu_count,
            "minimum": min_cpus,
        },
        "memory": {
            "ok": ram >= min_memory,
            "observed_bytes": ram,
            "minimum_bytes": min_memory,
        },
        "workspace": {
            "ok": workspace_ok and disk_free >= min_disk,
            "path": str(args.workspace),
            "writable": workspace_ok,
            "detail": workspace_detail,
            "total_bytes": disk_total,
            "free_bytes": disk_free,
            "minimum_free_bytes": min_disk,
        },
        "shared_memory": {
            "ok": shm_total >= min_shm,
            "path": str(args.shm_path),
            "total_bytes": shm_total,
            "free_bytes": shm_free,
            "minimum_bytes": min_shm,
            "dds_transport": transport,
            "mode": "udp-fallback" if transport.upper() == "UDPV4" else "shared-memory",
        },
        "ports": {"ok": ports_ok, "listeners": ports},
        "runtime": {
            "ok": (
                px4_binary.is_file()
                and os.access(px4_binary, os.X_OK)
                and xrce_binary is not None
                and qgc_apprun.is_file()
                and os.access(qgc_apprun, os.X_OK)
            ),
            "px4": str(px4_binary),
            "xrce_agent": xrce_binary or "",
            "qgroundcontrol": str(qgc_apprun),
            "ros_distro": os.getenv("ROS_DISTRO", ""),
        },
    }
    failures = [name for name, value in checks.items() if not value["ok"]]
    return {
        "schema_version": 1,
        "checked_at": utc_now(),
        "ok": not failures,
        "failures": failures,
        "checks": checks,
        "gpu": gpu_info(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--shm-path", default="/dev/shm", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--ports",
        default="udp:14540,udp:14550,udp:18570,udp:8888,tcp:4560,tcp:8080",
    )
    args = parser.parse_args()
    report = run(args)
    atomic_write_json(args.output, report)
    if report["ok"]:
        print(f"preflight: PASS ({args.output})")
        return 0
    for failure in report["failures"]:
        print(f"preflight: FAIL {failure}: {report['checks'][failure]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
