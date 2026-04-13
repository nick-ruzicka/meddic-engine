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
python3 scripts/update_dashboard.py
python3 scripts/update_analytics.py
python3 scripts/update_ops.py

# Start Flask API in background
python3 app.py &
API_PID=$!
echo "✓ API running on port ${PORT:-8765} (pid $API_PID)"

# Start dashboard static server
cd export && python3 -m http.server 8080 &
DASH_PID=$!
cd ..
echo "✓ Dashboard running on http://localhost:8080"
echo ""
echo "Press Ctrl+C to stop both servers"

# Cleanup on exit
trap "kill $API_PID $DASH_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait
