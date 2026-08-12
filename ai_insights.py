"""Optional AI-generated executive summary and narrative recommendations.

Only runs if ANTHROPIC_API_KEY is set in the environment. Fully optional —
the rest of the audit (scores, charts, action plan) works without it.
"""
from __future__ import annotations

import json
import os


def ai_insights_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


async def generate_ai_summary(audit_data: dict) -> str | None:
    if not ai_insights_available():
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.AsyncAnthropic()
    scoring = audit_data["scoring"]
    integ = audit_data.get("integrations", {})
    top_integrations = ", ".join(d["name"] for d in integ.get("detected", [])[:8]) or "none recognized"
    prompt = f"""You are a Senior UX Lead with 15+ years leading UX strategy for enterprise web
platforms, writing the executive summary section of a heuristic evaluation report for a
stakeholder audience (likely a VP of Product/Design or a client). Write with the voice of
someone who has run dozens of these and is being direct about what matters, not a generic
tool summarizing metrics back at the reader.

Structure your response in plain text (no markdown headers) as:
1. A 2-3 sentence overall read of where this site stands and why that matters for the business.
2. "Key risks" — the 2-3 findings most likely to cost the business something (conversions,
   compliance exposure, SEO visibility, or user trust) if left unaddressed, with a one-line
   rationale for each, not just a restated number.
3. "Recommended roadmap" — organize the fixes into Now (this sprint), Next (this quarter), and
   Later (backlog), with a one-line rationale per phase for why that sequencing makes sense.
Aim for substance over length, but don't artificially compress — 300-450 words is appropriate
for this audience.

Site: {audit_data['meta']['start_url']}
Pages crawled: {audit_data['meta']['pages_crawled']}
UX Maturity: {scoring['ux_maturity_score']}/100 ({scoring['ux_maturity_band']})
IA Health: {scoring['ia_health_score']}/100 — Content Quality: {scoring['content_quality_score']}/100 — Accessibility: {scoring['accessibility_score']}/100 — SEO: {scoring['seo_score']}/100
Orphan pages: {audit_data['ia']['orphan_page_count']} — Max click depth: {audit_data['ia']['max_click_depth']}
Thin content pages: {audit_data['content']['thin_content_count']} — Duplicate content pages: {audit_data['content']['duplicate_content_page_count']}
Accessibility issues on: {audit_data['accessibility']['pages_with_issues']} of {audit_data['accessibility']['pages_analyzed']} pages (missing alt text: {audit_data['accessibility']['images_missing_alt']}, unlabeled form fields: {audit_data['accessibility']['inputs_missing_label']})
SEO: {len(audit_data['seo']['title_issues'])} title issues, {len(audit_data['seo']['canonical_missing'])} missing canonicals, {audit_data['seo']['schema_coverage_pct']}% schema.org coverage
Detected third-party integrations: {top_integrations} (avg {integ.get('avg_scripts_per_page', 0)} external scripts/page)
Top action items already identified: {"; ".join(a["action"] for a in scoring["action_plan"][:6])}
"""
    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")
    except Exception as exc:
        return f"(AI summary unavailable: {exc})"
