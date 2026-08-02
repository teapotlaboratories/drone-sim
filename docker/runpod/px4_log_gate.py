#!/usr/bin/env python3
"""Normalize PX4 console output and classify smoke-gate failures."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ERROR_LINE = re.compile(r"ERROR \[[a-z_]+\][^\r\n]*")
QGC_ACK_LOSS = re.compile(
    r"ERROR \[mavlink\] vehicle_command_ack lost, generation [0-9]+ -> [0-9]+"
)
SENSOR_TIMEOUT = re.compile(r"fail(?:ed)?:\s*TIMEOUT")


def normalize(raw: str) -> str:
    return ANSI_ESCAPE.sub("", raw)


def analyze(raw: str) -> tuple[str, dict[str, object]]:
    plain = normalize(raw)
    error_lines = [match.group(0).strip() for match in ERROR_LINE.finditer(plain)]
    qgc_ack_losses = sum(
        QGC_ACK_LOSS.fullmatch(error_line) is not None for error_line in error_lines
    )
    total_errors = len(error_lines)
    report: dict[str, object] = {
        "schema_version": 1,
        "sensor_timeout_count": len(SENSOR_TIMEOUT.findall(plain)),
        "px4_error_count": total_errors - qgc_ack_losses,
        "px4_total_error_count": total_errors,
        "px4_qgc_ack_loss_count": qgc_ack_losses,
        "error_lines": error_lines,
    }
    return plain, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--plain-output", type=Path, required=True)
    parser.add_argument("--errors-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plain, report = analyze(args.input.read_text(errors="replace"))
    args.plain_output.write_text(plain)
    error_lines = report["error_lines"]
    assert isinstance(error_lines, list)
    args.errors_output.write_text(
        "".join(f"{error_line}\n" for error_line in error_lines)
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        report["sensor_timeout_count"],
        report["px4_error_count"],
        report["px4_total_error_count"],
        report["px4_qgc_ack_loss_count"],
    )


if __name__ == "__main__":
    main()
