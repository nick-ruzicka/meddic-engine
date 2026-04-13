"""dashboard/routes.py

Flask Blueprint that serves the static dashboard pages and their generated
JSON data files. Each HTML page fetches its own `*_data.json` asynchronously.
"""

import os

from flask import Blueprint, abort, send_file, send_from_directory

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


# JSON data files fetched by the pages
@dashboard_bp.route("/<path:filename>.json", methods=["GET"])
def data(filename: str):
    fname = f"{filename}.json"
    if fname not in _DATA_WHITELIST:
        abort(404)
    return send_from_directory(EXPORT, fname)
