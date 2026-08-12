"""Accessibility heuristics: alt text, form labels, ARIA landmarks, heading
order, and page language declaration. These are static-HTML WCAG proxies,
not a substitute for a full WCAG 2.x audit (no rendered contrast/focus checks).
"""
from __future__ import annotations

from models import PageRecord


def run_accessibility_analysis(pages: dict[str, PageRecord]) -> dict:
    per_page = {}
    total_images = total_missing_alt = 0
    total_inputs_missing_label = 0
    pages_missing_lang = []
    pages_no_landmarks = []
    pages_multi_h1 = []

    for url, rec in pages.items():
        if rec.status_code is None or rec.status_code >= 400:
            continue
        issues = []
        if rec.images_missing_alt:
            issues.append(f"{rec.images_missing_alt} image(s) missing alt text")
        if rec.inputs_missing_label:
            issues.append(f"{rec.inputs_missing_label} form field(s) missing an accessible label")
        if not rec.lang:
            issues.append("missing lang attribute on <html>")
            pages_missing_lang.append(url)
        if rec.aria_landmark_count == 0:
            issues.append("no ARIA landmark roles found")
            pages_no_landmarks.append(url)
        if len(rec.h1_list) > 1:
            issues.append("multiple <h1> elements")
            pages_multi_h1.append(url)

        if issues:
            per_page[url] = issues

        total_images += rec.images_total
        total_missing_alt += rec.images_missing_alt
        total_inputs_missing_label += rec.inputs_missing_label

    pages_analyzed = sum(1 for r in pages.values() if r.status_code and r.status_code < 400)
    clean_pages = pages_analyzed - len(per_page)

    recommendations = []
    if total_missing_alt:
        recommendations.append(f"Add descriptive alt text to {total_missing_alt} image(s) across the site.")
    if pages_missing_lang:
        recommendations.append(f"{len(pages_missing_lang)} page(s) are missing a lang attribute — add lang to <html> for screen readers.")
    if pages_no_landmarks:
        recommendations.append(f"{len(pages_no_landmarks)} page(s) have no ARIA landmark roles (header/nav/main/footer) — add these for assistive-tech navigation.")
    if total_inputs_missing_label:
        recommendations.append(f"{total_inputs_missing_label} form input(s) lack an accessible label.")

    return {
        "pages_analyzed": pages_analyzed,
        "pages_with_issues": len(per_page),
        "clean_pages": max(clean_pages, 0),
        "per_page_issues": per_page,
        "images_total": total_images,
        "images_missing_alt": total_missing_alt,
        "inputs_missing_label": total_inputs_missing_label,
        "pages_missing_lang": pages_missing_lang,
        "pages_no_landmarks": pages_no_landmarks,
        "pages_multi_h1": pages_multi_h1,
        "recommendations": recommendations,
    }
