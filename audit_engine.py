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

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from analyzers import accessibility, components, content, feature_matrix, freshness, ia, integrations, journey, keywords, link_health, locale, media, risk, scoring, seo, tech_fingerprint, templates, url_health, variance
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
    # pages yet still end up with almost everything showing as orphaned.
    # The clearest version of this is zero internal links extracted at all
    # (classic signature of a JS-rendered/SPA site with no server-rendered
    # <a href> navigation — this build doesn't run JS). But it can also
    # happen with a *nonzero* edge count on a real, successfully-fetched
    # site: e.g. most of what got crawled came from sitemap.xml rather than
    # from actually following links (so the graph has few real edges to
    # begin with), or the site's real navigation uses a URL form (a
    # different subdomain, a language prefix, protocol-relative links,
    # etc.) that isn't being recognized as "internal" and so isn't being
    # counted as a link at all. Whatever the exact mechanism, "the vast
    # majority of a mostly-successful crawl is orphaned" is itself a
    # specific, checkable signal worth calling out plainly rather than
    # requiring the edge count to be exactly zero to say anything.
    orphan_ratio = (ia_results["orphan_page_count"] / total_pages) if total_pages else 0.0
    if (
        crawl_warning is None
        and pages_with_real_data >= 3
        and total_pages >= 10
        and orphan_ratio >= 0.90
    ):
        if ia_results["graph_edge_count"] == 0:
            crawl_warning = (
                f"{pages_with_real_data} page(s) were fetched successfully, but zero internal links were "
                f"found in the crawled HTML — so essentially every page ({ia_results['orphan_page_count']} "
                f"of {total_pages}) shows up as orphaned. This usually means navigation is rendered "
                f"client-side by JavaScript (this build doesn't execute JS — see the project's known "
                f"limitations) rather than real &lt;a href&gt; links in the server-sent HTML. "
                f"Orphan/click-depth/internal-linking results below aren't reliable for this crawl."
            )
        else:
            crawl_warning = (
                f"{round(orphan_ratio * 100, 1)}% of pages ({ia_results['orphan_page_count']} of "
                f"{total_pages}) show up as orphaned despite {pages_with_real_data} pages fetching "
                f"successfully — {ia_results['graph_edge_count']} internal link(s) were found in total, "
                f"which isn't enough to connect a site this size. Common causes: most of what's counted "
                f"as \"crawled\" came from sitemap.xml rather than from actually following links on the "
                f"page (a real but very sparse link structure), or real internal links use a URL form — "
                f"a different subdomain, a language prefix, protocol-relative links — that this crawler "
                f"isn't recognizing as internal and so isn't counting. Worth spot-checking a few real "
                f"pages' actual HTML before trusting the orphan numbers below at face value."
            )

    content_results = content.run_content_analysis(pages)
    a11y_results = accessibility.run_accessibility_analysis(pages)
    seo_results = seo.run_seo_analysis(pages)
    url_health_results = url_health.run_url_health_analysis(pages)
    freshness_results = freshness.run_freshness_analysis(pages)
    media_results = media.run_media_analysis(
        crawler.image_domain_counts, crawler.video_embed_count,
        crawler.document_extension_counts, crawler.document_link_examples,
    )
    locale_results = locale.run_locale_analysis(pages)
    risk_results = risk.run_risk_analysis(pages, config.start_url, crawler.privacy_policy_url_found)
    tech_fingerprint_results = tech_fingerprint.run_tech_fingerprint_analysis(crawler.tech_signals, len(pages))
    ssl_result = await asyncio.to_thread(risk.check_ssl_expiry, config.start_url)
    component_results = components.run_component_analysis(crawler.component_hits, len(pages))
    score_results = scoring.run_scoring(
        ia_results, content_results, a11y_results, seo_results, len(pages),
        url_health_results, freshness_results, media_results, locale_results, risk_results,
        component_results,
    )
    keyword_results = keywords.run_keyword_analysis(
        crawler.global_word_counts, crawler.global_bigram_counts, crawler.global_doc_freq, len(pages)
    )
    integration_results = integrations.run_integration_analysis(
        crawler.integration_hits, crawler.unrecognized_script_domains, crawler.all_external_scripts, pages, len(pages)
    )
    feature_matrix_results = feature_matrix.run_feature_matrix(
        crawler.feature_hits, integration_results["detected"]
    )
    template_results = templates.run_template_analysis(pages)
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

    # A truncated crawl (the site has more pages than we were allowed to
    # visit) is a normal, expected outcome, not a problem to warn about —
    # but silently reporting on the first N pages as if that were the whole
    # site would be misleading, especially since 5000 is a hard ceiling in
    # this build (see run_audit_cli.py) regardless of what's requested.
    # There's deliberately no automatic multi-run continuation here: this
    # build's whole design avoids any mid-job git commit/push (see the
    # project history — that's what caused the original Pages-deploy-hang
    # bug), and chaining runs to cover a site in slices would need some
    # form of state carried between separate, independent workflow runs.
    # For now this is a plain, honest disclosure rather than a promise of
    # automatic continuation the pipeline doesn't actually do.
    crawl_truncated = crawler.queue_remaining_at_stop > 0
    truncation_notice = None
    if crawl_truncated:
        truncation_notice = (
            f"This crawl stopped at the {config.max_pages}-page limit with at least "
            f"{crawler.queue_remaining_at_stop} more page(s) still discovered and waiting to be crawled — "
            f"this site has more pages than fit in one run. Everything below reflects only the first "
            f"{len(pages)} pages reached, not the whole site. 5000 pages is also a hard per-run ceiling in "
            f"this build, so a single run can't cover a larger site no matter what's requested — there's no "
            f"automatic follow-up run that picks up where this one left off. To audit a specific section "
            f"of a large site instead of the whole thing, point Start URL at a subdirectory (e.g. "
            f"a specific city/category path) and run separately for each."
        )

    variance_result = variance.run_variance_analysis(config.client_stated_page_count, len(pages), crawl_truncated)

    audit_data = {
        "crawl_warning": crawl_warning,
        "truncation_notice": truncation_notice,
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
                "template_fingerprint": rec.template_fingerprint,
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
        "templates": template_results,
        "components": component_results,
        "url_health": url_health_results,
        "freshness": freshness_results,
        "media": media_results,
        "locale": locale_results,
        "risk": risk_results,
        "tech_fingerprint": tech_fingerprint_results,
        "ssl": ssl_result,
        "variance": variance_result,
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
