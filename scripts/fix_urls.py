#!/usr/bin/env python3
"""Fix alphasense.com → alpha-sense.com in all DB tables."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db
conn = get_db()
for table in ["ci_signals", "ci_sitemap_baseline", "ci_exa_baseline"]:
    try:
        conn.execute(f"UPDATE {table} SET raw_url=REPLACE(raw_url,'alphasense.com','alpha-sense.com') WHERE raw_url LIKE '%alphasense.com%'")
    except: pass
    try:
        conn.execute(f"UPDATE {table} SET url=REPLACE(url,'alphasense.com','alpha-sense.com') WHERE url LIKE '%alphasense.com%'")
    except: pass
conn.execute("UPDATE competitors SET url='https://www.alpha-sense.com' WHERE slug='alphasense'")
conn.commit()
conn.close()
print("URLs fixed")
