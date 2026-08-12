"""Composite scoring: turns the raw analyzer outputs into a 0-100 IA Health
Score and UX Maturity Score, plus a prioritized action list. Weights are
intentionally simple/transparent so they can be tuned per engagement.
"""
from __future__ import annotations


def _pct_score(bad: int, total: int) -> float:
    """0 bad -> 100, all bad -> 0."""
    if total <= 0:
        return 100.0
    return round(100 * (1 - bad / total), 1)


def score_ia(ia: dict, total_pages: int) -> float:
    orphan_score = _pct_score(ia["orphan_page_count"], max(total_pages, 1))
    depth_score = _pct_score(ia["pages_over_3_clicks"], max(total_pages, 1))
    return round((orphan_score * 0.5 + depth_score * 0.5), 1)


def score_content(content: dict, total_pages: int) -> float:
    thin_score = _pct_score(content["thin_content_count"], max(total_pages, 1))
    dup_score = _pct_score(content["duplicate_content_page_count"], max(total_pages, 1))
    heading_score = _pct_score(len(content["heading_issues"]), max(total_pages, 1))
    alt_score = content["image_alt_coverage_pct"]
    return round((thin_score * 0.25 + dup_score * 0.25 + heading_score * 0.25 + alt_score * 0.25), 1)


def score_accessibility(a11y: dict) -> float:
    return _pct_score(a11y["pages_with_issues"], max(a11y["pages_analyzed"], 1))


def score_seo(seo: dict, total_pages: int) -> float:
    title_score = _pct_score(len(seo["title_issues"]), max(total_pages, 1))
    desc_score = _pct_score(len(seo["description_issues"]), max(total_pages, 1))
    canonical_score = _pct_score(len(seo["canonical_missing"]), max(total_pages, 1))
    return round((title_score * 0.4 + desc_score * 0.4 + canonical_score * 0.2), 1)


def build_action_plan(ia: dict, content: dict, a11y: dict, seo: dict) -> list[dict]:
    """Merge all analyzer recommendations into a single prioritized list,
    with rough Impact/Effort sizing — the classic UX-lead prioritization
    lens for deciding what to actually schedule first. These are directional
    estimates from the kind of fix each finding usually requires, not a
    measurement of this specific codebase.
    """
    items = []
    if ia["orphan_page_count"]:
        items.append({"priority": "high", "area": "IA", "impact": "High", "effort": "Medium",
                      "action": f"Fix {ia['orphan_page_count']} orphan page(s) with no internal inbound links."})
    if ia["pages_over_3_clicks"]:
        items.append({"priority": "medium", "area": "IA", "impact": "Medium", "effort": "Medium",
                      "action": f"Reduce click depth for {ia['pages_over_3_clicks']} page(s) currently more than 3 clicks from the homepage."})
    for rec in content["recommendations"]:
        items.append({"priority": "medium", "area": "Content", "impact": "Medium", "effort": "Medium", "action": rec})
    for rec in a11y["recommendations"]:
        items.append({"priority": "high", "area": "Accessibility", "impact": "High", "effort": "Low", "action": rec})
    for rec in seo["recommendations"]:
        items.append({"priority": "medium", "area": "SEO", "impact": "Medium", "effort": "Low", "action": rec})

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: order.get(i["priority"], 3))
    return items


def run_scoring(ia: dict, content: dict, a11y: dict, seo: dict, total_pages: int) -> dict:
    ia_score = score_ia(ia, total_pages)
    content_score = score_content(content, total_pages)
    a11y_score = score_accessibility(a11y)
    seo_score = score_seo(seo, total_pages)
    ux_maturity = round((ia_score + content_score + a11y_score + seo_score) / 4, 1)

    def band(score: float) -> str:
        if score >= 85:
            return "Strong"
        if score >= 70:
            return "Adequate"
        if score >= 50:
            return "Needs Improvement"
        return "Critical"

    return {
        "ia_health_score": ia_score,
        "content_quality_score": content_score,
        "accessibility_score": a11y_score,
        "seo_score": seo_score,
        "ux_maturity_score": ux_maturity,
        "ux_maturity_band": band(ux_maturity),
        "action_plan": build_action_plan(ia, content, a11y, seo),
    }
