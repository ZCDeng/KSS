#!/usr/bin/env bash
# Scheduled investment-analysis daily report.  Credentials stay in Keychain:
# this wrapper delegates to the signed Swift helper and never reads/exports a
# model key itself.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"

# Keep the same portable runtime guard as every launchd wrapper. The Swift
# helper owns credential brokering, but it deliberately invokes this verified
# interpreter to run the isolated Python Research Runner.
if [ -n "${KSS_PYTHON:-}" ]; then
    PYTHON="$KSS_PYTHON"
elif [ -x "$HOME/Library/Application Support/KSS/venv/bin/python3" ]; then
    PYTHON="$HOME/Library/Application Support/KSS/venv/bin/python3"
elif [ -x "$PROJECT_ROOT/.venv-desktop/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
else
    echo "no usable python interpreter found (checked KSS_PYTHON, state-root venv, .venv-desktop)" >&2
    exit 1
fi

# shellcheck source=scripts/lib_scheduled_research.sh
source "$PROJECT_ROOT/scripts/lib_scheduled_research.sh"
HELPER="$(kss_find_scheduled_research_helper "$PROJECT_ROOT" || true)"
if [ -z "$HELPER" ]; then
  echo "scheduled research helper is unavailable; sync the signed KSS app or build KSSResearchSchedulerHelper" >&2
  exit 2
fi

exec "$HELPER" --project-root "$PROJECT_ROOT" --state-root "$KSS_STATE_ROOT" --cadence daily
