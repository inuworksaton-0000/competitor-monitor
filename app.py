from __future__ import annotations

from free_seo import audit_html, get_keyword_suggestions
import streamlit as st
from ninja_loader import ninja_loading

from change_detector import ChangeDetector
from config import AppConfig
from database import DatabaseManager
from keyword_service import KeywordEnrichmentService
from notifier import send_slack_alert
from pricing_monitor import PricingMonitor
from scraper import CompetitorCrawler, CrawlStatus

st.set_page_config(
    page_title="ContentNinja | Competitor Monitor",
    page_icon="🥷",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @keyframes fadeSlideUp {
        0% { opacity: 0; transform: translateY(14px); }
        100% { opacity: 1; transform: translateY(0); }
    }
    @keyframes pulseGlow {
        0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.55); }
        70% { box-shadow: 0 0 0 9px rgba(16, 185, 129, 0); }
        100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
    }
    @keyframes gradientShift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    .hero-banner {
        background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 45%, #312e81 100%);
        background-size: 200% 200%;
        animation: gradientShift 10s ease infinite, fadeSlideUp 0.55s ease-out;
        border: 1px solid rgba(99, 102, 241, 0.35);
        border-radius: 16px;
        padding: 24px 28px;
        margin-bottom: 22px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.28);
    }
    .hero-left {
        display: flex;
        align-items: center;
        gap: 18px;
    }
    .ninja-logo {
        width: 58px;
        height: 58px;
        border-radius: 14px;
        background: linear-gradient(135deg, #6366f1 0%, #06b6d4 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 30px;
        box-shadow: 0 6px 18px rgba(99, 102, 241, 0.45);
    }
    .hero-title {
        font-size: 1.75rem;
        font-weight: 800;
        color: #f8fafc;
        margin: 0;
        letter-spacing: -0.02em;
    }
    .hero-sub {
        color: #94a3b8;
        font-size: 0.95rem;
        margin-top: 4px;
    }
    .live-badge {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: rgba(16, 185, 129, 0.14);
        border: 1px solid rgba(16, 185, 129, 0.4);
        color: #34d399;
        padding: 6px 14px;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 600;
    }
    .pulse-dot {
        width: 9px;
        height: 9px;
        background-color: #10b981;
        border-radius: 50%;
        display: inline-block;
        animation: pulseGlow 2s infinite;
    }
    .kpi-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 14px;
        margin: 16px 0 22px 0;
        animation: fadeSlideUp 0.65s ease-out;
    }
    .kpi-card {
        background: linear-gradient(180deg, rgba(30, 41, 59, 0.85) 0%, rgba(15, 23, 42, 0.95) 100%);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 14px;
        padding: 16px 18px;
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .kpi-card:hover {
        transform: translateY(-3px);
        border-color: rgba(99, 102, 241, 0.6);
    }
    .kpi-label {
        color: #94a3b8;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-weight: 600;
    }
    .kpi-value {
        color: #f8fafc;
        font-size: 1.65rem;
        font-weight: 800;
        margin-top: 6px;
    }
    .kpi-sub {
        color: #64748b;
        font-size: 0.78rem;
        margin-top: 2px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_services():
    config = AppConfig.from_env()
    db = DatabaseManager(config=config)
    return (
        config,
        db,
        ChangeDetector(db=db),
        KeywordEnrichmentService(db=db, config=config),
        PricingMonitor(db),
    )


config, db, detector, kw_service, pricing = get_services()


def article_rows(articles):
    rows = []
    for a in articles:
        vol = getattr(a, "search_volume", None)
        rows.append(
            {
                "Title": getattr(a, "title", ""),
                "URL": getattr(a, "canonical_url", ""),
                "Published": getattr(a, "published_at", None) or "N/A",
                "Meta description": getattr(a, "meta_description", None) or "",
                "Target Keyword": getattr(a, "target_keyword", ""),
                "Search Volume": f"{vol:,}/mo" if isinstance(vol, int) else "N/A",
                "Keyword Status": getattr(a, "keyword_status", "") or "NO_DATA_RETURNED",
                "Keyword Source": getattr(a, "keyword_source", None) or "free_local",
            }
        )
    return rows


def show_table(articles):
    st.dataframe(
        article_rows(articles),
        use_container_width=True,
        hide_index=True,
        column_config={"URL": st.column_config.LinkColumn("Article URL")},
    )


st.markdown(
    """
    <div class="hero-banner">
        <div class="hero-left">
            <div class="ninja-logo">🥷</div>
            <div>
                <h1 class="hero-title">ContentNinja • Competitor Intelligence Radar</h1>
                <div class="hero-sub">Automated Blog Discovery • Canonical Change Detection • Pricing Diffs • Free On-Page SEO & Keyword Ideas</div>
            </div>
        </div>
        <div class="live-badge">
            <span class="pulse-dot"></span> SSRF-Safe Crawler Ready
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### ⚙️ Radar Controls")
    send_alert = st.checkbox("🔔 Send Slack alert on new changes", value=True)

    st.markdown("---")
    st.markdown("### 🔑 Integration Status")
    st.caption(
        f"**Slack Webhook:** {'🟢 Configured' if config.has_slack_webhook else '⚪ Not set in .env'}\n\n"
        "**SEO Mode:** 🟢 Free Local Audit + Google Autocomplete Ideas (₹0 budget; verified volume/CPC shown as N/A)"
    )

    st.markdown("---")
    st.markdown("### 🗂️ Monitored Competitors")
    try:
        tracked = db.list_competitors()
    except Exception:
        tracked = []

    if not tracked:
        st.caption("No domains scanned yet. Run your first scan!")
    else:
        for comp in tracked[:10]:
            st.markdown(
                f"**`{comp['domain']}`** — {comp.get('total_articles', 0)} posts "
                f"*(Last: `{comp.get('last_scan_status', 'N/A')}`)*"
            )

col_input, col_btn = st.columns([4, 1])
with col_input:
    domain = st.text_input(
        "Enter Competitor Domain",
        placeholder="e.g., basecamp.com, naukri.com, realpython.com, python.org",
        label_visibility="collapsed",
    )
with col_btn:
    run_clicked = st.button("🚀 Scan Competitor", type="primary", use_container_width=True)

st.caption("💡 **Quick Test Ideas:** Try `basecamp.com` (has both Blog + `/pricing` tiers), `naukri.com`, or `realpython.com`.")

if run_clicked and domain.strip():
    with st.spinner(f"🔍 Crawling {domain.strip()}..."):
        _slot = st.empty()
        ninja_loading(_slot, domain.strip())
        result = detector.run_scan(domain.strip())
        _slot.empty()

    if result.status != CrawlStatus.SUCCESS:
        st.session_state.pop("last_scan_bundle", None)
        st.error(f"**{result.status.value}**: {result.error_message or 'Scan did not complete.'}")
        st.info("🛡️ Safe Persistence Guard: Earlier saved data for this domain remains untouched.")
        st.stop()

    kw_warnings = []
    if result.new_articles:
        with st.spinner("📈 Running free keyword enrichment..."):
            _, kw_warnings = kw_service.enrich_articles(result.new_articles)

    pricing_results = []
    report = result.crawl_report
    if report is not None and report.pricing_pages_html:
        pricing_results = pricing.process_pricing_pages(
            result.competitor_id,
            scan_id=result.scan_id,
            pricing_pages_html=report.pricing_pages_html,
        )

    alert_info = None
    if send_alert:
        webhook = config.slack_webhook_url
        alert_info = send_slack_alert(
            result.domain,
            result.new_articles,
            [p for p in pricing_results if p.has_changes],
            webhook_url=webhook or None,
        )

    st.session_state["last_scan_bundle"] = {
        "result": result,
        "kw_warnings": kw_warnings,
        "pricing_results": pricing_results,
        "alert_info": alert_info,
        "audited_article_html": {},
    }

bundle = st.session_state.get("last_scan_bundle")
if bundle is not None:
    result = bundle["result"]
    kw_warnings = bundle["kw_warnings"]
    pricing_results = bundle["pricing_results"]
    alert_info = bundle["alert_info"]

    total_plans_found = sum(len(pr.plans) for pr in pricing_results)
    pricing_changes_detected = sum(len(pr.changes) for pr in pricing_results if pr.has_changes)

    st.markdown(
        f"""
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Scan Status</div>
                <div class="kpi-value" style="color: #34d399;">{result.status.value}</div>
                <div class="kpi-sub">{result.pages_fetched} pages inspected</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Newly Discovered Posts</div>
                <div class="kpi-value" style="color: #60a5fa;">{len(result.new_articles)}</div>
                <div class="kpi-sub">{'Initial baseline scan' if result.is_initial_baseline else 'Since previous scan'}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Previously Tracked Posts</div>
                <div class="kpi-value">{len(result.existing_articles)}</div>
                <div class="kpi-sub">Deduplicated in SQLite</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Pricing Monitor</div>
                <div class="kpi-value" style="color: {'#f87171' if pricing_changes_detected else '#a78bfa'};">
                    {f'{pricing_changes_detected} Changed' if pricing_changes_detected else f'{total_plans_found} Plans'}
                </div>
                <div class="kpi-sub">{len(pricing_results)} pricing page(s) checked</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if result.is_initial_baseline:
        st.info("✨ **First scan for this domain:** Every post found is saved as the baseline. Click **Scan Competitor** a second time right now to verify that **0 duplicate new posts** are reported!")

    for w in result.warnings + kw_warnings:
        st.warning(w)

    if alert_info is not None:
        if alert_info.sent:
            st.success("✅ **Slack Alert Sent:** Summary delivered to your configured Slack channel.")
        else:
            st.caption(f"ℹ️ Slack notification status: `{alert_info.status}` ({alert_info.message})")

    tab_new, tab_pricing, tab_existing, tab_seo = st.tabs(
        [
            f"🆕 Newly Discovered Posts ({len(result.new_articles)})",
            f"💰 Pricing Snapshot & Diffs ({total_plans_found} plans)",
            f"📚 Previously Known Posts ({len(result.existing_articles)})",
            "🔎 SEO Audit & Keyword Ideas",
        ]
    )

    with tab_new:
        if result.new_articles:
            show_table(result.new_articles)
        else:
            st.success("🎉 **Zero newly discovered posts since the last scan!** Change-detection idempotency verified.")

    with tab_pricing:
        if not pricing_results:
            st.info(
                f"No public `/pricing` or `/plans` page was found on `{result.domain}` "
                "(try scanning a SaaS domain like `basecamp.com` to see live plan & price extraction)."
            )
        for pr in pricing_results:
            st.markdown(f"#### 🔗 Pricing Page: [{pr.page_url}]({pr.page_url})")
            if pr.extraction_uncertain:
                st.warning(
                    "Could not extract structured price tokens from this page (it may use client-side JS or custom enterprise quotes). "
                    "Any previously saved snapshot remains untouched."
                )
            elif pr.is_initial_snapshot:
                st.success(f"📸 Baseline pricing snapshot saved ({len(pr.plans)} plans detected).")
            elif pr.has_changes:
                st.error("🚨 **Pricing Changes Detected Since Last Scan!**")
                for c in pr.changes:
                    st.markdown(f"- **{c}**")
            else:
                st.info("✅ No pricing changes since the last scan.")

            if pr.plans:
                st.dataframe(
                    [{"Plan / Tier": p.get("name", ""), "Extracted Price": p.get("price", "")} for p in pr.plans],
                    use_container_width=True,
                    hide_index=True,
                )

    with tab_existing:
        if result.existing_articles:
            show_table(result.existing_articles)
        else:
            st.caption("No previously known posts before this scan. Run a second scan on this domain to see these posts move here!")

    with tab_seo:
        st.subheader("🔎 Free On-Page SEO Audit")
        st.caption(
            "Checks actual HTML signals (Title, Meta Description, H1, Canonical URL, Robots tag, Image Alt attributes, Internal Links, and Keyword placement). "
            "This is not a Google ranking score or a substitute for verified search-volume data."
        )

        crawl_report = result.crawl_report
        pricing_html_map = (getattr(crawl_report, "pricing_pages_html", None) or {}) if crawl_report else {}
        cached_article_html = bundle.get("audited_article_html", {})

        all_articles = list(result.new_articles) + list(result.existing_articles)
        candidate_options: list[tuple[str, str, str]] = []
        for p_url in pricing_html_map:
            candidate_options.append((f"[Pricing Page HTML] {p_url}", p_url, "pricing"))
        for art in all_articles[:10]:
            art_url = getattr(art, "canonical_url", "")
            if art_url:
                candidate_options.append((f"[Article Page] {art.title[:55]} ({art_url})", art_url, "article"))

        if not candidate_options:
            st.info("No crawled pages are available for this audit yet.")
        else:
            labels = [opt[0] for opt in candidate_options]
            selected_idx = st.selectbox(
                "Choose a crawled page or discovered article to audit",
                range(len(labels)),
                format_func=lambda idx: labels[idx],
                key="seo_audit_page",
            )
            _, selected_url, page_kind = candidate_options[selected_idx]

            default_kw = ""
            for art in all_articles:
                if getattr(art, "canonical_url", "") == selected_url:
                    default_kw = getattr(art, "target_keyword", "")
                    break

            target_keyword = st.text_input(
                "Target keyword (optional)",
                value=default_kw,
                placeholder="e.g. saas analytics tool",
                key="seo_target_keyword",
            )

            html_to_audit = ""
            if page_kind == "pricing":
                html_to_audit = pricing_html_map.get(selected_url, "")
            else:
                html_to_audit = cached_article_html.get(selected_url, "")
                if not html_to_audit:
                    if st.button("📥 Fetch & Audit Selected Article HTML", key="fetch_article_audit_btn"):
                        with st.spinner(f"Fetching {selected_url}..."):
                            crawler = CompetitorCrawler(config=config)
                            fetch_res = crawler.safe_get(selected_url, result.domain)
                            if fetch_res.status_code == 200 and fetch_res.text:
                                cached_article_html[selected_url] = fetch_res.text
                                html_to_audit = fetch_res.text
                            else:
                                st.warning(
                                    f"Could not fetch HTML for `{selected_url}` "
                                    f"(HTTP {fetch_res.status_code}: {fetch_res.error or 'empty response'})."
                                )
                    else:
                        st.caption("Click **Fetch & Audit Selected Article HTML** above to inspect this article page's HTML signals.")

            if html_to_audit:
                seo_report = audit_html(html_to_audit, selected_url, target_keyword)
                st.caption(f"Audited page type: **{page_kind.upper()}** — `{selected_url}`")
                st.markdown(f"**Page title:** {seo_report['title'] or 'N/A'}")
                st.markdown("**Meta description:** " + (seo_report["meta_description"] or "N/A"))

                c1, c2 = st.columns(2)
                c1.metric("Images missing alt text", seo_report["images_missing_alt"])
                c2.metric("Internal links detected", seo_report["internal_link_count"])

                st.dataframe(
                    seo_report["checks"],
                    use_container_width=True,
                    hide_index=True,
                )

        st.divider()
        st.subheader("Google Autocomplete Keyword Ideas")
        st.caption(
            "Suggestions are not monthly search-volume figures. "
            "Volume, CPC and competition remain N/A without a verified data source."
        )

        article_keywords = sorted(
            {
                getattr(a, "target_keyword", "").strip()
                for a in all_articles
                if getattr(a, "target_keyword", "").strip()
            }
        )
        default_seed = article_keywords[0] if article_keywords else ""

        keyword_query = st.text_input(
            "Enter a seed keyword",
            value=default_seed,
            placeholder="e.g. saas analytics tool",
            key="autocomplete_keyword",
        )

        if st.button("Find keyword ideas", key="find_keyword_ideas"):
            if not keyword_query.strip():
                st.warning("Enter a keyword first.")
            else:
                with st.spinner("Fetching Google Autocomplete suggestions..."):
                    suggestions_result = get_keyword_suggestions(keyword_query)

                if suggestions_result["status"] == "SUCCESS":
                    ideas = suggestions_result["suggestions"]
                    if ideas:
                        st.dataframe(
                            [
                                {
                                    "Keyword idea": idea,
                                    "Monthly search volume": "N/A",
                                    "CPC": "N/A",
                                    "Competition": "N/A",
                                }
                                for idea in ideas
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.info("Google returned no suggestions for this query.")
                else:
                    st.warning("Google Autocomplete is currently unavailable. Try again later.")