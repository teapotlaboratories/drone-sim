#!/usr/bin/env python3
"""Local-only health, readiness, and status endpoints for the batch runtime."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    run_dir: Path

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.respond(200, {"ok": True})
            return
        if self.path == "/readyz":
            ready = (self.run_dir / "ready").is_file()
            self.respond(200 if ready else 503, {"ready": ready})
            return
        if self.path == "/status":
            try:
                status = json.loads(
                    (self.run_dir / "status.json").read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError):
                status = {"state": "unknown"}
            self.respond(200, status)
            return
        self.respond(404, {"error": "not found"})

    def respond(self, status: int, value: dict) -> None:
        body = (json.dumps(value, sort_keys=True) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    Handler.run_dir = args.run_dir
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
