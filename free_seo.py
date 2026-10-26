from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse
import requests


class _SEOHTMLParser(HTMLParser):
    """Extract basic on-page SEO signals from HTML without extra dependencies."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.title_depth = 0
        self.h1_parts = []
        self.h1_depth = 0
        self.meta_description = ""
        self.canonical = ""
        self.robots = ""
        self.images = 0
        self.images_missing_alt = 0
        self.links = []
        self._link_depth = 0
        self._current_link = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "title":
            self.title_depth += 1
        elif tag == "h1":
            self.h1_depth += 1
            self.h1_parts.append([])
        elif tag == "meta":
            name = (attrs.get("name") or "").lower()
            if name == "description":
                self.meta_description = attrs.get("content", "").strip()
            elif name == "robots":
                self.robots = attrs.get("content", "").strip()
        elif tag == "link":
            if "canonical" in (attrs.get("rel") or "").lower().split():
                self.canonical = attrs.get("href", "").strip()
        elif tag == "img":
            self.images += 1
            if "alt" not in attrs or not attrs.get("alt", "").strip():
                self.images_missing_alt += 1
        elif tag == "a":
            self._link_depth += 1
            self._current_link = attrs.get("href", "").strip()
            if self._current_link:
                self.links.append(self._current_link)

    def handle_endtag(self, tag):
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        elif tag == "h1" and self.h1_depth:
            self.h1_depth -= 1
        elif tag == "a" and self._link_depth:
            self._link_depth -= 1

    def handle_data(self, data):
        if self.title_depth:
            self.title_parts.append(data.strip())
        if self.h1_depth and self.h1_parts:
            self.h1_parts[-1].append(data.strip())


def get_keyword_suggestions(keyword: str, timeout: int = 5) -> dict:
    """Return real Google Autocomplete suggestions, never fabricated volume."""
    keyword = (keyword or "").strip()
    if not keyword:
        return {
            "keyword": keyword,
            "suggestions": [],
            "search_volume": None,
            "source": "google_autocomplete",
            "status": "KEYWORD_MISSING",
        }

    try:
        response = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": keyword},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        suggestions = data[1] if isinstance(data, list) and len(data) > 1 else []
        return {
            "keyword": keyword,
            "suggestions": [
                str(item) for item in suggestions if isinstance(item, str)
            ],
            "search_volume": None,
            "source": "google_autocomplete",
            "status": "SUCCESS",
        }
    except (requests.RequestException, ValueError, TypeError):
        return {
            "keyword": keyword,
            "suggestions": [],
            "search_volume": None,
            "source": "google_autocomplete",
            "status": "UNAVAILABLE",
        }


def audit_html(html: str, page_url: str, target_keyword: str = "") -> dict:
    """Audit on-page HTML signals. Results are checks, not a ranking guarantee."""
    parser = _SEOHTMLParser()
    parser.feed(html or "")

    title = " ".join(part for part in parser.title_parts if part).strip()
    headings = [" ".join(parts).strip() for parts in parser.h1_parts]
    headings = [heading for heading in headings if heading]
    keyword = (target_keyword or "").strip().casefold()
    title_has_keyword = bool(keyword and keyword in title.casefold())
    h1_has_keyword = bool(
        keyword and any(keyword in heading.casefold() for heading in headings)
    )
    parsed_page = urlparse(page_url)
    internal_links = 0
    for href in parser.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parsed_link = urlparse(href)
        if not parsed_link.netloc or parsed_link.netloc.casefold() == parsed_page.netloc.casefold():
            internal_links += 1

    checks = []
    checks.append({
        "check": "Title tag",
        "status": "PASS" if title else "ISSUE",
        "detail": f"{len(title)} characters" if title else "Missing title tag",
    })
    checks.append({
        "check": "Meta description",
        "status": "PASS" if parser.meta_description else "ISSUE",
        "detail": (
            f"{len(parser.meta_description)} characters"
            if parser.meta_description else "Missing meta description"
        ),
    })
    checks.append({
        "check": "H1 heading",
        "status": "PASS" if headings else "ISSUE",
        "detail": f"{len(headings)} H1 heading(s)" if headings else "No H1 found",
    })
    checks.append({
        "check": "Canonical URL",
        "status": "PASS" if parser.canonical else "CHECK",
        "detail": parser.canonical or "No canonical link detected",
    })
    checks.append({
        "check": "Robots directive",
        "status": "CHECK" if "noindex" in parser.robots.casefold() else "PASS",
        "detail": parser.robots or "No meta robots directive detected",
    })
    checks.append({
        "check": "Image alt text",
        "status": "ISSUE" if parser.images_missing_alt else "PASS",
        "detail": f"{parser.images_missing_alt} of {parser.images} images missing alt text",
    })
    if keyword:
        checks.append({
            "check": "Target keyword in title",
            "status": "PASS" if title_has_keyword else "CHECK",
            "detail": "Found" if title_has_keyword else "Not found in title",
        })
        checks.append({
            "check": "Target keyword in H1",
            "status": "PASS" if h1_has_keyword else "CHECK",
            "detail": "Found" if h1_has_keyword else "Not found in H1 headings",
        })

    return {
        "url": page_url,
        "title": title,
        "meta_description": parser.meta_description,
        "h1_headings": headings,
        "canonical_url": parser.canonical,
        "robots_directive": parser.robots,
        "image_count": parser.images,
        "images_missing_alt": parser.images_missing_alt,
        "internal_link_count": internal_links,
        "target_keyword": target_keyword,
        "keyword_in_title": title_has_keyword if keyword else None,
        "keyword_in_h1": h1_has_keyword if keyword else None,
        "checks": checks,
    }
