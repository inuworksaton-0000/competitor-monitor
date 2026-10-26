from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config import AppConfig
from database import DatabaseManager, StoredArticle, utc_now_iso

logger = logging.getLogger("competitor_monitor.keyword_service")


class KeywordStatus(str, Enum):
    SUCCESS = "SUCCESS"
    CACHED = "CACHED"
    NO_DATA_RETURNED = "NO_DATA_RETURNED"
    CREDENTIALS_NOT_CONFIGURED = "CREDENTIALS_NOT_CONFIGURED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


@dataclass
class KeywordMetrics:
    keyword: str
    search_volume: Optional[int]
    cpc: Optional[float]
    competition: Optional[str]
    source: str
    status: KeywordStatus
    error_message: Optional[str] = None


class KeywordEnrichmentService:
    """Free, local keyword enrichment without paid keyword APIs.

    No monthly search volume is invented. Google Autocomplete suggestions
    are handled separately by the application.
    """

    SOURCE = "free_local"

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[AppConfig] = None,
        session=None,
    ) -> None:
        self.db = db
        self.config = config or db.config

    def _get_cached_metrics(self, keyword: str) -> Optional[KeywordMetrics]:
        norm_kw = keyword.strip().lower()
        if not norm_kw:
            return None

        placeholder = self.db._ph(1)
        with self.db.connection() as conn:
            cur = self.db._exec(
                conn,
                f"SELECT * FROM keyword_cache WHERE keyword = {placeholder}",
                (norm_kw,),
            )
            row = cur.fetchone()

        if not row:
            return None

        return KeywordMetrics(
            keyword=norm_kw,
            search_volume=row["search_volume"],
            cpc=row["cpc"],
            competition=row["competition"],
            source=str(row["source"]),
            status=KeywordStatus.CACHED,
        )

    def _save_to_cache(self, metrics: KeywordMetrics) -> None:
        if metrics.status not in {
            KeywordStatus.SUCCESS,
            KeywordStatus.NO_DATA_RETURNED,
        }:
            return

        norm_kw = metrics.keyword.strip().lower()
        placeholder = self.db._ph(1)

        with self.db.connection() as conn:
            self.db._exec(
                conn,
                f"DELETE FROM keyword_cache WHERE keyword = {placeholder}",
                (norm_kw,),
            )
            placeholders = self.db._ph(7)
            self.db._exec(
                conn,
                "INSERT INTO keyword_cache "
                "(keyword, search_volume, cpc, competition, source, status, updated_at) "
                f"VALUES ({placeholders})",
                (
                    norm_kw,
                    metrics.search_volume,
                    metrics.cpc,
                    metrics.competition,
                    metrics.source,
                    metrics.status.value,
                    utc_now_iso(),
                ),
            )

    def enrich_articles(
        self, articles: list[StoredArticle]
    ) -> tuple[list[StoredArticle], list[str]]:
        if not articles:
            return articles, []

        metrics_by_keyword: dict[str, KeywordMetrics] = {}

        for article in articles:
            keyword = (article.target_keyword or "").strip().lower()
            if not keyword:
                continue

            if keyword not in metrics_by_keyword:
                cached = self._get_cached_metrics(keyword)
                if cached is not None:
                    metrics_by_keyword[keyword] = cached
                else:
                    metrics = KeywordMetrics(
                        keyword=keyword,
                        search_volume=None,
                        cpc=None,
                        competition=None,
                        source=self.SOURCE,
                        status=KeywordStatus.NO_DATA_RETURNED,
                        error_message=(
                            "Verified monthly search volume is unavailable "
                            "from the configured free sources."
                        ),
                    )
                    metrics_by_keyword[keyword] = metrics
                    self._save_to_cache(metrics)

        with self.db.connection() as conn:
            placeholders = [
                part.strip() for part in self.db._ph(6).split(",")
            ]

            for article in articles:
                keyword = (article.target_keyword or "").strip().lower()
                metrics = metrics_by_keyword.get(keyword)
                if metrics is None:
                    continue

                article.search_volume = metrics.search_volume
                article.keyword_cpc = metrics.cpc
                article.keyword_competition = metrics.competition
                article.keyword_source = metrics.source
                article.keyword_status = metrics.status.value

                self.db._exec(
                    conn,
                    "UPDATE articles SET "
                    f"search_volume = {placeholders[0]}, "
                    f"keyword_cpc = {placeholders[1]}, "
                    f"keyword_competition = {placeholders[2]}, "
                    f"keyword_source = {placeholders[3]}, "
                    f"keyword_status = {placeholders[4]} "
                    f"WHERE id = {placeholders[5]}",
                    (
                        article.search_volume,
                        article.keyword_cpc,
                        article.keyword_competition,
                        article.keyword_source,
                        article.keyword_status,
                        article.id,
                    ),
                )

        warnings = [
            "Verified monthly search volume, CPC and competition are unavailable "
            "from the free local keyword service. Keyword suggestions may still "
            "be available through Google Autocomplete."
        ]
        return articles, warnings
