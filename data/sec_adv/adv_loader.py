"""data/sec_adv/adv_loader.py

SEC Form ADV bulk-data ingestion. Turns the public SEC universe into:

    sec_universe    — ~15k–60k investment advisers we could target
    sec_principals  — named officers/partners from Schedule A filings

Both tables live in the same SQLite DB as firms/contacts. Schema init is
idempotent (CREATE TABLE IF NOT EXISTS) so this module owns nothing of
its own — it just augments database.py.

Entrypoints:

    python3 -m data.sec_adv.adv_loader --download
    python3 -m data.sec_adv.adv_loader --load-adv [--adv-file PATH]
    python3 -m data.sec_adv.adv_loader --schedule-a [--schedule-a-file PATH]
    python3 -m data.sec_adv.adv_loader --enrich      # promote TBD contacts
    python3 -m data.sec_adv.adv_loader --all         # the above in order

Env:
    ADV_BULK_URL   — download URL for the SEC bulk ZIP
    ADV_LOCAL_DIR  — directory containing manually-dropped CSVs
    HUNTER_API_KEY — required for --enrich step
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import logging
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database import get_db  # noqa: E402

load_dotenv()

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
MODULE_DIR = Path(__file__).resolve().parent
CACHE_DIR = MODULE_DIR / "cache"
SUGGESTIONS_LOG = MODULE_DIR / "suggestions.jsonl"

# ── Column name variants — normalized to lowercase + alnum-only ─────────────
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_COL_VARIANTS = {
    "crd":          ["organizationcrd", "crdnumber", "firmcrd", "1dcrd", "crd"],
    "firm_name":    ["legalname", "primarybusinessname", "1aname", "orgname",
                     "firmname", "name"],
    "city":         ["mainofficecity", "1bcitycorr", "1bcty", "mailingcity", "city"],
    "state":        ["mainofficestate", "1bstatecorr", "1bst", "mailingstate", "state"],
    # SEC Form ADV item 5F(2)(c) = Total Regulatory Assets Under Management
    "aum":          ["5f2c", "5f1", "regulatoryassetsundermanagement", "raum",
                     "5ftotalregulatoryassets", "5atotassets", "totalassets"],
    "num_clients":  ["5c1", "5bnumclients", "5bclients", "numclients",
                     "5a1numberofclients"],
    "num_employees":["5a", "numberofemployees", "employees"],
    # "Count of Private Funds - 7B(1)" → normalized strips punctuation/spaces
    "private_fund": ["countofprivatefunds7b1", "7aprivatefundadvsr", "7a",
                     "privatefundadviser", "section7a", "anypefunds",
                     "anyhedgefunds"],
    "is_ia":        ["1adviser", "registrationstatusia", "isinvestmentadviser"],
    "phone":        ["mainofficetelephonenumber", "1dphone", "phone", "mainphone"],
    "website":      ["websiteaddress", "1dwebsite", "website", "webaddress", "url"],
}

_SA_COLS = {
    "crd":        _COL_VARIANTS["crd"],
    "last_name":  ["lastname", "lastnamelegal", "lname", "familyname"],
    "first_name": ["firstname", "firstnamelegal", "fname", "givenname"],
    "title":      ["title", "titlestatus", "position", "titleposition"],
}

US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM",
    "NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA",
    "WV","WI","WY","PR","VI","GU",
}

_ICP_NAME_TOKENS = (
    "capital", "partners", "equity", "advisors", "asset", "management",
    "investment", "securities", "financial", "fund", "ventures",
)

_ICP_TITLE_TOKENS = (
    "ceo", "cto", "cio", "chief", "president", "managing", "partner",
    "principal", "director", "head of", "founder", "general counsel",
    "chief information", "chief technology", "chief investment",
    "chief ai", "head of ai", "head of technology", "head of data",
)

_FIRM_STOPWORDS = {
    "inc", "llc", "lp", "llp", "ltd", "limited", "corp", "corporation",
    "company", "co", "group", "holdings", "the", "llc.", "&",
}

# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_universe (
    crd_number            TEXT PRIMARY KEY,
    firm_name             TEXT,
    city                  TEXT,
    state                 TEXT,
    aum_reported          REAL,
    num_employees         INTEGER,
    is_private_fund       INTEGER,
    is_investment_adviser INTEGER,
    website               TEXT,
    phone                 TEXT,
    icp_fit               INTEGER,
    last_updated          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sec_universe_icp   ON sec_universe(icp_fit);
CREATE INDEX IF NOT EXISTS idx_sec_universe_state ON sec_universe(state);

CREATE TABLE IF NOT EXISTS sec_principals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    crd_number        TEXT NOT NULL,
    firm_name         TEXT,
    first_name        TEXT,
    last_name         TEXT,
    full_name         TEXT,
    title             TEXT,
    domain            TEXT,
    icp_title_match   INTEGER,
    FOREIGN KEY (crd_number) REFERENCES sec_universe(crd_number)
);

CREATE INDEX IF NOT EXISTS idx_sec_principals_crd   ON sec_principals(crd_number);
CREATE INDEX IF NOT EXISTS idx_sec_principals_icp   ON sec_principals(icp_title_match);
CREATE INDEX IF NOT EXISTS idx_sec_principals_firm  ON sec_principals(firm_name);
"""


def init_schema(conn) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _header_map(fieldnames: list[str], wanted: dict) -> dict[str, str | None]:
    """Map our logical keys to the actual CSV column name. Handles drift."""
    norm_to_real = {_norm(f): f for f in fieldnames or []}
    out: dict[str, str | None] = {}
    for key, variants in wanted.items():
        hit = None
        for v in variants:
            if _norm(v) in norm_to_real:
                hit = norm_to_real[_norm(v)]
                break
        out[key] = hit
    return out


def _truthy(v) -> bool:
    """Match Y/Yes/1/True/positive-integer. Private-fund count > 0 counts as truthy."""
    if v is None:
        return False
    s = str(v).strip().lower()
    if s in ("y", "yes", "1", "true", "t"):
        return True
    try:
        return int(float(s)) > 0
    except (ValueError, TypeError):
        return False


def _to_float(v) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _to_int(v) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _domain_from_website(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url if "://" in url else "http://" + url).hostname or ""
        return host.lower().removeprefix("www.")
    except Exception:
        return ""


def _firm_is_icp(firm_name: str, state: str, is_private_fund: bool,
                 aum: float | None) -> bool:
    # US-only
    if state and state.upper() not in US_STATES:
        return False

    # AUM floor: include if > $500M, OR if unknown (enrich later)
    if aum is not None and aum > 0 and aum < 500_000_000:
        return False

    if is_private_fund:
        return True

    name_lower = (firm_name or "").lower()
    return any(tok in name_lower for tok in _ICP_NAME_TOKENS)


def _title_is_icp(title: str) -> bool:
    t = (title or "").lower()
    return any(tok in t for tok in _ICP_TITLE_TOKENS)


def _firm_name_keywords(name: str) -> list[str]:
    """Strip suffixes like 'LLC', 'Inc.' so LIKE matches Blackstone ~ Blackstone Inc."""
    tokens = re.findall(r"[A-Za-z0-9']+", name or "")
    return [t for t in tokens if t.lower() not in _FIRM_STOPWORDS and len(t) > 2]


# ── Download ─────────────────────────────────────────────────────────────────

def download_bulk(force: bool = False) -> Path | None:
    """Fetch the SEC bulk ZIP if ADV_BULK_URL is configured. Returns cache dir."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = os.getenv("ADV_BULK_URL", "").strip()
    local_dir = os.getenv("ADV_LOCAL_DIR", "").strip()

    if local_dir:
        print(f"Using ADV_LOCAL_DIR={local_dir}")
        return Path(local_dir)

    if not url:
        print(
            "No ADV_BULK_URL or ADV_LOCAL_DIR set.\n"
            "  Option 1: export ADV_BULK_URL=<direct ZIP url>\n"
            "  Option 2: download CSVs manually and export ADV_LOCAL_DIR=<path>\n"
            "  SEC bulk data portal: "
            "https://www.iapd.sec.gov/content/standard/iadapabilityinformation.aspx\n"
        )
        return None

    zip_path = CACHE_DIR / "adv_bulk.zip"
    if zip_path.exists() and not force:
        print(f"✓ Cached {zip_path} — skipping download (force=True to redownload)")
    else:
        print(f"↓ Downloading {url} → {zip_path}")
        # SEC requires a descriptive UA
        ua = os.getenv("SEC_USER_AGENT",
                       "MEDDIC Engine research@.com")
        with requests.get(url, headers={"User-Agent": ua},
                          stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        print(f"✓ Downloaded {zip_path.stat().st_size // (1 << 20)} MB")

    # Extract
    extract_dir = CACHE_DIR
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    print(f"✓ Extracted to {extract_dir}")
    return extract_dir


# ── File discovery ──────────────────────────────────────────────────────────

def _find_csv(hint_globs: list[str], search_dir: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    search_dir = Path(search_dir)
    for g in hint_globs:
        for path in sorted(search_dir.glob(g), key=lambda p: p.stat().st_size, reverse=True):
            return path
    return None


# ── load_adv_base ───────────────────────────────────────────────────────────

def load_adv_base(conn, explicit_path: str | None = None,
                  search_dir: Path | None = None) -> tuple[int, int, int]:
    search_dir = Path(search_dir or CACHE_DIR)
    path = _find_csv(
        ["IA_SEC_*FIRM_ROSTER*.CSV", "IA_SEC_*FIRM_ROSTER*.csv",
         "IA_FIRM_SEC*.csv", "IA_ADV_Base*.csv", "ia_firm_sec*.csv",
         "IA_FIRM*.csv", "adv_sample.csv", "*.CSV", "*.csv"],
        search_dir, explicit_path,
    )
    if not path:
        print(f"✗ No ADV base CSV found in {search_dir}")
        return (0, 0, 0)

    init_schema(conn)
    print(f"↻ Loading ADV base from {path}")

    total = icp_rows = inserted = 0
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = _header_map(reader.fieldnames or [], _COL_VARIANTS)
        missing = [k for k in ("crd", "firm_name") if not cols[k]]
        if missing:
            print(f"✗ required columns missing from CSV: {missing}")
            print(f"  seen headers: {reader.fieldnames[:15]}…")
            return (0, 0, 0)

        for row in reader:
            total += 1
            crd = (row.get(cols["crd"]) or "").strip()
            if not crd:
                continue

            firm_name = (row.get(cols["firm_name"]) or "").strip()
            state     = (row.get(cols["state"]) or "").strip() if cols["state"] else ""
            city      = (row.get(cols["city"])  or "").strip() if cols["city"]  else ""
            aum       = _to_float(row.get(cols["aum"]))        if cols["aum"]   else None
            nclients  = _to_int(row.get(cols["num_clients"]))  if cols["num_clients"] else None
            nempl     = _to_int(row.get(cols["num_employees"])) if cols["num_employees"] else None
            is_pf     = _truthy(row.get(cols["private_fund"])) if cols["private_fund"] else False
            is_ia     = _truthy(row.get(cols["is_ia"]))        if cols["is_ia"]        else True
            phone     = (row.get(cols["phone"]) or "").strip()   if cols["phone"]   else ""
            website   = (row.get(cols["website"]) or "").strip() if cols["website"] else ""

            icp = _firm_is_icp(firm_name, state, is_pf, aum)
            if icp:
                icp_rows += 1

            conn.execute("""
                INSERT INTO sec_universe
                    (crd_number, firm_name, city, state, aum_reported,
                     num_employees, is_private_fund, is_investment_adviser,
                     website, phone, icp_fit, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(crd_number) DO UPDATE SET
                    firm_name=excluded.firm_name,
                    city=excluded.city, state=excluded.state,
                    aum_reported=excluded.aum_reported,
                    num_employees=excluded.num_employees,
                    is_private_fund=excluded.is_private_fund,
                    is_investment_adviser=excluded.is_investment_adviser,
                    website=excluded.website, phone=excluded.phone,
                    icp_fit=excluded.icp_fit,
                    last_updated=datetime('now')
            """, (crd, firm_name, city, state, aum,
                  nempl or nclients, int(is_pf), int(is_ia),
                  website, phone, int(icp)))
            inserted += 1

            if total % 10_000 == 0:
                conn.commit()
                print(f"  · {total:,} rows processed, {icp_rows:,} ICP so far")

    conn.commit()
    print(f"✓ ADV base loaded: {total:,} total, {icp_rows:,} ICP, {inserted:,} upserted")
    return (total, icp_rows, inserted)


# ── load_schedule_a ─────────────────────────────────────────────────────────

def load_schedule_a(conn, explicit_path: str | None = None,
                    search_dir: Path | None = None) -> tuple[int, int, int]:
    search_dir = Path(search_dir or CACHE_DIR)
    path = _find_csv(
        ["IA_Schedule_A*.csv", "IA_SCHEDULE_A*.csv", "ia_schedule_a*.csv",
         "schedule_a_sample.csv", "Schedule_A*.csv"],
        search_dir, explicit_path,
    )
    if not path:
        print(f"✗ No Schedule A CSV found in {search_dir}")
        return (0, 0, 0)

    init_schema(conn)
    print(f"↻ Loading Schedule A from {path}")

    # Preload domains & firm_names for quick lookup
    firm_idx: dict[str, tuple[str, str]] = {
        row["crd_number"]: (row["firm_name"] or "", _domain_from_website(row["website"] or ""))
        for row in conn.execute(
            "SELECT crd_number, firm_name, website FROM sec_universe"
        ).fetchall()
    }

    total = icp_matches = inserted = 0
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = _header_map(reader.fieldnames or [], _SA_COLS)
        missing = [k for k in ("crd", "last_name", "first_name") if not cols[k]]
        if missing:
            print(f"✗ required Schedule A columns missing: {missing}")
            return (0, 0, 0)

        for row in reader:
            total += 1
            crd = (row.get(cols["crd"]) or "").strip()
            if not crd:
                continue

            first = (row.get(cols["first_name"]) or "").strip()
            last  = (row.get(cols["last_name"])  or "").strip()
            title = (row.get(cols["title"]) or "").strip() if cols["title"] else ""

            if not (first or last):
                continue

            firm_name, domain = firm_idx.get(crd, ("", ""))
            is_icp = _title_is_icp(title)
            if is_icp:
                icp_matches += 1

            conn.execute("""
                INSERT INTO sec_principals
                    (crd_number, firm_name, first_name, last_name, full_name,
                     title, domain, icp_title_match)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (crd, firm_name, first, last,
                  f"{first} {last}".strip(), title, domain, int(is_icp)))
            inserted += 1

            if total % 10_000 == 0:
                conn.commit()
                print(f"  · {total:,} rows processed, {icp_matches:,} ICP titles")

    conn.commit()
    print(f"✓ Schedule A loaded: {total:,} total, {icp_matches:,} ICP titles, "
          f"{inserted:,} inserted")
    return (total, icp_matches, inserted)


# ── Enrich TBD contacts via SEC principals + Hunter ─────────────────────────

def _match_principals(conn, firm_name: str, limit: int = 3) -> list[dict]:
    keywords = _firm_name_keywords(firm_name)
    if not keywords:
        return []
    # Build LIKE across a couple of keywords (AND) for precision
    clauses, params = [], []
    for kw in keywords[:2]:  # top two distinctive tokens
        clauses.append("firm_name LIKE ?")
        params.append(f"%{kw}%")
    params.append(limit)
    rows = conn.execute(
        f"""SELECT crd_number, firm_name, full_name, title, domain
              FROM sec_principals
             WHERE icp_title_match = 1
               AND ({' AND '.join(clauses)})
             LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def enrich_from_sec(conn) -> int:
    """For TBD contacts, try to promote a real SEC-filed principal with an
    email discovered via Hunter. Returns number promoted."""
    try:
        from enrichment.hunter_enricher import find_contact
    except ImportError:
        logger.warning("hunter_enricher unavailable; skipping SEC enrichment")
        return 0

    init_schema(conn)

    tbd = conn.execute("""
        SELECT c.id, c.name, f.id AS firm_id, f.name AS firm_name, f.domain
          FROM contacts c
          JOIN firms    f ON f.id = c.firm_id
         WHERE (UPPER(c.name) LIKE '%TBD%' OR c.name IS NULL OR c.name = '')
    """).fetchall()

    if not tbd:
        print("✓ No TBD contacts to promote")
        return 0

    promoted = 0
    for c in tbd:
        firm_name = c["firm_name"]
        domain = c["domain"]
        candidates = _match_principals(conn, firm_name)
        if not candidates:
            _log_suggestion({"firm": firm_name, "candidates": [],
                             "reason": "no_sec_match"})
            print(f"  · {firm_name}: no SEC principals matched")
            continue

        hit = None
        for p in candidates:
            target_domain = domain or p["domain"] or ""
            if not target_domain:
                continue
            result = find_contact(p["full_name"], target_domain)
            score = int(result.get("score") or 0)
            verified = bool(result.get("verified"))
            if result.get("email") and (verified or score >= 65):
                hit = {**p, **result}
                break

        if hit:
            conn.execute("""
                UPDATE contacts
                   SET name = ?, title = ?, email = ?,
                       email_verified = ?, email_source = 'sec_adv',
                       updated_at = datetime('now')
                 WHERE id = ?
            """, (hit["full_name"], hit["title"], hit["email"],
                  1 if hit.get("verified") else 0, c["id"]))
            conn.commit()
            promoted += 1
            print(f"✓ Promoted {hit['full_name']} @ {firm_name} (was TBD) "
                  f"[src={hit.get('source')}, score={hit.get('score')}]")
        else:
            _log_suggestion({
                "firm": firm_name,
                "candidates": [{"name": p["full_name"], "title": p["title"]}
                               for p in candidates],
                "reason": "hunter_miss",
            })
            print(f"  · {firm_name}: {len(candidates)} SEC candidates, "
                  f"Hunter found no verified email")

    print(f"\n✓ SEC enrichment complete: {promoted}/{len(tbd)} TBD contacts promoted")
    return promoted


def _log_suggestion(payload: dict) -> None:
    SUGGESTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    payload["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(SUGGESTIONS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


# ── Summary ─────────────────────────────────────────────────────────────────

def print_summary(conn) -> None:
    q = conn.execute
    totals = {
        "total": q("SELECT COUNT(*) FROM sec_universe").fetchone()[0],
        "icp":   q("SELECT COUNT(*) FROM sec_universe WHERE icp_fit = 1").fetchone()[0],
        "aum":   q("SELECT COUNT(*) FROM sec_universe WHERE aum_reported > 500000000").fetchone()[0],
        "sa_total": q("SELECT COUNT(*) FROM sec_principals").fetchone()[0],
        "sa_icp":   q("SELECT COUNT(*) FROM sec_principals WHERE icp_title_match = 1").fetchone()[0],
        "firms_named": q("""SELECT COUNT(DISTINCT crd_number)
                              FROM sec_principals
                             WHERE icp_title_match = 1""").fetchone()[0],
    }
    rows = [
        ("Total firms in bulk file",      f"{totals['total']:,}"),
        ("ICP-qualified (PE/IB/credit)",  f"{totals['icp']:,}"),
        ("With AUM > $500M",              f"{totals['aum']:,}"),
        ("—",                             "—"),
        ("Schedule A principals loaded",  f"{totals['sa_total']:,}"),
        ("ICP title matches",             f"{totals['sa_icp']:,}"),
        ("Firms with named principal",    f"{totals['firms_named']:,}"),
    ]
    label_w = max(len(l) for l, _ in rows) + 2
    val_w = max(len(v) for _, v in rows) + 2
    print("┌" + "─" * (label_w + 2) + "┬" + "─" * (val_w + 2) + "┐")
    print("│  SEC ADV Universe" + " " * (label_w + val_w - 15) + "  │")
    print("├" + "─" * (label_w + 2) + "┼" + "─" * (val_w + 2) + "┤")
    for l, v in rows:
        print(f"│  {l:<{label_w}}│  {v:<{val_w}}│")
    print("└" + "─" * (label_w + 2) + "┴" + "─" * (val_w + 2) + "┘")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="SEC ADV bulk loader")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--load-adv", action="store_true")
    ap.add_argument("--schedule-a", action="store_true")
    ap.add_argument("--enrich", action="store_true",
                    help="Promote TBD contacts using SEC + Hunter")
    ap.add_argument("--all", action="store_true",
                    help="download + load-adv + schedule-a + enrich")
    ap.add_argument("--adv-file", default=None)
    ap.add_argument("--schedule-a-file", default=None)
    ap.add_argument("--search-dir", default=None,
                    help="directory to scan for CSVs (defaults to cache)")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    if not any([args.download, args.load_adv, args.schedule_a,
                args.enrich, args.all, args.summary]):
        ap.print_help()
        return 1

    search_dir = Path(args.search_dir) if args.search_dir else None

    conn = get_db()
    try:
        if args.all or args.download:
            download_bulk(force=args.force_download)
        if args.all or args.load_adv:
            load_adv_base(conn, args.adv_file, search_dir)
        if args.all or args.schedule_a:
            load_schedule_a(conn, args.schedule_a_file, search_dir)
        if args.all or args.enrich:
            enrich_from_sec(conn)
        print_summary(conn)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
