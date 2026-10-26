from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_bool(value: Optional[str], default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int, min_val: int = 1, max_val: int = 10000) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value.strip())
        return max(min_val, min(max_val, parsed))
    except ValueError:
        return default


@dataclass(frozen=True)
class AppConfig:
    sqlite_db_path: str
    database_url: str
    user_agent: str
    request_timeout_seconds: int
    max_retries: int
    max_pages_per_scan: int
    max_articles_per_scan: int
    respect_robots_txt: bool
    keyword_provider: str
    dataforseo_login: str
    dataforseo_password: str
    dataforseo_location_code: int
    dataforseo_language_code: str
    keywords_everywhere_api_key: str
    slack_webhook_url: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    alert_email_from: str
    alert_email_to: str
    log_level: str

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "AppConfig":
        source = env if env is not None else os.environ
        provider_raw = source.get("KEYWORD_PROVIDER", "dataforseo").strip().lower()
        if provider_raw not in {"dataforseo", "keywords_everywhere"}:
            provider_raw = "dataforseo"

        return cls(
            sqlite_db_path=source.get("SQLITE_DB_PATH", str(BASE_DIR / "competitor_monitor.db")).strip(),
            database_url=source.get("DATABASE_URL", "").strip(),
            user_agent=source.get(
                "CRAWLER_USER_AGENT",
                "CompetitorMonitorBot/1.0 (+https://github.com/contentninja-intern/competitor-monitor)",
            ).strip(),
            request_timeout_seconds=_parse_int(source.get("REQUEST_TIMEOUT_SECONDS"), default=12, min_val=3, max_val=60),
            max_retries=_parse_int(source.get("MAX_RETRIES"), default=2, min_val=0, max_val=5),
            max_pages_per_scan=_parse_int(source.get("MAX_PAGES_PER_SCAN"), default=25, min_val=5, max_val=100),
            max_articles_per_scan=_parse_int(source.get("MAX_ARTICLES_PER_SCAN"), default=15, min_val=1, max_val=100),
            respect_robots_txt=_parse_bool(source.get("RESPECT_ROBOTS_TXT"), default=True),
            keyword_provider=provider_raw,
            dataforseo_login=source.get("DATAFORSEO_LOGIN", "").strip(),
            dataforseo_password=source.get("DATAFORSEO_PASSWORD", "").strip(),
            dataforseo_location_code=_parse_int(source.get("DATAFORSEO_LOCATION_CODE"), default=2840, min_val=1, max_val=999999),
            dataforseo_language_code=source.get("DATAFORSEO_LANGUAGE_CODE", "en").strip() or "en",
            keywords_everywhere_api_key=source.get("KEYWORDS_EVERYWHERE_API_KEY", "").strip(),
            slack_webhook_url=source.get("SLACK_WEBHOOK_URL", "").strip(),
            smtp_host=source.get("SMTP_HOST", "").strip(),
            smtp_port=_parse_int(source.get("SMTP_PORT"), default=587, min_val=1, max_val=65535),
            smtp_username=source.get("SMTP_USERNAME", "").strip(),
            smtp_password=source.get("SMTP_PASSWORD", "").strip(),
            alert_email_from=source.get("ALERT_EMAIL_FROM", "").strip(),
            alert_email_to=source.get("ALERT_EMAIL_TO", "").strip(),
            log_level=source.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )

    @property
    def has_keyword_credentials(self) -> bool:
        if self.keyword_provider == "dataforseo":
            return bool(self.dataforseo_login and self.dataforseo_password)
        if self.keyword_provider == "keywords_everywhere":
            return bool(self.keywords_everywhere_api_key)
        return False

    @property
    def has_slack_webhook(self) -> bool:
        return self.slack_webhook_url.startswith("https://hooks.slack.com/")

    @property
    def has_smtp_config(self) -> bool:
        return bool(
            self.smtp_host
            and self.smtp_username
            and self.smtp_password
            and self.alert_email_from
            and self.alert_email_to
        )

    @property
    def using_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))


def configure_logging(level_name: str = "INFO") -> logging.Logger:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logger = logging.getLogger("competitor_monitor")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger

