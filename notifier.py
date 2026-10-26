from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import requests

SLACK_PREFIX = "https://hooks.slack.com/"
MAX_POSTS_IN_ALERT = 10


@dataclass
class AlertResult:
    sent: bool
    status: str  # SENT, NOTHING_TO_REPORT, NO_WEBHOOK, INVALID_WEBHOOK, FAILED
    message: str


def _g(obj: Any, *names: str, default=None):
    for n in names:
        val = getattr(obj, n, None)
        if val is not None:
            return val
    return default


def build_alert_text(domain: str, new_articles: list, pricing_results: Optional[list] = None) -> str:
    lines: list[str] = []
    n = len(new_articles)
    if n:
        word = "post" if n == 1 else "posts"
        lines.append(f"*{domain} published {n} new {word}*")
        for art in new_articles[:MAX_POSTS_IN_ALERT]:
            title = _g(art, "title", default="(untitled)")
            url = _g(art, "canonical_url", "url", default="")
            kw = _g(art, "target_keyword", default="n/a")
            vol = _g(art, "search_volume")
            status = _g(art, "keyword_status", default="")
            vol_txt = f"{vol:,}/mo" if isinstance(vol, int) else f"n/a ({status})" if status else "n/a"
            lines.append(f"• *{title}*\n   {url}\n   keyword: {kw} | search volume: {vol_txt}")
        if n > MAX_POSTS_IN_ALERT:
            lines.append(f"...and {n - MAX_POSTS_IN_ALERT} more")

    for pr in pricing_results or []:
        if getattr(pr, "has_changes", False):
            lines.append(f"*Pricing change on {pr.page_url}*")
            for c in pr.changes:
                lines.append(f"• {c}")
    return "\n".join(lines)


def send_slack_alert(
    domain: str,
    new_articles: list,
    pricing_results: Optional[list] = None,
    webhook_url: Optional[str] = None,
    session: Optional[requests.Session] = None,
    timeout: int = 10,
) -> AlertResult:
    text = build_alert_text(domain, new_articles, pricing_results)
    if not text:
        return AlertResult(False, "NOTHING_TO_REPORT", "No new posts or pricing changes, so no alert sent.")

    url = (webhook_url or os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if not url:
        return AlertResult(False, "NO_WEBHOOK", "SLACK_WEBHOOK_URL is not configured.")
    if not url.startswith(SLACK_PREFIX):
        return AlertResult(False, "INVALID_WEBHOOK", "Webhook URL must start with https://hooks.slack.com/")

    try:
        resp = (session or requests).post(url, json={"text": text}, timeout=timeout)
    except requests.RequestException as exc:
        return AlertResult(False, "FAILED", f"Network error sending Slack alert: {exc}")

    if resp.status_code == 200:
        return AlertResult(True, "SENT", "Slack alert sent.")
    if resp.status_code in {400, 403, 404}:
        return AlertResult(False, "INVALID_WEBHOOK", f"Slack rejected the webhook (HTTP {resp.status_code}).")
    return AlertResult(False, "FAILED", f"Slack returned HTTP {resp.status_code}.")