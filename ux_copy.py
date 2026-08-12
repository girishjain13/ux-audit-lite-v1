"""Plain-language framing for the report — the same numbers the analyzers
produce, described the way you'd explain them out loud in a design review
rather than as raw metric names.
"""
from __future__ import annotations

SCORE_COPY = {
    "ux_maturity_score": {
        "label": "Overall UX Maturity",
        "plain": "A blended read of the four scores below — how findable, well-written, accessible, and search-friendly the site is overall.",
    },
    "ia_health_score": {
        "label": "Information Architecture",
        "plain": "How easy it is to find anything by clicking around — fewer orphaned pages and shorter paths score higher.",
    },
    "content_quality_score": {
        "label": "Content Quality",
        "plain": "Whether pages have enough real substance, aren't duplicated, and use headings in a sensible order.",
    },
    "accessibility_score": {
        "label": "Accessibility",
        "plain": "Whether people using screen readers, keyboard navigation, or assistive tech can actually use the site.",
    },
    "seo_score": {
        "label": "SEO / Findability",
        "plain": "Whether search engines (and link previews) can tell what each page is actually about.",
    },
}

BAND_COPY = {
    "Strong": "This is in good shape — keep an eye on it, but it's not where your next effort should go.",
    "Adequate": "Workable, with some rough edges worth cleaning up when there's time.",
    "Needs Improvement": "Worth prioritizing — there are enough issues here to be affecting real visitors.",
    "Critical": "This needs attention soon — issues at this level are likely costing conversions, comprehension, or accessibility compliance.",
}

# Reference points a UX lead would actually cite in a review — not hard
# pass/fail thresholds, just what's commonly considered healthy.
BENCHMARKS = {
    "click_depth": "3 clicks or fewer from the homepage is the common guideline for anything a visitor is expected to find unprompted.",
    "word_count": "Most substantive pages (not utility pages like a contact form) tend to sit in the 300–1500 word range; well under that is often too thin to rank or satisfy intent.",
    "alt_text": "100% alt-text coverage on meaningful images is the WCAG 2.1 baseline (Level A, 1.1.1) — this isn't a stretch goal, it's the floor.",
    "title_length": "30–60 characters keeps a page title from being truncated in search results and browser tabs.",
}


def build_plain_summary(scoring: dict, ia: dict, content: dict, a11y: dict) -> list[str]:
    """A few sentences a designer could paste straight into a standup update."""
    lines = []
    band = scoring["ux_maturity_band"]
    lines.append(f"Overall, this site's UX maturity is **{band.lower()}** ({scoring['ux_maturity_score']}/100). {BAND_COPY.get(band, '')}")

    if ia["orphan_page_count"]:
        lines.append(f"{ia['orphan_page_count']} page(s) can't be reached by clicking through the site at all — visitors (and search engines) will only ever find them by a direct link.")
    if ia["pages_over_3_clicks"]:
        lines.append(f"{ia['pages_over_3_clicks']} page(s) take more than 3 clicks to reach from the homepage — that's usually a sign the navigation or category structure needs rethinking.")
    if content["thin_content_count"]:
        lines.append(f"{content['thin_content_count']} page(s) have very little actual content on them — worth checking whether they need expanding, merging, or removing.")
    if content["duplicate_content_page_count"]:
        lines.append(f"{content['duplicate_content_page_count']} page(s) duplicate content that already exists elsewhere on the site — a common source of confusing search results and self-competition.")
    if a11y["pages_with_issues"]:
        lines.append(f"{a11y['pages_with_issues']} of {a11y['pages_analyzed']} pages have at least one accessibility issue — most commonly missing alt text or unlabeled form fields.")
    return lines


def _pct(n: int, d: int) -> float:
    return round(100 * n / max(d, 1), 1)


def build_lead_assessment(
    scoring: dict, ia: dict, content: dict, a11y: dict, seo: dict,
    integrations: dict, keywords: dict, total_pages: int,
) -> dict:
    """A longer, narrative assessment written the way a senior UX lead would
    actually frame findings in a review deck — context and implication, not
    just a restated number. Rule-based (works with or without the optional
    AI executive summary), organized the way a real heuristic evaluation
    report is: overall read, then a paragraph per dimension, then where to
    focus first and why.
    """
    band = scoring["ux_maturity_band"].lower()

    # ---- overall ----
    strongest = max(
        [("Information Architecture", scoring["ia_health_score"]),
         ("Content", scoring["content_quality_score"]),
         ("Accessibility", scoring["accessibility_score"]),
         ("SEO", scoring["seo_score"])],
        key=lambda x: x[1],
    )
    weakest = min(
        [("Information Architecture", scoring["ia_health_score"]),
         ("Content", scoring["content_quality_score"]),
         ("Accessibility", scoring["accessibility_score"]),
         ("SEO", scoring["seo_score"])],
        key=lambda x: x[1],
    )
    overall = (
        f"Across {total_pages} pages, this site lands at {band} UX maturity overall "
        f"({scoring['ux_maturity_score']}/100). {strongest[0]} is the strongest of the four pillars "
        f"({strongest[1]}/100) and {weakest[0]} is the weakest ({weakest[1]}/100) — that gap is usually "
        f"the fastest place to look for a quick win, since it means the team already knows how to execute "
        f"well in at least one dimension; the gap is prioritization, not capability."
    )

    # ---- IA paragraph ----
    orphan_pct = _pct(ia["orphan_page_count"], total_pages)
    if ia["max_click_depth"] <= 3 and ia["orphan_page_count"]:
        depth_note = "Given how shallow the structure already is, depth is not the concern here — the orphan pages are the real IA gap."
    elif ia["pages_over_3_clicks"]:
        depth_note = "Both symptoms point the same direction: the navigation and internal linking aren't doing enough work to surface everything that exists."
    else:
        depth_note = "Depth itself looks healthy once the orphan pages are addressed."
    ia_para = (
        f"Structurally, {ia['orphan_page_count']} page(s) ({orphan_pct}% of the crawl) are orphaned — "
        f"unreachable by clicking through the site. In practice that means those pages only ever get traffic "
        f"from a saved link, a search result, or a campaign — never from someone browsing the site itself. "
        f"The deepest page sits {ia['max_click_depth']} clicks from the homepage, and "
        f"{ia['pages_over_3_clicks']} page(s) exceed the usual 3-click guideline for discoverable content. {depth_note}"
    )

    # ---- Content paragraph ----
    heading_issue_count = len(content["heading_issues"])
    content_para = (
        f"Content-wise, the average page runs {content['word_count_avg']} words. "
        f"{content['thin_content_count']} page(s) fall under the 150-word thin-content threshold, and "
        f"{content['duplicate_content_page_count']} page(s) duplicate content that exists elsewhere on the site — "
        f"both are classic signals of either abandoned pages that should be pruned, or a templated page type "
        f"that was never given real, page-specific content. Heading structure has issues on "
        f"{heading_issue_count} page(s), which matters more than it sounds: "
        f"screen-reader users navigate by heading outline, and a broken outline is functionally like a missing table of contents."
    )

    # ---- Accessibility paragraph ----
    a11y_pct = _pct(a11y["pages_with_issues"], a11y["pages_analyzed"])
    a11y_para = (
        f"{a11y['pages_with_issues']} of {a11y['pages_analyzed']} pages ({a11y_pct}%) have at least one accessibility "
        f"finding — most commonly missing alt text ({a11y['images_missing_alt']} image(s) across the site) or "
        f"unlabeled form fields ({a11y['inputs_missing_label']}). Worth being direct about this one: these aren't "
        f"style preferences, they're the difference between a screen-reader user completing a task and hitting a wall. "
        f"{'This is the pillar that most needs executive attention, both for the people it affects and for the legal exposure most jurisdictions now attach to WCAG conformance.' if scoring['accessibility_score'] < 60 else 'The baseline is reasonably solid — the remaining findings read more like gaps in a review checklist than a systemic problem.'}"
    )

    # ---- SEO paragraph ----
    seo_para = (
        f"From a findability standpoint, {len(seo['title_issues'])} page(s) have a missing or poorly-sized title tag "
        f"and {len(seo['canonical_missing'])} lack a canonical tag. Schema.org structured data is present on "
        f"{seo['schema_coverage_pct']}% of pages. None of this affects the human experience directly, but it "
        f"determines whether the work already put into the content ever gets discovered in the first place — "
        f"strong content behind weak metadata is invisible content."
    )

    # ---- Integrations / performance note ----
    integ_para = None
    if integrations.get("avg_scripts_per_page", 0) or integrations.get("heavy_script_pages"):
        integ_names = ", ".join(d["name"] for d in integrations.get("detected", [])[:6]) or "no recognized services"
        if integrations.get("heavy_script_pages"):
            heavy_note = (
                f"{len(integrations['heavy_script_pages'])} page(s) exceed {integrations['heavy_script_threshold']} scripts, "
                f"which is worth a tag-management audit — every one of those is a render-blocking risk and a page-speed "
                f"tax that compounds on mobile."
            )
        else:
            heavy_note = "Script load looks reasonable — not a current concern."
        integ_para = (
            f"The site is running an average of {integrations['avg_scripts_per_page']} external scripts per page "
            f"(recognized integrations include {integ_names}). {heavy_note}"
        )

    # ---- Where to focus first ----
    focus_candidates = []
    if a11y["pages_with_issues"]:
        focus_candidates.append(("Accessibility", scoring["accessibility_score"], "affects real people's ability to use the site, and carries the most legal/compliance weight of the four pillars"))
    if ia["orphan_page_count"] or ia["pages_over_3_clicks"]:
        focus_candidates.append(("Information Architecture", scoring["ia_health_score"], "is usually the fastest score to move, since fixes are internal linking changes rather than new content or design work"))
    if content["thin_content_count"] or content["duplicate_content_page_count"]:
        focus_candidates.append(("Content", scoring["content_quality_score"], "compounds over time — thin/duplicate pages actively work against the SEO score too, so fixing content here has a multiplier effect"))
    if seo["title_issues"] or seo["canonical_missing"]:
        focus_candidates.append(("SEO", scoring["seo_score"], "is typically the lowest-effort fix of the four — metadata changes rarely need design or engineering sign-off"))
    focus_candidates.sort(key=lambda x: x[1])
    focus_para = None
    if focus_candidates:
        top = focus_candidates[0]
        focus_para = (
            f"If I had to pick one place to start: {top[0]} ({top[1]}/100), because it {top[2]}. "
            f"That doesn't mean the others wait indefinitely — it means this is where the next sprint's UX work should go first."
        )

    paragraphs = [p for p in [overall, ia_para, content_para, a11y_para, seo_para, integ_para, focus_para] if p]
    return {"paragraphs": paragraphs}
