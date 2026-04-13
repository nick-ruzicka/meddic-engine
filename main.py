#!/usr/bin/env python3
"""MEDDIC Engine — CLI pipeline orchestrator.

Flags:
    --collect   Run signal collectors (Twitter + LinkedIn + stubs)
    --score     Score all unscored contacts in DB using contact_scorer
    --queue     Push top scored contacts to outreach_queue
    --full      collect + score + queue in sequence
    --seed      Run data/seed/seed_accounts.py
    --sample    Run on seeded data only — skip live API calls
    --limit N   Cap number of contacts scored / queued per run
    --threshold N  Minimum composite score to enqueue (default 55)
"""

from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import logging
import os
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from database import get_db, init_db
from utils.helpers import load_config, now_iso, calculate_freshness_days

console = Console()
logger = logging.getLogger(".main")

COLLECTOR_MODULES = [
    ("twitter",  "collectors.twitter_collector"),
    ("linkedin", "collectors.linkedin_collector"),
    ("press",    "collectors.exa_collector"),
    ("hiring",   "collectors.hiring_collector"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _banner(text: str) -> None:
    console.print(Panel.fit(f"[bold cyan]{text}[/bold cyan]", border_style="cyan"))


def _firm_id_for(conn, firm_name: str | None, domain: str | None) -> int | None:
    """Resolve a firm by name or domain. Returns None if not matched."""
    if firm_name:
        row = conn.execute("SELECT id FROM firms WHERE name = ?", (firm_name,)).fetchone()
        if row:
            return row["id"]
    if domain:
        row = conn.execute("SELECT id FROM firms WHERE domain = ?", (domain,)).fetchone()
        if row:
            return row["id"]
    return None


def _backfill_signal_authors() -> int:
    """Create contacts from signal.author_name when we can attribute them
    to one of our firms. Matches by: 1) explicit firm_id on signal,
    2) fuzzy firm-name token match against signal content."""
    import re as _re
    conn = get_db()
    try:
        firms = {
            row["id"]: row["name"]
            for row in conn.execute("SELECT id, name FROM firms").fetchall()
        }
        firm_tokens = {
            fid: [t.lower() for t in _re.findall(r"[A-Za-z]{3,}", name)
                  if t.lower() not in ("inc", "llc", "lp", "llp", "the",
                                       "company", "group", "capital",
                                       "partners", "management")]
            for fid, name in firms.items()
        }
        authors = conn.execute(
            """SELECT DISTINCT firm_id, author_name, author_handle, content
                 FROM signals
                WHERE author_name IS NOT NULL AND author_name != ''"""
        ).fetchall()

        added = 0
        for row in authors:
            name = (row["author_name"] or "").strip()
            if len(name.split()) < 2:
                continue

            firm_id = row["firm_id"]
            if not firm_id:
                txt = ((row["content"] or "") + " " + (row["author_handle"] or "")).lower()
                best = None
                for fid, toks in firm_tokens.items():
                    if not toks:
                        continue
                    if any(t in txt for t in toks):
                        best = fid
                        break
                firm_id = best
            if not firm_id:
                continue

            existing = conn.execute(
                """SELECT id FROM contacts
                   WHERE firm_id = ? AND LOWER(name) = LOWER(?)""",
                (firm_id, name),
            ).fetchone()
            if existing:
                continue

            conn.execute(
                """INSERT INTO contacts
                   (firm_id, name, title, email, email_verified,
                    email_source, notes, created_at, updated_at)
                   VALUES (?, ?, ?, NULL, 0, 'signal_author', ?,
                           datetime('now'), datetime('now'))""",
                (firm_id, name, "",
                 f"Backfilled from signal author"),
            )
            added += 1
            console.print(f"  + {name} @ {firms.get(firm_id)}")

        conn.commit()
    finally:
        conn.close()
    return added


def _load_firm_contact_signals(firm_id: int, contact_id: int) -> tuple[dict, dict, list[dict]]:
    """Load firm, contact, and associated signals for scoring."""
    conn = get_db()
    try:
        firm = dict(conn.execute("SELECT * FROM firms WHERE id = ?", (firm_id,)).fetchone())
        contact = dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())
        sig_rows = conn.execute(
            """SELECT * FROM signals
               WHERE firm_id = ? AND (contact_id IS NULL OR contact_id = ?)
               ORDER BY COALESCE(freshness_days, 999) ASC LIMIT 25""",
            (firm_id, contact_id),
        ).fetchall()
        signals = [dict(r) for r in sig_rows]
    finally:
        conn.close()
    return firm, contact, signals


MAX_SIGNAL_AGE_DAYS = 180  # drop signals older than this at ingest time


def _insert_signal(conn, sig: dict) -> bool:
    """Insert a normalized signal dict. Returns True if inserted."""
    firm_id = sig.get("firm_id") or _firm_id_for(conn, sig.get("firm_name"), sig.get("domain"))
    if not firm_id:
        logger.debug(f"skip signal — no firm match: {sig.get('firm_name') or sig.get('domain')}")
        return False

    sig_date = sig.get("signal_date")
    if sig_date:
        try:
            dt = datetime.fromisoformat(str(sig_date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_SIGNAL_AGE_DAYS)
            if dt < cutoff:
                logger.debug(f"skip signal — older than {MAX_SIGNAL_AGE_DAYS}d: {sig_date}")
                return False
        except Exception:
            pass  # unparseable date → allow through

    freshness = calculate_freshness_days(sig.get("signal_date", ""))
    conn.execute(
        """INSERT INTO signals
           (firm_id, contact_id, signal_type, signal_subtype, content,
            source_url, author_handle, author_name, signal_date,
            freshness_days, buying_stage, raw_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            firm_id,
            sig.get("contact_id"),
            sig.get("signal_type", "manual"),
            sig.get("signal_subtype"),
            sig.get("content", ""),
            sig.get("source_url"),
            sig.get("author_handle"),
            sig.get("author_name"),
            sig.get("signal_date"),
            freshness,
            sig.get("buying_stage"),
            json.dumps(sig.get("raw_data", {}), default=str),
        ),
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline steps
# ─────────────────────────────────────────────────────────────────────────────

def step_seed() -> None:
    _banner("SEED — seeding firms, contacts, signals")
    script = os.path.join(ROOT, "data", "seed", "seed_accounts.py")
    result = subprocess.run([sys.executable, script], capture_output=True, text=True)
    console.print(result.stdout)
    if result.returncode != 0:
        console.print(f"[red]{result.stderr}[/red]")


def step_collect(config: dict, sample: bool = False) -> dict:
    _banner("COLLECT — running signal collectors")
    totals = {"collected": 0, "inserted": 0, "by_source": {}}

    if sample:
        console.print("[yellow]--sample mode — skipping live collectors[/yellow]")
        return totals

    conn = get_db()
    try:
        for name, module_path in COLLECTOR_MODULES:
            try:
                mod = importlib.import_module(module_path)
            except ImportError as e:
                console.print(f"[yellow]  {name}: module missing ({e})[/yellow]")
                continue

            if not hasattr(mod, "collect"):
                console.print(f"[yellow]  {name}: no collect() — skipping[/yellow]")
                continue

            try:
                signals = mod.collect(config) or []
            except Exception as e:
                console.print(f"[red]  {name}: collector raised {e}[/red]")
                logger.debug(traceback.format_exc())
                continue

            inserted = 0
            for sig in signals:
                if _insert_signal(conn, sig):
                    inserted += 1

            totals["collected"] += len(signals)
            totals["inserted"] += inserted
            totals["by_source"][name] = {"collected": len(signals), "inserted": inserted}
            console.print(f"  [green]✓[/green] {name}: {len(signals)} collected, {inserted} inserted")

        conn.commit()
    finally:
        conn.close()

    _report_collection(totals)
    return totals


def _report_collection(totals: dict) -> None:
    table = Table(title="Collection Summary", show_lines=False)
    table.add_column("Source", style="cyan")
    table.add_column("Collected", justify="right")
    table.add_column("Inserted", justify="right")
    for src, stats in totals["by_source"].items():
        table.add_row(src, str(stats["collected"]), str(stats["inserted"]))
    table.add_row("[bold]total[/bold]",
                  f"[bold]{totals['collected']}[/bold]",
                  f"[bold]{totals['inserted']}[/bold]")
    console.print(table)


def step_score(config: dict, limit: int | None = None, sample: bool = False) -> dict:
    _banner("SCORE — scoring unscored contacts")

    try:
        from scoring.contact_scorer import score_contact
    except ImportError as e:
        console.print(f"[red]contact_scorer unavailable: {e}[/red]")
        return {"scored": 0, "failed": 0}

    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT c.id AS contact_id, f.id AS firm_id
               FROM contacts c
               JOIN firms f ON c.firm_id = f.id
               LEFT JOIN scores s ON s.contact_id = c.id
               WHERE s.id IS NULL
                 AND (f._status IS NULL OR f._status != 'customer')
               ORDER BY f.tier ASC, c.id ASC""" +
            (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]No unscored contacts.[/yellow]")
        return {"scored": 0, "failed": 0}

    console.print(f"Found {len(rows)} unscored contacts")
    scored = failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scoring…", total=len(rows))
        for r in rows:
            firm, contact, sigs = _load_firm_contact_signals(r["firm_id"], r["contact_id"])
            try:
                score_contact(firm, contact, sigs, mock_mode=sample)
                scored += 1
            except Exception as e:
                failed += 1
                logger.warning(f"score failed for contact {contact.get('id')}: {e}")
                logger.debug(traceback.format_exc())
            progress.advance(task)

    console.print(f"[green]Scored {scored}[/green]  [red]Failed {failed}[/red]")
    return {"scored": scored, "failed": failed}


def step_queue(config: dict, threshold: float = 55.0, limit: int | None = None) -> dict:
    _banner(f"QUEUE — enqueuing contacts with score ≥ {threshold}")

    conn = get_db()
    try:
        query = """
            SELECT s.contact_id, s.firm_id, s.id AS score_id, s.score,
                   s.label, c.name AS contact_name, f.name AS firm_name
            FROM scores s
            JOIN contacts c ON c.id = s.contact_id
            JOIN firms f    ON f.id = s.firm_id
            LEFT JOIN outreach_queue q ON q.contact_id = s.contact_id
            WHERE s.score >= ?
              AND q.id IS NULL
            ORDER BY s.score DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query, (threshold,)).fetchall()

        enqueued = 0
        for row in rows:
            conn.execute(
                """INSERT INTO outreach_queue
                   (contact_id, firm_id, score_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (row["contact_id"], row["firm_id"], row["score_id"], now_iso(), now_iso()),
            )
            enqueued += 1
        conn.commit()
    finally:
        conn.close()

    console.print(f"[green]Enqueued {enqueued} contact(s)[/green]")
    _print_top_queue(threshold)
    return {"enqueued": enqueued}


def _print_top_queue(threshold: float, n: int = 10) -> None:
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT f.name AS firm, c.name AS contact, c.title, s.score, s.label, q.status
               FROM outreach_queue q
               JOIN contacts c ON c.id = q.contact_id
               JOIN firms f    ON f.id = q.firm_id
               JOIN scores s   ON s.id = q.score_id
               WHERE s.score >= ?
               ORDER BY s.score DESC LIMIT ?""",
            (threshold, n),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return

    table = Table(title=f"Top {len(rows)} in queue", show_lines=False)
    table.add_column("Firm", style="cyan")
    table.add_column("Contact")
    table.add_column("Title", overflow="fold")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Label")
    table.add_column("Status")
    for r in rows:
        table.add_row(r["firm"], r["contact"], r["title"] or "—",
                      f"{r['score']:.1f}" if r["score"] is not None else "—",
                      r["label"] or "—", r["status"])
    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="MEDDIC Engine pipeline")
    parser.add_argument("--collect", action="store_true", help="Run signal collectors")
    parser.add_argument("--score",   action="store_true", help="Score unscored contacts")
    parser.add_argument("--enrich",  action="store_true", help="Run Hunter.io email enrichment")
    parser.add_argument("--sec-load",   action="store_true", help="Load SEC ADV + Schedule A bulk data")
    parser.add_argument("--sec-enrich", action="store_true", help="Promote TBD contacts using SEC + Hunter")
    parser.add_argument("--discover-team", nargs="*", metavar="FIRM",
                        help="Exa + Claude team-page discovery for given firm names (or all firms missing contacts)")
    parser.add_argument("--scrape-team-url", nargs=2, metavar=("FIRM", "URL"),
                        help="Scrape a specific team-page URL for a given firm name")
    parser.add_argument("--backfill-from-signals", action="store_true",
                        help="Turn signal authors into contacts at their firms")
    parser.add_argument("--queue",   action="store_true", help="Enqueue top scored contacts")
    parser.add_argument("--full",    action="store_true", help="collect + score + queue")
    parser.add_argument("--seed",    action="store_true", help="Seed firms/contacts/signals")
    parser.add_argument("--sample",  action="store_true", help="Seeded data only — no live APIs")
    parser.add_argument("--limit",   type=int, default=None)
    parser.add_argument("--threshold", type=float, default=55.0)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # Single-writer lock — block concurrent pipeline runs on the same DB
    _pipeline_lock = open('/tmp/_pipeline.lock', 'w')
    try:
        fcntl.flock(_pipeline_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        console.print("[yellow]Another pipeline is already running. Exiting.[/yellow]")
        return 0


    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    init_db()
    config = load_config(os.path.join(ROOT, "config", "config.yaml"))

    started = datetime.now(timezone.utc)
    console.print(f"[dim]Started {started.isoformat(timespec='seconds')}[/dim]")

    did_work = False
    if args.seed:
        step_seed(); did_work = True

    if args.full or args.collect:
        step_collect(config, sample=args.sample); did_work = True

    if args.full or args.score:
        step_score(config, limit=args.limit, sample=args.sample); did_work = True

    if args.full or args.enrich:
        _banner("ENRICH — Hunter.io email lookup")
        from enrichment.hunter_enricher import enrich_contacts
        n = enrich_contacts(limit=args.limit or 50)
        console.print(f"[green]Enriched {n} contact(s)[/green]")
        did_work = True

    if getattr(args, "sec_load", False):
        _banner("SEC ADV — load bulk universe + Schedule A")
        from data.sec_adv.adv_loader import (
            download_bulk, load_adv_base, load_schedule_a, print_summary,
        )
        conn = get_db()
        try:
            download_bulk()
            load_adv_base(conn)
            load_schedule_a(conn)
            print_summary(conn)
        finally:
            conn.close()
        did_work = True

    if getattr(args, "discover_team", None) is not None:
        _banner("DISCOVER — Exa team-page scrape + Hunter")
        from enrichment.exa_enricher import enrich_missing_firms
        conn = get_db()
        try:
            firm_names = args.discover_team or None
            summary = enrich_missing_firms(conn, firm_names)
            console.print(
                f"[green]Discovery: {summary['firms_processed']} firms → "
                f"{summary['contacts_added']} new contacts → "
                f"{summary['emails_found']} with verified email[/green]"
            )
        finally:
            conn.close()
        did_work = True

    if getattr(args, "scrape_team_url", None):
        firm_name, url = args.scrape_team_url
        _banner(f"SCRAPE — {firm_name} @ {url}")
        from enrichment.exa_enricher import discover_from_url, enrich_firm
        conn = get_db()
        try:
            firm = conn.execute("SELECT id FROM firms WHERE name = ?",
                                (firm_name,)).fetchone()
            if not firm:
                console.print(f"[red]firm not found: {firm_name}[/red]")
            else:
                cands = discover_from_url(url)
                console.print(f"  url returned {len(cands)} candidates")
                result = enrich_firm(conn, firm["id"], cands,
                                     source_label="team_page_direct")
                console.print(f"  [green]{result}[/green]")
        finally:
            conn.close()
        did_work = True

    if getattr(args, "backfill_from_signals", False):
        _banner("BACKFILL — convert signal authors into contacts")
        n = _backfill_signal_authors()
        console.print(f"[green]{n} new contacts from signal authors[/green]")
        did_work = True

    if args.full or getattr(args, "sec_enrich", False):
        _banner("SEC ENRICH — promote TBD contacts from SEC Schedule A")
        from data.sec_adv.adv_loader import enrich_from_sec
        conn = get_db()
        try:
            enrich_from_sec(conn)
        finally:
            conn.close()
        did_work = True

    if args.full or args.queue:
        step_queue(config, threshold=args.threshold, limit=args.limit); did_work = True

    if not did_work:
        parser.print_help()
        return 1

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    console.print(f"[dim]Done in {elapsed:.1f}s[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
