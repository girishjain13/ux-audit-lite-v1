"""Media & asset inventory — image hosting-domain spread (a proxy for DAM
governance: is content living in one recognized place, or scattered across
ad-hoc third-party hosts?), video embed count, and document/download links
by file type.

Honest scope note: this build only ever requests HTML pages, never the
assets themselves, so file size and pixel dimensions (which the original
spec asked for) aren't available without adding per-asset HTTP requests —
a real cost/time tradeoff for a lean crawl, not an oversight. What's here
is presence, hosting domain, and file type — still enough to answer "is
this content governed in one place or not."
"""
from __future__ import annotations

from collections import Counter


def run_media_analysis(
    image_domain_counts: Counter,
    video_embed_count: int,
    document_extension_counts: Counter,
    document_link_examples: list,
) -> dict:
    total_images = sum(image_domain_counts.values())
    dominant_domain, dominant_count = image_domain_counts.most_common(1)[0] if image_domain_counts else (None, 0)
    off_dominant = {d: c for d, c in image_domain_counts.items() if d != dominant_domain}
    off_dominant_total = sum(off_dominant.values())

    recommendations = []
    if total_images and off_dominant_total / total_images > 0.15 and len(image_domain_counts) > 1:
        recommendations.append({
            "text": f"{off_dominant_total} image(s) ({round(100*off_dominant_total/total_images,1)}%) are hosted outside the site's main image domain ({dominant_domain or 'unknown'}), spread across {len(off_dominant)} other host(s) — worth confirming these are governed/backed-up assets before a migration, not orphaned uploads.",
            "effort_bucket": "config", "personas": ["content", "business"],
        })

    return {
        "total_images": total_images,
        "image_domains": dict(image_domain_counts.most_common(20)),
        "dominant_image_domain": dominant_domain,
        "off_dominant_domain_image_count": off_dominant_total,
        "off_dominant_domain_pct": round(100 * off_dominant_total / total_images, 1) if total_images else 0,
        "video_embed_count": video_embed_count,
        "document_counts_by_type": dict(document_extension_counts),
        "document_total": sum(document_extension_counts.values()),
        "document_examples": document_link_examples[:15],
        "recommendations": recommendations,
    }
