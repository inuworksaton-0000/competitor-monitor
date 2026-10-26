from __future__ import annotations
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from change_detector import ChangeDetector
from config import AppConfig
from database import DatabaseManager
from extractor import ExtractedArticle
from free_seo import audit_html, get_keyword_suggestions
from keyword_service import KeywordEnrichmentService, KeywordStatus
from pricing_monitor import PricingMonitor, extract_pricing_plans
from scraper import CompetitorCrawler, CrawlReport, CrawlStatus

PRICING_HTML_V1 = """
<html><body>
<div class="pricing-card"><h3>Starter</h3><p>$29 / month</p></div>
<div class="pricing-card"><h3>Pro</h3><p>$79 / month</p></div>
<div class="pricing-card"><h3>Enterprise</h3><p>Contact Sales</p></div>
</body></html>
"""

PRICING_HTML_V2_CHANGED = """
<html><body>
<div class="pricing-card"><h3>Starter</h3><p>$29 / month</p></div>
<div class="pricing-card"><h3>Pro</h3><p>$99 / month</p></div>
<div class="pricing-card"><h3>Enterprise</h3><p>Contact Sales</p></div>
</body></html>
"""

class TestPricingAndKeywords(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_p5.db")
        self.config = AppConfig.from_env({"SQLITE_DB_PATH": self.db_path})
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
        url = "https://acme.com/pricing"

        res1 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={url: PRICING_HTML_V1},
        )
        self.assertEqual(len(res1), 1)
        self.assertTrue(res1[0].is_initial_snapshot)
        self.assertFalse(res1[0].has_changes)

        res2 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={url: PRICING_HTML_V1},
        )
        self.assertFalse(res2[0].has_changes)

        res3 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={url: PRICING_HTML_V2_CHANGED},
        )
        self.assertTrue(res3[0].has_changes)
        self.assertEqual(len(res3[0].changes), 1)
        self.assertIn("Pro", res3[0].changes[0])

        res4 = pm.process_pricing_pages(
            scan_run.competitor_id,
            scan_id=scan_run.scan_id,
            pricing_pages_html={url: "<html><body>Loading...</body></html>"},
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
                    canonical_url="https://acme.com/blog/ai-content",
                    title="AI Content Marketing Guide",
                    published_at="2026-10-20",
                    target_keyword="ai content marketing",
                )
            ],
            pages_fetched=2,
        )
        detector = ChangeDetector(db=self.db, crawler=mock_crawler)
        scan_res = detector.run_scan("acme.com")

        kw_service = KeywordEnrichmentService(db=self.db, config=self.config)
        enriched, warnings = kw_service.enrich_articles(scan_res.new_articles)

        self.assertEqual(len(warnings), 1)
        self.assertIsNone(enriched[0].search_volume)
        self.assertEqual(enriched[0].keyword_source, "free_local")
        self.assertEqual(
            enriched[0].keyword_status,
            KeywordStatus.NO_DATA_RETURNED.value,
        )

        enriched_cached, _ = kw_service.enrich_articles(scan_res.new_articles)
        self.assertIsNone(enriched_cached[0].search_volume)
        self.assertEqual(
            enriched_cached[0].keyword_status,
            KeywordStatus.CACHED.value,
        )

    def test_free_seo_audit_and_autocomplete_suggestions(self) -> None:
        html = (
            "<html><head><title>SaaS Analytics Tool</title>"
            "<meta name='description' content='Overview'/>"
            "<link rel='canonical' href='https://acme.com/blog/a'/></head>"
            "<body><h1>SaaS Analytics Tool Guide</h1>"
            "<img src='/a.png' alt='ok'/><img src='/b.png'/>"
            "<a href='/pricing'>Pricing</a></body></html>"
        )
        rep = audit_html(html, "https://acme.com/blog/a", "saas analytics tool")
        self.assertTrue(rep["keyword_in_title"])
        self.assertTrue(rep["keyword_in_h1"])
        self.assertEqual(rep["images_missing_alt"], 1)
        self.assertEqual(rep["internal_link_count"], 1)

        with patch("free_seo.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = ["saas", ["saas tool", "saas seo"]]
            mock_get.return_value = mock_resp
            sug = get_keyword_suggestions("saas")
            self.assertEqual(sug["status"], "SUCCESS")
            self.assertIsNone(sug["search_volume"])
            self.assertEqual(sug["suggestions"], ["saas tool", "saas seo"])
