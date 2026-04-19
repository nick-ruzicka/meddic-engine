#!/bin/bash
# Daily competitive signal collection — runs at 6am ET.
# Collects signals from all 6 competitors, classifies them,
# and generates digest on Mondays.
#
# Install:
#   chmod +x scripts/run_signals_daily.sh
#   crontab -e
#   0 6 * * * /Users/nicholasruzicka/Desktop/meddic-engine/scripts/run_signals_daily.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Activate venv if present
[ -d venv ] && source venv/bin/activate

mkdir -p logs

LOG="logs/signals_$(date +%Y-%m-%d).log"

echo "========================================" >> "$LOG"
echo "Signal Engine v2 — $(date)" >> "$LOG"
echo "========================================" >> "$LOG"

python3 run_daily.py >> "$LOG" 2>&1
EXIT_CODE=$?

echo "" >> "$LOG"
echo "Exit code: $EXIT_CODE" >> "$LOG"
echo "Finished: $(date)" >> "$LOG"

# Also regenerate the competitive dashboard JSON
python3 scripts/update_competitive.py >> "$LOG" 2>&1

exit $EXIT_CODE
