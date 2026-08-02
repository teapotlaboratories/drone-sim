"""Off-target contract tests for the Fern/Runpod batch runtime."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "docker" / "runpod"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, RUNTIME / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


artifacts = load("artifacts")
# preflight imports artifacts by module name, as it does inside the image.
import sys

sys.modules["artifacts"] = artifacts
preflight = load("preflight")


def test_artifact_contract_is_atomic_and_excludes_secrets(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "run-1"
    monkeypatch.setenv("RUNPOD_API_KEY", "must-not-appear")
    monkeypatch.setenv("DRONE_SIM_REVISION", "abc123")
    monkeypatch.setenv("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")

    artifacts.initialize(run_dir, 300)
    artifacts.update_status(run_dir, "succeeded", 0, None)

    assert (run_dir / "logs").is_dir()
    assert (run_dir / "artifacts").is_dir()
    request_text = (run_dir / "request.json").read_text()
    assert "must-not-appear" not in request_text
    assert json.loads(request_text)["drone_sim_revision"] == "abc123"
    status = json.loads((run_dir / "status.json").read_text())
    assert status["state"] == "succeeded"
    assert status["exit_code"] == 0
    assert json.loads(request_text)["scenario"] == "lane-a-smoke"
    assert json.loads(request_text)["seed"] == ""
    assert "finished_at" in status
    assert not list(run_dir.glob(".status.json.*"))


def test_artifact_contract_rejects_unsafe_run_id(tmp_path):
    with pytest.raises(ValueError):
        artifacts.initialize(tmp_path / "..", 60)


def preflight_args(tmp_path: Path, ports: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        workspace=tmp_path,
        shm_path=Path("/dev/shm"),
        output=tmp_path / "preflight.json",
        ports=ports,
    )


def configure_healthy_runtime(tmp_path: Path, monkeypatch) -> None:
    px4 = tmp_path / "px4"
    px4.write_text("#!/bin/sh\n")
    px4.chmod(0o755)
    qgc = tmp_path / "AppRun"
    qgc.write_text("#!/bin/sh\n")
    qgc.chmod(0o755)
    monkeypatch.setenv("DRONE_SIM_PX4_BINARY", str(px4))
    monkeypatch.setenv("DRONE_SIM_QGC_APPRUN", str(qgc))
    monkeypatch.setenv("DRONE_SIM_XRCE_COMMAND", "true")
    monkeypatch.setenv("DRONE_SIM_MIN_CPUS", "0")
    monkeypatch.setenv("DRONE_SIM_MIN_MEMORY_BYTES", "0")
    monkeypatch.setenv("DRONE_SIM_MIN_WORKSPACE_FREE_BYTES", "0")
    monkeypatch.setenv("DRONE_SIM_MIN_SHM_BYTES", "0")
    monkeypatch.setenv("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")


def test_preflight_healthy_udp_fallback(tmp_path, monkeypatch):
    configure_healthy_runtime(tmp_path, monkeypatch)
    report = preflight.run(preflight_args(tmp_path))

    assert report["ok"] is True
    assert report["checks"]["shared_memory"]["mode"] == "udp-fallback"
    assert report["checks"]["runtime"]["ok"] is True


def test_preflight_creates_a_missing_workspace(tmp_path, monkeypatch):
    configure_healthy_runtime(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"

    report = preflight.run(preflight_args(workspace))

    assert report["ok"] is True
    assert workspace.is_dir()
    assert report["checks"]["workspace"]["writable"] is True


def test_preflight_rejects_an_unknown_port_protocol(tmp_path, monkeypatch):
    configure_healthy_runtime(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="unsupported port protocol"):
        preflight.run(preflight_args(tmp_path, "sctp:9999"))


def test_preflight_rejects_insufficient_shared_memory(tmp_path, monkeypatch):
    configure_healthy_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("DRONE_SIM_MIN_SHM_BYTES", str(2**63))
    report = preflight.run(preflight_args(tmp_path))

    assert report["ok"] is False
    assert "shared_memory" in report["failures"]


def test_preflight_rejects_occupied_loopback_port(tmp_path, monkeypatch):
    configure_healthy_runtime(tmp_path, monkeypatch)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    try:
        port = listener.getsockname()[1]
        report = preflight.run(preflight_args(tmp_path, f"tcp:{port}"))
    finally:
        listener.close()

    assert report["ok"] is False
    assert report["checks"]["ports"]["listeners"][0]["available"] is False


def test_runner_files_keep_control_ports_local():
    dockerfile = (RUNTIME / "lane-a.Dockerfile").read_text()
    runner = (RUNTIME / "run-lane-a.sh").read_text()
    stop_helper = (RUNTIME / "request-stop.sh").read_text()
    smoke = (ROOT / "tests" / "lane-a-smoke.sh").read_text()
    assert 'ENTRYPOINT ["/usr/bin/tini", "-s", "--"]' in dockerfile
    assert "lane-a-entrypoint.sh" not in dockerfile
    assert "env -u LD_LIBRARY_PATH bash" in smoke
    assert "env -u LD_LIBRARY_PATH gz topic" in smoke
    assert 'timeout --kill-after=5s "$DURATION"' in smoke
    assert 'wait_for_recorder_exit 15' in smoke
    assert 'wait_for_recorder_exit 5' in smoke
    assert 'kill -INT "$BAG_PID"' in smoke
    assert 'kill -TERM "$BAG_PID"' in smoke
    assert 'kill -KILL "$BAG_PID"' in smoke
    assert "source /opt/ros/" in smoke
    assert "EXPOSE 14540" not in dockerfile
    assert "QGC_SHA256" in dockerfile
    assert "/qgc.AppImage --appimage-extract" in dockerfile
    assert "test -x /opt/qgc/squashfs-root/AppRun" in dockerfile
    assert "/opt/qgc/squashfs-root/AppRun" in (RUNTIME / "preflight.py").read_text()
    assert "qgc-entrypoint.sh" in dockerfile
    assert "qgc.log" in runner
    assert "RUNPOD_API_KEY" not in (RUNTIME / "artifacts.py").read_text()
    assert "127.0.0.1" in (RUNTIME / "runtime_api.py").read_text()
    assert "tcp:8080" in (RUNTIME / "preflight.py").read_text()
    assert "sleep infinity" in runner
    assert "request-runpod-stop" in dockerfile
    assert "RUNPOD_API_KEY" not in runner
    assert "RUNPOD_API_KEY" not in stop_helper
    assert "rest.runpod.io" not in stop_helper
    assert "screen -S px4sitl -X quit >/dev/null 2>&1 || true" in runner
    assert "screen -S px4sitl -X quit >/dev/null 2>&1 || true" in smoke
    preflight_position = runner.index('python3 "$RUNTIME_LIB/preflight.py"')
    api_position = runner.index('python3 "$RUNTIME_LIB/runtime_api.py"')
    running_position = runner.index("status --state running")
    assert preflight_position < api_position < running_position
    assert "runner_status=$exit_code\n  finalize" in runner
    assert 'exit "$exit_code"' not in runner



@pytest.mark.parametrize(
    ("current_cli", "expected"),
    [
        (True, ["pod", "stop", "pod-123"]),
        (False, ["stop", "pod", "pod-123"]),
    ],
)
def test_stop_helper_supports_current_and_legacy_cli(
    tmp_path, monkeypatch, current_cli, expected
):
    fake_cli = tmp_path / "runpodctl"
    trace = tmp_path / "trace"
    fake_cli.write_text(
        """#!/usr/bin/env bash
if [[ "$1" = "pod" && "$2" = "--help" ]]; then
  [[ "${CURRENT_CLI:-0}" = "1" ]]
  exit
fi
printf '%s\\n' "$@" > "$TRACE"
printf '%s\\n' 'Runpod config file not found, please run `runpodctl config` to create it'
printf 'pod "%s" stopped\\n' "${@: -1}"
"""
    )
    fake_cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("TRACE", str(trace))
    monkeypatch.setenv("CURRENT_CLI", "1" if current_cli else "0")

    result = subprocess.run(
        [str(RUNTIME / "request-stop.sh"), "pod-123"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == expected
    assert "Runpod config file not found" not in result.stdout
    assert 'pod "pod-123" stopped' in result.stdout


def test_stop_helper_preserves_provider_failure(tmp_path, monkeypatch):
    fake_cli = tmp_path / "runpodctl"
    fake_cli.write_text(
        """#!/usr/bin/env bash
if [[ "$1" = "pod" && "$2" = "--help" ]]; then
  exit 0
fi
printf '%s\\n' 'Runpod config file not found, please run `runpodctl config` to create it'
printf '%s\\n' 'provider stop failed' >&2
exit 23
"""
    )
    fake_cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    result = subprocess.run(
        [str(RUNTIME / "request-stop.sh"), "pod-123"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 23
    assert "Runpod config file not found" not in result.stderr
    assert "provider stop failed" in result.stderr
    assert result.stdout == ""


def test_sim_launch_owns_clock_bridge_and_sim_time():
    launch = (
        ROOT / "ros2_ws" / "src" / "bringup" / "launch" / "sim.launch.py"
    ).read_text()
    assert 'package="ros_gz_bridge"' in launch
    assert '"control.launch.py"' in launch
    assert 'LaunchConfiguration("world")' in launch
    assert '"use_sim_time": LaunchConfiguration("use_sim_time")' in launch
