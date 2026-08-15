"""Content freshness — how stale is the site, and how much of it can we
even tell? Feeds the Content Strategist's governance-load questions and
the Business Analyst's change-frequency-vs-content-type cross-reference.

Freshness data comes from whichever of these a page actually provides:
the HTTP Last-Modified response header, or sitemap.xml's <lastmod> for
that URL (see crawler.py). Many real sites provide neither reliably —
that's itself worth reporting rather than silently treating "unknown" as
"old" or dropping those pages from the picture.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from models import PageRecord

ONE_YEAR_DAYS = 365
THREE_YEAR_DAYS = 365 * 3


def _parse_last_modified(raw: str) -> datetime | None:
    if not raw:
        return None
    # HTTP-date form, e.g. "Wed, 21 Oct 2015 07:28:00 GMT"
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    # ISO-date form, e.g. "2015-10-21" or "2015-10-21T07:28:00+00:00"
    # (sitemap.xml's <lastmod> is typically this format)
    try:
        cleaned = raw.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def run_freshness_analysis(pages: dict[str, PageRecord]) -> dict:
    real_pages = {u: r for u, r in pages.items() if r.status_code and r.status_code < 400}
    now = datetime.now(timezone.utc)

    dated = []
    for url, rec in real_pages.items():
        dt = _parse_last_modified(rec.last_modified)
        if dt:
            dated.append((url, dt, (now - dt).days))

    unknown_count = len(real_pages) - len(dated)
    stale_1yr = [d for d in dated if d[2] > ONE_YEAR_DAYS]
    stale_3yr = [d for d in dated if d[2] > THREE_YEAR_DAYS]
    dated_sorted = sorted(dated, key=lambda d: d[2], reverse=True)

    recommendations = []
    if len(real_pages) and unknown_count / len(real_pages) > 0.5:
        recommendations.append({
            "text": f"No reliable last-modified date could be found for {unknown_count} of {len(real_pages)} pages (no Last-Modified header, no sitemap lastmod) — content-freshness reporting is only partial for this site.",
            "effort_bucket": "config", "personas": ["content", "business"],
        })
    if dated and len(stale_3yr) / max(len(dated), 1) > 0.2:
        recommendations.append({
            "text": f"{len(stale_3yr)} page(s) haven't been touched in 3+ years — worth a content-governance review before migrating them as-is.",
            "effort_bucket": "config", "personas": ["content", "business"],
        })

    return {
        "pages_with_known_date": len(dated),
        "pages_with_unknown_date": unknown_count,
        "unknown_date_pct": round(100 * unknown_count / len(real_pages), 1) if real_pages else 0,
        "stale_over_1yr_count": len(stale_1yr),
        "stale_over_1yr_pct": round(100 * len(stale_1yr) / len(dated), 1) if dated else 0,
        "stale_over_3yr_count": len(stale_3yr),
        "stale_over_3yr_pct": round(100 * len(stale_3yr) / len(dated), 1) if dated else 0,
        "stalest_pages": [{"url": u, "days_since_update": days} for u, _, days in dated_sorted[:15]],
        "recommendations": recommendations,
    }
