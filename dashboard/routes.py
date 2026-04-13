"""dashboard/routes.py

Flask Blueprint that serves the static dashboard pages and their generated
JSON data files. Each HTML page fetches its own `*_data.json` asynchronously.
"""

import html
import json
import os

from flask import Blueprint, abort, send_file, send_from_directory

from database import get_db

dashboard_bp = Blueprint("dashboard", __name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT = os.path.join(ROOT, "export")

_PAGES = {
    "":            "index.html",
    "analytics":   "analytics.html",
    "ops":         "ops.html",
    "methodology": "methodology.html",
}

_DATA_WHITELIST = {
    "contacts_data.json",
    "analytics_data.json",
    "ops_data.json",
}


def _serve_page(page: str):
    path = os.path.join(EXPORT, page)
    if not os.path.exists(path):
        return (
            f"{page} not yet generated — run the corresponding update_*.py",
            200,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    return send_file(path)


# Pretty routes — /, /analytics, /ops, /methodology
@dashboard_bp.route("/", methods=["GET"])
def index():
    return _serve_page(_PAGES[""])


@dashboard_bp.route("/analytics", methods=["GET"])
def analytics():
    return _serve_page(_PAGES["analytics"])


@dashboard_bp.route("/ops", methods=["GET"])
def ops():
    return _serve_page(_PAGES["ops"])


@dashboard_bp.route("/methodology", methods=["GET"])
def methodology():
    return _serve_page(_PAGES["methodology"])


# .html suffix form — existing pages cross-link with this pattern
@dashboard_bp.route("/<page>.html", methods=["GET"])
def html_page(page: str):
    fname = f"{page}.html"
    if fname not in _PAGES.values():
        abort(404)
    return _serve_page(fname)


# Meeting-ready one-page brief (printable)
@dashboard_bp.route("/brief/<int:queue_id>", methods=["GET"])
def brief(queue_id: int):
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT q.id AS queue_id, q.first_line, q.first_line_edited,
                   c.name AS contact_name, c.title, c.email, c.email_verified,
                   c.linkedin_url,
                   f.name AS firm_name, f.firm_type, f.buying_stage,
                   f.aum_range, f.geography, f.tier,
                   s.score, s.icp_fit, s.ai_readiness, s.reachability,
                   s.signal_freshness, s.label, s.action, s.account_brief,
                   COALESCE(sig_q.signal_type, sig_c.signal_type, sig_f.signal_type) AS signal_type,
                   COALESCE(sig_q.content,     sig_c.content,     sig_f.content)     AS signal_content,
                   COALESCE(sig_q.source_url,  sig_c.source_url,  sig_f.source_url)  AS signal_url,
                   COALESCE(sig_q.signal_date, sig_c.signal_date, sig_f.signal_date) AS signal_date
              FROM outreach_queue q
              JOIN contacts c ON c.id = q.contact_id
              JOIN firms    f ON f.id = q.firm_id
              LEFT JOIN scores  s   ON s.id = q.score_id
              LEFT JOIN signals sig_q ON sig_q.id = q.signal_id
              LEFT JOIN signals sig_c ON sig_c.id = (
                  SELECT id FROM signals WHERE contact_id = c.id
                   ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1)
              LEFT JOIN signals sig_f ON sig_f.id = (
                  SELECT id FROM signals WHERE firm_id = f.id
                   ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1)
             WHERE q.id = ?
        """, (queue_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        abort(404)

    try:
        brief_data = json.loads(row["account_brief"]) if row["account_brief"] else {}
    except Exception:
        brief_data = {}

    e = html.escape
    n = lambda v: "-" if v is None else str(round(float(v)))
    first_line = row["first_line_edited"] or row["first_line"] or ""

    def block(label: str, value: str) -> str:
        if not value:
            return ""
        return (
            f'<section class="blk"><div class="lbl">{e(label)}</div>'
            f'<div class="val">{e(value)}</div></section>'
        )

    sig_html = ""
    if row["signal_content"] or row["signal_type"]:
        sig_url = row["signal_url"] or ""
        sig_meta = f"{row['signal_type'] or 'signal'}"
        if row["signal_date"]:
            sig_meta += f" · {row['signal_date']}"
        sig_html = (
            '<section class="blk"><div class="lbl">Source Signal</div>'
            f'<div class="val"><div class="meta">{e(sig_meta)}</div>'
            f'<div>{e((row["signal_content"] or "")[:800])}</div>'
            + (f'<div class="src"><a href="{e(sig_url)}">{e(sig_url)}</a></div>' if sig_url else '')
            + '</div></section>'
        )

    linkedin_html = (
        f' · <a href="{e(row["linkedin_url"])}">LinkedIn</a>'
        if row["linkedin_url"] else ""
    )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Brief · {e(row["firm_name"])} · {e(row["contact_name"])}</title>
<style>
  *{{box-sizing:border-box}}
  html,body{{margin:0;padding:0;background:#fff;color:#111;
    font-family:Georgia,'Times New Roman',serif;font-size:12pt;line-height:1.45}}
  .wrap{{max-width:760px;margin:0 auto;padding:32px 40px 60px}}
  .bar{{display:flex;align-items:center;justify-content:space-between;
    padding:0 0 14px;border-bottom:1px solid #222;margin-bottom:22px}}
  .brand{{font-family:'Helvetica Neue',Arial,sans-serif;font-size:11px;
    letter-spacing:0.12em;text-transform:uppercase;color:#111;font-weight:600}}
  .print-btn{{font-family:'Helvetica Neue',Arial,sans-serif;font-size:11px;
    letter-spacing:0.08em;text-transform:uppercase;padding:6px 12px;
    border:1px solid #111;background:#fff;cursor:pointer;color:#111}}
  h1{{font-size:22pt;margin:0 0 4px;font-weight:600;letter-spacing:-0.01em}}
  .meta{{font-family:'Helvetica Neue',Arial,sans-serif;font-size:10pt;
    color:#666;letter-spacing:0.04em;margin-bottom:18px}}
  .contact-card{{padding:12px 0 14px;border-top:1px solid #ddd;border-bottom:1px solid #ddd;margin:14px 0 20px}}
  .contact-name{{font-size:14pt;font-weight:600}}
  .contact-sub{{color:#444;font-size:11pt;margin-top:2px}}
  .score-row{{display:flex;gap:18px;align-items:baseline;margin:14px 0 4px;
    font-family:'Helvetica Neue',Arial,sans-serif;font-size:11pt}}
  .score-big{{font-size:26pt;font-weight:700;letter-spacing:-0.02em}}
  .dims{{font-family:'Helvetica Neue',Arial,sans-serif;font-size:10pt;
    color:#444;letter-spacing:0.02em}}
  .blk{{margin:16px 0 0;page-break-inside:avoid}}
  .lbl{{font-family:'Helvetica Neue',Arial,sans-serif;font-size:9pt;
    letter-spacing:0.14em;text-transform:uppercase;color:#666;
    margin-bottom:4px;font-weight:600}}
  .val{{white-space:pre-wrap}}
  .val .meta{{font-family:'Helvetica Neue',Arial,sans-serif;font-size:10pt;
    color:#666;margin-bottom:4px}}
  .src a{{color:#1a4ae0;font-size:10pt;word-break:break-all}}
  a{{color:#111;text-decoration:underline}}
  @media print {{
    .print-btn{{display:none}}
    .wrap{{max-width:none;padding:0 0.5in}}
    body{{font-size:11pt}}
  }}
</style>
</head><body>
<div class="wrap">
  <div class="bar">
    <div class="brand">⊞ MEDDIC Engine · Account Brief</div>
    <button class="print-btn" onclick="window.print()">Print / Save PDF</button>
  </div>

  <h1>{e(row["firm_name"])}</h1>
  <div class="meta">{e((row["firm_type"] or "").upper())} · {e(row["aum_range"] or "AUM n/a")} · {e(row["geography"] or "")} · Tier {row["tier"] or 1}</div>

  <div class="contact-card">
    <div class="contact-name">{e(row["contact_name"])}</div>
    <div class="contact-sub">{e(row["title"] or "")}</div>
    <div class="contact-sub">{e(row["email"] or "no email on file")}{" (verified)" if row["email_verified"] else ""}{linkedin_html}</div>
  </div>

  <div class="score-row">
    <div class="score-big">{n(row["score"])}</div>
    <div>{e(row["label"] or "")} · {e(row["action"] or "")}</div>
  </div>
  <div class="dims">ICP {n(row["icp_fit"])} · AI {n(row["ai_readiness"])} · Reach {n(row["reachability"])} · Fresh {n(row["signal_freshness"])}</div>

  {block("Identified Pain", brief_data.get("identified_pain") or brief_data.get("why_now", ""))}
  {block("Likely Objection", brief_data.get("objection", ""))}
  {block("Decision Criteria", brief_data.get("decision_criteria") or brief_data.get("angle", ""))}
  {block("Metrics", brief_data.get("metrics") or brief_data.get("proof_point", ""))}
  {block("Decision Process", brief_data.get("decision_process", ""))}
  {block("Why This Person", brief_data.get("champion_eb") or brief_data.get("why_this_contact", ""))}
  {block("Multi-Thread", brief_data.get("thread", ""))}
  {block("Suggested First Line", first_line)}
  {sig_html}
</div>
</body></html>
"""


# Shared stylesheet — single source of truth for design tokens
@dashboard_bp.route("/shared.css", methods=["GET"])
def shared_css():
    return send_from_directory(EXPORT, "shared.css", mimetype="text/css")


# JSON data files fetched by the pages
@dashboard_bp.route("/<path:filename>.json", methods=["GET"])
def data(filename: str):
    fname = f"{filename}.json"
    if fname not in _DATA_WHITELIST:
        abort(404)
    return send_from_directory(EXPORT, fname)
