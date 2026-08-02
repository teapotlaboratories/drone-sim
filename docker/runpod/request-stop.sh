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

# Runpod images currently receive either the noun-first CLI or its supported legacy
# verb-first predecessor. Probe help silently so an unsupported form does not pollute
# terminal logs with an expected "unknown command" error.
if runpodctl pod --help >/dev/null 2>&1; then
  exec timeout 30 runpodctl pod stop "$pod_id"
fi

exec timeout 30 runpodctl stop pod "$pod_id"
