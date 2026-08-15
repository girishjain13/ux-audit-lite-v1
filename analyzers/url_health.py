"""URL structure & redirect complexity — the Business Analyst's "how messy
is this site's URL layer" questions: how many hops does it take to reach
pages that redirect, are there actual loops, and does the site have
inconsistent URL forms (trailing slash, case, tracking-param bloat) that
would complicate a migration or hurt SEO via duplicate-content dilution.

Everything here is computed from what the crawl already recorded — no
extra requests, no browser needed.
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse, parse_qs

from models import PageRecord

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAM_NAMES = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}


def _is_tracking_param(name: str) -> bool:
    return name in TRACKING_PARAM_NAMES or any(name.startswith(p) for p in TRACKING_PARAM_PREFIXES)


def run_url_health_analysis(pages: dict[str, PageRecord]) -> dict:
    chains = []
    redirect_loop_count = 0
    for url, rec in pages.items():
        if rec.error == "redirect_loop":
            redirect_loop_count += 1
        if rec.redirect_chain_length:
            chains.append({
                "hops": rec.redirect_chain_length,
                "chain": rec.redirect_chain,
                "final_url": rec.url,
            })
    chains.sort(key=lambda c: c["hops"], reverse=True)
    multi_hop_chains = [c for c in chains if c["hops"] >= 2]

    # Non-canonical URL pattern detection — compare every crawled URL
    # against every other by path, looking for the same effective page
    # existing under more than one URL form. This is exactly the kind of
    # thing that silently splits SEO authority and confuses a migration's
    # URL-mapping spreadsheet if it isn't caught up front.
    by_path_lower_no_slash: dict[str, set] = defaultdict(set)
    param_bloat_urls = []
    tracking_param_urls = []

    for url, rec in pages.items():
        parsed = urlparse(url)
        normalized_key = (parsed.netloc.lower(), parsed.path.lower().rstrip("/"))
        by_path_lower_no_slash[normalized_key].add(url)

        qs = parse_qs(parsed.query)
        if len(qs) > 3:
            param_bloat_urls.append({"url": url, "param_count": len(qs)})
        tracking_found = [k for k in qs if _is_tracking_param(k)]
        if tracking_found:
            tracking_param_urls.append({"url": url, "tracking_params": tracking_found})

    trailing_slash_inconsistencies = []
    case_inconsistencies = []
    for (netloc, path_key), urls in by_path_lower_no_slash.items():
        if len(urls) < 2:
            continue
        urls_list = sorted(urls)
        paths = [urlparse(u).path for u in urls_list]
        paths_no_slash = [p.rstrip("/") for p in paths]
        # stripping trailing slashes collapsed some distinct paths together
        # -> at least two of these URLs differ only by a trailing slash
        if len(set(paths_no_slash)) < len(set(paths)):
            trailing_slash_inconsistencies.append({"pages": urls_list})
        # still differ even after removing the trailing slash, but they
        # collapsed to the same lowercased key -> a real case difference
        if len(set(paths_no_slash)) > 1:
            case_inconsistencies.append({"pages": urls_list})

    recommendations = []
    if redirect_loop_count:
        recommendations.append({
            "text": f"{redirect_loop_count} page(s) hit a redirect loop and couldn't be reached at all — these need fixing before anything else about them can be assessed.",
            "effort_bucket": "custom_dev", "personas": ["business", "ux"],
        })
    if multi_hop_chains:
        recommendations.append({
            "text": f"{len(multi_hop_chains)} page(s) are reached through a redirect chain of 2+ hops (worst case: {chains[0]['hops']} hops) — each extra hop adds latency and SEO-authority loss; point these directly at the final URL.",
            "effort_bucket": "config", "personas": ["business"],
        })
    if trailing_slash_inconsistencies:
        recommendations.append({
            "text": f"{len(trailing_slash_inconsistencies)} URL pair(s) exist in both trailing-slash and non-trailing-slash form — pick one and 301 the other, or duplicate-content dilution and a messier migration URL-map are the result.",
            "effort_bucket": "config", "personas": ["business", "content"],
        })
    if case_inconsistencies:
        recommendations.append({
            "text": f"{len(case_inconsistencies)} URL pair(s) exist in more than one letter-case form — most servers treat these as different pages even though they're meant to be the same one.",
            "effort_bucket": "config", "personas": ["business", "content"],
        })
    if tracking_param_urls:
        recommendations.append({
            "text": f"{len(tracking_param_urls)} internal link(s) carry tracking parameters (utm_*, fbclid, etc.) baked into the href — these should only ever appear on inbound campaign links, never on internal navigation, or they fragment analytics and duplicate URLs for crawlers.",
            "effort_bucket": "config", "personas": ["business", "content"],
        })

    return {
        "redirect_loop_count": redirect_loop_count,
        "pages_reached_via_redirect": len(chains),
        "multi_hop_redirect_count": len(multi_hop_chains),
        "longest_redirect_chain": chains[0]["hops"] if chains else 0,
        "example_chains": chains[:10],
        "trailing_slash_inconsistencies": trailing_slash_inconsistencies[:15],
        "case_inconsistencies": case_inconsistencies[:15],
        "param_bloat_count": len(param_bloat_urls),
        "param_bloat_examples": param_bloat_urls[:10],
        "tracking_param_count": len(tracking_param_urls),
        "tracking_param_examples": tracking_param_urls[:10],
        "recommendations": recommendations,
    }
