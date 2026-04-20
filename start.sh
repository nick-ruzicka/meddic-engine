#!/bin/bash
set -e
echo "Starting MEDDIC Engine..."

# Load .env (strip inline comments, export all)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source <(sed -E 's/[[:space:]]+#.*$//' .env | grep -E '^[A-Za-z_][A-Za-z0-9_]*=')
  set +a
fi

# Initialize DB and run seed if DB is empty
python3 -m database
python3 data/seed/seed_accounts.py

# Generate initial data for all pages
scripts/refresh_dashboards.sh

# Start Flask API via gunicorn (4 workers) in background
gunicorn app:app -w 4 --bind 0.0.0.0:${PORT:-8765} --timeout 120 &
API_PID=$!
echo "✓ API running on port ${PORT:-8765} (pid $API_PID, 4 workers)"

# Start dashboard static server (gzip-enabled)
python3 scripts/static_server.py 8080 export &
DASH_PID=$!
echo "✓ Dashboard running on http://localhost:8080"
echo ""
echo "Press Ctrl+C to stop both servers"

# Cleanup on exit
trap "kill $API_PID $DASH_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait
