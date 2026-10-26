"""
Change detection and atomic scan persistence engine.
Compares newly crawled articles against stored canonical URLs for a competitor,
distinguishing net-new posts from previously seen posts and ensuring failed
or partial scans never overwrite or erase existing historical data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from database import DatabaseManager, StoredArticle, utc_now_iso
from extractor import ExtractedArticle, normalize_url
from scraper import CompetitorCrawler, CrawlReport, CrawlStatus

logger = logging.getLogger("competitor_monitor.change_detector")


@dataclass
class ScanExecutionResult:
    """Complete result of running a scan and change-detection pass on a domain."""

    scan_id: int
    competitor_id: int
    domain: str
    root_url: str
    status: CrawlStatus
    is_initial_baseline: bool
    new_articles: list[StoredArticle] = field(default_factory=list)
    existing_articles: list[StoredArticle] = field(default_factory=list)
    pages_fetched: int = 0
    error_message: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    crawl_report: Optional[CrawlReport] = None


class ChangeDetector:
    """
    Coordinates crawling and database persistence so that:
    1. Every discovered article URL is normalized to its canonical form before comparison.
    2. First-seen timestamps (when our scanner first saw the URL) are stored separately
       from the article's published_at date.
    3. Re-scanning an unchanged website always yields 0 newly discovered articles.
    4. Blocked, timed-out, or failed scans are logged in scan_runs without deleting
       or corrupting previously stored articles.
    """

    def __init__(
        self,
        db: DatabaseManager,
        crawler: Optional[CompetitorCrawler] = None,
    ) -> None:
        self.db = db
        self.crawler = crawler or CompetitorCrawler(config=db.config)

    def run_scan(self, domain_input: str) -> ScanExecutionResult:
        """Crawl domain_input and atomically persist scan results and article deltas."""
        started_at = utc_now_iso()
        report = self.crawler.crawl_domain(domain_input)

        if report.status == CrawlStatus.INVALID_DOMAIN:
            return ScanExecutionResult(
                scan_id=0,
                competitor_id=0,
                domain=report.domain,
                root_url="",
                status=report.status,
                is_initial_baseline=False,
                error_message=report.error_message,
                warnings=report.warnings,
                crawl_report=report,
            )

        competitor_id = self.db.get_or_create_competitor(report.domain, report.root_url)
        existing_urls_before = self.db.get_existing_article_urls(competitor_id)
        is_initial_baseline = len(existing_urls_before) == 0

        completed_at = utc_now_iso()
        new_canonical_urls: set[str] = set()

        with self.db.connection() as conn:
            ph10 = self.db._ph(10)
            warnings_json = json.dumps(report.warnings)

            if self.db.using_postgres:
                cur = self.db._exec(
                    conn,
                    f"""
                    INSERT INTO scan_runs (
                        competitor_id, started_at, completed_at, status,
                        pages_fetched, total_articles_found, new_articles_count,
                        pricing_changes_count, error_message, warnings_json
                    ) VALUES ({ph10}) RETURNING id
                    """,
                    (
                        competitor_id,
                        started_at,
                        completed_at,
                        report.status.value,
                        report.pages_fetched,
                        len(report.articles),
                        0,
                        0,
                        report.error_message,
                        warnings_json,
                    ),
                )
                scan_id = int(cur.fetchone()["id"])
            else:
                cur = self.db._exec(
                    conn,
                    f"""
                    INSERT INTO scan_runs (
                        competitor_id, started_at, completed_at, status,
                        pages_fetched, total_articles_found, new_articles_count,
                        pricing_changes_count, error_message, warnings_json
                    ) VALUES ({ph10})
                    """,
                    (
                        competitor_id,
                        started_at,
                        completed_at,
                        report.status.value,
                        report.pages_fetched,
                        len(report.articles),
                        0,
                        0,
                        report.error_message,
                        warnings_json,
                    ),
                )
                scan_id = int(cur.lastrowid)

            if report.status == CrawlStatus.SUCCESS and report.articles:
                seen_in_batch: set[str] = set()
                for art in report.articles:
                    canon_url = normalize_url(art.canonical_url)
                    if not canon_url or canon_url in seen_in_batch:
                        continue
                    seen_in_batch.add(canon_url)

                    if canon_url not in existing_urls_before:
                        new_canonical_urls.add(canon_url)
                        ph12 = self.db._ph(12)
                        self.db._exec(
                            conn,
                            f"""
                            INSERT INTO articles (
                                competitor_id, canonical_url, title, published_at,
                                meta_description, target_keyword, keyword_status,
                                discovery_source, first_seen_scan_id, first_seen_at,
                                last_seen_scan_id, last_seen_at
                            ) VALUES ({ph12})
                            """,
                            (
                                competitor_id,
                                canon_url,
                                art.title,
                                art.published_at,
                                art.meta_description,
                                art.target_keyword,
                                "PENDING",
                                art.discovery_source,
                                scan_id,
                                completed_at,
                                scan_id,
                                completed_at,
                            ),
                        )
                    else:
                        ph6 = self.db._ph(6)
                        tokens = [t.strip() for t in ph6.split(",")]
                        self.db._exec(
                            conn,
                            f"""
                            UPDATE articles
                            SET last_seen_scan_id = {tokens[0]},
                                last_seen_at = {tokens[1]},
                                published_at = COALESCE(published_at, {tokens[2]}),
                                meta_description = COALESCE(meta_description, {tokens[3]})
                            WHERE competitor_id = {tokens[4]} AND canonical_url = {tokens[5]}
                            """,
                            (
                                scan_id,
                                completed_at,
                                art.published_at,
                                art.meta_description,
                                competitor_id,
                                canon_url,
                            ),
                        )

            ph2 = [t.strip() for t in self.db._ph(2).split(",")]
            self.db._exec(
                conn,
                f"UPDATE scan_runs SET new_articles_count = {ph2[0]} WHERE id = {ph2[1]}",
                (len(new_canonical_urls), scan_id),
            )
            ph3 = [t.strip() for t in self.db._ph(3).split(",")]
            self.db._exec(
                conn,
                f"""
                UPDATE competitors
                SET last_scanned_at = {ph3[0]}, last_scan_status = {ph3[1]}
                WHERE id = {ph3[2]}
                """,
                (completed_at, report.status.value, competitor_id),
            )

        all_stored = self.db.get_competitor_articles(competitor_id, latest_scan_id=scan_id)
        new_articles = [a for a in all_stored if a.canonical_url in new_canonical_urls]
        existing_articles = [a for a in all_stored if a.canonical_url not in new_canonical_urls]

        return ScanExecutionResult(
            scan_id=scan_id,
            competitor_id=competitor_id,
            domain=report.domain,
            root_url=report.root_url,
            status=report.status,
            is_initial_baseline=is_initial_baseline,
            new_articles=new_articles,
            existing_articles=existing_articles,
            pages_fetched=report.pages_fetched,
            error_message=report.error_message,
            warnings=report.warnings,
            crawl_report=report,
        )
