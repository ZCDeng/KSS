#!/usr/bin/env bash
# Weekly sibling of run_investment_analysis_daily.sh. The Python runner checks
# an explicitly persisted exchange calendar, so Friday holidays never turn into
# a guessed report date.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"

# Persist the exchange calendar before the scheduler decides whether today is
# the final open day of its week.  This credential is limited to the short
# Tushare subprocess and explicitly removed before the Swift helper creates
# the model Credential Broker.
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

# shellcheck source=scripts/lib_cron_credentials.sh
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
if kss_load_credential TUSHARE_TOKEN "$PROJECT_ROOT/.env"; then
  "$PYTHON" "$PROJECT_ROOT/scripts/persist_trading_calendar.py" \
    --state-root "$KSS_STATE_ROOT" >/dev/null \
    || echo "[investment-weekly] trading calendar unavailable; scheduler will record a blocked goal" >&2
  unset TUSHARE_TOKEN
else
  echo "[investment-weekly] Tushare credential unavailable; scheduler will record a blocked goal" >&2
fi

# shellcheck source=scripts/lib_scheduled_research.sh
source "$PROJECT_ROOT/scripts/lib_scheduled_research.sh"
HELPER="$(kss_find_scheduled_research_helper "$PROJECT_ROOT" || true)"
if [ -z "$HELPER" ]; then
  echo "scheduled research helper is unavailable; sync the signed KSS app or build KSSResearchSchedulerHelper" >&2
  exit 2
fi

exec "$HELPER" --project-root "$PROJECT_ROOT" --state-root "$KSS_STATE_ROOT" --cadence weekly
