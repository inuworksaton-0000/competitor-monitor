"""
Persistent storage layer for the Competitor Monitoring Alert Tool.
Uses SQLite by default for zero-config local development and automated testing,
and supports PostgreSQL (via DATABASE_URL) for durable cloud deployments.
All scan commits use atomic transactions so failed or partial crawls never corrupt state.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from config import AppConfig


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format (YYYY-MM-DDTHH:MM:SSZ)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class StoredArticle:
    id: int
    competitor_id: int
    canonical_url: str
    title: str
    published_at: Optional[str]
    meta_description: Optional[str]
    target_keyword: str
    search_volume: Optional[int]
    keyword_cpc: Optional[float]
    keyword_competition: Optional[str]
    keyword_source: Optional[str]
    keyword_status: str
    discovery_source: str
    first_seen_scan_id: int
    first_seen_at: str
    last_seen_at: str
    is_new_in_latest_scan: bool = False


@dataclass
class ScanRecord:
    id: int
    competitor_id: int
    domain: str
    started_at: str
    completed_at: Optional[str]
    status: str
    pages_fetched: int
    total_articles_found: int
    new_articles_count: int
    pricing_changes_count: int
    error_message: Optional[str]
    warnings: list[str] = field(default_factory=list)


class DatabaseManager:
    """
    Manages schema initialization and atomic CRUD operations across
    competitors, scan_runs, articles, keyword_cache, pricing_snapshots, and notifications.
    """

    def __init__(self, config: Optional[AppConfig] = None, db_path: Optional[str] = None) -> None:
        self.config = config or AppConfig.from_env()
        self.db_path = db_path or self.config.sqlite_db_path
        self.using_postgres = bool(self.config.using_postgres and not db_path)
        self.init_schema()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Yield a transactional database connection (auto-commits on exit, rolls back on exception)."""
        if self.using_postgres:
            import psycopg2
            import psycopg2.extras

            conn = psycopg2.connect(self.config.database_url)
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def _ph(self, count: int = 1) -> str:
        """Return parameter placeholders ('?' for SQLite, '%s' for Postgres)."""
        token = "%s" if self.using_postgres else "?"
        return ", ".join([token] * count)

    def _exec(self, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
        if self.using_postgres:
            import psycopg2.extras

            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            return cur
        return conn.execute(sql, params)

    def init_schema(self) -> None:
        """Create all required tables and indexes if they do not already exist."""
        id_pk = "SERIAL PRIMARY KEY" if self.using_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        with self.connection() as conn:
            self._exec(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS competitors (
                    id {id_pk},
                    domain TEXT NOT NULL UNIQUE,
                    root_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_scanned_at TEXT,
                    last_scan_status TEXT
                );
                """,
            )
            self._exec(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id {id_pk},
                    competitor_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    pages_fetched INTEGER NOT NULL DEFAULT 0,
                    total_articles_found INTEGER NOT NULL DEFAULT 0,
                    new_articles_count INTEGER NOT NULL DEFAULT 0,
                    pricing_changes_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (competitor_id) REFERENCES competitors(id) ON DELETE CASCADE
                );
                """,
            )
            self._exec(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS articles (
                    id {id_pk},
                    competitor_id INTEGER NOT NULL,
                    canonical_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT,
                    meta_description TEXT,
                    target_keyword TEXT NOT NULL DEFAULT '',
                    search_volume INTEGER,
                    keyword_cpc REAL,
                    keyword_competition TEXT,
                    keyword_source TEXT,
                    keyword_status TEXT NOT NULL DEFAULT 'PENDING',
                    discovery_source TEXT NOT NULL DEFAULT 'html',
                    first_seen_scan_id INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_scan_id INTEGER NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(competitor_id, canonical_url),
                    FOREIGN KEY (competitor_id) REFERENCES competitors(id) ON DELETE CASCADE
                );
                """,
            )
            self._exec(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS keyword_cache (
                    keyword TEXT PRIMARY KEY,
                    search_volume INTEGER,
                    cpc REAL,
                    competition TEXT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """,
            )
            self._exec(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS pricing_snapshots (
                    id {id_pk},
                    competitor_id INTEGER NOT NULL,
                    scan_id INTEGER NOT NULL,
                    page_url TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    plans_json TEXT NOT NULL,
                    raw_summary TEXT NOT NULL DEFAULT '',
                    has_changes INTEGER NOT NULL DEFAULT 0,
                    diff_summary_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (competitor_id) REFERENCES competitors(id) ON DELETE CASCADE,
                    FOREIGN KEY (scan_id) REFERENCES scan_runs(id) ON DELETE CASCADE
                );
                """,
            )
            self._exec(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS notifications (
                    id {id_pk},
                    competitor_id INTEGER NOT NULL,
                    scan_id INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_summary TEXT NOT NULL,
                    error_message TEXT,
                    sent_at TEXT NOT NULL,
                    FOREIGN KEY (competitor_id) REFERENCES competitors(id) ON DELETE CASCADE,
                    FOREIGN KEY (scan_id) REFERENCES scan_runs(id) ON DELETE CASCADE
                );
                """,
            )

    def get_or_create_competitor(self, domain: str, root_url: str) -> int:
        """Return competitor_id for domain, inserting it only if needed."""
        now = utc_now_iso()
        ph1 = self._ph(1)
        ph3 = self._ph(3)

        with self.connection() as conn:
            if self.using_postgres:
                cur = self._exec(
                    conn,
                    f"""
                    INSERT INTO competitors (domain, root_url, created_at)
                    VALUES ({ph3})
                    ON CONFLICT (domain) DO NOTHING
                    RETURNING id
                    """,
                    (domain, root_url, now),
                )
                row = cur.fetchone()
                if row:
                    return int(row["id"])
            else:
                self._exec(
                    conn,
                    f"""
                    INSERT OR IGNORE INTO competitors
                        (domain, root_url, created_at)
                    VALUES ({ph3})
                    """,
                    (domain, root_url, now),
                )

            cur = self._exec(
                conn,
                f"SELECT id FROM competitors WHERE domain = {ph1}",
                (domain,),
            )
            row = cur.fetchone()

            if row:
                return int(row["id"])

            raise RuntimeError(
                f"Could not find or create competitor for domain: {domain}"
            )

    def get_existing_article_urls(self, competitor_id: int) -> set[str]:
        """Return the set of all canonical_urls already stored for a competitor."""
        ph1 = self._ph(1)
        with self.connection() as conn:
            cur = self._exec(
                conn,
                f"SELECT canonical_url FROM articles WHERE competitor_id = {ph1}",
                (competitor_id,),
            )
            return {str(r["canonical_url"]) for r in cur.fetchall()}

    def get_competitor_articles(self, competitor_id: int, latest_scan_id: Optional[int] = None) -> list[StoredArticle]:
        """Return all stored articles for a competitor, ordered newest first."""
        ph1 = self._ph(1)
        with self.connection() as conn:
            cur = self._exec(
                conn,
                f"""
                SELECT * FROM articles
                WHERE competitor_id = {ph1}
                ORDER BY first_seen_at DESC, COALESCE(published_at, '') DESC, id DESC
                """,
                (competitor_id,),
            )
            rows = cur.fetchall()
            results: list[StoredArticle] = []
            for r in rows:
                first_scan = int(r["first_seen_scan_id"])
                results.append(
                    StoredArticle(
                        id=int(r["id"]),
                        competitor_id=int(r["competitor_id"]),
                        canonical_url=str(r["canonical_url"]),
                        title=str(r["title"]),
                        published_at=r["published_at"],
                        meta_description=r["meta_description"],
                        target_keyword=str(r["target_keyword"] or ""),
                        search_volume=r["search_volume"],
                        keyword_cpc=r["keyword_cpc"],
                        keyword_competition=r["keyword_competition"],
                        keyword_source=r["keyword_source"],
                        keyword_status=str(r["keyword_status"] or "PENDING"),
                        discovery_source=str(r["discovery_source"] or "html"),
                        first_seen_scan_id=first_scan,
                        first_seen_at=str(r["first_seen_at"]),
                        last_seen_at=str(r["last_seen_at"]),
                        is_new_in_latest_scan=(latest_scan_id is not None and first_scan == latest_scan_id),
                    )
                )
            return results

    def list_competitors(self) -> list[dict[str, Any]]:
        """Return all monitored competitors with article and scan counts."""
        with self.connection() as conn:
            cur = self._exec(
                conn,
                """
                SELECT
                    c.id,
                    c.domain,
                    c.root_url,
                    c.created_at,
                    c.last_scanned_at,
                    c.last_scan_status,
                    COUNT(DISTINCT a.id) AS total_articles,
                    COUNT(DISTINCT s.id) AS total_scans
                FROM competitors c
                LEFT JOIN articles a ON a.competitor_id = c.id
                LEFT JOIN scan_runs s ON s.competitor_id = c.id
                GROUP BY c.id, c.domain, c.root_url, c.created_at, c.last_scanned_at, c.last_scan_status
                ORDER BY COALESCE(c.last_scanned_at, c.created_at) DESC
                """,
            )
            return [dict(r) for r in cur.fetchall()]

    def list_scan_runs(self, competitor_id: Optional[int] = None, limit: int = 25) -> list[ScanRecord]:
        """Return recent scan runs (optionally filtered by competitor_id)."""
        with self.connection() as conn:
            if competitor_id is not None:
                ph2 = self._ph(2)
                cur = self._exec(
                    conn,
                    f"""
                    SELECT s.*, c.domain
                    FROM scan_runs s
                    JOIN competitors c ON c.id = s.competitor_id
                    WHERE s.competitor_id = {ph2.split(',')[0]}
                    ORDER BY s.id DESC
                    LIMIT {ph2.split(',')[1].strip()}
                    """,
                    (competitor_id, limit),
                )
            else:
                ph1 = self._ph(1)
                cur = self._exec(
                    conn,
                    f"""
                    SELECT s.*, c.domain
                    FROM scan_runs s
                    JOIN competitors c ON c.id = s.competitor_id
                    ORDER BY s.id DESC
                    LIMIT {ph1}
                    """,
                    (limit,),
                )
            records: list[ScanRecord] = []
            for r in cur.fetchall():
                try:
                    warnings = json.loads(r["warnings_json"] or "[]")
                except json.JSONDecodeError:
                    warnings = []
                records.append(
                    ScanRecord(
                        id=int(r["id"]),
                        competitor_id=int(r["competitor_id"]),
                        domain=str(r["domain"]),
                        started_at=str(r["started_at"]),
                        completed_at=r["completed_at"],
                        status=str(r["status"]),
                        pages_fetched=int(r["pages_fetched"]),
                        total_articles_found=int(r["total_articles_found"]),
                        new_articles_count=int(r["new_articles_count"]),
                        pricing_changes_count=int(r["pricing_changes_count"]),
                        error_message=r["error_message"],
                        warnings=warnings,
                    )
                )
            return records
