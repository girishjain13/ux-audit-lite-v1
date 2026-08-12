"""Detects third-party integrations and inventories JavaScript references
found while crawling. Matching happens against absolute script `src` URLs
and, for a few integrations that only show up as inline snippets (e.g. a
raw gtag() call with no separate script tag), against inline script text.

This is pattern-matching against publicly known loader URLs/globals, not
a hidden-secret scanner — an unrecognized script isn't automatically
suspicious, it's just not in this registry yet.
"""
from __future__ import annotations

from collections import Counter

KNOWN_INTEGRATIONS = [
    {"name": "Google Analytics", "category": "Analytics", "patterns": ["google-analytics.com", "googletagmanager.com/gtag", "gtag("]},
    {"name": "Google Tag Manager", "category": "Tag Manager", "patterns": ["googletagmanager.com/gtm.js"]},
    {"name": "Google Ads", "category": "Advertising", "patterns": ["googleadservices.com", "googlesyndication.com"]},
    {"name": "Meta / Facebook Pixel", "category": "Advertising", "patterns": ["connect.facebook.net", "fbevents.js", "fbq("]},
    {"name": "LinkedIn Insight Tag", "category": "Advertising", "patterns": ["snap.licdn.com"]},
    {"name": "TikTok Pixel", "category": "Advertising", "patterns": ["analytics.tiktok.com"]},
    {"name": "HubSpot", "category": "Marketing Automation", "patterns": ["hs-scripts.com", "hs-analytics.net", "hubspot"]},
    {"name": "Marketo", "category": "Marketing Automation", "patterns": ["munchkin.js", "marketo.net"]},
    {"name": "Pardot / Salesforce", "category": "Marketing Automation", "patterns": ["pi.pardot.com"]},
    {"name": "Mailchimp", "category": "Marketing Automation", "patterns": ["chimpstatic.com"]},
    {"name": "Intercom", "category": "Chat / Support", "patterns": ["widget.intercom.io", "intercomcdn.com"]},
    {"name": "Drift", "category": "Chat / Support", "patterns": ["js.driftt.com"]},
    {"name": "Zendesk", "category": "Chat / Support", "patterns": ["static.zdassets.com"]},
    {"name": "Hotjar", "category": "Analytics", "patterns": ["static.hotjar.com"]},
    {"name": "Segment", "category": "Analytics", "patterns": ["cdn.segment.com"]},
    {"name": "Mixpanel", "category": "Analytics", "patterns": ["cdn.mxpnl.com"]},
    {"name": "Optimizely", "category": "A/B Testing", "patterns": ["cdn.optimizely.com"]},
    {"name": "Stripe", "category": "Payments", "patterns": ["js.stripe.com"]},
    {"name": "PayPal", "category": "Payments", "patterns": ["paypalobjects.com"]},
    {"name": "Shopify", "category": "E-commerce Platform", "patterns": ["cdn.shopify.com"]},
    {"name": "reCAPTCHA", "category": "Security", "patterns": ["recaptcha", "gstatic.com/recaptcha"]},
    {"name": "Cloudflare Insights", "category": "Analytics", "patterns": ["static.cloudflareinsights.com"]},
    {"name": "Sentry", "category": "Error Monitoring", "patterns": ["sentry-cdn.com", "sentry.io"]},
    {"name": "New Relic", "category": "Performance Monitoring", "patterns": ["js-agent.newrelic.com"]},
    {"name": "YouTube Embed", "category": "Media", "patterns": ["youtube.com/iframe_api"]},
    {"name": "Vimeo Player", "category": "Media", "patterns": ["player.vimeo.com"]},
    {"name": "Twitter / X Widgets", "category": "Social", "patterns": ["platform.twitter.com"]},
    {"name": "jQuery", "category": "Framework / Library", "patterns": ["jquery.min.js", "jquery-", "code.jquery.com"]},
    {"name": "React", "category": "Framework / Library", "patterns": ["react.production.min.js", "react-dom"]},
    {"name": "Vue.js", "category": "Framework / Library", "patterns": ["vue.min.js", "vue.global"]},
    {"name": "Bootstrap", "category": "Framework / Library", "patterns": ["bootstrap.min.js", "bootstrap.bundle"]},
]


def match_integrations(src: str, inline_text: str = "") -> list[str]:
    haystack = f"{src} {inline_text}".lower()
    return [integ["name"] for integ in KNOWN_INTEGRATIONS if any(p in haystack for p in integ["patterns"])]


def run_integration_analysis(
    integration_hits: dict[str, set],
    unrecognized_domains: Counter,
    all_external_scripts: set,
    pages: dict,
    total_pages: int,
) -> dict:
    detected = []
    for integ in KNOWN_INTEGRATIONS:
        found_on = integration_hits.get(integ["name"])
        if found_on:
            detected.append({
                "name": integ["name"],
                "category": integ["category"],
                "pages_found_on": len(found_on),
                "pct_of_pages": round(100 * len(found_on) / max(total_pages, 1), 1),
            })
    detected.sort(key=lambda d: -d["pages_found_on"])

    other_scripts = [
        {"domain": domain, "reference_count": count}
        for domain, count in unrecognized_domains.most_common(30)
    ]

    script_counts = [rec.external_script_count for rec in pages.values() if rec.status_code and rec.status_code < 400]
    avg_scripts_per_page = round(sum(script_counts) / len(script_counts), 1) if script_counts else 0.0
    heavy_threshold = 15
    heavy_pages = [url for url, rec in pages.items() if rec.external_script_count > heavy_threshold]

    return {
        "detected": detected,
        "other_scripts": other_scripts,
        "unique_external_scripts": len(all_external_scripts),
        "categories_detected": sorted({d["category"] for d in detected}),
        "avg_scripts_per_page": avg_scripts_per_page,
        "heavy_script_pages": heavy_pages,
        "heavy_script_threshold": heavy_threshold,
    }
