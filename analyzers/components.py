"""Summarizes the recurring UI components (cards, nav bars, CTAs, forms,
tables, accordions, etc.) found across the crawl — see crawler.py's
_extract_components for how each one gets identified and counted.

Distinct from analyzers/templates.py: a template is a whole page's shape;
a component is a smaller, reusable piece that can show up across many
different templates (the same "card" component might appear inside a
product-listing template and a search-results template). Together they
give a rough two-level picture of a site's actual design system, built
purely from what's really in the crawled HTML rather than any design-file
or component-library source of truth.
"""
from __future__ import annotations

from collections import defaultdict

# Tags meaningful enough to have an actual visual "style" worth being
# consistent about — not every div wrapper. A button in particular is the
# classic design-system-drift tell: 3+ genuinely different button classes
# across a site is rarely 3+ deliberate variants.
STYLE_SENSITIVE_TAGS = {"button", "nav", "form", "table"}
INCONSISTENCY_THRESHOLD = 3


def run_component_analysis(component_hits: dict, total_pages: int) -> dict:
    components = []
    for sig, urls in component_hits.items():
        if not urls:
            continue
        tag, _, class_part = sig.partition(".")
        components.append({
            "signature": sig,
            "tag": tag,
            "classes": class_part,
            "page_count": len(urls),
            "page_coverage_pct": round(len(urls) / total_pages * 100, 1) if total_pages else 0,
            "example_url": sorted(urls)[0],
        })
    components.sort(key=lambda c: c["page_count"], reverse=True)

    # A component used on only 1 page isn't really "reusable" — it's just
    # a classed element that happened to get picked up once. Reserve the
    # "component" label for things that actually repeat across pages, the
    # same way analyzers/templates.py only calls something a template once
    # more than one page shares it (its one-off list is the complement of
    # that, kept separate rather than folded in here).
    reusable = [c for c in components if c["page_count"] >= 2]

    # Style-inconsistency signal: several genuinely different class
    # signatures for the same kind of element usually means near-duplicate
    # components exist where one would do, rather than deliberate variants
    # — e.g. "primary" and "secondary" button styles are normal (2), but 4
    # or 5 different button classes across a site is a design-system-drift
    # smell, not 4 or 5 intentional choices.
    by_tag = defaultdict(list)
    for c in reusable:
        if c["tag"] in STYLE_SENSITIVE_TAGS and c["classes"]:
            by_tag[c["tag"]].append(c)

    style_inconsistencies = []
    for tag, variants in by_tag.items():
        if len(variants) < INCONSISTENCY_THRESHOLD:
            continue
        pages_covered = set()
        for v in variants:
            pages_covered |= component_hits.get(v["signature"], set())
        style_inconsistencies.append({
            "tag": tag,
            "distinct_style_count": len(variants),
            "signatures": [v["signature"] for v in variants],
            "total_pages_covered": len(pages_covered),
        })
    style_inconsistencies.sort(key=lambda i: i["distinct_style_count"], reverse=True)

    recommendations = [
        {
            "text": f"{inc['distinct_style_count']} visually distinct <{inc['tag']}> styles detected across {inc['total_pages_covered']} page(s) — usually design-system drift rather than {inc['distinct_style_count']} deliberate variants worth keeping.",
            "effort_bucket": "custom_dev", "personas": ["ux", "business"],
        }
        for inc in style_inconsistencies
    ]

    return {
        "unique_component_count": len(reusable),
        "components": reusable[:40],
        "pages_analyzed": total_pages,
        "style_inconsistencies": style_inconsistencies,
        "recommendations": recommendations,
    }

