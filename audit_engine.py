"""Ties the crawler and analyzers together into a single audit run.

This is the GitHub-Pages-focused build: it deliberately does not include
JS rendering (Playwright), PageSpeed performance sampling, or git-committed
run history — those specifically caused repeated GitHub Actions/Pages
deployment failures (browser install steps, slow external API calls, and a
mid-job git push all fighting with the deploy step). Everything else from
the full engine is kept: heuristics, keywords, integrations, feature
matrix, journey maps, UX Lead assessment, external link checks, Basic Auth.
The full-featured build (Streamlit/Docker) lives in the sibling project.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from analyzers import accessibility, content, feature_matrix, ia, integrations, journey, keywords, link_health, scoring, seo
from analyzers.heuristics import classify_into_heuristics, heuristic_summary
from ai_insights import generate_ai_summary, generate_target_customers
from crawler import AsyncCrawler, CrawlConfig
from models import AuditStatus, CrawlProgress
from ux_copy import build_lead_assessment, build_plain_summary


async def run_audit(
    config: CrawlConfig,
    progress: CrawlProgress,
    on_progress: Optional[Callable[[], Awaitable[None]]] = None,
    with_ai_summary: bool = True,
    custom_personas: Optional[list[dict]] = None,
) -> dict:
    progress.started_at = datetime.now(timezone.utc)
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl(on_progress=on_progress)

    start_url_resolved = crawler_start_url(config, pages)

    # A crawl that fetched nothing real (blocked by robots.txt, bot-detected,
    # the target down, etc.) still produces *some* score — the math treats
    # "zero issues found in zero real pages" the same as "found and checked
    # everything, no issues," which reads as a suspiciously perfect result
    # rather than an obvious failure. Surface that distinction explicitly
    # rather than let a failed crawl look like a clean bill of health.
    #
    # This also needs to catch the *partial*-failure case, not just total
    # failure: if robots.txt/a WAF blocks the crawler but sitemap.xml still
    # lists thousands of URLs, those URLs still get queued and counted as
    # "pages" (see crawler.py's sitemap-seeding) even though none of them
    # were ever actually fetched. A page-count-based total that only fires
    # at exactly 0% real data misses this — a site that's 99% blocked still
    # looks "mostly fine" by that math, while every other metric (orphan
    # count, keywords, status codes) quietly goes empty or nonsensical.
    total_pages = len(pages)
    pages_with_real_data = sum(1 for rec in pages.values() if rec.status_code and rec.status_code < 400)
    real_data_ratio = (pages_with_real_data / total_pages) if total_pages else 0.0
    crawl_warning = None
    if pages_with_real_data == 0:
        sample_errors = {rec.error for rec in pages.values() if rec.error}
        error_note = f" (seen: {', '.join(sorted(sample_errors))})" if sample_errors else ""
        crawl_warning = (
            f"No pages were actually fetched successfully{error_note} — the scores below reflect an "
            f"empty crawl, not a clean audit. Common causes: robots.txt disallowing this crawler, the "
            f"site blocking automated requests (bot detection), or the URL redirecting somewhere "
            f"unexpected. Check the Page Inventory tab's error column for specifics."
        )
    elif total_pages >= 10 and real_data_ratio < 0.10:
        sample_errors = {rec.error for rec in pages.values() if rec.error}
        error_note = f" (seen: {', '.join(sorted(sample_errors))})" if sample_errors else ""
        crawl_warning = (
            f"Only {pages_with_real_data} of {total_pages} pages ({round(real_data_ratio * 100, 1)}%) were "
            f"actually fetched successfully{error_note} — most of what's counted as \"crawled\" here was "
            f"seeded from sitemap.xml but never really reached (robots.txt/WAF block, bot detection, etc.), "
            f"so orphan counts, keywords, and link-graph metrics below reflect a mostly-empty crawl, not a "
            f"real picture of the site. Check the Page Inventory tab's error column for specifics."
        )

    ia_results = ia.run_ia_analysis(pages, edges, start_url_resolved)

    # Distinct from the above: a crawl can fetch plenty of real 200-status
    # pages yet still extract zero usable internal links — the classic
    # signature of a JS-rendered/SPA site with no server-rendered <a href>
    # navigation (this build has no JS rendering; see README's known
    # limitations). When that happens, every page looks orphaned and the
    # link graph is empty, which is a specific, useful thing to say
    # out loud rather than leaving the reader to guess why the numbers
    # look broken.
    if (
        crawl_warning is None
        and pages_with_real_data >= 3
        and ia_results["graph_edge_count"] == 0
        and ia_results["orphan_page_count"] >= max(3, int(0.8 * total_pages))
    ):
        crawl_warning = (
            f"{pages_with_real_data} page(s) were fetched successfully, but zero internal links were found "
            f"in the crawled HTML — so essentially every page ({ia_results['orphan_page_count']} of "
            f"{total_pages}) shows up as orphaned. This usually means navigation is rendered client-side by "
            f"JavaScript (this build doesn't execute JS — see the project's known limitations) rather than "
            f"real &lt;a href&gt; links in the server-sent HTML. Orphan/click-depth/internal-linking results "
            f"below aren't reliable for this crawl."
        )

    content_results = content.run_content_analysis(pages)
    a11y_results = accessibility.run_accessibility_analysis(pages)
    seo_results = seo.run_seo_analysis(pages)
    score_results = scoring.run_scoring(ia_results, content_results, a11y_results, seo_results, len(pages))
    keyword_results = keywords.run_keyword_analysis(
        crawler.global_word_counts, crawler.global_bigram_counts, crawler.global_doc_freq, len(pages)
    )
    integration_results = integrations.run_integration_analysis(
        crawler.integration_hits, crawler.unrecognized_script_domains, crawler.all_external_scripts, pages, len(pages)
    )
    feature_matrix_results = feature_matrix.run_feature_matrix(
        crawler.feature_hits, integration_results["detected"]
    )
    journey_map = journey.build_journey_map(pages, ia_results["click_depths"], custom_personas=custom_personas)
    heuristics_results = classify_into_heuristics(score_results["action_plan"])
    plain_summary = build_plain_summary(score_results, ia_results, content_results, a11y_results)
    lead_assessment = build_lead_assessment(
        score_results, ia_results, content_results, a11y_results, seo_results,
        integration_results, keyword_results, len(pages),
    )

    link_health_results = {"checked": 0, "broken": [], "broken_count": 0}
    if config.check_external_links and crawler.external_link_targets:
        progress.note(f"Checking {len(crawler.external_link_targets)} external link(s)…")
        link_health_results = await link_health.check_external_links(crawler.external_link_targets)

    progress.status = AuditStatus.DONE
    progress.finished_at = datetime.now(timezone.utc)

    audit_data = {
        "crawl_warning": crawl_warning,
        "meta": {
            "start_url": config.start_url,
            "pages_crawled": len(pages),
            "pages_errored": progress.pages_errored,
            "max_pages_configured": config.max_pages,
            "started_at": progress.started_at.isoformat(),
            "finished_at": progress.finished_at.isoformat(),
            "elapsed_seconds": round(progress.elapsed_seconds, 1),
        },
        "pages": {
            url: {
                "url": rec.url,
                "status_code": rec.status_code,
                "title": rec.title,
                "meta_description": rec.meta_description,
                "word_count": rec.word_count,
                "path_depth": rec.path_depth,
                "click_depth": ia_results["click_depths"].get(url),
                "is_thin_content": rec.is_thin_content,
                "is_duplicate_of": rec.is_duplicate_of,
                "images_total": rec.images_total,
                "images_missing_alt": rec.images_missing_alt,
                "has_schema_org": rec.has_schema_org,
                "canonical": rec.canonical,
                "internal_links_out_count": len(rec.internal_links_out),
                "reading_time_seconds": rec.reading_time_seconds,
                "rendered_height_estimate": rec.rendered_height_estimate,
                "script_count": rec.script_count,
                "external_script_count": rec.external_script_count,
                "error": rec.error,
            }
            for url, rec in pages.items()
        },
        "link_edges": edges,
        "ia": {k: v for k, v in ia_results.items() if k != "click_depths"},
        "content": content_results,
        "accessibility": a11y_results,
        "seo": seo_results,
        "scoring": score_results,
        "keywords": keyword_results,
        "integrations": integration_results,
        "feature_matrix": feature_matrix_results,
        "journey_map": journey_map,
        "link_health": link_health_results,
        "heuristics": heuristics_results,
        "heuristics_summary": heuristic_summary(heuristics_results),
        "plain_summary": plain_summary,
        "lead_assessment": lead_assessment,
    }

    if with_ai_summary:
        summary = await generate_ai_summary(audit_data)
        audit_data["ai_summary"] = summary
        audit_data["target_customers"] = await generate_target_customers(audit_data)

    return audit_data


def crawler_start_url(config: CrawlConfig, pages: dict) -> str:
    from crawler import normalize_url

    normalized = normalize_url(config.start_url)
    if normalized in pages:
        return normalized
    # fall back to whatever the crawler resolved the start page to (redirects)
    for url, rec in pages.items():
        if rec.redirected_from == normalized:
            return url
    return normalized
