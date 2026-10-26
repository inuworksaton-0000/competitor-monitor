from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import requests

from config import AppConfig
from extractor import (
    extract_article_metadata,
    normalize_url,
    parse_publication_date,
)
from scraper import (
    CompetitorCrawler,
    CrawlStatus,
    SSRFViolationError,
    is_likely_article_url,
    normalize_domain_input,
)

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>How to Automate B2B Content Marketing in 2026 | AcmeAI</title>
    <link rel="canonical" href="https://acme.com/blog/automate-b2b-content-marketing/?utm_source=newsletter" />
    <meta name="description" content="Learn how modern marketing teams automate B2B content workflows and scale organic traffic." />
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": "How to Automate B2B Content Marketing in 2026",
        "datePublished": "2026-09-15T08:30:00Z",
        "description": "Learn how modern marketing teams automate B2B content workflows and scale organic traffic."
    }
    </script>
</head>
<body>
    <article>
        <h1>How to Automate B2B Content Marketing in 2026</h1>
        <h2>Building a Repeatable Content Marketing Workflow</h2>
        <p>Content marketing automation helps lean teams publish high-quality articles consistently.</p>
    </article>
</body>
</html>
"""

SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>AcmeAI Blog</title>
    <link>https://acme.com/blog</link>
    <item>
        <title>10 SaaS Pricing Strategies for High Growth</title>
        <link>https://acme.com/blog/saas-pricing-strategies?utm_medium=rss</link>
        <pubDate>Wed, 14 Oct 2026 10:00:00 GMT</pubDate>
        <description><![CDATA[<p>Discover the most effective SaaS pricing strategies for B2B startups.</p>]]></description>
    </item>
</channel>
</rss>
"""

SAMPLE_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://acme.com/pricing</loc>
        <lastmod>2026-10-01</lastmod>
    </url>
    <url>
        <loc>https://acme.com/blog/ai-seo-keyword-research</loc>
        <lastmod>2026-10-10</lastmod>
    </url>
</urlset>
"""


def _make_mock_response(url: str, status_code: int = 200, text: str = "", headers: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.url = url
    resp.status_code = status_code
    resp.is_redirect = status_code in {301, 302, 303, 307, 308}
    resp.text = text
    resp.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
    return resp


class TestExtractorAndCrawler(unittest.TestCase):
    def test_normalize_url_strips_tracking_and_trailing_slash(self) -> None:
        raw = "HTTPS://Acme.com/blog/post-slug/?utm_source=twitter&fbclid=123&page=2#section1"
        self.assertEqual(normalize_url(raw), "https://acme.com/blog/post-slug?page=2")

    def test_ssrf_blocks_localhost_and_private_ips(self) -> None:
        for bad_input in ["localhost", "http://127.0.0.1/admin", "192.168.1.1", "10.0.0.5", "ftp://acme.com"]:
            with self.assertRaises(SSRFViolationError):
                normalize_domain_input(bad_input)

    def test_extract_article_metadata_from_jsonld_and_html(self) -> None:
        article = extract_article_metadata(
            SAMPLE_ARTICLE_HTML,
            page_url="https://acme.com/blog/automate-b2b-content-marketing",
        )
        self.assertIsNotNone(article)
        assert article is not None
        self.assertEqual(article.canonical_url, "https://acme.com/blog/automate-b2b-content-marketing")
        self.assertEqual(article.title, "How to Automate B2B Content Marketing in 2026")
        self.assertEqual(article.published_at, "2026-09-15")
        self.assertIn("automate B2B content workflows", article.meta_description or "")
        self.assertIn("b2b", article.target_keyword)
        self.assertIn("content", article.target_keyword)

    def test_parse_publication_date_formats(self) -> None:
        self.assertEqual(parse_publication_date("2026-10-20T14:22:00Z"), "2026-10-20")
        self.assertEqual(parse_publication_date("October 5, 2026"), "2026-10-05")
        self.assertIsNone(parse_publication_date("not-a-date"))

    def test_is_likely_article_url_heuristics(self) -> None:
        self.assertTrue(is_likely_article_url("https://acme.com/blog/how-to-build-rag-agents", "acme.com"))
        self.assertTrue(is_likely_article_url("https://blog.acme.com/scaling-postgres-in-production", "acme.com"))
        self.assertFalse(is_likely_article_url("https://acme.com/blog", "acme.com"))
        self.assertFalse(is_likely_article_url("https://acme.com/privacy-policy", "acme.com"))
        self.assertFalse(is_likely_article_url("https://evil.com/blog/post", "acme.com"))

    def test_crawler_discovers_feed_sitemap_and_pricing(self) -> None:
        routes = {
            "https://acme.com/robots.txt": _make_mock_response(
                "https://acme.com/robots.txt",
                200,
                "User-agent: *\nAllow: /\nSitemap: https://acme.com/sitemap.xml\n",
            ),
            "https://acme.com": _make_mock_response(
                "https://acme.com",
                200,
                '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"/></head>'
                '<body><a href="/pricing">Pricing</a></body></html>',
            ),
            "https://acme.com/feed.xml": _make_mock_response("https://acme.com/feed.xml", 200, SAMPLE_RSS_XML),
            "https://acme.com/sitemap.xml": _make_mock_response("https://acme.com/sitemap.xml", 200, SAMPLE_SITEMAP_XML),
            "https://acme.com/blog/ai-seo-keyword-research": _make_mock_response(
                "https://acme.com/blog/ai-seo-keyword-research",
                200,
                SAMPLE_ARTICLE_HTML.replace(
                    "automate-b2b-content-marketing", "ai-seo-keyword-research"
                ),
            ),
            "https://acme.com/pricing": _make_mock_response(
                "https://acme.com/pricing",
                200,
                "<html><body><h1>Pricing</h1><div>Pro Plan $49/month</div></body></html>",
            ),
        }

        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.side_effect = lambda url, **kwargs: routes.get(
            url, _make_mock_response(url, 404, "Not Found")
        )

        crawler = CompetitorCrawler(
            config=AppConfig.from_env({}),
            session=mock_session,
            dns_verifier=lambda host: None,
        )
        report = crawler.crawl_domain("acme.com")

        self.assertEqual(report.status, CrawlStatus.SUCCESS)
        self.assertEqual(len(report.articles), 2)
        urls = {a.canonical_url for a in report.articles}
        self.assertIn("https://acme.com/blog/saas-pricing-strategies", urls)
        self.assertIn("https://acme.com/blog/ai-seo-keyword-research", urls)
        self.assertIn("https://acme.com/pricing", report.pricing_urls)

    def test_crawler_handles_blocked_and_no_blog_sites(self) -> None:
        blocked_session = MagicMock(spec=requests.Session)
        blocked_session.headers = {}
        blocked_session.get.return_value = _make_mock_response(
            "https://blocked.com", 403, "<html><title>Just a moment... Cloudflare</title></html>"
        )
        crawler_blocked = CompetitorCrawler(session=blocked_session, dns_verifier=lambda h: None)
        res_blocked = crawler_blocked.crawl_domain("blocked.com")
        self.assertEqual(res_blocked.status, CrawlStatus.BLOCKED_OR_FORBIDDEN)

        noblog_session = MagicMock(spec=requests.Session)
        noblog_session.headers = {}
        noblog_session.get.side_effect = lambda url, **kwargs: (
            _make_mock_response(url, 200, "<html><body><h1>Welcome to StaticCorp</h1><p>We sell widgets offline.</p></body></html>")
            if url == "https://staticcorp.com"
            else _make_mock_response(url, 404, "Not Found")
        )
        crawler_noblog = CompetitorCrawler(session=noblog_session, dns_verifier=lambda h: None)
        res_noblog = crawler_noblog.crawl_domain("staticcorp.com")
        self.assertEqual(res_noblog.status, CrawlStatus.NO_BLOG_FOUND)
        self.assertEqual(len(res_noblog.articles), 0)


if __name__ == "__main__":
    unittest.main()
