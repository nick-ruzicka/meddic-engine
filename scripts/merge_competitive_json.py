#!/usr/bin/env python3
"""Merge briefs from git with v2 signals from local DB into one JSON."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "export", "competitive_data.json")

with open(OUT) as f:
    data = json.load(f)

conn = get_db()
try:
    rows = conn.execute("SELECT * FROM ci_signals WHERE category != 'noise' ORDER BY predictive_score DESC LIMIT 100").fetchall()
    v2 = [dict(r) for r in rows]
    for s in v2:
        if isinstance(s.get("payload"), str):
            s["payload"] = json.loads(s["payload"])
except Exception:
    v2 = []
conn.close()

data["v2_signals"] = v2
data["stats"]["v2_signals_total"] = len(v2)

with open(OUT, "w") as f:
    json.dump(data, f, indent=2, default=str)

print(f"Merged: {len(data['competitors'])} competitors, {len(v2)} v2 signals")
