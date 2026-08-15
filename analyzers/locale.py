"""Locale/language coverage — for multi-region sites, which locales this
site actually declares (via hreflang and each page's own lang attribute),
how many pages exist in each, and where an hreflang alternate points
somewhere that was never actually reached in the crawl (a broken locale
link, or a locale variant this crawl's scope didn't cover).
"""
from __future__ import annotations

from collections import Counter

from models import PageRecord


def run_locale_analysis(pages: dict[str, PageRecord]) -> dict:
    real_pages = {u: r for u, r in pages.items() if r.status_code and r.status_code < 400}

    lang_counts: Counter = Counter(r.lang for r in real_pages.values() if r.lang)
    pages_without_lang = sum(1 for r in real_pages.values() if not r.lang)

    hreflang_locales: Counter = Counter()
    broken_hreflang = []
    pages_with_hreflang = 0
    for url, rec in real_pages.items():
        if not rec.hreflang_links:
            continue
        pages_with_hreflang += 1
        for locale, target_url in rec.hreflang_links.items():
            hreflang_locales[locale] += 1
            if target_url not in pages:
                broken_hreflang.append({"from_page": url, "locale": locale, "target_url": target_url})

    is_multilingual = len(lang_counts) > 1 or len(hreflang_locales) > 1

    recommendations = []
    if broken_hreflang:
        recommendations.append({
            "text": f"{len(broken_hreflang)} hreflang alternate link(s) point to a URL that was never reached in this crawl — either a broken locale link, or a locale variant outside this crawl's scope (different subdomain/domain).",
            "effort_bucket": "config", "personas": ["content", "business"],
        })
    if is_multilingual and pages_without_lang and pages_without_lang / max(len(real_pages), 1) > 0.1:
        recommendations.append({
            "text": f"This site has multiple locales, but {pages_without_lang} page(s) have no lang attribute at all — screen readers and search engines can't tell what language they're in.",
            "effort_bucket": "config", "personas": ["ux", "content"],
        })

    return {
        "is_multilingual": is_multilingual,
        "lang_attribute_counts": dict(lang_counts),
        "pages_without_lang": pages_without_lang,
        "hreflang_locale_counts": dict(hreflang_locales),
        "pages_with_hreflang": pages_with_hreflang,
        "broken_hreflang_count": len(broken_hreflang),
        "broken_hreflang_examples": broken_hreflang[:15],
        "recommendations": recommendations,
    }
