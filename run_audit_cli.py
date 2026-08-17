"""Run one UX audit from the command line and write a static GitHub Pages report.

Configuration is supplied through environment variables so the same entry point
can be used locally and from GitHub Actions.

Important behavior:
- Empty environment variables fall back to defaults instead of crashing.
- AUDIT_MODE can be ``quick`` or ``full`` and supplies sensible defaults.
- JavaScript rendering is controlled by RENDER_JS and browser limits.
- The report and exports are written to docs/ for GitHub Pages deployment.

Local examples:
    START_URL=https://example.com AUDIT_MODE=quick python run_audit_cli.py
    START_URL=https://example.com AUDIT_MODE=full MAX_PAGES=1000 python run_audit_cli.py

GitHub Actions should pass the workflow inputs through environment variables.
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


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _env_text(name: str, default: str = "") -> str:
    """Return a stripped environment value, treating blank as unset."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, *, minimum: int | None = None,
             maximum: int | None = None) -> int:
    """Read an integer safely.

    GitHub Actions can provide an empty string for an optional input.  Using
    int(os.environ.get(name, default)) is unsafe because the environment key
    can exist with value ''.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            print(
                f"WARNING: {name}={raw!r} is not a valid integer; using {default}.",
                file=sys.stderr,
            )
            value = default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None,
               maximum: float | None = None) -> float:
    """Read a float safely, including blank GitHub Actions inputs."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = float(raw.strip())
        except ValueError:
            print(
                f"WARNING: {name}={raw!r} is not a valid number; using {default}.",
                file=sys.stderr,
            )
            value = default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _parse_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        print(
            f"WARNING: {name}={raw!r} is not a valid integer; ignoring it.",
            file=sys.stderr,
        )
        return None
    return value if value >= 0 else None


def _print_progress_line(progress: CrawlProgress) -> None:
    # Plain-text progress is useful in GitHub Actions logs.
    print(
        f"[audit] status={progress.status.value} "
        f"crawled={progress.pages_crawled} queued={progress.pages_queued} "
        f"errors={progress.pages_errored} current={progress.current_url}",
        flush=True,
    )


def _resolve_mode_defaults() -> tuple[str, int, int, bool]:
    """Resolve audit mode and its sensible page/browser defaults.

    Quick mode is intentionally small and representative. Full mode is the
    deeper crawl. Explicit MAX_PAGES/BROWSER_MAX_PAGES values always win.
    """
    raw_mode = _env_text("AUDIT_MODE", "quick").lower()
    if raw_mode in {"quick", "quick_scan", "quick-scan", "high_level", "high-level"}:
        mode = "quick"
        default_max_pages = 25
        default_browser_pages = 10
    elif raw_mode in {"full", "full_audit", "full-audit"}:
        mode = "full"
        default_max_pages = 500
        default_browser_pages = 50
    else:
        print(
            f"WARNING: AUDIT_MODE={raw_mode!r} is not recognized; using 'quick'.",
            file=sys.stderr,
        )
        mode = "quick"
        default_max_pages = 25
        default_browser_pages = 10

    # JS rendering is expected for the current cloud audit workflow.  Allow
    # an explicit false value for local troubleshooting.
    render_js = _env_bool("RENDER_JS", True)
    return mode, default_max_pages, default_browser_pages, render_js


async def main() -> int:
    start_url = _env_text("START_URL")
    if not start_url:
        print("ERROR: START_URL environment variable is required.", file=sys.stderr)
        return 1

    if not (start_url.startswith("http://") or start_url.startswith("https://")):
        print("ERROR: START_URL must include http:// or https://", file=sys.stderr)
        return 1

    mode, mode_default_pages, mode_default_browser_pages, render_js = _resolve_mode_defaults()

    # Explicit environment values override mode defaults. Blank values fall
    # back to the mode defaults instead of raising ValueError.
    max_pages = _env_int("MAX_PAGES", mode_default_pages, minimum=1, maximum=5000)
    max_depth = _env_int("MAX_DEPTH", 12, minimum=1, maximum=100)
    concurrency = _env_int("CONCURRENCY", 8, minimum=1, maximum=32)

    respect_robots = _env_bool("RESPECT_ROBOTS", True)
    use_sitemap = _env_bool("USE_SITEMAP", True)
    include_subdomains = _env_bool("INCLUDE_SUBDOMAINS", False)
    with_ai_summary = _env_bool("WITH_AI_SUMMARY", True)
    check_external_links = _env_bool("CHECK_EXTERNAL_LINKS", False)

    # UAT/staging Basic Auth. Read from GitHub Secrets rather than workflow
    # inputs so credentials are not exposed in a public Actions run.
    basic_auth_username = _env_text("BASIC_AUTH_USERNAME") or None
    basic_auth_password = _env_text("BASIC_AUTH_PASSWORD") or None

    verify_ssl = _env_bool("VERIFY_SSL", True)
    custom_user_agent = _env_text("CUSTOM_USER_AGENT") or None

    custom_personas_raw = _env_text("CUSTOM_PERSONAS")
    custom_personas = (
        parse_custom_personas(custom_personas_raw)
        if custom_personas_raw
        else []
    )

    include_url_patterns = [
        p.strip()
        for p in os.environ.get("INCLUDE_URL_PATTERNS", "").split(",")
        if p.strip()
    ]
    exclude_url_patterns = [
        p.strip()
        for p in os.environ.get("EXCLUDE_URL_PATTERNS", "").split(",")
        if p.strip()
    ]

    client_stated_page_count = _parse_optional_int("CLIENT_STATED_PAGE_COUNT")

    browser_max_pages = _env_int(
        "BROWSER_MAX_PAGES",
        mode_default_browser_pages,
        minimum=0,
        maximum=max_pages,
    )
    browser_timeout = _env_float(
        "BROWSER_TIMEOUT",
        25.0,
        minimum=5.0,
        maximum=120.0,
    )

    # Keep browser rendering disabled only when the caller explicitly asks for
    # it. This allows local HTTP-only troubleshooting while keeping the cloud
    # workflow JS-enabled by default.
    if not render_js:
        browser_max_pages = 0

    config = CrawlConfig(
        start_url=start_url,
        max_pages=max_pages,
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
        include_url_patterns=include_url_patterns,
        exclude_url_patterns=exclude_url_patterns,
        client_stated_page_count=client_stated_page_count,
        render_js=render_js,
        browser_max_pages=browser_max_pages,
        browser_timeout=browser_timeout,
    )
    progress = CrawlProgress()

    print(
        f"[audit] mode={mode} start={start_url} "
        f"max_pages={config.max_pages} max_depth={config.max_depth} "
        f"concurrency={config.concurrency} render_js={config.render_js} "
        f"browser_max_pages={config.browser_max_pages} "
        f"browser_timeout={config.browser_timeout}s",
        flush=True,
    )

    last_logged = -1

    async def on_progress() -> None:
        nonlocal last_logged
        if progress.pages_crawled != last_logged:
            last_logged = progress.pages_crawled
            _print_progress_line(progress)

    try:
        audit_data = await run_audit(
            config,
            progress,
            on_progress=on_progress,
            with_ai_summary=with_ai_summary,
            custom_personas=custom_personas,
        )
    except Exception as exc:
        print(f"ERROR: audit failed: {exc}", file=sys.stderr)
        return 1

    audit_data["audit_id"] = "latest"
    audit_data.setdefault("meta", {})["audit_mode"] = mode
    audit_data["meta"]["render_js_requested"] = config.render_js
    audit_data["meta"]["browser_max_pages"] = config.browser_max_pages

    if progress.status == AuditStatus.ERROR:
        print("ERROR: audit failed", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    html = render_html_report(audit_data)

    # GitHub Pages serves a project under /<repo-name>/, so absolute /static
    # and /api URLs do not work. Inline CSS and Chart.js and use relative
    # export links so report.html is self-contained.
    style_css = (
        Path(__file__).parent / "static" / "style.css"
    ).read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="/static/style.css">',
        f"<style>\n{style_css}\n</style>",
    )

    chartjs_path = Path(__file__).parent / "static" / "chart.umd.js"
    if chartjs_path.exists():
        chartjs = chartjs_path.read_text(encoding="utf-8")
        html = html.replace(
            '<script src="/static/chart.umd.js"></script>',
            f"<script>\n{chartjs}\n</script>",
        )

    html = html.replace("/api/audits/latest/export/json", "exports/audit.json")
    html = html.replace("/api/audits/latest/export/csv", "exports/audit.csv")
    html = html.replace("/api/audits/latest/export/xlsx", "exports/audit.xlsx")

    (OUT_DIR / "report.html").write_text(html, encoding="utf-8")

    (EXPORTS_DIR / "audit.json").write_bytes(export_json(audit_data))
    (EXPORTS_DIR / "audit.csv").write_bytes(export_csv(audit_data))
    (EXPORTS_DIR / "audit.xlsx").write_bytes(export_xlsx(audit_data))

    # Rebuild the persistent launcher on every deployment because docs/ is
    # generated output and is not committed to the repository.
    launcher_path = Path(__file__).parent / "templates" / "launcher.html"
    if launcher_path.exists():
        launcher_html = launcher_path.read_text(encoding="utf-8")
        launcher_html = launcher_html.replace("{{ inline_css }}", style_css)
        (OUT_DIR / "index.html").write_text(launcher_html, encoding="utf-8")

    meta = audit_data.get("meta", {})
    scoring = audit_data.get("scoring", {})
    print(
        f"[audit] done — pages_crawled={meta.get('pages_crawled', progress.pages_crawled)} "
        f"UX maturity={scoring.get('ux_maturity_score', 'n/a')} "
        f"({scoring.get('ux_maturity_band', 'n/a')})",
        flush=True,
    )
    print(
        f"[audit] wrote {OUT_DIR / 'report.html'} and refreshed "
        f"{OUT_DIR / 'index.html'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
