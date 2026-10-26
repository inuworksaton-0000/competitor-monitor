"""
SSRF-safe, multi-strategy website crawler for the Competitor Monitoring Alert Tool.

Discovers blog and resource posts through RSS/Atom feeds, XML sitemaps, homepage
navigation and common blog path probes, without relying on any third-party
scraping API.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import defusedxml.ElementTree as SafeET
import requests
from bs4 import BeautifulSoup

from config import AppConfig
from extractor import (
    ExtractedArticle,
    extract_article_metadata,
    generate_target_keyword,
    normalize_url,
    parse_publication_date,
)

logger = logging.getLogger("competitor_monitor.scraper")


class CrawlStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_BLOG_FOUND = "NO_BLOG_FOUND"
    BLOCKED_OR_FORBIDDEN = "BLOCKED_OR_FORBIDDEN"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    JS_RENDERING_REQUIRED = "JS_RENDERING_REQUIRED"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    UNREACHABLE_OR_TIMEOUT = "UNREACHABLE_OR_TIMEOUT"


COMMON_FEED_PATHS = [
    "/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
    "/blog/feed", "/blog/rss.xml", "/blog/index.xml",
]

COMMON_BLOG_LISTING_PATHS = [
    "/blog", "/resources", "/insights", "/articles",
    "/news", "/learn", "/guides", "/posts",
]

COMMON_PRICING_PATHS = [
    "/pricing", "/plans", "/plans-and-pricing", "/subscriptions",
]

BLOG_PATH_KEYWORDS = (
    "blog", "resource", "insight", "article", "news", "learn",
    "guide", "post", "story", "stories", "case-stud", "changelog", "update",
)

BLOG_SUBDOMAIN_PREFIXES = ("blog.", "news.", "resources.", "learn.")

NON_ARTICLE_PATH_SEGMENTS = {
    "login", "signin", "signup", "register", "account", "cart", "checkout",
    "privacy", "privacy-policy", "terms", "terms-of-service", "cookie-policy",
    "security", "contact", "contact-us", "about", "about-us", "careers",
    "jobs", "pricing", "plans", "demo", "book-a-demo", "author", "authors",
    "tag", "tags", "category", "categories", "search", "cdn-cgi",
    "wp-admin", "wp-login.php", "wp-json",
}

NON_HTML_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mjs", ".json", ".xml", ".zip", ".gz", ".mp4", ".mp3",
    ".woff", ".woff2", ".ttf", ".eot",
)

BOT_BLOCK_MARKERS = (
    "cf-browser-verification", "cloudflare", "attention required!",
    "just a moment...", "verify you are human", "access denied",
    "captcha", "ddos-guard", "incapsula", "bot protection",
)

JS_SHELL_MARKERS = (
    "you need to enable javascript",
    "enable javascript to run this app",
    'id="root"',
    'id="app"',
    "__next",
)


class SSRFViolationError(ValueError):
    """Raised when a URL targets a non-public IP, localhost, or out-of-scope domain."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    text: str = ""
    content_type: str = ""
    error: Optional[str] = None
    blocked: bool = False


@dataclass
class CrawlReport:
    domain: str
    root_url: str
    status: CrawlStatus
    articles: list[ExtractedArticle] = field(default_factory=list)
    pricing_urls: list[str] = field(default_factory=list)
    pricing_pages_html: dict[str, str] = field(default_factory=dict)
    attempted_methods: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_message: Optional[str] = None
    pages_fetched: int = 0


def normalize_domain_input(raw_input: str) -> tuple[str, str]:
    """Validate user input and return (base_domain, root_url). Raises SSRFViolationError."""
    cleaned = (raw_input or "").strip()
    if not cleaned:
        raise SSRFViolationError("Domain input cannot be empty.")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = "https://" + cleaned.lstrip("/")

    parsed = urlparse(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise SSRFViolationError(f"Unsupported URL scheme '{scheme}'. Only http and https are allowed.")

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        raise SSRFViolationError("Could not extract a valid hostname from input.")

    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal", ".arpa")):
        raise SSRFViolationError(f"Blocked internal/local hostname: {hostname}")

    try:
        ip_obj = ipaddress.ip_address(hostname)
        if not ip_obj.is_global:
            raise SSRFViolationError(f"Blocked non-public IP address: {hostname}")
    except ValueError:
        if "." not in hostname or not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", hostname):
            raise SSRFViolationError(f"Invalid domain name format: '{hostname}'")

    base_domain = hostname.removeprefix("www.")
    root_url = f"{scheme}://{hostname}"
    return base_domain, root_url


def is_allowed_domain(candidate_url: str, base_domain: str) -> bool:
    """True if the URL is http(s) and belongs to base_domain or one of its subdomains."""
    parsed = urlparse(candidate_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return False
    return host == base_domain or host.endswith("." + base_domain)


def verify_public_ip(hostname: str) -> None:
    """Resolve hostname and reject any non-public address (SSRF protection)."""
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise IOError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    for entry in addr_info:
        ip_str = entry[4][0]
        ip_obj = ipaddress.ip_address(ip_str)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise SSRFViolationError(f"Hostname '{hostname}' resolved to restricted non-public IP {ip_str}.")


def is_likely_article_url(url: str, base_domain: str) -> bool:
    """Heuristic URL filter. Final article validation happens after fetching the page."""
    if not is_allowed_domain(url, base_domain):
        return False

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return False

    lower_path = path.lower()
    if any(lower_path.endswith(ext) for ext in NON_HTML_EXTENSIONS):
        return False

    segments = [seg for seg in lower_path.split("/") if seg]
    if not segments:
        return False

    if any(seg in NON_ARTICLE_PATH_SEGMENTS for seg in segments):
        return False
    if len(segments) >= 2 and segments[-2] in {"page", "tag", "category", "author", "topic"}:
        return False

    last_seg = segments[-1]

    if len(segments) >= 2 and any(any(kw in seg for kw in BLOG_PATH_KEYWORDS) for seg in segments[:-1]):
        return len(last_seg) >= 4 and not last_seg.isdigit()

    host = (parsed.hostname or "").lower()
    if host.startswith(BLOG_SUBDOMAIN_PREFIXES):
        if len(segments) >= 1 and last_seg not in BLOG_PATH_KEYWORDS and len(last_seg) >= 5:
            return True

    if re.search(r"/20[1-3]\d/(?:0[1-9]|1[0-2])/", lower_path) and len(last_seg) >= 4:
        return True

    if len(segments) == 1 and last_seg.count("-") >= 2 and len(last_seg) >= 14:
        return True

    return False


def has_blog_segment(url: str) -> bool:
    """True only when the URL itself looks blog-like (blog path or blog subdomain)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    segments = [s for s in parsed.path.lower().split("/") if s]
    if host.startswith(BLOG_SUBDOMAIN_PREFIXES):
        return True
    return len(segments) >= 2 and any(
        any(kw in seg for kw in BLOG_PATH_KEYWORDS) for seg in segments[:-1]
    )


def _detect_bot_block(status_code: int, html_text: str) -> bool:
    if status_code in {401, 403, 429, 503}:
        lower = (html_text or "")[:4000].lower()
        if status_code in {403, 429} or any(marker in lower for marker in BOT_BLOCK_MARKERS):
            return True
    return False


def _detect_js_shell(html_text: str) -> bool:
    """Detect near-empty HTML shells that need client-side rendering."""
    if not html_text:
        return False
    soup = BeautifulSoup(html_text, "lxml")
    body_text = soup.body.get_text(" ", strip=True) if soup.body else ""
    links = soup.find_all("a", href=True)
    if len(body_text) < 120 and len(links) <= 2:
        lower = html_text.lower()
        return any(marker in lower for marker in JS_SHELL_MARKERS)
    return False


def _local_name(tag: str) -> str:
    """Strip an XML namespace from a tag name and lowercase it."""
    return tag.split("}", 1)[-1].lower() if "}" in tag else tag.lower()


class CompetitorCrawler:
    """Crawls a competitor domain and returns discovered articles and pricing pages."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        session: Optional[requests.Session] = None,
        dns_verifier: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config or AppConfig.from_env()
        self.session = session or requests.Session()
        self.session.headers.clear()
        self.session.headers.update(requests.utils.default_headers())
        self.dns_verifier = dns_verifier or verify_public_ip
        self.pages_fetched = 0

    def safe_get(self, url: str, base_domain: str, max_redirects: int = 4) -> FetchResult:
        """
        Fetch a URL with SSRF protection. Every redirect hop is re-validated against
        the permitted domain and re-checked for public DNS resolution.
        """
        current_url = url
        for _ in range(max_redirects + 1):
            if not is_allowed_domain(current_url, base_domain):
                raise SSRFViolationError(
                    f"URL '{current_url}' is outside the permitted competitor domain '{base_domain}'."
                )

            parsed = urlparse(current_url)
            hostname = (parsed.hostname or "").lower()
            self.dns_verifier(hostname)

            last_exc: Optional[Exception] = None
            for attempt in range(self.config.max_retries + 1):
                try:
                    resp = self.session.get(
                        current_url,
                        timeout=(min(5, self.config.request_timeout_seconds), self.config.request_timeout_seconds),
                        allow_redirects=False,
                    )
                    self.pages_fetched += 1
                    break
                except requests.RequestException as exc:
                    last_exc = exc
                    if isinstance(exc, requests.Timeout) or attempt >= self.config.max_retries:
                        return FetchResult(
                            url=url,
                            final_url=current_url,
                            status_code=0,
                            error=str(exc),
                        )
            else:
                return FetchResult(url=url, final_url=current_url, status_code=0, error=str(last_exc))

            if resp.is_redirect or resp.status_code in {301, 302, 303, 307, 308}:
                location = resp.headers.get("Location")
                if not location:
                    break
                current_url = urljoin(current_url, location)
                continue

            content_type = resp.headers.get("Content-Type", "")
            if "charset" not in content_type.lower():
                resp.encoding = "utf-8"
            text = resp.text or ""
            blocked = _detect_bot_block(resp.status_code, text)
            return FetchResult(
                url=url,
                final_url=current_url,
                status_code=resp.status_code,
                text=text,
                content_type=content_type,
                blocked=blocked,
            )

        return FetchResult(
            url=url,
            final_url=current_url,
            status_code=0,
            error="Exceeded maximum redirect hops.",
        )

    def _get_robots_and_sitemaps(
        self, root_url: str, base_domain: str
    ) -> tuple[Optional[RobotFileParser], list[str]]:
        """Fetch robots.txt and return (parser, sitemap URLs declared in it)."""
        robots_url = urljoin(root_url, "/robots.txt")
        sitemap_urls: list[str] = []
        parser = RobotFileParser()
        try:
            res = self.safe_get(robots_url, base_domain)
        except (SSRFViolationError, IOError):
            return None, sitemap_urls

        if res.status_code == 200 and res.text:
            lines = res.text.splitlines()
            parser.parse(lines)
            for line in lines:
                match = re.match(r"^\s*Sitemap:\s*(\S+)", line, flags=re.I)
                if match:
                    sm_url = match.group(1).strip()
                    if is_allowed_domain(sm_url, base_domain):
                        sitemap_urls.append(sm_url)
            return parser, sitemap_urls
        return None, sitemap_urls

    def _can_fetch(self, parser: Optional[RobotFileParser], url: str) -> bool:
        if not self.config.respect_robots_txt or parser is None:
            return True
        try:
            return parser.can_fetch(self.config.user_agent, url)
        except Exception:
            return True

    def parse_feed_xml(self, xml_text: str, feed_url: str, base_domain: str) -> list[ExtractedArticle]:
        """Parse an RSS or Atom document into ExtractedArticle objects."""
        articles: list[ExtractedArticle] = []
        if not xml_text or not xml_text.strip():
            return articles

        try:
            root = SafeET.fromstring(xml_text.strip())
        except ET.ParseError:
            return articles

        for elem in root.iter():
            if _local_name(elem.tag) not in {"item", "entry"}:
                continue

            title = ""
            link = ""
            pub_date: Optional[str] = None
            description: Optional[str] = None

            for child in elem:
                cname = _local_name(child.tag)
                ctext = (child.text or "").strip()
                if cname == "title" and ctext and not title:
                    title = " ".join(html.unescape(ctext).split())
                elif cname == "link":
                    href = child.attrib.get("href", "").strip()
                    rel = child.attrib.get("rel", "alternate").strip().lower()
                    if href and (rel in {"alternate", ""} or not link):
                        link = href
                    elif ctext and not link:
                        link = ctext
                elif cname in {"pubdate", "published", "updated", "date"} and ctext and not pub_date:
                    pub_date = parse_publication_date(ctext)
                elif cname in {"description", "summary", "encoded", "content"} and ctext and not description:
                    raw_desc = BeautifulSoup(ctext, "lxml").get_text(" ", strip=True)
                    description = " ".join(raw_desc.split())[:280]

            if not link or not title:
                continue

            norm_link = normalize_url(link, base_url=feed_url)
            if not norm_link or not is_allowed_domain(norm_link, base_domain):
                continue

            articles.append(
                ExtractedArticle(
                    canonical_url=norm_link,
                    title=title,
                    published_at=pub_date,
                    meta_description=description,
                    target_keyword=generate_target_keyword(
                        title=title, url=norm_link, meta_description=description
                    ),
                    discovery_source="rss_feed",
                )
            )

        return articles

    def parse_sitemap_xml(
        self, xml_text: str, sitemap_url: str, base_domain: str
    ) -> tuple[list[str], list[tuple[str, Optional[str]]]]:
        """Parse a sitemap or sitemap index. Returns (child sitemaps, [(url, lastmod)])."""
        child_sitemaps: list[str] = []
        url_entries: list[tuple[str, Optional[str]]] = []
        if not xml_text or not xml_text.strip():
            return child_sitemaps, url_entries

        try:
            root = SafeET.fromstring(xml_text.strip())
        except ET.ParseError:
            return child_sitemaps, url_entries

        root_name = _local_name(root.tag)
        if root_name == "sitemapindex":
            for sm in root:
                if _local_name(sm.tag) != "sitemap":
                    continue
                for child in sm:
                    if _local_name(child.tag) == "loc" and child.text:
                        loc = normalize_url(child.text.strip(), base_url=sitemap_url)
                        if loc and is_allowed_domain(loc, base_domain):
                            child_sitemaps.append(loc)
        elif root_name == "urlset":
            for entry in root:
                if _local_name(entry.tag) != "url":
                    continue
                loc_val = ""
                lastmod_val: Optional[str] = None
                for child in entry:
                    cname = _local_name(child.tag)
                    if cname == "loc" and child.text:
                        loc_val = normalize_url(child.text.strip(), base_url=sitemap_url)
                    elif cname == "lastmod" and child.text:
                        lastmod_val = parse_publication_date(child.text.strip())
                if loc_val and is_allowed_domain(loc_val, base_domain):
                    url_entries.append((loc_val, lastmod_val))

        return child_sitemaps, url_entries

    def crawl_domain(self, domain_input: str) -> CrawlReport:
        """Run the full discovery pipeline for one competitor domain."""
        self.pages_fetched = 0
        try:
            base_domain, root_url = normalize_domain_input(domain_input)
        except SSRFViolationError as exc:
            return CrawlReport(
                domain=domain_input.strip(),
                root_url="",
                status=CrawlStatus.INVALID_DOMAIN,
                error_message=str(exc),
            )

        report = CrawlReport(domain=base_domain, root_url=root_url, status=CrawlStatus.SUCCESS)

        robots_parser, robots_sitemaps = self._get_robots_and_sitemaps(root_url, base_domain)
        if not self._can_fetch(robots_parser, root_url + "/"):
            report.status = CrawlStatus.ROBOTS_DISALLOWED
            report.error_message = f"Crawling '{base_domain}' is disallowed by its robots.txt policy."
            return report

        # Step 1: homepage
        report.attempted_methods.append("homepage_html")
        try:
            home_res = self.safe_get(root_url, base_domain)
        except (SSRFViolationError, IOError) as exc:
            report.status = CrawlStatus.UNREACHABLE_OR_TIMEOUT
            report.error_message = str(exc)
            return report

        if home_res.error or home_res.status_code == 0:
            report.status = CrawlStatus.UNREACHABLE_OR_TIMEOUT
            report.error_message = f"Failed to reach {root_url}: {home_res.error}"
            return report

        if home_res.blocked:
            report.status = CrawlStatus.BLOCKED_OR_FORBIDDEN
            report.error_message = (
                f"Access to {root_url} was blocked (HTTP {home_res.status_code} / bot protection)."
            )
            return report

        effective_root = normalize_url(home_res.final_url) or root_url
        report.root_url = effective_root
        homepage_is_js_shell = _detect_js_shell(home_res.text)

        discovered_feeds: list[str] = []
        discovered_blog_listings: list[str] = []
        discovered_pricing_urls: list[str] = []
        candidate_article_urls: dict[str, Optional[str]] = {}

        if home_res.status_code == 200 and home_res.text:
            soup = BeautifulSoup(home_res.text, "lxml")

            for link_tag in soup.find_all("link", href=True):
                ltype = str(link_tag.get("type", "")).lower()
                if "rss" in ltype or "atom" in ltype or "xml" in ltype:
                    feed_url = normalize_url(str(link_tag["href"]), base_url=effective_root)
                    if feed_url and is_allowed_domain(feed_url, base_domain) and "opensearch" not in ltype:
                        if feed_url not in discovered_feeds:
                            discovered_feeds.append(feed_url)

            for a_tag in soup.find_all("a", href=True):
                href = normalize_url(str(a_tag["href"]), base_url=effective_root)
                if not href or not is_allowed_domain(href, base_domain):
                    continue
                anchor_text = a_tag.get_text(" ", strip=True).lower()
                path_lower = urlparse(href).path.lower().rstrip("/")

                if (
                    any(path_lower == p or path_lower.startswith(p + "/") for p in COMMON_PRICING_PATHS)
                    or "pricing" in anchor_text
                ):
                    if href not in discovered_pricing_urls:
                        discovered_pricing_urls.append(href)

                is_blog_subdomain = (
                    urlparse(href).hostname != urlparse(effective_root).hostname
                    and (urlparse(href).hostname or "").startswith("blog.")
                )
                if (
                    any(path_lower == p for p in COMMON_BLOG_LISTING_PATHS)
                    or anchor_text in {"blog", "resources", "insights", "news", "articles", "learn"}
                    or is_blog_subdomain
                ):
                    if href not in discovered_blog_listings:
                        discovered_blog_listings.append(href)
                elif is_likely_article_url(href, base_domain):
                    candidate_article_urls.setdefault(href, None)

        # Step 2: RSS / Atom feeds
        report.attempted_methods.append("rss_atom_feeds")
        feed_candidates = list(discovered_feeds)
        for feed_path in COMMON_FEED_PATHS:
            candidate = normalize_url(feed_path, base_url=effective_root)
            if candidate and candidate not in feed_candidates:
                feed_candidates.append(candidate)

        articles_by_url: dict[str, ExtractedArticle] = {}
        for feed_url in feed_candidates[:10]:
            if self.pages_fetched >= self.config.max_pages_per_scan:
                break
            if not self._can_fetch(robots_parser, feed_url):
                continue
            try:
                feed_res = self.safe_get(feed_url, base_domain)
            except (SSRFViolationError, IOError):
                continue
            if feed_res.status_code == 200 and feed_res.text:
                feed_items = self.parse_feed_xml(feed_res.text, feed_res.final_url, base_domain)
                if feed_items:
                    for item in feed_items[: self.config.max_articles_per_scan]:
                        articles_by_url.setdefault(item.canonical_url, item)
                    break

        # Step 3: XML sitemaps
        report.attempted_methods.append("xml_sitemaps")
        sitemap_queue = list(robots_sitemaps)
        default_sitemap = normalize_url("/sitemap.xml", base_url=effective_root)
        if default_sitemap and default_sitemap not in sitemap_queue:
            sitemap_queue.append(default_sitemap)

        visited_sitemaps: set[str] = set()
        while (
            sitemap_queue
            and len(visited_sitemaps) < 4
            and self.pages_fetched < self.config.max_pages_per_scan
        ):
            sm_url = sitemap_queue.pop(0)
            if sm_url in visited_sitemaps or not self._can_fetch(robots_parser, sm_url):
                continue
            visited_sitemaps.add(sm_url)
            try:
                sm_res = self.safe_get(sm_url, base_domain)
            except (SSRFViolationError, IOError):
                continue
            if sm_res.status_code != 200 or not sm_res.text:
                continue

            child_sitemaps, url_entries = self.parse_sitemap_xml(sm_res.text, sm_res.final_url, base_domain)
            # Prefer child sitemaps whose names suggest editorial content.
            child_sitemaps.sort(
                key=lambda u: 0 if any(k in u.lower() for k in ("post", "blog", "article", "news", "insight")) else 1
            )
            for child_sitemap in child_sitemaps[:3]:
                if child_sitemap not in visited_sitemaps:
                    sitemap_queue.append(child_sitemap)

            for loc_url, lastmod in url_entries:
                path_low = urlparse(loc_url).path.lower().rstrip("/")
                if any(path_low == p for p in COMMON_PRICING_PATHS) and loc_url not in discovered_pricing_urls:
                    discovered_pricing_urls.append(loc_url)
                elif is_likely_article_url(loc_url, base_domain):
                    candidate_article_urls.setdefault(loc_url, lastmod)

        # Step 4: blog listing pages (only probed when nothing blog-like was found yet)
        report.attempted_methods.append("blog_listing_pages")
        listing_queue = list(discovered_blog_listings)
        has_blog_like_candidate = any(has_blog_segment(u) for u in candidate_article_urls)
        if not listing_queue and not articles_by_url and not has_blog_like_candidate:
            for blog_path in COMMON_BLOG_LISTING_PATHS[:5]:
                candidate = normalize_url(blog_path, base_url=effective_root)
                if candidate and candidate not in listing_queue:
                    listing_queue.append(candidate)

        for listing_url in listing_queue[:4]:
            if self.pages_fetched >= self.config.max_pages_per_scan:
                break
            if not self._can_fetch(robots_parser, listing_url):
                continue
            try:
                listing_res = self.safe_get(listing_url, base_domain)
            except (SSRFViolationError, IOError):
                continue
            if listing_res.status_code == 200 and listing_res.text:
                listing_soup = BeautifulSoup(listing_res.text, "lxml")
                for a_tag in listing_soup.find_all("a", href=True):
                    href = normalize_url(str(a_tag["href"]), base_url=listing_res.final_url)
                    if href and is_likely_article_url(href, base_domain):
                        candidate_article_urls.setdefault(href, None)

        # Step 5: fetch candidates, most blog-like and most recent first
        sorted_candidates = sorted(
            candidate_article_urls.items(),
            key=lambda kv: (has_blog_segment(kv[0]), kv[1] is not None, kv[1] or ""),
            reverse=True,
        )

        for art_url, lastmod_hint in sorted_candidates:
            if len(articles_by_url) >= self.config.max_articles_per_scan:
                break
            if self.pages_fetched >= self.config.max_pages_per_scan:
                break
            if art_url in articles_by_url:
                continue
            if not self._can_fetch(robots_parser, art_url):
                continue

            try:
                art_res = self.safe_get(art_url, base_domain)
            except (SSRFViolationError, IOError):
                continue

            if art_res.status_code != 200 or not art_res.text:
                continue

            extracted = extract_article_metadata(
                html_text=art_res.text,
                page_url=art_res.final_url,
                discovery_source="html",
            )
            if not extracted:
                continue
            if not has_blog_segment(art_url) and not extracted.published_at:
                continue  # not a blog-like path and no publish date, so not an article
            if not extracted.meta_description:
                continue  # category and listing pages usually have no meta description
            if not extracted.published_at and lastmod_hint:
                extracted.published_at = lastmod_hint
            articles_by_url.setdefault(extracted.canonical_url, extracted)

        # Step 6: pricing pages
        if not discovered_pricing_urls:
            for pricing_path in COMMON_PRICING_PATHS[:2]:
                candidate = normalize_url(pricing_path, base_url=effective_root)
                if candidate:
                    discovered_pricing_urls.append(candidate)

        for pricing_url in discovered_pricing_urls[:2]:
            if self.pages_fetched >= self.config.max_pages_per_scan:
                break
            if not self._can_fetch(robots_parser, pricing_url):
                continue
            try:
                pricing_res = self.safe_get(pricing_url, base_domain)
            except (SSRFViolationError, IOError):
                continue
            if pricing_res.status_code == 200 and pricing_res.text and not pricing_res.blocked:
                normalized = normalize_url(pricing_res.final_url) or pricing_url
                if normalized not in report.pricing_urls:
                    report.pricing_urls.append(normalized)
                report.pricing_pages_html[normalized] = pricing_res.text

        report.articles = list(articles_by_url.values())
        report.pages_fetched = self.pages_fetched

        if not report.articles:
            if homepage_is_js_shell:
                report.status = CrawlStatus.JS_RENDERING_REQUIRED
                report.error_message = (
                    f"{base_domain} appears to require client-side JavaScript rendering "
                    "and exposed no RSS or XML sitemap feeds."
                )
            else:
                report.status = CrawlStatus.NO_BLOG_FOUND
                report.error_message = (
                    f"No blog or resource articles were found on {base_domain} after checking "
                    f"{', '.join(report.attempted_methods)}."
                )

        return report
