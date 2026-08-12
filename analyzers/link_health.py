"""Checks whether external links (pointing off-site) actually resolve.

Bounded and separate from the main crawl on purpose: an enterprise site can
easily link to hundreds of distinct external domains, and checking all of
them would multiply crawl time and hit other people's servers more than
necessary for what's meant to be a spot-check, not an exhaustive audit.
"""
from __future__ import annotations

import asyncio

import httpx

DEFAULT_HEADERS = {"User-Agent": "IA-UX-AuditBot/1.0 (+link-health-check)"}


async def check_external_links(
    link_targets: dict[str, set[str]], concurrency: int = 10, timeout: float = 8.0
) -> dict:
    """link_targets: {external_url: {internal pages that link to it}}"""
    if not link_targets:
        return {"checked": 0, "broken": [], "broken_count": 0}

    broken = []
    sem = asyncio.Semaphore(concurrency)

    async def check_one(client: httpx.AsyncClient, url: str, linking_pages: set[str]):
        async with sem:
            try:
                resp = await client.head(url, timeout=timeout, follow_redirects=True)
                status = resp.status_code
                if status >= 400:
                    # some servers don't implement HEAD correctly — confirm with GET before flagging
                    resp = await client.get(url, timeout=timeout, follow_redirects=True)
                    status = resp.status_code
                if status >= 400:
                    broken.append({
                        "url": url, "status_code": status,
                        "linked_from_count": len(linking_pages),
                        "example_linking_page": next(iter(linking_pages), ""),
                    })
            except httpx.HTTPError as exc:
                broken.append({
                    "url": url, "status_code": None, "error": exc.__class__.__name__,
                    "linked_from_count": len(linking_pages),
                    "example_linking_page": next(iter(linking_pages), ""),
                })

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        await asyncio.gather(*[check_one(client, url, pages) for url, pages in link_targets.items()])

    return {
        "checked": len(link_targets),
        "broken": sorted(broken, key=lambda b: -b["linked_from_count"]),
        "broken_count": len(broken),
    }
