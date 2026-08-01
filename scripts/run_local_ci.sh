#!/usr/bin/env bash
# Local CI — the accepted substitute for an automated flight gate.
#
# DECIDED 2026-07-31: tier 2 (the SITL gate in GitHub Actions) is deferred, and running
# this on the workstation counts as having run the gate. See P1-07 in
# docs/lane-a/todo.md for why: the flight gate cannot run on a GitHub-hosted runner
# (12.6 GB image, 2 vCPU against an RTF floor of 0.95), and a self-hosted runner on a
# PUBLIC repo would let any fork's pull request execute code on this machine.
#
# So the gate stays a human-triggered thing — but a SINGLE COMMAND, with a summary you can
# paste into a PR, rather than a sequence someone has to remember correctly.
#
#   ./scripts/run_local_ci.sh              # fast checks only (~30 s)
#   ./scripts/run_local_ci.sh --gate       # + the 10-seed flight gate (~19 min)
#   ./scripts/run_local_ci.sh --gate --seeds 3
#
# Exits non-zero if anything fails, so it is usable as a pre-merge check.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

RUN_GATE=0
SEEDS=10
SCENARIO=scenarios/square-10m.yaml
while [ $# -gt 0 ]; do
  case "$1" in
    --gate)     RUN_GATE=1; shift ;;
    --seeds)    SEEDS="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *)          echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

FAILED=()
step() {
  local name="$1"; shift
  printf '  %-34s' "$name"
  if out=$("$@" 2>&1); then
    echo "PASS"
  else
    echo "FAIL"
    FAILED+=("$name")
    echo "$out" | tail -12 | sed 's/^/      /'
  fi
}

echo "local CI — $(date -u '+%Y-%m-%d %H:%M:%SZ')"
echo "commit: $(git rev-parse --short HEAD)  branch: $(git rev-parse --abbrev-ref HEAD)"
if [ -n "$(git status --porcelain)" ]; then
  echo "WORKING TREE IS DIRTY — this result describes uncommitted code, not the commit above."
fi
echo

# --- the same checks CI runs, so a local pass means the same thing -------------------
echo "tier 1 — what GitHub Actions also runs:"
step "off-target tests"        python3 -m pytest tests/ -q
step "shell scripts parse"     bash -c 'f=0; while IFS= read -r -d "" s; do bash -n "$s" || f=1; done < <(git ls-files -z "*.sh"); exit $f'
step "python scripts parse"    bash -c 'f=0; while IFS= read -r -d "" s; do python3 -m py_compile "$s" || f=1; done < <(git ls-files -z "*.py"); exit $f'
step "compose file valid"      docker compose -f docker/compose.yaml config --quiet
step "worklog renders"         python3 scripts/check_worklog_renders.py
step ".repos matches lock"     python3 scripts/check_repos_manifest.py

# --- the part CI cannot do --------------------------------------------------------
if [ "$RUN_GATE" = "1" ]; then
  echo
  echo "tier 2 — the flight gate (this is the part CI cannot run):"
  echo "  $SEEDS seeded runs against $SCENARIO, roughly $((SEEDS * 2)) minutes"
  if python3 -u ./scripts/run_gate.py "$SCENARIO" --seeds "$SEEDS"; then
    echo "  flight gate                        PASS"
  else
    echo "  flight gate                        FAIL"
    FAILED+=("flight gate")
  fi
else
  echo
  echo "tier 2 — flight gate SKIPPED (pass --gate to run it)."
  echo "  Skipping is fine for a docs or tooling change. It is NOT fine for anything that"
  echo "  touches the controller, the scenario runner, the overlay or the compose stack —"
  echo "  nothing else in this repo would catch a regression there."
fi

echo
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "RESULT: PASS"
  [ "$RUN_GATE" = "1" ] && echo "  (flight gate included — safe to quote as a gate run)" \
                        || echo "  (fast checks only — say so if you quote this)"
  exit 0
fi
echo "RESULT: FAIL — ${FAILED[*]}"
exit 1
