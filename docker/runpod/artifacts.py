#!/usr/bin/env python3
"""Create and update the durable Fern run artifact contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TERMINAL_STATES = {"succeeded", "failed", "interrupted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def validate_run_dir(run_dir: Path) -> None:
    if not SAFE_RUN_ID.fullmatch(run_dir.name):
        raise ValueError(
            f"run directory name {run_dir.name!r} must match {SAFE_RUN_ID.pattern}"
        )


def initialize(run_dir: Path, duration: int) -> None:
    validate_run_dir(run_dir)
    for child in ("logs", "artifacts"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)

    request = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "profile": os.getenv("FERN_PROFILE", "drone-sim-lane-a"),
        "duration_seconds": duration,
        "scenario": os.getenv("SCENARIO", "lane-a-smoke"),
        "seed": os.getenv("SEED", ""),
        "created_at": utc_now(),
        "drone_sim_revision": os.getenv("DRONE_SIM_REVISION", "unknown"),
        "image_reference": os.getenv("DRONE_SIM_IMAGE", "unknown"),
        "image_digest": os.getenv("DRONE_SIM_IMAGE_DIGEST", "unknown"),
        "dds_transport": os.getenv("FASTDDS_BUILTIN_TRANSPORTS", "DEFAULT"),
        "runpod": {
            "pod_id": os.getenv("RUNPOD_POD_ID", ""),
            "gpu_count": os.getenv("RUNPOD_GPU_COUNT", ""),
            "cpu_count": os.getenv("RUNPOD_CPU_COUNT", ""),
        },
    }
    atomic_write_json(run_dir / "request.json", request)
    atomic_write_json(
        run_dir / "status.json",
        {
            "schema_version": 1,
            "run_id": run_dir.name,
            "state": "initializing",
            "started_at": request["created_at"],
            "updated_at": request["created_at"],
        },
    )


def update_status(
    run_dir: Path, state: str, exit_code: int | None, message: str | None
) -> None:
    validate_run_dir(run_dir)
    path = run_dir / "status.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        current = {
            "schema_version": 1,
            "run_id": run_dir.name,
            "started_at": utc_now(),
        }

    current["state"] = state
    current["updated_at"] = utc_now()
    if exit_code is not None:
        current["exit_code"] = exit_code
    if message:
        current["message"] = message
    if state in TERMINAL_STATES:
        current["finished_at"] = current["updated_at"]
    atomic_write_json(path, current)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--duration", required=True, type=int)

    status = subcommands.add_parser("status")
    status.add_argument("--run-dir", required=True, type=Path)
    status.add_argument("--state", required=True)
    status.add_argument("--exit-code", type=int)
    status.add_argument("--message")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        if args.duration < 1:
            raise SystemExit("duration must be at least one second")
        initialize(args.run_dir, args.duration)
    else:
        update_status(args.run_dir, args.state, args.exit_code, args.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
