"""Content quality analysis: word counts, heading order, duplicate content,
thin content, media coverage, reading time / scroll-depth UX signals.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median

from models import PageRecord

THIN_CONTENT_THRESHOLD = 150
LONG_CONTENT_THRESHOLD = 2000


def heading_order_issues(rec: PageRecord) -> list[str]:
    issues = []
    seq = rec.heading_sequence
    if not seq:
        return ["no_headings_found"]
    if seq[0] != "h1":
        issues.append("first_heading_not_h1")
    if rec.h1_list and len(rec.h1_list) > 1:
        issues.append("multiple_h1")
    levels = [int(h[1]) for h in seq]
    for prev, nxt in zip(levels, levels[1:]):
        if nxt - prev > 1:
            issues.append(f"skipped_heading_level_h{prev}_to_h{nxt}")
            break
    return issues


def find_duplicates(pages: dict[str, PageRecord]) -> dict[str, list[str]]:
    """Map a representative URL -> list of other URLs sharing identical content hash."""
    by_hash: dict[str, list[str]] = defaultdict(list)
    for url, rec in pages.items():
        if rec.text_hash:
            by_hash[rec.text_hash].append(url)
    duplicate_groups = {urls[0]: urls[1:] for urls in by_hash.values() if len(urls) > 1}
    for primary, dupes in duplicate_groups.items():
        for d in dupes:
            pages[d].is_duplicate_of = primary
    return duplicate_groups


def duplicate_titles_and_descriptions(pages: dict[str, PageRecord]) -> dict:
    titles: Counter[str] = Counter(rec.title for rec in pages.values() if rec.title)
    descs: Counter[str] = Counter(rec.meta_description for rec in pages.values() if rec.meta_description)
    return {
        "duplicate_titles": {t: c for t, c in titles.items() if c > 1},
        "duplicate_descriptions": {d: c for d, c in descs.items() if c > 1},
        "missing_titles": sum(1 for rec in pages.values() if not rec.title),
        "missing_descriptions": sum(1 for rec in pages.values() if not rec.meta_description),
    }


def run_content_analysis(pages: dict[str, PageRecord]) -> dict:
    word_counts = [rec.word_count for rec in pages.values() if rec.status_code and rec.status_code < 400]
    thin_pages = [url for url, rec in pages.items() if rec.is_thin_content]
    long_pages = [url for url, rec in pages.items() if rec.word_count > LONG_CONTENT_THRESHOLD]
    heading_issue_map = {url: heading_order_issues(rec) for url, rec in pages.items() if rec.heading_sequence or rec.status_code == 200}
    pages_with_issues = {u: i for u, i in heading_issue_map.items() if i and i != []}

    duplicate_groups = find_duplicates(pages)
    meta_dupes = duplicate_titles_and_descriptions(pages)

    images_total = sum(rec.images_total for rec in pages.values())
    images_missing_alt = sum(rec.images_missing_alt for rec in pages.values())

    recommendations = []
    if thin_pages:
        recommendations.append(
            f"{len(thin_pages)} page(s) have under {THIN_CONTENT_THRESHOLD} words — expand or consolidate to avoid thin-content UX and SEO issues."
        )
    if duplicate_groups:
        n_dupes = sum(len(v) for v in duplicate_groups.values())
        recommendations.append(f"{n_dupes} page(s) duplicate content found on another URL — canonicalize or merge.")
    if meta_dupes["duplicate_titles"]:
        recommendations.append(f"{len(meta_dupes['duplicate_titles'])} title(s) are reused across multiple pages — make titles unique per page.")
    if images_total and images_missing_alt / max(images_total, 1) > 0.2:
        recommendations.append("Over 20% of images are missing alt text — this hurts both accessibility and image SEO.")

    return {
        "total_pages_analyzed": len(pages),
        "word_count_avg": round(mean(word_counts), 1) if word_counts else 0,
        "word_count_median": round(median(word_counts), 1) if word_counts else 0,
        "thin_content_pages": thin_pages,
        "thin_content_count": len(thin_pages),
        "long_content_pages": long_pages,
        "heading_issues": pages_with_issues,
        "duplicate_content_groups": duplicate_groups,
        "duplicate_content_page_count": sum(len(v) for v in duplicate_groups.values()),
        "meta_duplicates": meta_dupes,
        "images_total": images_total,
        "images_missing_alt": images_missing_alt,
        "image_alt_coverage_pct": round(100 * (1 - images_missing_alt / images_total), 1) if images_total else 100.0,
        "recommendations": recommendations,
    }
