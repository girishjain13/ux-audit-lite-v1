"""Client-stated vs. crawled variance — a simple, honest check against
whatever number the client gave in discovery ("we think we have about
500 pages") versus what this crawl actually found. Useful context for a
Business Analyst scoping a SOW: a big variance either way is worth asking
about before finalizing scope, not something to silently paper over.
"""
from __future__ import annotations


def run_variance_analysis(client_stated_page_count: int | None, crawled_page_count: int, crawl_truncated: bool) -> dict | None:
    if client_stated_page_count is None:
        return None

    diff = crawled_page_count - client_stated_page_count
    pct_diff = round(100 * diff / max(client_stated_page_count, 1), 1)

    if crawl_truncated:
        note = (
            f"This crawl was truncated at the per-run page limit, so the actual site is at least "
            f"{crawled_page_count} pages — the comparison below understates the real total."
        )
    elif abs(pct_diff) <= 10:
        note = "Within a reasonable margin of what the client stated — no major surprise here."
    elif diff > 0:
        note = f"The crawl found meaningfully more pages than the client stated — worth asking whether this includes content the client wasn't aware of (old campaign pages, an unlinked subsection, a forgotten subdomain)."
    else:
        note = f"The crawl found meaningfully fewer pages than the client stated — worth checking whether some of the client's stated pages are blocked (robots.txt/WAF), behind a login this crawl couldn't reach, or simply weren't discoverable from the homepage/sitemap."

    return {
        "client_stated_page_count": client_stated_page_count,
        "crawled_page_count": crawled_page_count,
        "difference": diff,
        "difference_pct": pct_diff,
        "note": note,
    }
