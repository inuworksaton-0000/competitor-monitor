from types import SimpleNamespace
from unittest.mock import MagicMock

from notifier import build_alert_text, send_slack_alert

ART = SimpleNamespace(
    title="Post A", canonical_url="https://x.com/blog/a",
    target_keyword="kw a", search_volume=1200, keyword_status="SUCCESS",
)


def test_alert_text_has_title_url_keyword():
    text = build_alert_text("x.com", [ART])
    assert "x.com published 1 new post" in text
    assert "Post A" in text and "https://x.com/blog/a" in text
    assert "1,200/mo" in text


def test_nothing_to_report():
    assert send_slack_alert("x.com", []).status == "NOTHING_TO_REPORT"


def test_no_webhook(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert send_slack_alert("x.com", [ART]).status == "NO_WEBHOOK"


def test_invalid_webhook():
    assert send_slack_alert("x.com", [ART], webhook_url="http://evil.com/x").status == "INVALID_WEBHOOK"


def test_sent_ok():
    sess = MagicMock()
    sess.post.return_value = MagicMock(status_code=200)
    r = send_slack_alert("x.com", [ART], webhook_url="https://hooks.slack.com/services/T/B/X", session=sess)
    assert r.sent and sess.post.call_count == 1