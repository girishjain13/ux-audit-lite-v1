"""Run one audit from the command line and write a static report.

This exists so the audit can run inside a GitHub Actions runner (a real
Linux VM with Python — no CORS issues, no server needed to *serve* the
result) and publish its output as plain files that GitHub Pages can serve
for free. Configure via environment variables so a GitHub Actions
workflow_dispatch input maps straight onto it; falls back to sane defaults
for local use.

This is the lean GitHub-Pages build — no JS rendering, no PageSpeed
performance sampling, no git-committed run history. Those specifically
caused repeated deployment failures on this hosting path (a Playwright
browser install step, slow external API calls, and a mid-job git push all
competing with the Pages deploy step). See the sibling Streamlit/Docker
project for the full-featured build with those included.

Usage (local):
    START_URL=https://example.com MAX_PAGES=100 python run_audit_cli.py

Writes:
    docs/report.html             the report (what GitHub Pages serves)
    docs/index.html               the persistent launcher page
    docs/exports/audit.json
    docs/exports/audit.csv
    docs/exports/audit.xlsx
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from audit_engine import run_audit
from crawler import CrawlConfig
from analyzers.journey import parse_custom_personas
from models import AuditStatus, CrawlProgress
from report_builder import export_csv, export_json, export_xlsx, render_html_report

OUT_DIR = Path(__file__).parent / "docs"
EXPORTS_DIR = OUT_DIR / "exports"


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _print_progress_line(progress: CrawlProgress) -> None:
    # Plain-text progress so it's readable in the GitHub Actions log stream,
    # which doesn't render the live dashboard the web app has.
    print(
        f"[audit] status={progress.status.value} "
        f"crawled={progress.pages_crawled} queued={progress.pages_queued} "
        f"errors={progress.pages_errored} current={progress.current_url}",
        flush=True,
    )


async def main() -> int:
    start_url = os.environ.get("START_URL", "").strip()
    if not start_url:
        print("ERROR: START_URL environment variable is required.", file=sys.stderr)
        return 1
    if not (start_url.startswith("http://") or start_url.startswith("https://")):
        print("ERROR: START_URL must include http:// or https://", file=sys.stderr)
        return 1

    max_pages = int(os.environ.get("MAX_PAGES", "5000"))
    max_depth = int(os.environ.get("MAX_DEPTH", "12"))
    concurrency = int(os.environ.get("CONCURRENCY", "8"))
    respect_robots = _env_bool("RESPECT_ROBOTS", True)
    use_sitemap = _env_bool("USE_SITEMAP", True)
    include_subdomains = _env_bool("INCLUDE_SUBDOMAINS", False)
    with_ai_summary = _env_bool("WITH_AI_SUMMARY", True)  # only fires if ANTHROPIC_API_KEY is set too
    check_external_links = _env_bool("CHECK_EXTERNAL_LINKS", False)
    # UAT/staging Basic Auth — deliberately read from repo Secrets (see the
    # workflow file), never a plain workflow_dispatch input, since a public
    # repo's run inputs are visible to anyone who can see the Actions run.
    basic_auth_username = os.environ.get("BASIC_AUTH_USERNAME", "").strip() or None
    basic_auth_password = os.environ.get("BASIC_AUTH_PASSWORD", "").strip() or None
    verify_ssl = _env_bool("VERIFY_SSL", True)
    custom_user_agent = os.environ.get("CUSTOM_USER_AGENT", "").strip() or None
    # Rule-based custom personas — no AI/API key needed, works purely by
    # keyword-matching against crawled URLs/titles (see analyzers/journey.py).
    # Separate from the AI-inferred target-customer section, which needs
    # ANTHROPIC_API_KEY; this is the fallback/complement that works without it.
    custom_personas_raw = os.environ.get("CUSTOM_PERSONAS", "").strip()
    custom_personas = parse_custom_personas(custom_personas_raw) if custom_personas_raw else []

    config = CrawlConfig(
        start_url=start_url,
        max_pages=min(max_pages, 5000),
        max_depth=max_depth,
        concurrency=concurrency,
        respect_robots=respect_robots,
        use_sitemap=use_sitemap,
        include_subdomains=include_subdomains,
        check_external_links=check_external_links,
        basic_auth_username=basic_auth_username,
        basic_auth_password=basic_auth_password,
        verify_ssl=verify_ssl,
        custom_user_agent=custom_user_agent,
    )
    progress = CrawlProgress()

    print(f"[audit] starting crawl of {start_url} (max_pages={config.max_pages})", flush=True)

    last_logged = -1

    async def on_progress():
        nonlocal last_logged
        if progress.pages_crawled != last_logged:
            last_logged = progress.pages_crawled
            _print_progress_line(progress)

    audit_data = await run_audit(
        config, progress, on_progress=on_progress, with_ai_summary=with_ai_summary,
        custom_personas=custom_personas,
    )
    audit_data["audit_id"] = "latest"

    if progress.status == AuditStatus.ERROR:
        print("ERROR: audit failed", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    html = render_html_report(audit_data)
    # the live app serves exports from /api/... and CSS from a /static
    # mount — neither route exists in a static build, and GitHub Pages
    # project sites are served from a /<repo-name>/ subpath, so an
    # absolute "/static/..." link would 404. Inline the CSS and rewrite
    # the export links to plain relative files instead.
    style_css = (Path(__file__).parent / "static" / "style.css").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="/static/style.css">',
        f"<style>\n{style_css}\n</style>",
    )
    # Chart.js was previously loaded from a CDN (cdnjs.cloudflare.com). That
    # meant the report's charts silently failed to render — with every chart
    # section after the first one going blank too, since one uncaught
    # "Chart is not defined" error halts the rest of the inline <script>
    # block — for anyone viewing the report from a network that blocks
    # third-party CDNs (common on corporate networks) or has an ad-blocker
    # treating cdnjs as a tracker. Inlining it the same way as the CSS above
    # makes the report a single self-contained file with no runtime
    # dependency on any external network request at all.
    chartjs = (Path(__file__).parent / "static" / "chart.umd.js").read_text(encoding="utf-8")
    html = html.replace(
        '<script src="/static/chart.umd.js"></script>',
        f"<script>\n{chartjs}\n</script>",
    )
    html = html.replace("/api/audits/latest/export/json", "exports/audit.json")
    html = html.replace("/api/audits/latest/export/csv", "exports/audit.csv")
    html = html.replace("/api/audits/latest/export/xlsx", "exports/audit.xlsx")
    # the report lives at report.html, not index.html — index.html is the
    # persistent launcher (see below), which a run must never overwrite.
    (OUT_DIR / "report.html").write_text(html, encoding="utf-8")

    (EXPORTS_DIR / "audit.json").write_bytes(export_json(audit_data))
    (EXPORTS_DIR / "audit.csv").write_bytes(export_csv(audit_data))
    (EXPORTS_DIR / "audit.xlsx").write_bytes(export_xlsx(audit_data))

    # Regenerate the launcher page too. It's static/audit-independent, but
    # docs/ isn't committed to the repo (see .gitignore) — it's rebuilt
    # fresh by every workflow run, so this has to happen every run to
    # exist at all, not just once.
    launcher_html = (Path(__file__).parent / "templates" / "launcher.html").read_text(encoding="utf-8")
    launcher_html = launcher_html.replace("{{ inline_css }}", style_css)
    (OUT_DIR / "index.html").write_text(launcher_html, encoding="utf-8")

    print(f"[audit] done — {audit_data['meta']['pages_crawled']} pages, "
          f"UX maturity {audit_data['scoring']['ux_maturity_score']} "
          f"({audit_data['scoring']['ux_maturity_band']})", flush=True)
    print(f"[audit] wrote {OUT_DIR}/report.html and refreshed {OUT_DIR}/index.html", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
