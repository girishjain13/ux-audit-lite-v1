# Site Blueprint — GitHub Pages Edition

A UX & Information Architecture audit tool: crawl any website and get back
a heuristic evaluation — scored, prioritized, and organized the way a
design review reads — published as a free static site via GitHub Actions
and GitHub Pages.

This is the **GitHub-Pages-focused build**. It keeps the fast HTTP crawler as
the default, but now has an **opt-in Playwright/Chromium rendering mode** for
JavaScript-heavy sites. When enabled, the audit can execute client-side JS,
inspect the rendered DOM, discover internal links that appear only after
rendering, capture desktop screenshots, and collect basic interaction signals.
Because browser rendering is slower and requires a Chromium install, it is
deliberately disabled by default.

## What it does

- Crawls a site (up to 5,000 pages) respecting `robots.txt`,
  discovering extra URLs from `sitemap.xml`, following redirects correctly
  (including through pages that themselves redirect), with bounded
  concurrency.
- Optional JS rendering with Playwright/Chromium for JS-heavy sites;
  rendered pages can produce screenshots and JS-only internal links.
- Supports HTTP Basic Auth and a custom User-Agent, for auditing
  password-protected UAT/staging sites or sites you're authorized to audit
  that block the default crawler identity via WAF.
- Analyzes:
  - **IA** — URL hierarchy, click-depth, orphan pages, taxonomy.
  - **Content** — word counts, thin/duplicate content, heading order.
  - **Accessibility** — alt text, form labels, ARIA landmarks, `lang`.
  - **SEO** — titles, descriptions, canonicals, schema.org, HTTP status.
  - **Keywords** — most-used words/phrases site-wide.
  - **Integrations** — 30+ known analytics/chat/marketing tools detected
    from actual script tags, plus unrecognized scripts listed by domain.
  - **Feature Matrix** — a checklist of common website features (search,
    login, e-commerce, FAQ, newsletter, etc.) detected from real markup.
  - **Inferred User Journey Maps** — four personas (Prospective Customer,
    Job Seeker, Existing Customer/Support, Press/Investor) mapped onto the
    site's actual structure — explicitly inferred from structure, not real
    behavioral data, since a crawler has no access to analytics.
  - **External link health** — optional spot-check of outbound links for
    broken (4xx/5xx) targets.
- Scores the site 0–100 on IA/Content/Accessibility/SEO plus an overall
  **UX Maturity** score, a **Heuristic Evaluation** against Nielsen's 10
  usability heuristics, a narrative **UX Lead's Assessment**, and a
  **Prioritized Action Plan** ranked by Impact/Effort.
- Exports to JSON, CSV, and a multi-tab formatted Excel workbook, plus the
  interactive HTML report itself.
- A persistent launcher page lets you trigger a run with just a URL — see
  DEPLOY.md for how that works and its one real constraint (a personal
  access token, since GitHub has no way to trigger a workflow anonymously).

## Running it

This build is meant to run via GitHub Actions + GitHub Pages — see
`DEPLOY.md` for the full setup. To run it locally instead:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
START_URL=https://example.com python run_audit_cli.py
```

For a JavaScript-heavy site, install Chromium once and enable rendering:

```bash
python -m playwright install chromium
START_URL=https://example.com RENDER_JS=true BROWSER_MAX_PAGES=25 python run_audit_cli.py
```

The default HTTP-only mode remains the recommended option for large sites;
use browser rendering selectively because it is substantially slower.

That writes `docs/report.html` (open it directly in a browser) plus
`docs/exports/*` and `docs/index.html` (the launcher, mostly relevant when
actually deployed).

## Running the tests

```bash
pytest -v
```

The suite spins up several small local HTTP servers as test fixtures
(a redirecting site, a Basic-Auth-protected site, a robots.txt-blocked
site, a simulated WAF) and runs the real crawler and every analyzer
against each — a good reference for expected output shapes, and for
exactly which real-world scenarios this tool has been checked against.

## Project layout

```
run_audit_cli.py         CLI entrypoint — the thing GitHub Actions runs
audit_engine.py            Orchestrates crawl + all analyzers into one result
crawler.py                   Async crawler (robots.txt, sitemap, redirects, auth)
robots.py                      robots.txt / sitemap.xml parsing helpers
models.py                        Shared dataclasses (PageRecord, CrawlProgress)
ai_insights.py                     Optional LLM executive summary
ux_copy.py                           Plain-language + UX Lead narrative copy
report_builder.py                      HTML report render + JSON/CSV/XLSX export
analyzers/
  ia.py, content.py, accessibility.py, seo.py    Core four pillars
  scoring.py                                       Composite scores + action plan
  heuristics.py                                     Nielsen's 10 heuristic mapping
  keywords.py, integrations.py, feature_matrix.py, journey.py, link_health.py
templates/
  launcher.html               Persistent URL-input page (what Pages serves at /)
  report.html                   The audit report itself
static/style.css                 Shared design system
sample_site/                    Local fixture site used by the main test
tests/                            Full integration test suite + fixtures
.github/workflows/audit.yml        The GitHub Actions workflow
```

## Known limitations (by design, for this build specifically)

- **No JS rendering.** Sites that render primary content client-side
  (heavy SPAs) will show as thin/empty. That's supported in the sibling
  Streamlit/Docker build, deliberately not here.
- **No real performance data.** No Core Web Vitals sampling in this build.
- **No run history / trend tracking.** Each run is a fresh snapshot; there's
  no persisted record of past scores for the same domain in this build.
- **Orphan-page detection depends on sitemap coverage.** A page with zero
  inbound internal links *and* absent from `sitemap.xml` is invisible to
  any crawler by definition.
- **Scoring weights are simple and transparent** (see
  `analyzers/scoring.py`) rather than tuned against real benchmark data.
