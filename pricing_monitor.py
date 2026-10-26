"""
Pricing page monitor.

Extracts plan names and prices from pricing page HTML, stores a snapshot per
competitor page, and reports plan-level changes between consecutive scans.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from database import DatabaseManager, utc_now_iso

logger = logging.getLogger("competitor_monitor.pricing_monitor")

# Class names that usually mark a pricing card or plan container.
_CLASS_RE = re.compile(r"(pric|plan|tier|card|package|bundle)", re.I)

# Text that looks like a price or a "talk to sales" placeholder.
_PRICE_RE = re.compile(
    r"[$\u20ac\u00a3\u20b9]\s?\d|\d\s?(/|per)\s?(mo|month|yr|year|user|seat)|\bfree\b|contact|custom",
    re.I,
)

# Extracts a concise price token like "$29/month", "₹999/yr", "$15/user", or "Custom / Contact Sales"
_CLEAN_PRICE_TOKEN_RE = re.compile(
    r"(?:[$\u20ac\u00a3\u20b9]|\bUSD\b|\bINR\b|\bEUR\b|\bGBP\b|\bRs\.?\b)\s*\d[\d,]*(?:\.\d{1,2})?"
    r"(?:\s*(?:/|per\s+)\s*(?:mo|month|yr|year|user|seat|account|org)\w*)?",
    re.I,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_pricing_plans(html: str) -> list[dict]:
    """Return a list of {"name": ..., "price": ...} dicts found in the page."""
    soup = BeautifulSoup(html or "", "lxml")
    plans: list[dict] = []
    seen: set[str] = set()

    # Strategy 1: Cards with pricing/plan/tier class names
    for element in soup.find_all(class_=_CLASS_RE):
        # Skip outer wrapper only if a child with _CLASS_RE also contains its own heading
        child_cards = [
            c for c in element.find_all(class_=_CLASS_RE)
            if c.find(["h1", "h2", "h3", "h4", "h5"]) is not None
        ]
        if child_cards:
            continue

        heading = element.find(["h1", "h2", "h3", "h4", "h5"])
        if not heading:
            continue
        name = _clean(heading.get_text(" ", strip=True))
        if not name or len(name) > 55 or name.lower() in seen:
            continue
        raw_card_text = _clean(_clean(element.get_text(" ", strip=True)).replace(name, "", 1))
        if not _PRICE_RE.search(raw_card_text):
            continue

        token_match = _CLEAN_PRICE_TOKEN_RE.search(raw_card_text)
        if token_match and len(raw_card_text) > 80:
            price = _clean(token_match.group(0))
        else:
            price = raw_card_text[:90]

        seen.add(name.lower())
        plans.append({"name": name, "price": price})

    # Strategy 2: Heading proximity fallback for modern Tailwind/utility-CSS sites (e.g. Basecamp)
    if not plans:
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            name = _clean(heading.get_text(" ", strip=True))
            if not name or len(name) > 50 or name.lower() in seen:
                continue
            parent = heading.find_parent(["section", "article", "div", "li", "td"])
            if not parent:
                continue
            card_text = _clean(parent.get_text(" ", strip=True))
            if len(card_text) > 900:
                if heading.parent:
                    card_text = _clean(heading.parent.get_text(" ", strip=True))
            token_match = _CLEAN_PRICE_TOKEN_RE.search(card_text)
            if token_match:
                seen.add(name.lower())
                plans.append({"name": name, "price": _clean(token_match.group(0))})
            elif re.search(r"\b(contact sales|custom pricing)\b", card_text, re.I):
                seen.add(name.lower())
                plans.append({"name": name, "price": "Custom / Contact Sales"})

    return plans


def _diff(old: list[dict], new: list[dict]) -> list[str]:
    """Describe plan-level differences between two snapshots."""
    old_map = {p["name"]: p["price"] for p in old}
    new_map = {p["name"]: p["price"] for p in new}
    changes: list[str] = []

    for name, price in new_map.items():
        if name not in old_map:
            changes.append(f"New plan added: {name} ({price})")
        elif old_map[name] != price:
            changes.append(f"{name}: {old_map[name]} -> {price}")
    for name, price in old_map.items():
        if name not in new_map:
            changes.append(f"Plan removed: {name} (was {price})")

    return changes


@dataclass
class PricingResult:
    page_url: str
    plans: list[dict] = field(default_factory=list)
    is_initial_snapshot: bool = False
    has_changes: bool = False
    changes: list[str] = field(default_factory=list)
    extraction_uncertain: bool = False


class PricingMonitor:
    """Stores pricing snapshots and detects changes between scans."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db.connection() as conn:
            self.db._exec(
                conn,
                "CREATE TABLE IF NOT EXISTS pricing_page_snapshots ("
                "competitor_id TEXT, page_url TEXT, plans_json TEXT, "
                "scan_id TEXT, updated_at TEXT)",
                (),
            )

    def _get_snapshot(self, competitor_id, page_url: str):
        ph = [t.strip() for t in self.db._ph(2).split(",")]
        with self.db.connection() as conn:
            cur = self.db._exec(
                conn,
                f"SELECT plans_json FROM pricing_page_snapshots "
                f"WHERE competitor_id = {ph[0]} AND page_url = {ph[1]}",
                (str(competitor_id), page_url),
            )
            row = cur.fetchone()
            if not row:
                return None
            return json.loads(row["plans_json"])

    def _save_snapshot(self, competitor_id, page_url: str, plans: list[dict], scan_id) -> None:
        ph = [t.strip() for t in self.db._ph(2).split(",")]
        with self.db.connection() as conn:
            self.db._exec(
                conn,
                f"DELETE FROM pricing_page_snapshots "
                f"WHERE competitor_id = {ph[0]} AND page_url = {ph[1]}",
                (str(competitor_id), page_url),
            )
            ph5 = self.db._ph(5)
            self.db._exec(
                conn,
                f"INSERT INTO pricing_page_snapshots "
                f"(competitor_id, page_url, plans_json, scan_id, updated_at) VALUES ({ph5})",
                (str(competitor_id), page_url, json.dumps(plans), str(scan_id), utc_now_iso()),
            )

    def list_snapshots(self, competitor_id) -> list[dict]:
        """Return stored pricing snapshots for a competitor."""
        ph1 = self.db._ph(1)
        with self.db.connection() as conn:
            cur = self.db._exec(
                conn,
                f"SELECT page_url, plans_json, updated_at FROM pricing_page_snapshots WHERE competitor_id = {ph1} ORDER BY updated_at DESC",
                (str(competitor_id),),
            )
            out = []
            for r in cur.fetchall():
                try:
                    plans = json.loads(r["plans_json"] or "[]")
                except json.JSONDecodeError:
                    plans = []
                out.append({"page_url": r["page_url"], "plans": plans, "updated_at": r["updated_at"]})
            return out

    def process_pricing_pages(
        self, competitor_id, scan_id=None, pricing_pages_html: dict[str, str] | None = None
    ) -> list[PricingResult]:
        """Compare each pricing page with its stored snapshot and update the snapshot."""
        results: list[PricingResult] = []

        for page_url, html in (pricing_pages_html or {}).items():
            plans = extract_pricing_plans(html)
            result = PricingResult(page_url=page_url, plans=plans)

            if not plans:
                result.extraction_uncertain = True
                results.append(result)
                continue

            previous = self._get_snapshot(competitor_id, page_url)
            if previous is None:
                result.is_initial_snapshot = True
                self._save_snapshot(competitor_id, page_url, plans, scan_id)
            else:
                changes = _diff(previous, plans)
                if changes:
                    result.has_changes = True
                    result.changes = changes
                    self._save_snapshot(competitor_id, page_url, plans, scan_id)

            results.append(result)

        return results

    
