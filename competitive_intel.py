"""competitive_intel.py — CLI entrypoint for the competitive intelligence pipeline.

Usage
-----
    python competitive_intel.py                       # run full pipeline for all competitors
    python competitive_intel.py --slug f2             # single competitor
    python competitive_intel.py --force-ingest        # re-fetch pages even if recently ingested
    python competitive_intel.py --regenerate-briefs   # regenerate cached briefs/trajectories
    python competitive_intel.py --ingest-only         # ingest only, skip Claude analysis
    python competitive_intel.py --seed-only           # seed competitors to DB and exit
"""

import argparse
import logging
import sys
import traceback
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-25s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


def run(
    slugs: Optional[List[str]] = None,
    force_ingest: bool = False,
    regenerate_briefs: bool = False,
    skip_analysis: bool = False,
    ingest_only: bool = False,
) -> int:
    """Orchestrate the competitive intelligence pipeline.

    Parameters
    ----------
    slugs:
        Optional list of competitor slugs to process.  When *None* all
        seeded competitors are processed.
    force_ingest:
        Re-fetch pages even if they were recently ingested.
    regenerate_briefs:
        Regenerate cached briefs and trajectories even if they already exist.
    skip_analysis:
        Skip the Claude analysis phase (brief / trajectory / signals).
    ingest_only:
        Alias for ``skip_analysis`` — ingest pages and news but skip Claude.

    Returns
    -------
    int
        0 on success, non-zero on error.
    """
    from competitive.models import (
        init_competitive_db, get_all_competitors, get_competitor,
        create_pipeline_run, update_pipeline_run,
    )
    from competitive.competitors import seed_competitors, verify_urls
    from competitive.ingestion import ingest_competitor, search_news
    from competitive.analysis import (
        generate_brief, generate_trajectory, detect_signals,
        reset_run_stats, get_run_stats,
    )

    # 0. Reset cost tracking and create pipeline run record
    reset_run_stats()

    # 1. Initialise schema and seed default competitors
    logger.info("Initialising competitive database …")
    init_competitive_db()
    seed_competitors()

    run_id = create_pipeline_run()

    # 2. Load the target competitor set
    all_competitors = get_all_competitors()
    if slugs:
        competitors = [c for c in all_competitors if c["slug"] in slugs]
        missing = set(slugs) - {c["slug"] for c in competitors}
        if missing:
            logger.warning("Unknown competitor slug(s) – skipping: %s", ", ".join(sorted(missing)))
    else:
        competitors = all_competitors

    if not competitors:
        logger.warning("No competitors matched the given slugs; nothing to do.")
        return 0

    # 3. Verify URLs for selected competitors
    selected_slugs = [c["slug"] for c in competitors]
    logger.info("Verifying URLs for %d competitor(s) …", len(competitors))
    verify_urls(slugs=selected_slugs)

    # 4. Record pre-run last_ingested state (for baseline detection)
    pre_run_ingested = {c["slug"]: c["last_ingested"] for c in competitors}

    # 5. Ingest + analyse each competitor (with per-competitor error isolation)
    run_analysis = not (ingest_only or skip_analysis)
    succeeded: list[str] = []
    failed: dict[str, str] = {}

    for comp in competitors:
        slug = comp["slug"]
        url = comp["url"]
        name = comp["name"]
        positioning = comp["positioning"] or ""

        try:
            # Ingestion
            logger.info("[%s] Ingesting pages from %s …", slug, url)
            ingest_competitor(slug, url)
            logger.info("[%s] Searching news for '%s' …", slug, name)
            search_news(name, slug)

            # Claude analysis (unless skipped)
            if run_analysis:
                logger.info("[%s] Generating brief …", slug)
                generate_brief(slug, name, force=regenerate_briefs)
                logger.info("[%s] Generating trajectory …", slug)
                generate_trajectory(slug, name, force=regenerate_briefs)

                # Skip signal detection on first ingestion (baseline run)
                if pre_run_ingested.get(slug) is None:
                    logger.info("Skipping signal detection for %s — first ingestion (baseline)", name)
                else:
                    logger.info("[%s] Detecting signals …", slug)
                    detect_signals(slug, name, positioning)

            succeeded.append(slug)

        except Exception:
            logger.error("[%s] Pipeline failed:\n%s", slug, traceback.format_exc())
            failed[slug] = traceback.format_exc()
            continue

    if not run_analysis:
        logger.info("Analysis phase skipped (ingest_only=%s, skip_analysis=%s).", ingest_only, skip_analysis)

    # Summary
    logger.info(
        "Pipeline complete: %d succeeded, %d failed out of %d competitor(s).",
        len(succeeded), len(failed), len(competitors),
    )
    if failed:
        logger.warning("Failed competitors: %s", ", ".join(sorted(failed.keys())))

    # Cost logging
    stats = get_run_stats()
    input_cost = stats["input_tokens"] * 3.0 / 1_000_000
    output_cost = stats["output_tokens"] * 15.0 / 1_000_000
    total_cost = input_cost + output_cost
    logger.info(
        "Claude usage: %d calls, %d input tokens, %d output tokens, $%.2f",
        stats["claude_calls"], stats["input_tokens"], stats["output_tokens"], total_cost,
    )

    from datetime import datetime, timezone
    update_pipeline_run(
        run_id,
        completed_at=datetime.now(timezone.utc).isoformat(),
        competitors_processed=len(succeeded),
        competitors_failed=len(failed),
        claude_calls=stats["claude_calls"],
        claude_input_tokens=stats["input_tokens"],
        claude_output_tokens=stats["output_tokens"],
        claude_cost_usd=total_cost,
        status="completed" if not failed else "partial",
    )

    return 0


def main() -> None:
    """Parse CLI arguments and invoke :func:`run`."""
    parser = argparse.ArgumentParser(
        description="Competitive intelligence pipeline for .",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--slug",
        dest="slug",
        metavar="SLUG",
        help="Run for a single competitor (by slug).",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        default=False,
        help="Re-fetch pages even if they were recently ingested.",
    )
    parser.add_argument(
        "--regenerate-briefs",
        action="store_true",
        default=False,
        help="Force regeneration of cached briefs and trajectories.",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        default=False,
        help="Only ingest pages and news; skip Claude analysis.",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        default=False,
        help="Only seed competitors to the database and exit.",
    )

    args = parser.parse_args()

    if args.seed_only:
        from competitive.models import init_competitive_db
        from competitive.competitors import seed_competitors
        logger.info("Seeding competitors …")
        init_competitive_db()
        seed_competitors()
        logger.info("Seed complete.")
        sys.exit(0)

    slugs = [args.slug] if args.slug else None
    exit_code = run(
        slugs=slugs,
        force_ingest=args.force_ingest,
        regenerate_briefs=args.regenerate_briefs,
        ingest_only=args.ingest_only,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
