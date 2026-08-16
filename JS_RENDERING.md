# JavaScript Rendering Update

This version adds an **opt-in Playwright/Chromium rendering layer** without replacing the existing HTTP crawler.

## What changed

- Added `browser_renderer.py` for optional Chromium rendering.
- Added rendered UX signals to `PageRecord`.
- Added desktop full-page screenshots under `docs/evidence/` when rendering is enabled.
- Added discovery of internal links that only appear after JavaScript execution.
- Added rendered interaction counts: buttons, forms, inputs, CTAs, nav links, dialogs, tabs and accordions.
- Captures JavaScript/page errors and console errors.
- Added `render_js`, `browser_max_pages`, and `browser_timeout` configuration options.
- Added GitHub Actions inputs for enabling rendering and installing Chromium only when requested.
- Added launcher controls for JavaScript rendering and browser-page limits.
- Added browser-rendering status and screenshot links to the report.
- Existing HTTP-only mode remains the default.

## Local use

```bash
pip install -r requirements.txt
python -m playwright install chromium
START_URL=https://example.com RENDER_JS=true BROWSER_MAX_PAGES=25 python run_audit_cli.py
```

## GitHub Actions

Enable **JavaScript rendering (Chromium)** in the launcher. The workflow will install Chromium only for that run. Keep the browser-page limit relatively small (for example 25–50) because browser rendering is substantially slower than HTTP crawling.

## Important limitation

The browser pass is intentionally bounded. It enriches the existing crawl and can discover JavaScript-only internal links. It is not intended to replace HTTP crawling for thousands of pages. The recommended pattern is to use the HTTP crawler for broad coverage and Playwright selectively for visual/rendered evidence.
