#!/bin/bash
# Regenerate dashboard JSON from SQLite. Installed as a cron job on the VPS.
# These outputs are gitignored, so a bare `git pull` does not refresh them.
#
# Install on a host:
#   chmod +x scripts/refresh_dashboards.sh
#   ( crontab -l 2>/dev/null | grep -v refresh_dashboards
#     echo "*/10 * * * * /root/meddic-engine/scripts/refresh_dashboards.sh"
#   ) | crontab -

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -d venv ] && source venv/bin/activate
mkdir -p logs
python3 scripts/update_dashboard.py >> logs/refresh.log 2>&1
python3 scripts/update_analytics.py >> logs/refresh.log 2>&1
python3 scripts/update_ops.py       >> logs/refresh.log 2>&1
python3 scripts/update_competitive.py >> logs/refresh.log 2>&1
