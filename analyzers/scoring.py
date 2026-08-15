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


def build_action_plan(
    ia: dict, content: dict, a11y: dict, seo: dict, url_health: dict | None = None,
    freshness: dict | None = None, media: dict | None = None, locale: dict | None = None,
    risk: dict | None = None, components: dict | None = None,
) -> list[dict]:
    """Merge all analyzer recommendations into a single prioritized list,
    with rough Impact/Effort sizing — the classic UX-lead prioritization
    lens for deciding what to actually schedule first. These are directional
    estimates from the kind of fix each finding usually requires, not a
    measurement of this specific codebase.

    Each item also carries an effort_bucket (ootb fix / config effort /
    custom dev) and the persona(s) it's most relevant to — this is what
    lets the Business Analyst export a SOW-scoping-ready findings list,
    and what the report's persona tabs filter the action plan by.
    """
    items = []
    if ia["orphan_page_count"]:
        items.append({"priority": "high", "area": "IA", "impact": "High", "effort": "Medium",
                      "effort_bucket": "config", "personas": ["ux", "business"],
                      "action": f"Fix {ia['orphan_page_count']} orphan page(s) with no internal inbound links."})
    if ia["pages_over_3_clicks"]:
        items.append({"priority": "medium", "area": "IA", "impact": "Medium", "effort": "Medium",
                      "effort_bucket": "config", "personas": ["ux", "business"],
                      "action": f"Reduce click depth for {ia['pages_over_3_clicks']} page(s) currently more than 3 clicks from the homepage."})
    for rec in content["recommendations"]:
        items.append({"priority": "medium", "area": "Content", "impact": "Medium", "effort": "Medium",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in a11y["recommendations"]:
        items.append({"priority": "high", "area": "Accessibility", "impact": "High", "effort": "Low",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in seo["recommendations"]:
        items.append({"priority": "medium", "area": "SEO", "impact": "Medium", "effort": "Low",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in (url_health or {}).get("recommendations", []):
        priority = "high" if "redirect loop" in rec["text"] else "medium"
        items.append({"priority": priority, "area": "URL Structure", "impact": "Medium", "effort": "Low",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in (freshness or {}).get("recommendations", []):
        items.append({"priority": "low", "area": "Content Freshness", "impact": "Low", "effort": "Medium",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in (media or {}).get("recommendations", []):
        items.append({"priority": "low", "area": "Media/Assets", "impact": "Low", "effort": "Medium",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in (locale or {}).get("recommendations", []):
        items.append({"priority": "medium", "area": "Locale", "impact": "Medium", "effort": "Low",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in (risk or {}).get("recommendations", []):
        items.append({"priority": "high", "area": "Risk", "impact": "High", "effort": "Low",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})
    for rec in (components or {}).get("recommendations", []):
        items.append({"priority": "low", "area": "Components", "impact": "Low", "effort": "Medium",
                      "effort_bucket": rec["effort_bucket"], "personas": rec["personas"], "action": rec["text"]})

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: order.get(i["priority"], 3))
    return items


def run_scoring(
    ia: dict, content: dict, a11y: dict, seo: dict, total_pages: int,
    url_health: dict | None = None, freshness: dict | None = None,
    media: dict | None = None, locale: dict | None = None, risk: dict | None = None,
    components: dict | None = None,
) -> dict:
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
        "action_plan": build_action_plan(ia, content, a11y, seo, url_health, freshness, media, locale, risk, components),
    }
