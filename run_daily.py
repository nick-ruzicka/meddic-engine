#!/usr/bin/env python3
"""run_daily.py — Daily orchestrator for the competitive signal engine.

Runs all 5 collectors against all 6 competitors, classifies signals,
stores to DB, and triggers weekly digest on Mondays.

Usage:
    python run_daily.py                  # Full daily run
    python run_daily.py --baseline       # First-time baseline capture
    python run_daily.py --dry-run        # Collect + classify but don't store
    python run_daily.py --digest-only    # Just generate this week's digest
    python run_daily.py --competitor f2  # Run for one competitor only
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from database import get_db
from competitive.collectors.base import RawSignal
from competitive.collectors.sitemap_collector import SitemapCollector
from competitive.collectors.jobs_collector import JobsCollector
from competitive.collectors.dns_collector import DNSCollector
from competitive.collectors.github_collector import GitHubCollector
from competitive.collectors.exa_collector import ExaCollector
from competitive.classifier.signal_classifier import classify
from competitive.classifier.predictive_score import score
from competitive.digest.weekly_digest import generate_digest
from competitive.digest.email_formatter import format_email
from competitive.digest.slack_delivery import send_digest as send_slack_digest, send_alert as send_slack_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_daily")

CONFIG_PATH = os.path.join(ROOT, "config", "competitors.yaml")

COLLECTORS = [
    SitemapCollector(),
    JobsCollector(),
    DNSCollector(),
    GitHubCollector(),
    ExaCollector(),
]


def load_competitors(slug_filter=None):
    """Load competitor configs from YAML."""
    with open(CONFIG_PATH, "r") as f:
        data = yaml.safe_load(f)
    competitors = data.get("competitors", [])
    if slug_filter:
        competitors = [c for c in competitors if c["slug"] == slug_filter]
    return competitors


def init_signals_table():
    """Create the shared ci_signals table if it doesn't exist."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ci_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor TEXT NOT NULL,
            source TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            raw_url TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            category TEXT,
            predictive_score REAL,
            lead_time_estimate TEXT,
            sales_takeaway TEXT,
            classified_at TEXT,
            sent_in_digest_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ci_signals_observed ON ci_signals(observed_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ci_signals_competitor ON ci_signals(competitor, observed_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ci_signals_category ON ci_signals(category)")
    conn.commit()
    conn.close()


def is_duplicate(signal, conn):
    """Check if this signal already exists in the DB (same competitor + source + signal_type + raw_url on same day)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute("""
        SELECT id FROM ci_signals
        WHERE competitor = ? AND source = ? AND signal_type = ? AND raw_url = ?
          AND observed_at LIKE ?
        LIMIT 1
    """, (signal.competitor, signal.source, signal.signal_type,
          signal.raw_url or "", today + "%")).fetchone()
    return row is not None


def save_signals(signals):
    """Save classified signals to the database, skipping duplicates."""
    conn = get_db()
    saved = 0
    skipped = 0
    for s in signals:
        if is_duplicate(s, conn):
            skipped += 1
            continue
        conn.execute("""
            INSERT INTO ci_signals
                (competitor, source, signal_type, payload, observed_at,
                 raw_url, confidence, category, predictive_score,
                 lead_time_estimate, sales_takeaway, classified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s.competitor, s.source, s.signal_type,
            json.dumps(s.payload), s.observed_at.isoformat(),
            s.raw_url, s.confidence, s.category,
            s.predictive_score, s.lead_time_estimate,
            s.sales_takeaway, datetime.now(timezone.utc).isoformat(),
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved, skipped


def run_baseline(competitors):
    """First-time baseline capture for all collectors."""
    for collector in COLLECTORS:
        for comp in competitors:
            try:
                logger.info("[baseline] %s × %s", collector.name, comp["slug"])
                collector.baseline(comp)
            except Exception as e:
                logger.error("[baseline] %s × %s failed: %s", collector.name, comp["slug"], e)


def run_collect(competitors):
    """Run all collectors against all competitors. Return raw signals."""
    all_signals = []
    for collector in COLLECTORS:
        for comp in competitors:
            try:
                logger.info("[collect] %s × %s", collector.name, comp["slug"])
                signals = collector.collect(comp)
                if signals:
                    logger.info("  → %d signals from %s for %s",
                                len(signals), collector.name, comp["slug"])
                all_signals.extend(signals)
            except Exception as e:
                logger.error("[collect] %s × %s failed: %s", collector.name, comp["slug"], e)
    return all_signals


def run_classify(raw_signals):
    """Classify and score raw signals."""
    classified = classify(raw_signals)
    for s in classified:
        score(s)
    # Filter noise from actionable
    actionable = [s for s in classified if s.category != "noise"]
    noise = [s for s in classified if s.category == "noise"]
    return classified, actionable, noise


def get_week_signals():
    """Load this week's classified signals from the DB for digest."""
    conn = get_db()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = conn.execute("""
        SELECT * FROM ci_signals
        WHERE observed_at >= ? AND category IS NOT NULL AND category != 'noise'
        ORDER BY predictive_score DESC
    """, (week_ago,)).fetchall()
    conn.close()

    from competitive.classifier.base import ClassifiedSignal
    signals = []
    for r in rows:
        signals.append(ClassifiedSignal(
            competitor=r["competitor"],
            source=r["source"],
            signal_type=r["signal_type"],
            payload=json.loads(r["payload"]),
            observed_at=datetime.fromisoformat(r["observed_at"]),
            raw_url=r["raw_url"],
            confidence=r["confidence"],
            category=r["category"],
            predictive_score=r["predictive_score"] or 0.0,
            lead_time_estimate=r["lead_time_estimate"] or "",
            sales_takeaway=r["sales_takeaway"] or "",
        ))
    return signals


def run_digest():
    """Generate and print this week's digest."""
    signals = get_week_signals()
    week_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%B %d, %Y")
    digest = generate_digest(signals, week_start)
    email = format_email(digest)
    logger.info("Weekly digest generated: %d actionable signals", digest.get("actionable_signals", 0))
    print("\n" + "=" * 60)
    print(email)
    print("=" * 60 + "\n")
    return digest


def main():
    parser = argparse.ArgumentParser(description=" Competitive Signal Engine — Daily Runner")
    parser.add_argument("--baseline", action="store_true", help="First-time baseline capture")
    parser.add_argument("--dry-run", action="store_true", help="Collect + classify but don't store")
    parser.add_argument("--digest-only", action="store_true", help="Generate weekly digest from stored signals")
    parser.add_argument("--competitor", type=str, help="Run for a single competitor slug")
    args = parser.parse_args()

    init_signals_table()

    competitors = load_competitors(slug_filter=args.competitor)
    if not competitors:
        logger.error("No competitors found (filter: %s)", args.competitor)
        return 1

    logger.info("Signal Engine v2 — %d competitors, %d collectors",
                len(competitors), len(COLLECTORS))

    if args.digest_only:
        run_digest()
        return 0

    if args.baseline:
        logger.info("Running baseline capture...")
        run_baseline(competitors)
        logger.info("Baseline complete.")
        return 0

    # Daily run
    raw_signals = run_collect(competitors)
    logger.info("Collection complete: %d raw signals", len(raw_signals))

    if not raw_signals:
        logger.info("No new signals detected. Pipeline complete.")
        if datetime.now(timezone.utc).weekday() == 0:  # Monday
            digest = run_digest()
            send_slack_digest(digest)
        return 0

    classified, actionable, noise = run_classify(raw_signals)
    logger.info("Classification: %d actionable, %d noise", len(actionable), len(noise))

    # Sanity bounds: 2-15 actionable signals per day is normal.
    # Outside this band = something is wrong (broken collector or noisy source).
    SANITY_LOW = 2
    SANITY_HIGH = 15
    if len(actionable) == 0:
        logger.warning("SANITY CHECK: 0 actionable signals. All %d signals were noise. "
                        "Check if collectors are working or if classification is too aggressive.", len(raw_signals))
    elif len(actionable) < SANITY_LOW:
        logger.warning("SANITY CHECK: Only %d actionable signals (expected %d-%d). "
                        "Collectors may be under-reporting.", len(actionable), SANITY_LOW, SANITY_HIGH)
    elif len(actionable) > SANITY_HIGH:
        logger.warning("SANITY CHECK: %d actionable signals (expected %d-%d). "
                        "Possible noise leak — review classification rules. "
                        "NOT sending digest until reviewed.",
                        len(actionable), SANITY_LOW, SANITY_HIGH)
    else:
        logger.info("Sanity check passed: %d actionable signals within expected band (%d-%d).",
                    len(actionable), SANITY_LOW, SANITY_HIGH)

    if not args.dry_run:
        saved, skipped = save_signals(classified)
        logger.info("Storage: %d saved, %d duplicates skipped", saved, skipped)
    else:
        logger.info("[DRY RUN] Would save %d signals", len(classified))
        for s in actionable:
            logger.info("  [%s] %s — %s (score=%.2f, lead=%s)",
                        s.category, s.competitor, s.sales_takeaway[:80],
                        s.predictive_score, s.lead_time_estimate)

    # Real-time alerts for high-score signals
    for s in actionable:
        if s.predictive_score > 0.85:
            logger.info("HIGH SCORE ALERT: %s %s (%.2f)", s.competitor, s.category, s.predictive_score)
            send_slack_alert(s)

    # Monday digest — suppress if sanity check failed high
    if datetime.now(timezone.utc).weekday() == 0:  # Monday
        if len(actionable) <= SANITY_HIGH:
            digest = run_digest()
            send_slack_digest(digest)
        else:
            logger.warning("Monday digest SUPPRESSED due to sanity check failure (%d signals). "
                           "Review signals manually before sending.", len(actionable))

    logger.info("Pipeline complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
