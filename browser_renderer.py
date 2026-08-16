"""Optional Playwright-based rendering/enrichment for JS-heavy sites.

The HTTP crawler remains the source of truth for ordinary audits.  When JS
rendering is enabled, this module opens crawled pages in a real Chromium
browser, executes client-side JavaScript, captures screenshots, records basic
interaction/viewport signals, and adds internal links found only after render.

This is intentionally opt-in because browser rendering is slower and requires
an installed Chromium binary.  It is designed to run locally or in GitHub
Actions, not in GitHub Pages itself.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from models import PageRecord
from robots import RobotsInfo


class BrowserRenderError(RuntimeError):
    """Raised for browser setup/rendering failures that should be surfaced clearly."""


def _normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    return parsed._replace(path=parsed.path or "/").geturl()


def _same_site(url: str, root_netloc: str, include_subdomains: bool) -> bool:
    netloc = urlparse(url).netloc
    if include_subdomains:
        root = root_netloc.split(":")[0]
        return netloc == root_netloc or netloc == root or netloc.endswith("." + root)
    return netloc == root_netloc


def _looks_like_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not path.endswith((
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip",
        ".rar", ".mp4", ".mp3", ".avi", ".mov", ".css", ".js", ".xml",
        ".ico", ".woff", ".woff2", ".ttf", ".eot", ".doc", ".docx", ".xls",
        ".xlsx", ".ppt", ".pptx",
    ))


async def enrich_with_browser(crawler, output_dir: str = "docs/evidence") -> dict:
    """Render a bounded set of pages and merge rendered signals into crawler state.

    Returns a summary suitable for inclusion in the audit report.
    """
    cfg = crawler.config
    max_rendered = min(max(int(cfg.browser_max_pages), 0), cfg.max_pages)
    if not cfg.render_js or max_rendered == 0:
        return {
            "enabled": False,
            "pages_rendered": 0,
            "new_pages_discovered": 0,
            "screenshots_captured": 0,
            "errors": [],
        }

    try:
        from playwright.async_api import async_playwright as _probe  # noqa: F401
    except ImportError as exc:
        raise BrowserRenderError(
            "JS rendering is enabled but Playwright is not installed. "
            "Run 'pip install playwright' and 'playwright install chromium'."
        ) from exc

    evidence_dir = Path(output_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Use the same basic-auth credentials as the HTTP crawler where available.
    http_auth = None
    if cfg.basic_auth_username is not None:
        http_auth = (cfg.basic_auth_username, cfg.basic_auth_password or "")

    root_netloc = urlparse(cfg.start_url).netloc
    queue = deque(sorted(crawler.pages.keys(), key=lambda u: crawler.pages[u].depth))
    queued = set(queue)
    rendered = set()
    errors: list[str] = []
    screenshots = 0
    new_pages = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context_kwargs = {
            "ignore_https_errors": not cfg.verify_ssl,
            "viewport": {"width": 1440, "height": 900},
            "user_agent": cfg.custom_user_agent or None,
        }
        if http_auth:
            context_kwargs["http_credentials"] = {
                "username": http_auth[0],
                "password": http_auth[1],
            }
        context_kwargs = {k: v for k, v in context_kwargs.items() if v is not None}
        context = await browser.new_context(**context_kwargs)

        try:
            while queue and len(rendered) < max_rendered:
                url = queue.popleft()
                if url in rendered or not _looks_like_page(url):
                    continue
                if not _same_site(url, root_netloc, cfg.include_subdomains):
                    continue
                if not crawler._url_allowed(url):
                    continue

                page = await context.new_page()
                console_errors: list[str] = []
                js_errors: list[str] = []
                page.on("pageerror", lambda exc: js_errors.append(str(exc)[:500]))
                page.on("console", lambda msg: console_errors.append(msg.text[:500]) if msg.type == "error" else None)

                t0 = time.monotonic()
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=int(cfg.browser_timeout * 1000))
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(int(cfg.browser_timeout * 1000), 5000))
                    except PlaywrightTimeoutError:
                        pass

                    final_url = _normalize_url(page.url)
                    if final_url != _normalize_url(url):
                        crawler.redirect_map[url] = final_url

                    html = await page.content()
                    title = await page.title()
                    viewport = await page.evaluate("""() => ({
                        width: window.innerWidth,
                        height: window.innerHeight,
                        documentHeight: Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0),
                        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 2,
                    })""")

                    links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href,
                        text: (a.innerText || a.textContent || '').trim().slice(0, 160),
                    }))""")
                    interaction = await page.evaluate("""() => {
                        const visible = el => {
                            const s = getComputedStyle(el);
                            const r = el.getBoundingClientRect();
                            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                        };
                        const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
                        const anchors = Array.from(document.querySelectorAll('a[href]')).filter(visible);
                        const forms = Array.from(document.querySelectorAll('form')).filter(visible);
                        const inputs = Array.from(document.querySelectorAll('input, textarea, select')).filter(visible);
                        const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
                        const ctaHints = /get started|start now|contact|request|demo|buy|shop|sign up|signup|register|subscribe|learn more|download|book|schedule|apply|quote|try/i;
                        const ctas = [...buttons, ...anchors].filter(el => ctaHints.test(text(el) || el.getAttribute('aria-label') || '')).map(text);
                        return {
                            buttons: buttons.length,
                            anchors: anchors.length,
                            forms: forms.length,
                            inputs: inputs.length,
                            ctaCount: ctas.length,
                            ctaLabels: ctas.slice(0, 20),
                            navCount: document.querySelectorAll('nav a[href]').length,
                            dialogs: document.querySelectorAll('dialog, [role="dialog"]').length,
                            tabs: document.querySelectorAll('[role="tab"]').length,
                            accordions: document.querySelectorAll('[aria-expanded]').length,
                        };
                    }""")

                    record = crawler.pages.get(url) or crawler.pages.get(final_url)
                    if record is None:
                        record = PageRecord(url=final_url, depth=1, path_depth=crawler._path_depth(final_url))
                        crawler.pages[final_url] = record
                        new_pages += 1
                    record.url = final_url
                    record.status_code = response.status if response else 200
                    record.content_type = (response.headers.get("content-type", "text/html") if response else "text/html")
                    record.rendered = True
                    record.render_ms = (time.monotonic() - t0) * 1000
                    record.rendered_title = title or ""
                    record.rendered_height = int(viewport.get("documentHeight") or 0)
                    record.viewport_width = int(viewport.get("width") or 1440)
                    record.viewport_height = int(viewport.get("height") or 900)
                    record.horizontal_overflow = bool(viewport.get("horizontalOverflow"))
                    record.rendered_button_count = int(interaction["buttons"])
                    record.rendered_form_count = int(interaction["forms"])
                    record.rendered_input_count = int(interaction["inputs"])
                    record.rendered_cta_count = int(interaction["ctaCount"])
                    record.rendered_cta_labels = interaction["ctaLabels"]
                    record.rendered_nav_link_count = int(interaction["navCount"])
                    record.rendered_dialog_count = int(interaction["dialogs"])
                    record.rendered_tab_count = int(interaction["tabs"])
                    record.rendered_accordion_count = int(interaction["accordions"])
                    record.js_errors = js_errors[:20]
                    record.console_errors = console_errors[:20]

                    # Capture one full-page desktop screenshot per rendered page.
                    safe_name = __import__("hashlib").sha1(final_url.encode()).hexdigest()[:16]
                    screenshot_path = evidence_dir / f"{safe_name}-desktop.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    record.screenshot_path = str(screenshot_path.relative_to(Path("docs"))).replace("\\", "/")
                    screenshots += 1

                    # Merge links found after JavaScript execution into the IA graph.
                    for item in links:
                        href = item.get("href") or ""
                        target = _normalize_url(href)
                        if not target or not _looks_like_page(target):
                            continue
                        if not _same_site(target, root_netloc, cfg.include_subdomains):
                            continue
                        if not crawler._url_allowed(target):
                            continue
                        crawler.edges.append((final_url, target))
                        if target not in crawler.pages and len(crawler.pages) < cfg.max_pages:
                            # Render-discovered pages are intentionally added to the browser queue. They
                            # become first-class PageRecords, allowing IA analysis to see JS-only navigation.
                            new_record = PageRecord(url=target, depth=record.depth + 1, path_depth=crawler._path_depth(target))
                            crawler.pages[target] = new_record
                            new_pages += 1
                        if target in crawler.pages and target not in rendered and target not in queued:
                            queue.append(target)
                            queued.add(target)

                    crawler.progress.note(f"Rendered JS: {final_url}")
                except Exception as exc:
                    message = f"{url}: {exc.__class__.__name__}: {str(exc)[:250]}"
                    errors.append(message)
                    if url in crawler.pages:
                        crawler.pages[url].render_error = message
                finally:
                    rendered.add(url)
                    await page.close()

            # Normalize duplicate edges after browser enrichment.
            crawler.edges = list(dict.fromkeys(crawler.edges))
            crawler.edges = crawler._resolve_edges(crawler.edges)
        finally:
            await context.close()
            await browser.close()

    return {
        "enabled": True,
        "pages_rendered": len(rendered),
        "new_pages_discovered": new_pages,
        "screenshots_captured": screenshots,
        "errors": errors[:50],
    }
