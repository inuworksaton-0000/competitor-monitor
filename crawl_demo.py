import sys

from scraper import CompetitorCrawler

domain = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10

report = CompetitorCrawler().crawl_domain(domain)

print(f"Status: {report.status}")
print(f"Pages fetched: {report.pages_fetched}")
print(f"Articles found: {len(report.articles)}")
for attr in ("error_message", "errors", "warnings"):
    val = getattr(report, attr, None)
    if val:
        print(f"{attr}: {val}")
print()

for a in report.articles[:limit]:
    print(f"- {a.title}")
    print(f"  {a.canonical_url}")
    print(f"  date: {a.published_at}")
    desc = getattr(a, "meta_description", None) or getattr(a, "description", None) or ""
    print(f"  desc: {desc[:100]}")
    print(f"  keyword: {getattr(a, 'target_keyword', None)}\n")