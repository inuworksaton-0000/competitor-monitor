from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

import requests

from change_detector import ChangeDetector
from config import AppConfig
from database import DatabaseManager
from extractor import ExtractedArticle
from keyword_service import KeywordEnrichmentService, KeywordStatus
from pricing_monitor import PricingMonitor, extract_pricing_plans
from scraper import CompetitorCrawler, CrawlReport, CrawlStatus

PRICING_HTML_V1 = """
<html>
<body>
    <div class="pricing-card">
        <h3>Starter</h3>
        <p>$29 / month</p>
    </div>
    <div class="pricing-card">
        <h3>Pro</h3>
        <p>$79 / month</p>
    </div>
    <div class="pricing-card">
        <h3>Enterprise</h3>
        <p>Contact Sales for Custom pricing</p>
    </div>
</body>
</html>
"""

PRICING_HTML_V2_CHANGED = """
<html>
<body>
    <div class="pricing-card">
        <h3>Starter</h3>
        <p>$29 / month</p>
    </div>
    <div class="pricing-card">
        <h3>Pro</h3>
        <p>$99 / month</p>
    </div>
    <div class="pricing-card">
        <h3>Enterprise</h3>
        <p>Contact Sales for Custom pricing</p>
    </div>
</body>
</html>
"""


class TestPricingAndKeywords(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_phase5.db")
        self.config = AppConfig.from_env(
            {
                "SQLITE_DB_PATH": self.db_path,
                "KEYWORD_PROVIDER": "dataforseo",
                "DATAFORSEO_LOGIN": "trial_user@example.com",
                "DATAFORSEO_PASSWORD": "trial_password_123",
            }
        )
        self.db = DatabaseManager(config=self.config, db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pricing_extraction_and_snapshot_diffing(self) -> None:
        plans = extract_pricing_plans(PRICING_HTML_V1)
        self.assertEqual(len(plans), 3)

        mock_crawler = MagicMock(spec=CompetitorCrawler)
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=[],
            pages_fetched=1,
        )
        detector = ChangeDetector(db=self.db, crawler=mock_crawler)
        scan_run = detector.run_scan("acme.com")

        pm = PricingMonitor(self.db)

        res1 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={"https://acme.com/pricing": PRICING_HTML_V1},
        )
        self.assertEqual(len(res1), 1)
        self.assertTrue(res1[0].is_initial_snapshot)
        self.assertFalse(res1[0].has_changes)

        res2 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={"https://acme.com/pricing": PRICING_HTML_V1},
        )
        self.assertFalse(res2[0].has_changes)

        res3 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={"https://acme.com/pricing": PRICING_HTML_V2_CHANGED},
        )
        self.assertTrue(res3[0].has_changes)
        self.assertEqual(len(res3[0].changes), 1)
        self.assertIn("Pro", res3[0].changes[0])
        self.assertIn("79", res3[0].changes[0])
        self.assertIn("99", res3[0].changes[0])

        res4 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={"https://acme.com/pricing": "<html><body>Loading...</body></html>"},
        )
        self.assertFalse(res4[0].has_changes)
        self.assertTrue(res4[0].extraction_uncertain)

    def test_keyword_enrichment_and_caching(self) -> None:
        mock_crawler = MagicMock(spec=CompetitorCrawler)
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=[
                ExtractedArticle(
                    canonical_url="https://acme.com/blog/ai-content-marketing",
                    title="AI Content Marketing Guide",
                    published_at="2026-10-20",
                    target_keyword="ai content marketing",
                )
            ],
            pages_fetched=2,
        )
        detector = ChangeDetector(db=self.db, crawler=mock_crawler)
        scan_res = detector.run_scan("acme.com")

        mock_http = MagicMock(spec=requests.Session)
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 20000,
            "tasks": [
                {
                    "status_code": 20000,
                    "result": [
                        {
                            "keyword": "ai content marketing",
                            "search_volume": 14800,
                            "cpc": 6.45,
                            "competition": "HIGH",
                        }
                    ],
                }
            ],
        }
        mock_http.post.return_value = mock_resp

        kw_service = KeywordEnrichmentService(db=self.db, config=self.config, session=mock_http)
        enriched, warnings = kw_service.enrich_articles(scan_res.new_articles)

        self.assertEqual(len(warnings), 0)
        self.assertEqual(enriched[0].search_volume, 14800)
        self.assertEqual(enriched[0].keyword_status, KeywordStatus.SUCCESS.value)
        self.assertEqual(mock_http.post.call_count, 1)

        enriched_cached, _ = kw_service.enrich_articles(scan_res.new_articles)
        self.assertEqual(enriched_cached[0].search_volume, 14800)
        self.assertEqual(enriched_cached[0].keyword_status, KeywordStatus.CACHED.value)
        self.assertEqual(mock_http.post.call_count, 1)

    def test_keyword_service_handles_invalid_api_key(self) -> None:
        mock_crawler = MagicMock(spec=CompetitorCrawler)
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=[
                ExtractedArticle(
                    canonical_url="https://acme.com/blog/new-post",
                    title="New Post",
                    target_keyword="saas analytics tool",
                )
            ],
            pages_fetched=2,
        )
        detector = ChangeDetector(db=self.db, crawler=mock_crawler)
        scan_res = detector.run_scan("acme.com")

        mock_http = MagicMock(spec=requests.Session)
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 401
        mock_http.post.return_value = mock_resp

        kw_service = KeywordEnrichmentService(db=self.db, config=self.config, session=mock_http)
        enriched, warnings = kw_service.enrich_articles(scan_res.new_articles)

        self.assertIsNone(enriched[0].search_volume)
        self.assertEqual(enriched[0].keyword_status, KeywordStatus.INVALID_CREDENTIALS.value)
        self.assertTrue(any("authentication failed" in w.lower() for w in warnings))


if __name__ == "__main__":
    unittest.main()
