#!/usr/bin/env bash
# Fern/Runpod full-stack runner. The current simulation implementation is Lane A.
set -Eeuo pipefail

DURATION=${DURATION:-300}
FERN_WORKSPACE=${FERN_WORKSPACE:-/workspace}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-${DRONE_SIM_REVISION:-unknown}}
RUN_DIR="${FERN_WORKSPACE%/}/runs/${RUN_ID}"
RUNTIME_LIB=/usr/local/lib/drone-sim
terminal=0
api_pid=""
qgc_pid=""
runner_status=70

status() {
  python3 "$RUNTIME_LIB/artifacts.py" status --run-dir "$RUN_DIR" "$@"
}

stop_children() {
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
  fi
  if [[ -n "$qgc_pid" ]]; then
    kill "$qgc_pid" 2>/dev/null || true
  fi
  screen -S px4sitl -X quit >/dev/null 2>&1 || true
  pkill -f '[b]in/px4' 2>/dev/null || true
  pkill -f '[g]z sim' 2>/dev/null || true
  pkill -f '[M]icroXRCEAgent' 2>/dev/null || true
  pkill -INT -f '[r]os2 bag record' 2>/dev/null || true
  pkill -f '[Q]GroundControl' 2>/dev/null || true
  pkill -f '[X]vfb' 2>/dev/null || true
  pkill -f '[o]penbox' 2>/dev/null || true
}

request_stop() {
  [[ -n "${RUNPOD_POD_ID:-}" ]] || return 1
  /usr/local/bin/request-runpod-stop "$RUNPOD_POD_ID"
}

finalize() {
  cp /etc/drone-sim-versions "$RUN_DIR/artifacts/versions.txt" 2>/dev/null || true
  terminal=1
  trap - ERR
  stop_children

  if [[ -z "${RUNPOD_POD_ID:-}" ]]; then
    exit "$runner_status"
  fi

  if request_stop; then
    echo "runner: Runpod stop accepted for ${RUNPOD_POD_ID}"
  else
    echo "runner: WARNING automatic stop failed for ${RUNPOD_POD_ID}; external Fern cleanup required" >&2
  fi

  # Runpod restarts an exited container while the Pod remains RUNNING. Idling after the
  # terminal status prevents a failed stop request from rerunning a billable simulation.
  exec sleep infinity
}

interrupted() {
  if [[ "$terminal" = "0" && -d "$RUN_DIR" ]]; then
    status --state interrupted --exit-code 130 --message "runner interrupted" || true
  fi
  stop_children
  exit 130
}

failed() {
  local exit_code=$?
  trap - ERR
  set +e
  if [[ "$terminal" = "0" && -d "$RUN_DIR" ]]; then
    status --state failed --exit-code "$exit_code" --message "runner infrastructure failed" || true
  fi
  runner_status=$exit_code
  finalize
}
trap interrupted INT TERM
trap failed ERR

python3 "$RUNTIME_LIB/artifacts.py" init --run-dir "$RUN_DIR" --duration "$DURATION"
status --state preflight
if ! python3 "$RUNTIME_LIB/preflight.py" \
  --workspace "$FERN_WORKSPACE" \
  --output "$RUN_DIR/artifacts/preflight.json" \
  > "$RUN_DIR/logs/preflight.log" 2>&1; then
  runner_status=2
  status --state failed --exit-code "$runner_status" --message "preflight failed"
else
  python3 "$RUNTIME_LIB/runtime_api.py" --run-dir "$RUN_DIR" \
    > "$RUN_DIR/logs/runtime-api.log" 2>&1 &
  api_pid=$!
  status --state running
  export OUTDIR="$RUN_DIR"
  echo "runner: starting full Drone Sim stack with pinned QGroundControl datalink"
  /usr/local/bin/qgc-entrypoint.sh > "$RUN_DIR/logs/qgc.log" 2>&1 &
  qgc_pid=$!
  sleep 5
  kill -0 "$qgc_pid"

  export SMOKE_LOG_DIR="$RUN_DIR/logs"
  export SMOKE_ARTIFACT_DIR="$RUN_DIR/artifacts"
  export SMOKE_METRICS_PATH="$RUN_DIR/metrics.json"
  export READY_FILE="$RUN_DIR/ready"
  export RECORD_MCAP=${RECORD_MCAP:-1}

  if /usr/local/bin/drone-sim-lane-a-smoke 2>&1 | tee "$RUN_DIR/logs/smoke.log"; then
    runner_status=0
  else
    runner_status=${PIPESTATUS[0]}
  fi
  if [[ "$runner_status" = "0" ]]; then
    status --state succeeded --exit-code 0
  else
    status --state failed --exit-code "$runner_status" --message "Drone Sim stack smoke failed"
  fi
fi

finalize
