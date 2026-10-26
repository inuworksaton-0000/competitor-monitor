from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from change_detector import ChangeDetector
from config import AppConfig
from database import DatabaseManager
from extractor import ExtractedArticle
from scraper import CompetitorCrawler, CrawlReport, CrawlStatus


class TestChangeDetector(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_monitor.db")
        self.config = AppConfig.from_env({"SQLITE_DB_PATH": self.db_path})
        self.db = DatabaseManager(config=self.config, db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_first_scan_vs_second_scan_idempotency(self) -> None:
        initial_articles = [
            ExtractedArticle(
                canonical_url="https://acme.com/blog/post-1",
                title="Post 1: AI Agents in Production",
                published_at="2026-10-01",
                meta_description="First post description.",
                target_keyword="ai agents production",
            ),
            ExtractedArticle(
                canonical_url="https://acme.com/blog/post-2/",
                title="Post 2: Scaling SaaS Content",
                published_at="2026-10-05",
                meta_description="Second post description.",
                target_keyword="scaling saas content",
            ),
        ]

        mock_crawler = MagicMock(spec=CompetitorCrawler)
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=initial_articles,
            pages_fetched=4,
        )

        detector = ChangeDetector(db=self.db, crawler=mock_crawler)

        run1 = detector.run_scan("acme.com")
        self.assertEqual(run1.status, CrawlStatus.SUCCESS)
        self.assertTrue(run1.is_initial_baseline)
        self.assertEqual(len(run1.new_articles), 2)
        self.assertEqual(len(run1.existing_articles), 0)

        repeat_articles = [
            ExtractedArticle(
                canonical_url="https://acme.com/blog/post-1?utm_source=rss",
                title="Post 1: AI Agents in Production",
                published_at="2026-10-01",
                meta_description="First post description.",
                target_keyword="ai agents production",
            ),
            ExtractedArticle(
                canonical_url="https://acme.com/blog/post-2",
                title="Post 2: Scaling SaaS Content",
                published_at="2026-10-05",
                meta_description="Second post description.",
                target_keyword="scaling saas content",
            ),
        ]
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=repeat_articles,
            pages_fetched=4,
        )

        run2 = detector.run_scan("acme.com")
        self.assertEqual(run2.status, CrawlStatus.SUCCESS)
        self.assertFalse(run2.is_initial_baseline)
        self.assertEqual(len(run2.new_articles), 0)
        self.assertEqual(len(run2.existing_articles), 2)

        third_articles = repeat_articles + [
            ExtractedArticle(
                canonical_url="https://acme.com/blog/post-3-brand-new",
                title="Post 3: Brand New Release",
                published_at="2026-10-24",
                meta_description="Third post description.",
                target_keyword="brand release",
            )
        ]
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=third_articles,
            pages_fetched=5,
        )

        run3 = detector.run_scan("acme.com")
        self.assertEqual(len(run3.new_articles), 1)
        self.assertEqual(run3.new_articles[0].canonical_url, "https://acme.com/blog/post-3-brand-new")
        self.assertEqual(len(run3.existing_articles), 2)

    def test_failed_or_blocked_scan_preserves_historical_articles(self) -> None:
        mock_crawler = MagicMock(spec=CompetitorCrawler)
        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.SUCCESS,
            articles=[
                ExtractedArticle(
                    canonical_url="https://acme.com/blog/post-1",
                    title="Post 1",
                    published_at="2026-10-01",
                )
            ],
            pages_fetched=2,
        )
        detector = ChangeDetector(db=self.db, crawler=mock_crawler)
        first_run = detector.run_scan("acme.com")
        self.assertEqual(len(first_run.new_articles), 1)

        mock_crawler.crawl_domain.return_value = CrawlReport(
            domain="acme.com",
            root_url="https://acme.com",
            status=CrawlStatus.BLOCKED_OR_FORBIDDEN,
            articles=[],
            error_message="HTTP 403 Cloudflare block",
            pages_fetched=1,
        )
        blocked_run = detector.run_scan("acme.com")
        self.assertEqual(blocked_run.status, CrawlStatus.BLOCKED_OR_FORBIDDEN)
        self.assertEqual(len(blocked_run.new_articles), 0)
        self.assertEqual(len(blocked_run.existing_articles), 1)
        self.assertEqual(blocked_run.existing_articles[0].canonical_url, "https://acme.com/blog/post-1")


if __name__ == "__main__":
    unittest.main()
