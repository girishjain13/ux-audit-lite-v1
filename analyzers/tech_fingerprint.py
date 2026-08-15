"""Technology fingerprinting — CMS/platform, JS framework, and CDN, guessed
from generator meta tags, known asset-path patterns, and response headers
collected across the crawl (see crawler.py's _detect_tech_from_headers /
_detect_tech_from_markup). Feeds the Business Analyst's scoping questions
("what are we actually migrating off of").

This is pattern-matching against publicly-known signatures, not a
guarantee — a site can hide or lack all of these signals and still run a
given CMS, and a coincidental asset path can produce a false positive.
Report the strongest signal, not a certainty.
"""
from __future__ import annotations

from collections import Counter


def run_tech_fingerprint_analysis(tech_signals: Counter, pages_crawled: int) -> dict:
    if not tech_signals:
        return {
            "signals_found": False,
            "top_signals": [],
            "likely_cms": None,
            "likely_frameworks": [],
            "likely_cdn": [],
        }

    ranked = tech_signals.most_common(20)
    cms_signals = [(label, count) for label, count in ranked if label.startswith("CMS:")]
    framework_signals = [(label, count) for label, count in ranked if label.startswith("Framework:")]
    cdn_signals = [(label, count) for label, count in ranked if label.startswith("CDN:")]

    return {
        "signals_found": True,
        "top_signals": [{"signal": label, "page_count": count} for label, count in ranked],
        "likely_cms": cms_signals[0][0].replace("CMS: ", "") if cms_signals else None,
        "likely_cms_confidence_pct": round(100 * cms_signals[0][1] / max(pages_crawled, 1), 1) if cms_signals else 0,
        "likely_frameworks": [label.replace("Framework: ", "") for label, _ in framework_signals],
        "likely_cdn": [label.replace("CDN: ", "") for label, _ in cdn_signals],
    }
