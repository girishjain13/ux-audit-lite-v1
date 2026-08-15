"""Groups crawled pages by their HTML structural fingerprint (see
crawler.py's _structural_fingerprint) into "templates" — pages that share
the same layout/skeleton, regardless of URL pattern or title.

Useful for two different questions a URL-based grouping (like taxonomy.py's
top-level-section breakdown) can't answer on its own:
  - How many genuinely distinct page layouts does this site actually use?
    A site with 5000 pages but only 6 templates is far more consistent
    (and far cheaper to redesign/maintain) than one with 40.
  - Which specific pages are one-off outliers — built from a layout no
    other page on the site uses? That's often either a legitimately
    special page (a campaign landing page, a one-time announcement) or a
    sign of drift from the design system worth a second look.
"""
from __future__ import annotations


def run_template_analysis(pages: dict) -> dict:
    real_pages = {url: rec for url, rec in pages.items() if rec.status_code and rec.status_code < 400 and rec.template_fingerprint}

    groups: dict[str, list] = {}
    for url, rec in real_pages.items():
        groups.setdefault(rec.template_fingerprint, []).append(rec)

    templates = []
    for fingerprint, recs in groups.items():
        recs_sorted = sorted(recs, key=lambda r: r.url)
        templates.append({
            "fingerprint": fingerprint,
            "page_count": len(recs),
            "example_url": recs_sorted[0].url,
            "example_title": recs_sorted[0].title or "(untitled)",
            "sample_urls": [r.url for r in recs_sorted[:5]],
        })
    templates.sort(key=lambda t: t["page_count"], reverse=True)

    one_off_pages = [
        {"url": t["example_url"], "title": t["example_title"]}
        for t in templates if t["page_count"] == 1
    ]

    return {
        "unique_template_count": len(templates),
        "templates": templates[:25],  # cap what gets embedded/rendered — a report listing all 400 templates on a very fragmented site isn't more useful than the top ones
        "templates_with_reuse": sum(1 for t in templates if t["page_count"] >= 2),
        "one_off_count": len(one_off_pages),
        "one_off_pages": one_off_pages[:25],
        "pages_analyzed": len(real_pages),
    }
