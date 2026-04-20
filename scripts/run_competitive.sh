#!/bin/bash
# Daily competitive intelligence pipeline.
# Install as its own cron job, separate from refresh_dashboards.sh:
#   chmod +x scripts/run_competitive.sh
#   ( crontab -l 2>/dev/null | grep -v run_competitive
#     echo "0 6 * * * /root/meddic-engine/scripts/run_competitive.sh"
#   ) | crontab -

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -d venv ] && source venv/bin/activate
mkdir -p logs
echo "$(date) — starting competitive intelligence run" >> logs/competitive.log
python3 competitive_intel.py >> logs/competitive.log 2>&1
python3 scripts/update_competitive.py >> logs/competitive.log 2>&1
echo "$(date) — competitive intelligence run complete" >> logs/competitive.log
