# File Ownership Contract

Each worker owns EXACTLY these paths. Do NOT edit files owned by other workers. If you need changes to another owner's file, coordinate through PROGRESS.md.

## Terminal 1 — Sitemap
OWNS:
- `competitive/collectors/sitemap_collector.py`
- `competitive/collectors/content_hash.py`
- `tests/test_sitemap_collector.py`

## Terminal 2 — Jobs
OWNS:
- `competitive/collectors/jobs_collector.py`
- `competitive/collectors/job_classifier.py`
- `tests/test_jobs_collector.py`

## Terminal 3 — DNS
OWNS:
- `competitive/collectors/dns_collector.py`
- `tests/test_dns_collector.py`

## Terminal 4 — GitHub
OWNS:
- `competitive/collectors/github_collector.py`
- `competitive/collectors/commit_patterns.py`
- `tests/test_github_collector.py`

## Terminal 5 — Exa + Classifier + Digest
OWNS:
- `competitive/collectors/exa_collector.py`
- `competitive/classifier/signal_classifier.py`
- `competitive/classifier/predictive_score.py`
- `competitive/digest/weekly_digest.py`
- `competitive/digest/email_formatter.py`
- `tests/test_signal_classifier.py`
- `tests/test_digest.py`

## Shared (coordinator-only)
- `config/competitors.yaml`
- `run_daily.py`
- `competitive/collectors/base.py`
- `competitive/classifier/base.py`
- `competitive/digest/__init__.py`
- `competitive/collectors/__init__.py`
- `competitive/classifier/__init__.py`
- `SPEC.md`
- `CONTRACTS.md`
- `PROGRESS.md`
