#!/usr/bin/env bash
# Request a self-stop with whichever runpodctl syntax the provider injected.
set -u

pod_id=${1:-${RUNPOD_POD_ID:-}}
if [[ -z "$pod_id" ]]; then
  echo "runner: RUNPOD_POD_ID is unavailable; external Fern cleanup required" >&2
  exit 1
fi
if ! command -v runpodctl >/dev/null 2>&1; then
  echo "runner: runpodctl is unavailable; external Fern cleanup required" >&2
  exit 1
fi

run_stop() {
  local output="" exit_code=0

  if output=$(timeout 30 runpodctl "$@" 2>&1); then
    exit_code=0
  else
    exit_code=$?
  fi

  # Runpod injects the Pod credential through the environment, so the CLI can stop the
  # Pod even though its optional user-level config file is absent. Keep every other line
  # and the real exit status so provider failures remain actionable.
  if [[ -n "$output" ]]; then
    if [[ "$exit_code" = "0" ]]; then
      printf '%s\n' "$output" \
        | sed '/^Runpod config file not found, please run `runpodctl config` to create it$/d'
    else
      printf '%s\n' "$output" \
        | sed '/^Runpod config file not found, please run `runpodctl config` to create it$/d' >&2
    fi
  fi
  return "$exit_code"
}

# Runpod images currently receive either the noun-first CLI or its supported legacy
# verb-first predecessor. Probe help silently so an unsupported form does not pollute
# terminal logs with an expected "unknown command" error.
if runpodctl pod --help >/dev/null 2>&1; then
  run_stop pod stop "$pod_id"
  exit $?
fi

run_stop stop pod "$pod_id"
