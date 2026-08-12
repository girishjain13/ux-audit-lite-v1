"""Async breadth-first website crawler with robots.txt / sitemap support,
canonicalization, redirect handling, and concurrency control.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from analyzers.feature_matrix import scan_page_features
from analyzers.integrations import match_integrations
from analyzers.keywords import bigrams, tokenize
from models import AuditStatus, CrawlProgress, PageRecord
from robots import RobotsInfo

DEFAULT_HEADERS = {
    "User-Agent": "IA-UX-AuditBot/1.0 (+https://example.com/bot; respects robots.txt)"
}

SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".rar",
    ".mp4", ".mp3", ".avi", ".mov", ".css", ".js", ".xml", ".ico", ".woff",
    ".woff2", ".ttf", ".eot", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


def normalize_url(url: str) -> str:
    """Strip fragments and ensure an empty path becomes '/'.

    Deliberately does NOT strip trailing slashes: many servers 301-redirect
    a no-slash directory URL ("/about") to its slash form ("/about/"), and
    httpx follows that automatically. If we stripped the slash here, the
    pre-fetch (queued) URL and the post-redirect (crawled/stored) URL would
    end up as two different strings, which silently breaks link-graph edges
    between them. Leaving both forms distinct is safer than mismatching.
    """
    url, _frag = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path or "/"
    normalized = parsed._replace(path=path, query=parsed.query)
    return normalized.geturl()


def same_site(url: str, root_netloc: str, include_subdomains: bool) -> bool:
    netloc = urlparse(url).netloc
    if include_subdomains:
        return netloc == root_netloc or netloc.endswith("." + root_netloc.split(":")[0])
    return netloc == root_netloc


@dataclass
class CrawlConfig:
    start_url: str
    max_pages: int = 200
    max_depth: int = 12
    concurrency: int = 8
    request_timeout: float = 15.0
    include_subdomains: bool = False
    respect_robots: bool = True
    use_sitemap: bool = True
    auth_headers: Optional[dict] = None
    check_external_links: bool = False
    external_link_cap: int = 100
    # HTTP Basic Auth — for UAT/staging sites gated behind a password wall
    # (the common case: nginx/.htaccess/Vercel/Cloudflare-style site-wide
    # Basic Auth, not a form-based application login, which varies too much
    # per site to support generically)
    basic_auth_username: Optional[str] = None
    basic_auth_password: Optional[str] = None
    # UAT/staging environments frequently run self-signed or internal-CA
    # certificates — set False to skip verification for exactly those cases.
    # Never disable this for a real production audit.
    verify_ssl: bool = True
    # Overrides the default self-identifying User-Agent. Useful when a site
    # you have permission to audit runs a WAF that blocks anything
    # self-identifying as a generic bot — give it something identifiable
    # (e.g. "AcmeCorp-InternalUXAudit/1.0 (contact: security@acme.com)")
    # and have the site's security team allowlist that exact string. This
    # is deliberately NOT a way to impersonate a real browser to evade
    # detection — it's how legitimate internal scanning tools get through
    # enterprise bot management: transparently, with the site owner's
    # cooperation, not by disguise.
    custom_user_agent: Optional[str] = None


class AsyncCrawler:
    """Breadth-first crawler. Produces a dict[url] -> PageRecord and an
    internal-link edge list suitable for building a NetworkX graph.
    """

    def __init__(self, config: CrawlConfig, progress: CrawlProgress):
        self.config = config
        self.progress = progress
        self.pages: dict[str, PageRecord] = {}
        self.edges: list[tuple[str, str]] = []  # (from_url, to_url) internal links
        self._seen: set[str] = set()
        # maps a pre-redirect requested URL -> the final URL it resolved to.
        # Needed because a link's href is recorded (and queued) BEFORE we
        # know whether the target redirects — if it does, the page ends up
        # stored under its final URL while the edge still points at the
        # original, pre-redirect one. Left unresolved, every such edge
        # silently fails the `dst in pages` check used for orphan/click-depth
        # analysis, making perfectly reachable pages look orphaned. Resolved
        # once, in bulk, at the end of crawl() — see _resolve_redirect().
        self.redirect_map: dict[str, str] = {}
        self._root_netloc = urlparse(config.start_url).netloc
        # site-wide keyword/phrase counters, built incrementally per page
        # (see analyzers/keywords.py) rather than re-scanning stored text later
        self.global_word_counts: Counter = Counter()
        self.global_bigram_counts: Counter = Counter()
        self.global_doc_freq: Counter = Counter()
        # third-party integration / script inventory, built the same way —
        # incrementally per page, so we never need to hold script content
        # around after a page is parsed
        self.integration_hits: dict = {}  # integration name -> set of page urls
        self.unrecognized_script_domains: Counter = Counter()
        self.all_external_scripts: set = set()
        # bounded external-link health check (see analyzers/link_health.py)
        self.external_link_targets: dict = {}  # url -> set of internal pages that link to it
        # website feature matrix (see analyzers/feature_matrix.py)
        self.feature_hits: dict = {}  # feature id -> set of page urls

    async def crawl(self, on_progress: Optional[Callable[[], Awaitable[None]]] = None):
        cfg = self.config
        self.progress.status = AuditStatus.CRAWLING
        self.progress.max_pages = cfg.max_pages
        headers = dict(DEFAULT_HEADERS)
        if cfg.custom_user_agent:
            headers["User-Agent"] = cfg.custom_user_agent
        if cfg.auth_headers:
            headers.update(cfg.auth_headers)

        limits = httpx.Limits(max_connections=cfg.concurrency, max_keepalive_connections=cfg.concurrency)
        basic_auth = None
        if cfg.basic_auth_username is not None:
            basic_auth = httpx.BasicAuth(cfg.basic_auth_username, cfg.basic_auth_password or "")
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=cfg.request_timeout, limits=limits,
            auth=basic_auth, verify=cfg.verify_ssl,
        ) as client:
            robots = RobotsInfo(cfg.start_url)
            if cfg.respect_robots:
                await robots.load(client)

            queue: deque[tuple[str, int]] = deque()
            start = normalize_url(cfg.start_url)
            queue.append((start, 0))
            self._seen.add(start)

            # Seed extra URLs from sitemap.xml so IA analysis reflects the
            # declared site structure, not just what's link-reachable.
            if cfg.use_sitemap:
                try:
                    sitemap_urls = await robots.discover_sitemap_urls(client, cap=cfg.max_pages * 2)
                    for u in sitemap_urls:
                        nu = normalize_url(u)
                        if nu not in self._seen and same_site(nu, self._root_netloc, cfg.include_subdomains):
                            self._seen.add(nu)
                            queue.append((nu, 1))
                except Exception:
                    pass

            sem = asyncio.Semaphore(cfg.concurrency)
            t0 = time.monotonic()

            async def worker(url: str, depth: int):
                async with sem:
                    await self._fetch_and_parse(client, robots, url, depth, queue)
                    self.progress.pages_crawled = len(self.pages)
                    self.progress.pages_queued = len(queue)
                    self.progress.current_url = url
                    elapsed = time.monotonic() - t0
                    self.progress.elapsed_seconds = elapsed
                    n = max(self.progress.pages_crawled, 1)
                    self.progress.avg_page_seconds = elapsed / n
                    remaining = min(len(queue), cfg.max_pages - self.progress.pages_crawled)
                    self.progress.eta_seconds = max(remaining, 0) * self.progress.avg_page_seconds
                    if on_progress:
                        await on_progress()

            while queue and len(self.pages) < cfg.max_pages:
                # launch a batch up to available concurrency
                batch = []
                while queue and len(batch) < cfg.concurrency and len(self.pages) + len(batch) < cfg.max_pages:
                    url, depth = queue.popleft()
                    if depth > cfg.max_depth:
                        continue
                    batch.append(worker(url, depth))
                if not batch:
                    break
                await asyncio.gather(*batch)

        self.progress.status = AuditStatus.ANALYZING
        self.edges = self._resolve_edges(self.edges)
        return self.pages, self.edges

    def _resolve_redirect(self, url: str, max_hops: int = 5) -> str:
        """Follows redirect_map to the final URL a link actually landed on,
        guarding against a redirect loop with a hop limit.
        """
        seen = set()
        current = url
        for _ in range(max_hops):
            nxt = self.redirect_map.get(current)
            if nxt is None or nxt == current or nxt in seen:
                return current
            seen.add(current)
            current = nxt
        return current

    def _resolve_edges(self, edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Rewrites every edge's destination through the redirect map, so a
        link that was recorded against its pre-redirect URL still correctly
        points at whichever page it actually landed on (see redirect_map's
        docstring in __init__ for why this matters for orphan detection).
        Edges whose resolved destination still isn't a crawled page are
        genuinely broken/out-of-scope links, not a resolution failure —
        they're left as-is and simply won't match a page later.
        """
        return [(src, self._resolve_redirect(dst)) for src, dst in edges]

    async def _fetch_and_parse(
        self,
        client: httpx.AsyncClient,
        robots: RobotsInfo,
        url: str,
        depth: int,
        queue: deque,
    ):
        record = PageRecord(url=url, depth=depth, path_depth=self._path_depth(url))
        if self.config.respect_robots and not robots.can_fetch(url):
            record.error = "blocked_by_robots_txt"
            self.pages[url] = record
            self.progress.note(f"Skipped (robots.txt disallow): {url}")
            return

        t0 = time.monotonic()

        try:
            resp = await client.get(url)
            record.fetch_ms = (time.monotonic() - t0) * 1000
            record.status_code = resp.status_code
            if str(resp.url) != url:
                record.redirected_from = url
                record.url = str(resp.url)
                self.redirect_map[url] = str(resp.url)
            content_type = resp.headers.get("content-type", "")
            record.content_type = content_type

            if resp.status_code >= 400:
                record.error = f"http_{resp.status_code}"
                self.pages[record.url] = record
                self.progress.pages_errored += 1
                self.progress.note(f"Error {resp.status_code}: {url}")
                return

            if "text/html" not in content_type:
                self.pages[record.url] = record
                return

            self._parse_html(record, resp.text, queue, depth)
            self.pages[record.url] = record

        except httpx.HTTPError as exc:
            record.error = f"request_failed: {exc.__class__.__name__}"
            record.fetch_ms = (time.monotonic() - t0) * 1000
            self.pages[url] = record
            self.progress.pages_errored += 1
            self.progress.note(f"Failed: {url} ({exc.__class__.__name__})")

    def _path_depth(self, url: str) -> int:
        path = urlparse(url).path
        return len([seg for seg in path.split("/") if seg])

    def _parse_html(self, record: PageRecord, html: str, queue: deque, depth: int):
        # feature matrix scan happens on the raw HTML before any parsing —
        # cheap regex/substring checks, same "scan once, discard text"
        # pattern as keyword/integration tallying
        for feature_id in scan_page_features(record.url, html):
            self.feature_hits.setdefault(feature_id, set()).add(record.url)

        # html.parser (stdlib) instead of lxml — no compiled C extension to
        # build, which has repeatedly been a source of platform-specific
        # deployment failures (missing libxml2/libxslt dev headers, no
        # prebuilt wheel for a given Python version, etc.). Slightly more
        # lenient on malformed HTML and marginally slower than lxml, which
        # is an acceptable trade for "installs reliably everywhere."
        soup = BeautifulSoup(html, "html.parser")

        # --- metadata ---
        if soup.title and soup.title.string:
            record.title = soup.title.string.strip()
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            record.meta_description = meta_desc["content"].strip()
        canonical = soup.find("link", attrs={"rel": "canonical"})
        if canonical and canonical.get("href"):
            record.canonical = canonical["href"]
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            record.lang = html_tag["lang"]

        for og in soup.find_all("meta", attrs={"property": lambda p: p and p.startswith("og:")}):
            record.og_tags[og["property"]] = og.get("content", "")

        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            record.has_schema_org = True
            txt = (script.string or "")[:200]
            record.schema_types.append(txt)

        # --- headings ---
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            record.heading_sequence.append(tag.name)
            if tag.name == "h1":
                record.h1_list.append(tag.get_text(strip=True))

        # --- content ---
        body = soup.find("body")
        text = body.get_text(separator=" ", strip=True) if body else soup.get_text(separator=" ", strip=True)
        words = text.split()
        record.word_count = len(words)
        record.reading_time_seconds = int(len(words) / 3.5)  # ~200 wpm
        record.text_hash = hashlib.sha1(" ".join(words).encode("utf-8", "ignore")).hexdigest()
        record.is_thin_content = record.word_count < 150

        # site-wide keyword/phrase tallies
        tokens = tokenize(text)
        if tokens:
            self.global_word_counts.update(tokens)
            self.global_doc_freq.update(set(tokens))
            self.global_bigram_counts.update(bigrams(tokens))

        # scroll-depth proxy: estimate rendered height from block-level element count
        block_tags = soup.find_all(["p", "div", "section", "article", "li", "img", "h1", "h2", "h3"])
        record.rendered_height_estimate = 80 + len(block_tags) * 45  # rough px estimate per block

        # --- accessibility ---
        images = soup.find_all("img")
        record.images_total = len(images)
        record.images_missing_alt = sum(1 for img in images if not img.get("alt", "").strip())
        forms = soup.find_all("form")
        record.forms_total = len(forms)
        missing_labels = 0
        for form in forms:
            for inp in form.find_all(["input", "textarea", "select"]):
                itype = inp.get("type", "text")
                if itype in ("hidden", "submit", "button"):
                    continue
                has_label = bool(inp.get("aria-label")) or bool(inp.get("id") and soup.find("label", attrs={"for": inp.get("id")}))
                if not has_label:
                    missing_labels += 1
        record.inputs_missing_label = missing_labels
        record.aria_landmark_count = len(soup.find_all(attrs={"role": True}))

        # --- links ---
        base = record.url
        internal, external = 0, 0
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            absolute = urljoin(base, href)
            if any(absolute.lower().split("?")[0].endswith(ext) for ext in SKIP_EXTENSIONS):
                continue
            normalized = normalize_url(absolute)
            if same_site(normalized, self._root_netloc, self.config.include_subdomains):
                internal += 1
                record.internal_links_out.append(normalized)
                self.edges.append((record.url, normalized))
                if normalized not in self._seen and len(self._seen) < self.config.max_pages * 3:
                    self._seen.add(normalized)
                    queue.append((normalized, depth + 1))
            else:
                external += 1
                if self.config.check_external_links and len(self.external_link_targets) < self.config.external_link_cap:
                    ext_normalized = normalize_url(absolute)
                    self.external_link_targets.setdefault(ext_normalized, set()).add(record.url)
        record.external_links_out_count = external

        # --- scripts / third-party integrations ---
        scripts = soup.find_all("script")
        record.script_count = len(scripts)
        ext_count = 0
        for s in scripts:
            src = s.get("src")
            if src:
                ext_count += 1
                abs_src = urljoin(base, src)
                self.all_external_scripts.add(abs_src)
                matched = match_integrations(abs_src)
                if matched:
                    for name in matched:
                        self.integration_hits.setdefault(name, set()).add(record.url)
                else:
                    self.unrecognized_script_domains[urlparse(abs_src).netloc] += 1
            else:
                txt = s.string or ""
                if txt.strip():
                    for name in match_integrations("", txt):
                        self.integration_hits.setdefault(name, set()).add(record.url)
        record.external_script_count = ext_count
