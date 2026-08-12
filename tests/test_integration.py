"""End-to-end test: spins up the bundled sample_site on a local HTTP
server, runs the real crawler + all analyzers against it, and checks
the results are structurally and numerically sane. This is the fixture
also used to manually validate the tool during development.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from analyzers import accessibility, content, ia, scoring, seo
from crawler import AsyncCrawler, CrawlConfig
from models import CrawlProgress

SAMPLE_SITE_DIR = Path(__file__).parent.parent / "sample_site"


SAMPLE_SITE_PORT = 8099  # must match the absolute host:port baked into sample_site/sitemap.xml


@pytest.fixture(scope="module")
def sample_server():
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(SAMPLE_SITE_DIR), **kw)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", SAMPLE_SITE_PORT), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://localhost:{SAMPLE_SITE_PORT}/"
        httpd.shutdown()


@pytest.mark.asyncio
async def test_crawl_and_analyze(sample_server):
    config = CrawlConfig(start_url=sample_server, max_pages=50, concurrency=5, use_sitemap=True)
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()

    # sanity: crawler found the known fixture pages
    assert len(pages) >= 9
    assert any(u.rstrip("/").endswith("about") for u in pages)
    assert any("orphan.html" in u for u in pages), "orphan page should be discovered via sitemap.xml"

    start_url = sample_server.rstrip("/") + "/" if not sample_server.endswith("/") else sample_server
    # normalize like the app does
    from crawler import normalize_url
    start_norm = normalize_url(sample_server)

    ia_results = ia.run_ia_analysis(pages, edges, start_norm)
    assert ia_results["orphan_page_count"] == 1
    assert any("orphan.html" in u for u in ia_results["orphan_pages"])
    assert ia_results["max_click_depth"] >= 2

    content_results = content.run_content_analysis(pages)
    assert content_results["thin_content_count"] > 0  # fixture pages are short
    assert content_results["duplicate_content_page_count"] >= 1  # widget-pro/widget-lite are identical

    a11y_results = accessibility.run_accessibility_analysis(pages)
    assert a11y_results["images_missing_alt"] >= 1
    assert a11y_results["inputs_missing_label"] >= 1  # contact form has unlabeled inputs

    seo_results = seo.run_seo_analysis(pages)
    # login.html and pricing.html are intentionally broken links in the
    # fixture (added to validate feature-matrix/journey-map detection
    # against real link text without needing real pages behind them)
    broken_link_count = 2
    assert seo_results["pages_ok"] == len(pages) - broken_link_count
    assert len(seo_results["title_issues"]) > 0

    score_results = scoring.run_scoring(ia_results, content_results, a11y_results, seo_results, len(pages))
    for key in ("ia_health_score", "content_quality_score", "accessibility_score", "seo_score", "ux_maturity_score"):
        assert 0 <= score_results[key] <= 100
    assert score_results["action_plan"], "expected at least one action item given fixture issues"

    from analyzers import feature_matrix, journey

    fm_results = feature_matrix.run_feature_matrix(crawler.feature_hits, [])
    detected_ids = {row["id"] for row in fm_results["rows"] if row["present"]}
    for expected in ("search", "login", "newsletter", "faq", "pricing", "blog", "contact_form"):
        assert expected in detected_ids, f"expected '{expected}' to be detected in the feature matrix fixture"

    jm = journey.build_journey_map(pages, ia_results["click_depths"])
    prospective = next(j for j in jm["journeys"] if j["id"] == "prospective_customer")
    stage_status = {s["id"]: s["present"] for s in prospective["stages"]}
    assert stage_status["awareness"] is True   # blog
    assert stage_status["consideration"] is True  # about
    assert stage_status["action"] is True      # contact form
    assert len(jm["journeys"]) == 4  # prospective customer, job seeker, existing customer, press/investor


def test_normalize_url_preserves_trailing_slash_forms():
    from crawler import normalize_url
    assert normalize_url("http://x.test/about/") == "http://x.test/about/"
    assert normalize_url("http://x.test/about") == "http://x.test/about"
    assert normalize_url("http://x.test/about#section") == "http://x.test/about"
    assert normalize_url("http://x.test") == "http://x.test/"


REDIRECT_SITE_DIR = Path(__file__).parent / "fixtures" / "redirect_site"
REDIRECT_SITE_PORT = 8124


@pytest.fixture(scope="module")
def redirect_server():
    """Serves fixtures/redirect_site, 301-redirecting /about -> /about/ —
    a linked page that only exists at a different URL than the one it was
    linked from. This is the exact scenario that silently broke orphan
    detection (see crawler.py's redirect_map): the link graph recorded the
    edge against the pre-redirect URL, which never matched the page's
    actual (post-redirect) key, so a perfectly reachable page looked
    orphaned.
    """
    class RedirectHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(REDIRECT_SITE_DIR), **kwargs)

        def do_GET(self):
            if self.path == "/about":
                self.send_response(301)
                self.send_header("Location", "/about/")
                self.end_headers()
                return
            super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", REDIRECT_SITE_PORT), RedirectHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{REDIRECT_SITE_PORT}/"
        httpd.shutdown()


@pytest.mark.asyncio
async def test_redirected_internal_links_are_not_orphaned(redirect_server):
    from crawler import normalize_url

    config = CrawlConfig(start_url=redirect_server, max_pages=20, concurrency=3, use_sitemap=False, respect_robots=False)
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()

    about_url = next(u for u in pages if u.rstrip("/").endswith("about"))
    # confirm the redirect actually happened, i.e. this test is exercising
    # the scenario it claims to — if this ever stops being true because the
    # fixture changed, the rest of the assertions would be vacuous
    assert crawler.redirect_map, "expected the fixture's /about -> /about/ redirect to be recorded"

    start_norm = normalize_url(redirect_server)
    ia_results = ia.run_ia_analysis(pages, edges, start_norm)
    assert ia_results["orphan_page_count"] == 0, "the redirected /about/ page should be reachable, not orphaned"
    assert about_url not in ia_results["orphan_pages"]


BASICAUTH_SITE_DIR = Path(__file__).parent / "fixtures" / "basicauth_site"
BASICAUTH_SITE_PORT = 8126
BASICAUTH_USER = "uatuser"
BASICAUTH_PASS = "uatpass123"


@pytest.fixture(scope="module")
def basicauth_server():
    """Serves fixtures/basicauth_site behind real HTTP Basic Auth — the
    common way UAT/staging environments are gated (nginx, .htaccess,
    Vercel, Cloudflare Access all implement this the same standard way).
    """
    import base64

    valid = base64.b64encode(f"{BASICAUTH_USER}:{BASICAUTH_PASS}".encode()).decode()

    class AuthHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(BASICAUTH_SITE_DIR), **kwargs)

        def do_GET(self):
            if self.headers.get("Authorization", "") != f"Basic {valid}":
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="UAT"')
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", BASICAUTH_SITE_PORT), AuthHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{BASICAUTH_SITE_PORT}/"
        httpd.shutdown()


@pytest.mark.asyncio
async def test_basic_auth_required_for_uat_site(basicauth_server):
    # no credentials -> every page should fail with 401, nothing crawled successfully
    config = CrawlConfig(start_url=basicauth_server, max_pages=10, concurrency=2, use_sitemap=False, respect_robots=False)
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()
    assert all(rec.status_code == 401 for rec in pages.values())


@pytest.mark.asyncio
async def test_basic_auth_with_correct_credentials_succeeds(basicauth_server):
    config = CrawlConfig(
        start_url=basicauth_server, max_pages=10, concurrency=2, use_sitemap=False, respect_robots=False,
        basic_auth_username=BASICAUTH_USER, basic_auth_password=BASICAUTH_PASS,
    )
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()
    assert len(pages) >= 2  # homepage + the linked about page
    assert all(rec.status_code == 200 for rec in pages.values())


@pytest.mark.asyncio
async def test_basic_auth_with_wrong_credentials_fails(basicauth_server):
    config = CrawlConfig(
        start_url=basicauth_server, max_pages=10, concurrency=2, use_sitemap=False, respect_robots=False,
        basic_auth_username="wronguser", basic_auth_password="wrongpass",
    )
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()
    assert all(rec.status_code == 401 for rec in pages.values())


BLOCKED_SITE_DIR = Path(__file__).parent / "fixtures" / "blocked_site"
BLOCKED_SITE_PORT = 8128


@pytest.fixture(scope="module")
def blocked_server():
    """Serves fixtures/blocked_site, whose robots.txt disallows everything —
    reproduces a real user-reported symptom: a crawl that's blocked entirely
    still needs to surface a clear warning instead of a misleadingly
    perfect-looking score (see audit_engine.py's crawl_warning).
    """
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(BLOCKED_SITE_DIR), **kw)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", BLOCKED_SITE_PORT), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{BLOCKED_SITE_PORT}/"
        httpd.shutdown()


@pytest.mark.asyncio
async def test_fully_blocked_crawl_surfaces_a_warning(blocked_server):
    from audit_engine import run_audit

    config = CrawlConfig(start_url=blocked_server, max_pages=20, concurrency=3, respect_robots=True, use_sitemap=False)
    progress = CrawlProgress()
    data = await run_audit(config, progress, with_ai_summary=False)

    assert data["crawl_warning"] is not None
    assert "blocked_by_robots_txt" in data["crawl_warning"]


@pytest.mark.asyncio
async def test_normal_crawl_has_no_warning(sample_server):
    from audit_engine import run_audit

    config = CrawlConfig(start_url=sample_server, max_pages=50, concurrency=5, use_sitemap=True)
    progress = CrawlProgress()
    data = await run_audit(config, progress, with_ai_summary=False)

    assert data["crawl_warning"] is None


WAF_SITE_DIR = Path(__file__).parent / "fixtures" / "waf_site"
WAF_SITE_PORT = 8131
WAF_ALLOWED_UA = "AcmeCorp-InternalUXAudit/1.0 (contact: security@acme.com)"


@pytest.fixture(scope="module")
def waf_server():
    """Simulates a site whose WAF blocks any User-Agent containing "bot"
    except one specific allowlisted string — the exact real-world scenario
    a user hit auditing a live enterprise site, and the reason
    CrawlConfig.custom_user_agent exists.
    """
    class WAFHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WAF_SITE_DIR), **kwargs)

        def do_GET(self):
            ua = self.headers.get("User-Agent", "")
            if "bot" in ua.lower() and ua != WAF_ALLOWED_UA:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden - bot detected")
                return
            super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", WAF_SITE_PORT), WAFHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{WAF_SITE_PORT}/"
        httpd.shutdown()


@pytest.mark.asyncio
async def test_default_user_agent_gets_blocked_by_waf(waf_server):
    config = CrawlConfig(start_url=waf_server, max_pages=5, concurrency=2, use_sitemap=False, respect_robots=False)
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()
    assert all(rec.status_code == 403 for rec in pages.values())


@pytest.mark.asyncio
async def test_allowlisted_custom_user_agent_gets_through(waf_server):
    config = CrawlConfig(
        start_url=waf_server, max_pages=5, concurrency=2, use_sitemap=False, respect_robots=False,
        custom_user_agent=WAF_ALLOWED_UA,
    )
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()
    assert all(rec.status_code == 200 for rec in pages.values())


@pytest.mark.asyncio
async def test_unlisted_custom_user_agent_still_blocked(waf_server):
    config = CrawlConfig(
        start_url=waf_server, max_pages=5, concurrency=2, use_sitemap=False, respect_robots=False,
        custom_user_agent="SomeOtherBot/1.0",
    )
    progress = CrawlProgress()
    crawler = AsyncCrawler(config, progress)
    pages, edges = await crawler.crawl()
    assert all(rec.status_code == 403 for rec in pages.values())
