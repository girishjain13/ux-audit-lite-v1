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


async def generate_target_customers(audit_data: dict) -> dict | None:
    """Infers likely target-customer personas and a plausible journey for
    each, purely from what was actually crawled (titles, keywords, URL
    taxonomy, detected integrations) — not from any external knowledge of
    the brand. Same honesty framing as journey.py's rule-based journeys:
    this is an informed inference from site structure/content, not real
    user research, and should read that way rather than as a confident
    claim about who actually uses the site.

    Returns None if no API key is configured, the crawl found too little
    real content to infer anything from, or the model call fails — the
    rest of the report works fine without this section.
    """
    if not ai_insights_available():
        return None

    pages_with_real_data = sum(
        1 for p in audit_data.get("pages", {}).values() if p.get("status_code") and p["status_code"] < 400
    )
    if pages_with_real_data < 3:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.AsyncAnthropic()

    sample_pages = [
        {"url": p["url"], "title": p.get("title"), "meta_description": p.get("meta_description")}
        for p in audit_data["pages"].values()
        if p.get("status_code") and p["status_code"] < 400 and (p.get("title") or p.get("meta_description"))
    ][:40]
    top_keywords = [k["term"] for k in audit_data.get("keywords", {}).get("top_keywords", [])[:20]]
    top_phrases = [p["term"] for p in audit_data.get("keywords", {}).get("top_phrases", [])[:15]]
    taxonomy = audit_data.get("ia", {}).get("taxonomy", {})
    integrations = [d["name"] for d in audit_data.get("integrations", {}).get("detected", [])[:10]]

    prompt = f"""You are a UX researcher inferring who a website is actually built for, based only on
what was crawled from it — no outside knowledge of the brand, no assumptions beyond this evidence.
If the evidence is thin or generic, say fewer personas rather than inventing detail you can't support.

Site: {audit_data['meta']['start_url']}
Sample page titles/descriptions (subset of {audit_data['meta']['pages_crawled']} pages crawled):
{json.dumps(sample_pages, indent=2)[:4000]}
Top keywords: {", ".join(top_keywords)}
Top phrases: {", ".join(top_phrases)}
URL sections and page counts: {json.dumps(taxonomy)}
Detected integrations: {", ".join(integrations) or "none recognized"}

Respond with ONLY valid JSON (no markdown fences, no preamble), matching exactly this shape:
{{
  "personas": [
    {{
      "name": "short persona label, e.g. 'Cost-Conscious Small Business Owner'",
      "description": "1-2 sentences on who this is and what they're trying to accomplish on this site",
      "evidence": "1 sentence citing the specific pages/keywords/sections that support this persona existing",
      "journey": [
        {{"step": "short stage name, e.g. 'Discovers via search'", "detail": "1 sentence, referencing an actual crawled page/URL where relevant"}}
      ]
    }}
  ]
}}
Include 2-4 personas — only as many as the evidence actually supports. Each journey should have
3-5 steps. Keep every field genuinely short; this renders as compact cards, not an essay."""

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text").strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("personas"), list):
            return None
        return parsed
    except Exception:
        # Deliberately silent — this is a "nice to have" section layered on
        # top of a report that's already complete without it. A malformed
        # model response or a transient API error shouldn't be surfaced as
        # a report-breaking problem; it should just result in the section
        # not appearing, same as when no API key is configured at all.
        return None
