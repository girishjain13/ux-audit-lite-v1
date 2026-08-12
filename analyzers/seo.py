"""SEO / metadata analysis: title & description length, canonicals,
Open Graph coverage, Schema.org presence, HTTP status breakdown."""
from __future__ import annotations

from collections import Counter

from models import PageRecord

TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 50, 160


def run_seo_analysis(pages: dict[str, PageRecord]) -> dict:
    status_breakdown: Counter[int] = Counter()
    title_issues = []
    description_issues = []
    canonical_missing = []
    og_missing = []
    schema_missing = []

    for url, rec in pages.items():
        if rec.status_code:
            status_breakdown[rec.status_code] += 1
        if rec.status_code and rec.status_code >= 400:
            continue

        tlen = len(rec.title)
        if not rec.title:
            title_issues.append({"url": url, "issue": "missing_title"})
        elif tlen < TITLE_MIN or tlen > TITLE_MAX:
            title_issues.append({"url": url, "issue": f"length_{tlen}_outside_{TITLE_MIN}-{TITLE_MAX}"})

        dlen = len(rec.meta_description)
        if not rec.meta_description:
            description_issues.append({"url": url, "issue": "missing_description"})
        elif dlen < DESC_MIN or dlen > DESC_MAX:
            description_issues.append({"url": url, "issue": f"length_{dlen}_outside_{DESC_MIN}-{DESC_MAX}"})

        if not rec.canonical:
            canonical_missing.append(url)
        if not rec.og_tags:
            og_missing.append(url)
        if not rec.has_schema_org:
            schema_missing.append(url)

    pages_ok = sum(1 for r in pages.values() if r.status_code and r.status_code < 400)

    recommendations = []
    if title_issues:
        recommendations.append(f"{len(title_issues)} page(s) have missing or poorly-sized <title> tags (ideal {TITLE_MIN}-{TITLE_MAX} chars).")
    if description_issues:
        recommendations.append(f"{len(description_issues)} page(s) have missing or poorly-sized meta descriptions (ideal {DESC_MIN}-{DESC_MAX} chars).")
    if canonical_missing:
        recommendations.append(f"{len(canonical_missing)} page(s) lack a canonical tag — add one to prevent duplicate-content dilution.")
    if schema_missing and len(schema_missing) < len(pages):
        recommendations.append(f"{len(schema_missing)} page(s) have no Schema.org structured data — consider adding for rich results.")

    return {
        "status_code_breakdown": dict(status_breakdown),
        "pages_ok": pages_ok,
        "title_issues": title_issues,
        "description_issues": description_issues,
        "canonical_missing": canonical_missing,
        "og_missing": og_missing,
        "schema_missing": schema_missing,
        "schema_coverage_pct": round(100 * (1 - len(schema_missing) / max(pages_ok, 1)), 1),
        "recommendations": recommendations,
    }
