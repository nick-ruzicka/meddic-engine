#!/usr/bin/env python3
"""scripts/attribute_signals.py

Walk every signal with contact_id IS NULL and try to attach it to a named
contact via:
  1. Exact twitter_handle match (confidence 1.0)
  2. Two-gate name match: last name exact + first initial exact + ratio >= 0.80

Run standalone or from main.py after --collect.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db

logger = logging.getLogger(__name__)

_WORD_RX = re.compile(r"[A-Za-z'\-]+")


def _normalize(s: str | None) -> str:
    return (s or "").lower().strip()


def _name_tokens(name: str) -> list[str]:
    """First+last tokens — lowercase, stripped of punctuation. Ignore middle
    initials so 'John Q. Smith' → ['john', 'smith']."""
    toks = [t.lower() for t in _WORD_RX.findall(name or "")]
    toks = [t for t in toks if len(t) > 1]  # drop middle initials
    if len(toks) < 2:
        return toks
    return [toks[0], toks[-1]]


def is_name_match(author_name: str, contact_name: str) -> tuple[bool, float]:
    """Two-gate matcher. Returns (matched, ratio). Gates:
      - Last tokens equal
      - First initial equal
      - SequenceMatcher ratio >= 0.80
    """
    a = _name_tokens(author_name)
    c = _name_tokens(contact_name)
    if len(a) < 2 or len(c) < 2:
        return False, 0.0
    if a[-1] != c[-1]:
        return False, 0.0
    if not a[0] or not c[0] or a[0][0] != c[0][0]:
        return False, 0.0
    ratio = SequenceMatcher(None, _normalize(author_name),
                            _normalize(contact_name)).ratio()
    return ratio >= 0.80, ratio


def _handle_match(author_handle: str, contact_handle: str) -> bool:
    if not author_handle or not contact_handle:
        return False
    a = _normalize(author_handle).lstrip("@")
    c = _normalize(contact_handle).lstrip("@")
    return bool(a) and a == c


def attribute_signals(conn) -> dict:
    """Best-effort attach for signals missing contact_id. Idempotent: a signal
    already attached to a contact is never touched."""
    signals = conn.execute(
        """SELECT id, author_name, author_handle, firm_id
             FROM signals
            WHERE contact_id IS NULL
              AND ((author_name   IS NOT NULL AND author_name   != '')
                OR (author_handle IS NOT NULL AND author_handle != ''))"""
    ).fetchall()

    handle_hits = name_hits = 0
    for sig in signals:
        # Prefer firm-scoped candidates; fall back to all contacts only when
        # the signal has no firm context (rare). A firm-scoped match is far
        # less likely to cross-wire a same-named contact at a different firm.
        if sig["firm_id"]:
            contacts = conn.execute(
                "SELECT id, name, twitter_handle FROM contacts WHERE firm_id=?",
                (sig["firm_id"],),
            ).fetchall()
        else:
            contacts = conn.execute(
                "SELECT id, name, twitter_handle FROM contacts"
            ).fetchall()

        matched_id: int | None = None
        matched_by = ""
        matched_conf = 0.0

        # Gate 1: twitter handle exact.
        if sig["author_handle"]:
            for c in contacts:
                if _handle_match(sig["author_handle"], c["twitter_handle"]):
                    matched_id = c["id"]
                    matched_by = "handle"
                    matched_conf = 1.0
                    break

        # Gate 2: last + first-initial + ratio.
        if not matched_id and sig["author_name"]:
            best_id, best_ratio = None, 0.0
            for c in contacts:
                ok, ratio = is_name_match(sig["author_name"], c["name"])
                if ok and ratio > best_ratio:
                    best_id, best_ratio = c["id"], ratio
            if best_id is not None:
                matched_id = best_id
                matched_by = "name"
                matched_conf = best_ratio

        if matched_id and matched_conf >= 0.80:
            conn.execute(
                "UPDATE signals SET contact_id=? WHERE id=?",
                (matched_id, sig["id"]),
            )
            if matched_by == "handle":
                handle_hits += 1
            else:
                name_hits += 1

    conn.commit()
    return {
        "scanned": len(signals),
        "matched_handle": handle_hits,
        "matched_name": name_hits,
        "matched_total": handle_hits + name_hits,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = get_db()
    try:
        stats = attribute_signals(conn)
    finally:
        conn.close()
    print(f"Signal attribution: scanned {stats['scanned']} unattributed · "
          f"matched {stats['matched_total']} "
          f"({stats['matched_handle']} by handle, "
          f"{stats['matched_name']} by name)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
