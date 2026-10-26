"""
Article metadata extractor and candidate target keyword generator.
Extracts canonical URLs, titles, publication dates, meta descriptions, and
plausible SEO target keywords from HTML pages and RSS/Atom feed entries.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref",
    "source", "_hsenc", "_hsmi", "hsCtaTracking",
}

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "get", "getting", "got",
    "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "her",
    "here", "hers", "herself", "him", "himself", "his", "how", "however", "i",
    "if", "in", "into", "is", "isn't", "it", "its", "itself", "just", "let's",
    "make", "more", "most", "must", "my", "myself", "need", "new", "no", "nor",
    "not", "now", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
    "ours", "ourselves", "out", "over", "own", "part", "same", "she", "should",
    "shouldn't", "so", "some", "such", "than", "that", "the", "their", "theirs",
    "them", "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "top", "ultimate", "under", "until", "up", "use",
    "using", "very", "vs", "via", "want", "was", "wasn't", "way", "ways", "we",
    "were", "weren't", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "within", "without", "won't", "would", "wouldn't",
    "year", "years", "you", "your", "yours", "yourself", "yourselves", "guide",
    "complete", "best", "step", "steps", "introduction", "intro",
    "overview", "everything", "know", "learn", "blog", "post", "article",
}

ARTICLE_JSONLD_TYPES = {
    "Article", "BlogPosting", "NewsArticle", "TechArticle",
    "SocialMediaPosting", "Report", "WebPage",
}


@dataclass
class ExtractedArticle:
    canonical_url: str
    title: str
    published_at: Optional[str] = None
    meta_description: Optional[str] = None
    target_keyword: str = ""
    discovery_source: str = "html"
    headings: list[str] = field(default_factory=list)


def clean_text(value: Optional[str]) -> str:
    """Decode HTML entities (like &amp;) and collapse whitespace."""
    if not value:
        return ""
    return " ".join(html.unescape(str(value)).split())


def normalize_url(url: str, base_url: Optional[str] = None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if base_url:
        raw = urljoin(base_url, raw)

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    if scheme not in {"http", "https"}:
        return ""

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        return ""

    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    query_pairs.sort()
    query_str = urlencode(query_pairs)

    return urlunparse((scheme, netloc, path, "", query_str, ""))


def parse_publication_date(raw_date: Optional[str]) -> Optional[str]:
    if not raw_date or not isinstance(raw_date, str):
        return None
    cleaned = raw_date.strip()
    if not cleaned or len(cleaned) < 6:
        return None

    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})", cleaned)
    if iso_match:
        try:
            dt = datetime.strptime(iso_match.group(1), "%Y-%m-%d")
            if 1995 <= dt.year <= datetime.now(timezone.utc).year + 1:
                return iso_match.group(1)
        except ValueError:
            pass

    try:
        dt = date_parser.parse(cleaned, fuzzy=True)
        if 1995 <= dt.year <= datetime.now(timezone.utc).year + 1:
            return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError, TypeError):
        return None
    return None


def _iter_jsonld_objects(data: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if isinstance(data, dict):
        objects.append(data)
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                objects.extend(_iter_jsonld_objects(item))
    elif isinstance(data, list):
        for item in data:
            objects.extend(_iter_jsonld_objects(item))
    return objects


def _extract_jsonld_metadata(soup: BeautifulSoup) -> dict[str, Optional[str]]:
    result: dict[str, Optional[str]] = {
        "title": None,
        "published_at": None,
        "description": None,
        "url": None,
    }
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw_text = script.string or script.get_text()
        if not raw_text:
            continue
        try:
            payload = json.loads(raw_text.strip())
        except json.JSONDecodeError:
            continue

        for obj in _iter_jsonld_objects(payload):
            obj_type = obj.get("@type")
            types = [obj_type] if isinstance(obj_type, str) else (obj_type if isinstance(obj_type, list) else [])
            if not any(t in ARTICLE_JSONLD_TYPES for t in types) and "headline" not in obj:
                continue

            if not result["title"]:
                headline = obj.get("headline") or obj.get("name")
                if isinstance(headline, str) and headline.strip():
                    result["title"] = clean_text(headline)

            if not result["published_at"]:
                raw_pub = obj.get("datePublished") or obj.get("dateCreated") or obj.get("dateModified")
                if isinstance(raw_pub, str):
                    result["published_at"] = parse_publication_date(raw_pub)

            if not result["description"]:
                desc = obj.get("description")
                if isinstance(desc, str) and desc.strip():
                    result["description"] = clean_text(desc)

            if not result["url"]:
                main_entity = obj.get("mainEntityOfPage")
                if isinstance(main_entity, dict) and isinstance(main_entity.get("@id"), str):
                    result["url"] = main_entity["@id"].strip()
                elif isinstance(main_entity, str):
                    result["url"] = main_entity.strip()
                elif isinstance(obj.get("url"), str):
                    result["url"] = obj["url"].strip()

    return result


def generate_target_keyword(
    title: str,
    url: str = "",
    headings: Optional[list[str]] = None,
    meta_description: Optional[str] = None,
) -> str:
    headings = headings or []
    cleaned_title = re.split(r"\s+(?:\||—|–|-|:)\s+(?=[A-Z0-9][\w\s]{1,25}$)", title or "")[0].strip()
    if not cleaned_title:
        cleaned_title = (title or "").strip()

    def _tokenize(text: str) -> list[str]:
        text_lower = text.lower()
        text_lower = re.sub(r"\b20[1-3]\d\b", " ", text_lower)
        tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text_lower)
        filtered: list[str] = []
        for tok in tokens:
            word = tok.replace("-", " ").strip()
            for sub in word.split():
                if len(sub) >= 2 and not sub.isdigit() and sub not in STOPWORDS:
                    filtered.append(sub)
        return filtered

    title_tokens = _tokenize(cleaned_title)

    parsed_path = urlparse(url).path.strip("/")
    last_segment = parsed_path.split("/")[-1] if parsed_path else ""
    last_segment = re.sub(r"\.(html?|php|aspx?)$", "", last_segment, flags=re.I)
    slug_tokens = _tokenize(last_segment.replace("-", " ").replace("_", " "))

    heading_tokens: list[str] = []
    for h in headings[:3]:
        heading_tokens.extend(_tokenize(h))

    reinforced = [t for t in title_tokens if t in set(slug_tokens + heading_tokens)]
    chosen: list[str] = []
    for tok in reinforced + title_tokens + slug_tokens:
        if tok not in chosen:
            chosen.append(tok)
        if len(chosen) >= 3:
            break

    if not chosen and meta_description:
        for tok in _tokenize(meta_description):
            if tok not in chosen:
                chosen.append(tok)
            if len(chosen) >= 3:
                break

    if not chosen:
        return "competitor blog article"

    return " ".join(chosen[:4])


def extract_article_metadata(
    html_text: str,
    page_url: str,
    discovery_source: str = "html",
) -> Optional[ExtractedArticle]:
    if not html_text or not html_text.strip():
        return None

    soup = BeautifulSoup(html_text, "lxml")
    jsonld = _extract_jsonld_metadata(soup)

    canonical_href: Optional[str] = None
    link_canon = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    if link_canon and link_canon.get("href"):
        canonical_href = str(link_canon["href"]).strip()
    if not canonical_href:
        og_url = soup.find("meta", attrs={"property": "og:url"})
        if og_url and og_url.get("content"):
            canonical_href = str(og_url["content"]).strip()
    if not canonical_href and jsonld.get("url"):
        canonical_href = jsonld["url"]

    canonical_url = normalize_url(canonical_href or page_url, base_url=page_url)
    if not canonical_url:
        canonical_url = normalize_url(page_url)

    page_host = (urlparse(page_url).hostname or "").lower().removeprefix("www.")
    canon_host = (urlparse(canonical_url).hostname or "").lower().removeprefix("www.")
    if page_host and canon_host and not (canon_host == page_host or canon_host.endswith("." + page_host)):
        canonical_url = normalize_url(page_url)

    h1_tag = soup.find("h1")
    h1_text = clean_text(h1_tag.get_text(" ", strip=True)) if h1_tag else ""

    og_title_tag = soup.find("meta", attrs={"property": "og:title"}) or soup.find(
        "meta", attrs={"name": "twitter:title"}
    )
    og_title = clean_text(og_title_tag["content"]) if (og_title_tag and og_title_tag.get("content")) else ""

    doc_title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""

    title = jsonld.get("title") or h1_text or og_title or doc_title
    if not title:
        return None
    title = clean_text(title)

    published_at = jsonld.get("published_at")
    if not published_at:
        meta_date_selectors = [
            {"property": "article:published_time"},
            {"property": "og:published_time"},
            {"name": "pubdate"},
            {"name": "publishdate"},
            {"name": "date"},
            {"name": "DC.date.issued"},
            {"itemprop": "datePublished"},
        ]
        for attrs in meta_date_selectors:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                published_at = parse_publication_date(str(tag["content"]))
                if published_at:
                    break

    if not published_at:
        for time_tag in soup.find_all("time"):
            candidate = time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
            published_at = parse_publication_date(str(candidate))
            if published_at:
                break

    meta_description = jsonld.get("description")
    if not meta_description:
        for attrs in [
            {"name": "description"},
            {"property": "og:description"},
            {"name": "twitter:description"},
        ]:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                desc_candidate = clean_text(tag["content"])
                if desc_candidate:
                    meta_description = desc_candidate
                    break

    if not meta_description:
        container = soup.find("article") or soup.find("main") or soup.body
        if container:
            for p in container.find_all("p", limit=6):
                txt = clean_text(p.get_text(" ", strip=True))
                if len(txt) >= 60:
                    meta_description = txt[:280]
                    break

    if meta_description:
        meta_description = clean_text(meta_description)

    headings: list[str] = []
    if h1_text:
        headings.append(h1_text)
    for h2 in soup.find_all("h2", limit=5):
        h2_txt = clean_text(h2.get_text(" ", strip=True))
        if h2_txt:
            headings.append(h2_txt)

    target_keyword = generate_target_keyword(
        title=title,
        url=canonical_url,
        headings=headings,
        meta_description=meta_description,
    )

    return ExtractedArticle(
        canonical_url=canonical_url,
        title=title,
        published_at=published_at,
        meta_description=meta_description,
        target_keyword=target_keyword,
        discovery_source=discovery_source,
        headings=headings,
    )