#!/usr/bin/env bash
# Assert no AI attribution anywhere in the tracked tree (.ai/AGENTS.md).
#
# Shared by tier-1 CI and scripts/run_local_ci.sh so a local pass and a CI pass mean the
# same thing. They previously did not: run_local_ci.sh ran six of the workflow's eight
# steps and omitted this one, while still printing "the same checks CI runs".
#
# Three files are excluded because they DEFINE the rule and necessarily quote the forbidden
# strings in order to forbid them: the two rule documents, and the workflow itself.
#
# git ls-files lists TRACKED files only - which is deliberate and was itself a finding: an
# untracked file cannot be seen here, so this check only becomes meaningful after `git add`.
set -uo pipefail
cd "$(dirname "$0")/.."

hits=$(git ls-files -z \
       | grep -zvE '^(\.ai/AGENTS\.md|CLAUDE\.md|\.github/workflows/checks\.yml|scripts/check_attribution\.sh)$' \
       | xargs -0 grep -lniE 'co-authored-by:.*claude|generated with .*claude|🤖' 2>/dev/null || true)

if [ -n "$hits" ]; then
  echo "AI attribution found in:" >&2
  printf '  %s\n' $hits >&2
  exit 1
fi
echo "no AI attribution in $(git ls-files | wc -l) tracked files"
