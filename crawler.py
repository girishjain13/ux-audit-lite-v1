"""Async breadth-first website crawler with robots.txt / sitemap support,
canonicalization, redirect handling, and concurrency control.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
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

    # URL filtering — only crawl URLs matching at least one include pattern
    # (if any given), and never crawl URLs matching an exclude pattern.
    # Patterns are plain substrings, not regex — simpler to reason about
    # for someone typing a comma-separated list into a launcher field, at
    # the cost of not supporting real wildcards/regex. Applied to every
    # URL before it's queued, whether discovered via in-page links or
    # sitemap seeding.
    include_url_patterns: list = field(default_factory=list)
    exclude_url_patterns: list = field(default_factory=list)

    # What the client told you the site's page count is, before crawling —
    # purely informational, compared against the actual crawled count to
    # produce a variance report (see audit_engine.py). None means "not
    # provided," not "zero."
    client_stated_page_count: Optional[int] = None


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
        # recurring UI components (nav bars, cards, ctas, forms, etc.) —
        # see analyzers/components.py — same incremental site-wide pattern
        self.component_hits: dict = {}  # component signature -> set of page urls
        # sitemap.xml's <lastmod> per URL, when present — used as a
        # freshness fallback for pages whose HTTP response doesn't carry
        # its own Last-Modified header (see analyzers/freshness.py)
        self.sitemap_lastmod: dict = {}
        # media/asset inventory (see analyzers/media.py) — image src
        # domains (to spot off-DAM hosting), video embeds, document/
        # download links by file type
        self.image_domain_counts: Counter = Counter()
        self.video_embed_count: int = 0
        self.document_extension_counts: Counter = Counter()
        self.document_link_examples: list = []
        # first privacy-policy-looking link found anywhere in the crawl —
        # used as a site-wide proxy (most sites share one footer) for
        # "does this site have a privacy policy at all" (see analyzers/risk.py)
        self.privacy_policy_url_found: Optional[str] = None
        # tech fingerprinting signals (see analyzers/tech_fingerprint.py)
        self.tech_signals: Counter = Counter()
        # set at the end of crawl() — see there for why these matter for
        # detecting a truncated (site bigger than max_pages) crawl
        self.queue_remaining_at_stop = 0
        self.total_urls_discovered = 0

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
                    sitemap_lastmod = await robots.discover_sitemap_urls(client, cap=cfg.max_pages * 2)
                    for u, lastmod in sitemap_lastmod.items():
                        nu = normalize_url(u)
                        if lastmod:
                            self.sitemap_lastmod[nu] = lastmod
                        if nu not in self._seen and same_site(nu, self._root_netloc, cfg.include_subdomains):
                            self._seen.add(nu)
                            if self._url_allowed(nu):
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

            # Whatever's still waiting in the queue once we stop is real
            # evidence the site has more pages than max_pages allowed us to
            # reach — as opposed to genuinely running out of new URLs to
            # follow. Surfaced later as a truncation notice rather than
            # silently reporting on an incomplete slice of the site as if
            # it were the whole thing.
            self.queue_remaining_at_stop = len(queue)
            self.total_urls_discovered = len(self._seen)

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
            if resp.history:
                record.redirect_chain_length = len(resp.history)
                record.redirect_chain = [str(r.url) for r in resp.history] + [str(resp.url)]
            if str(resp.url) != url:
                record.redirected_from = url
                record.url = str(resp.url)
                self.redirect_map[url] = str(resp.url)
            content_type = resp.headers.get("content-type", "")
            record.content_type = content_type
            record.last_modified = resp.headers.get("last-modified") or self.sitemap_lastmod.get(record.url) or self.sitemap_lastmod.get(url)
            self._detect_tech_from_headers(resp.headers)

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

        except httpx.TooManyRedirects:
            record.error = "redirect_loop"
            record.fetch_ms = (time.monotonic() - t0) * 1000
            self.pages[url] = record
            self.progress.pages_errored += 1
            self.progress.note(f"Redirect loop: {url}")
        except httpx.HTTPError as exc:
            record.error = f"request_failed: {exc.__class__.__name__}"
            record.fetch_ms = (time.monotonic() - t0) * 1000
            self.pages[url] = record
            self.progress.pages_errored += 1
            self.progress.note(f"Failed: {url} ({exc.__class__.__name__})")

    def _url_allowed(self, url: str) -> bool:
        """Include/exclude URL-pattern filtering — plain substring match,
        checked against the full URL. Exclude wins if a URL somehow
        matches both. An empty include list means "no restriction" (every
        URL passes that check); an empty exclude list means "nothing is
        excluded." The start URL itself is never filtered — the person
        explicitly asked to crawl from there.
        """
        cfg = self.config
        if cfg.exclude_url_patterns and any(p in url for p in cfg.exclude_url_patterns if p):
            return False
        if cfg.include_url_patterns and not any(p in url for p in cfg.include_url_patterns if p):
            return False
        return True

    def _path_depth(self, url: str) -> int:
        path = urlparse(url).path
        return len([seg for seg in path.split("/") if seg])

    @staticmethod
    def _estimate_syllables(word: str) -> int:
        """Standard vowel-group heuristic — not linguistically perfect,
        but the same approximation Flesch-Kincaid tooling has always used;
        good enough for a directional readability read, not phonetic
        transcription."""
        word = word.lower().strip(".,!?;:\"'()")
        if not word:
            return 0
        groups = re.findall(r"[aeiouy]+", word)
        count = len(groups)
        if word.endswith("e") and not word.endswith("le") and count > 1:
            count -= 1  # silent trailing e
        return max(count, 1)

    def _flesch_reading_ease(self, text: str, words: list) -> float:
        """0-100 Flesch Reading Ease score — higher means easier to read.
        Approximates sentence count via terminal punctuation, which is
        imprecise for content full of headings/labels/short fragments (as
        much web copy is) — treat this as a directional readability read
        per page, not a precise linguistic measurement."""
        sentences = max(len(re.findall(r"[.!?]+", text)), 1)
        n_words = max(len(words), 1)
        syllables = sum(self._estimate_syllables(w) for w in words)
        score = 206.835 - 1.015 * (n_words / sentences) - 84.6 * (syllables / n_words)
        return round(max(0.0, min(100.0, score)), 1)

    def _structural_fingerprint(self, soup, max_depth: int = 6) -> str:
        """Hashes a normalized skeleton of the page's HTML structure (tags +
        a few of their classes, ignoring actual text/content) into a short
        ID — pages sharing an ID are very likely built from the same
        template/layout, regardless of what URL pattern or title they have.

        Two things make this robust rather than brittle:
        - Only the first few class names (sorted) count per element, so
          utility-class frameworks (Tailwind etc.) that generate a slightly
          different class soup per element don't defeat matching.
        - Consecutive sibling elements with an identical shape (e.g. 5 vs
          50 product cards in a grid, blog post list items) collapse into
          one representative — so template detection isn't thrown off by
          how much content happens to be on a given page.

        This is a heuristic, not a guarantee — two genuinely different
        templates that happen to use very similar generic markup could
        collide, and two pages built from the same template but with
        significantly different optional sections could split apart. Good
        enough to spot real structural patterns/outliers at a glance,
        not a substitute for actually looking at the pages.
        """
        body = soup.find("body")
        root = body if body else soup
        skeleton = self._normalize_node(root, max_depth, 0)
        return hashlib.sha1(skeleton.encode("utf-8", "ignore")).hexdigest()[:12]

    def _normalize_node(self, node, max_depth: int, depth: int) -> str:
        if depth > max_depth:
            return ""
        parts = []
        prev_sig = None
        run_length = 0

        def flush():
            if prev_sig is not None:
                parts.append(prev_sig if run_length <= 1 else prev_sig + "*")

        for child in node.find_all(recursive=False):
            if not getattr(child, "name", None) or child.name in ("script", "style", "noscript", "svg"):
                continue
            classes = sorted(child.get("class", []))[:3]
            child_skeleton = self._normalize_node(child, max_depth, depth + 1)
            sig = f"{child.name}.{'.'.join(classes)}({child_skeleton})"
            if sig == prev_sig:
                run_length += 1
                continue
            flush()
            prev_sig, run_length = sig, 1
        flush()
        return "|".join(parts)

    # Tags worth treating as candidate "components" — deliberately excludes
    # bare structural tags like div/span (too generic to mean anything on
    # their own; a div only becomes interesting once it has a class, see
    # below) and covers both semantic HTML5 tags (nav, form, table, dialog
    # — meaningful even with no class) and the common wrapper tags actual
    # sites use classes on for cards/ctas/accordions/etc.
    _COMPONENT_TAGS = frozenset({
        "div", "section", "article", "nav", "header", "footer", "aside",
        "form", "ul", "table", "dialog", "figure", "button",
    })
    _BARE_TAG_OK = frozenset({"nav", "form", "table", "dialog", "button", "header", "footer"})

    _DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip")
    _VIDEO_EMBED_HOSTS = ("youtube.com", "youtube-nocookie.com", "vimeo.com", "wistia.com", "player.vimeo.com")
    # Deliberately specific compound terms, not bare words like "tel" or
    # "name" — those would false-positive on "hotel", "detail", "username"
    # etc. since this matches as a plain substring against concatenated
    # name/id/placeholder attributes. type="email"/type="tel" (an exact
    # HTML5 input type, not a substring guess) are checked separately.
    _PII_FIELD_HINTS = (
        "email", "e-mail", "phone", "mobile", "fullname", "firstname", "first_name",
        "lastname", "last_name", "address", "ssn", "social security",
        "zipcode", "zip_code", "postalcode", "postal_code", "creditcard", "card_number", "cardnumber",
    )
    _PII_INPUT_TYPES = ("email", "tel")

    _HEADER_TECH_SIGNALS = {
        "server": {
            "cloudflare": "CDN: Cloudflare", "nginx": "Server: nginx", "apache": "Server: Apache",
            "microsoft-iis": "Server: IIS", "litespeed": "Server: LiteSpeed",
        },
        "x-powered-by": {
            "php": "Backend: PHP", "asp.net": "Backend: ASP.NET", "express": "Backend: Node/Express",
            "next.js": "Framework: Next.js",
        },
        "x-generator": {},  # captured verbatim below, any value is meaningful
    }

    def _detect_tech_from_headers(self, headers) -> None:
        for header_name, known_values in self._HEADER_TECH_SIGNALS.items():
            value = headers.get(header_name, "")
            if not value:
                continue
            lower = value.lower()
            matched = False
            for needle, label in known_values.items():
                if needle in lower:
                    self.tech_signals[label] += 1
                    matched = True
            if not matched and header_name == "x-generator":
                self.tech_signals[f"Generator (header): {value[:60]}"] += 1
        if headers.get("cf-ray"):
            self.tech_signals["CDN: Cloudflare"] += 1
        if headers.get("x-amz-cf-id"):
            self.tech_signals["CDN: Amazon CloudFront"] += 1
        if headers.get("x-served-by", "").startswith("cache") or "fastly" in headers.get("via", "").lower():
            self.tech_signals["CDN: Fastly"] += 1
        if headers.get("x-drupal-cache") is not None:
            self.tech_signals["CMS: Drupal (header)"] += 1
        if headers.get("x-shopify-stage") is not None:
            self.tech_signals["CMS: Shopify (header)"] += 1

    _CMS_ASSET_PATTERNS = {
        "CMS: WordPress": ("/wp-content/", "/wp-includes/", "/wp-json/"),
        "CMS: Drupal": ("/sites/default/files/", "/sites/all/modules/"),
        "CMS: Joomla": ("/media/jui/", "/components/com_"),
        "CMS: Magento": ("/skin/frontend/", "/media/catalog/", "/static/frontend/"),
        "CMS: Shopify": ("cdn.shopify.com", "/cdn/shop/"),
        "CMS: Squarespace": ("static1.squarespace.com",),
        "CMS: Wix": ("static.wixstatic.com",),
        "CMS: Webflow": ("assets-global.website-files.com",),
        "CMS: HubSpot": ("hs-scripts.com", "hsforms.net", "hubspot.com/cs/"),
        "CMS: Contentful (headless)": ("images.ctfassets.net",),
        "CMS: Sanity (headless)": ("cdn.sanity.io",),
    }
    _FRAMEWORK_MARKUP_PATTERNS = {
        "Framework: Next.js": ("/_next/static/", '"__next"'),
        "Framework: Nuxt/Vue": ("/_nuxt/",),
        "Framework: Angular": ("ng-version",),
        "Framework: Gatsby": ("___gatsby",),
    }

    def _detect_tech_from_markup(self, soup, html_lower: str) -> None:
        generator = soup.find("meta", attrs={"name": "generator"})
        if generator and generator.get("content"):
            self.tech_signals[f"Generator (meta tag): {generator['content'][:60]}"] += 1
        for label, needles in self._CMS_ASSET_PATTERNS.items():
            if any(n in html_lower for n in needles):
                self.tech_signals[label] += 1
        for label, needles in self._FRAMEWORK_MARKUP_PATTERNS.items():
            if any(n in html_lower for n in needles):
                self.tech_signals[label] += 1
        if soup.find(attrs={"data-reactroot": True}) or soup.find(attrs={"data-reactid": True}):
            self.tech_signals["Framework: React (generic)"] += 1

    def _extract_media_assets(self, soup, record: PageRecord) -> None:
        """Inventories images (by hosting domain, to spot content living
        outside whatever the site's main/recognized asset host is —
        the DAM-governance signal the Content Strategist cares about),
        video embeds, and document/download links by file type. No file
        is actually fetched for size/dimensions — this build only ever
        requests HTML pages, not their assets, so those specific spec
        fields (file size, pixel dimensions) aren't available here; this
        is presence/domain/type inventory only, and says so honestly in
        the report rather than fabricating numbers it can't measure.
        """
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue
            try:
                domain = urlparse(urljoin(record.url, src)).netloc.lower()
            except ValueError:
                continue
            if domain:
                self.image_domain_counts[domain] += 1

        self.video_embed_count += len(soup.find_all("video"))
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if any(host in src for host in self._VIDEO_EMBED_HOSTS):
                self.video_embed_count += 1

        for a in soup.find_all("a", href=True):
            lower_href = a["href"].lower().split("?")[0].split("#")[0]
            if lower_href.endswith(self._DOC_EXTENSIONS):
                ext = lower_href.rsplit(".", 1)[-1]
                self.document_extension_counts[ext] += 1
                if len(self.document_link_examples) < 200:
                    try:
                        full_url = urljoin(record.url, a["href"])
                    except ValueError:
                        full_url = a["href"]
                    self.document_link_examples.append({"url": full_url, "page_url": record.url, "ext": ext})

    def _extract_components(self, soup, record: PageRecord, page_text_len: int) -> None:
        """Finds recurring, class-marked (or semantically distinct) chunks
        of markup on this page — cards, nav bars, CTAs, accordions, tables,
        forms, etc. — and records which page(s) each one shows up on.

        Deliberately looser than _structural_fingerprint: a "component" is
        identified mainly by its own tag+class, not by hashing everything
        beneath it, since the goal here is "what kind of reusable widget is
        this" rather than "is this pixel-identical to another instance."
        A signature only counts once per page (10 product cards on one
        page = one occurrence of the "card" component for that page, not
        ten) — repetition is only meaningful when it's across pages.

        Skips anything whose text content is a large fraction of the whole
        page's — that's the page's own main content wrapper, not a
        reusable component, even if it happens to carry a class name.
        """
        page_text_len = max(page_text_len, 1)
        seen_on_this_page = set()

        for el in soup.find_all(self._COMPONENT_TAGS):
            classes = sorted(el.get("class", []))[:2]
            if not classes and el.name not in self._BARE_TAG_OK:
                continue
            el_text_len = len(el.get_text(strip=True))
            if el_text_len / page_text_len > 0.6:
                continue  # this is basically the whole page, not a component
            sig = f"{el.name}.{'.'.join(classes)}" if classes else f"{el.name}"
            if sig in seen_on_this_page:
                continue
            seen_on_this_page.add(sig)
            self.component_hits.setdefault(sig, set()).add(record.url)

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

        for link_tag in soup.find_all("link", attrs={"rel": "alternate", "hreflang": True}):
            href = link_tag.get("href", "")
            hreflang = link_tag.get("hreflang", "")
            if href and hreflang:
                try:
                    record.hreflang_links[hreflang] = urljoin(record.url, href)
                except ValueError:
                    record.hreflang_links[hreflang] = href

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
        record.template_fingerprint = self._structural_fingerprint(soup)
        self._extract_components(soup, record, len(text))
        self._extract_media_assets(soup, record)
        self._detect_tech_from_markup(soup, html.lower())
        if record.word_count >= 30:
            record.readability_score = self._flesch_reading_ease(text, words)

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
            form_field_hints = []
            for inp in form.find_all(["input", "textarea", "select"]):
                itype = inp.get("type", "text")
                if itype in ("hidden", "submit", "button"):
                    continue
                has_label = bool(inp.get("aria-label")) or bool(inp.get("id") and soup.find("label", attrs={"for": inp.get("id")}))
                if not has_label:
                    missing_labels += 1
                identifiers = " ".join(str(inp.get(a, "")) for a in ("name", "id", "placeholder")).lower()
                if itype in self._PII_INPUT_TYPES:
                    form_field_hints.append(True)
                else:
                    form_field_hints.append(any(hint in identifiers for hint in self._PII_FIELD_HINTS))
            if any(form_field_hints):
                record.has_pii_form = True
        record.inputs_missing_label = missing_labels
        record.aria_landmark_count = len(soup.find_all(attrs={"role": True}))

        # --- risk flags ---
        robots_meta = soup.find("meta", attrs={"name": "robots"})
        if robots_meta and "noindex" in (robots_meta.get("content", "") or "").lower():
            record.has_noindex = True
        if record.url.startswith("https://"):
            asset_urls = (
                [img.get("src", "") for img in images]
                + [s.get("src", "") for s in soup.find_all("script", src=True)]
                + [l.get("href", "") for l in soup.find_all("link", href=True)]
                + [f.get("src", "") for f in soup.find_all("iframe", src=True)]
            )
            if any(u.strip().lower().startswith("http://") for u in asset_urls):
                record.has_mixed_content = True
        privacy_link = next(
            (a for a in soup.find_all("a", href=True) if "privacy" in a["href"].lower() or "privacy" in a.get_text().lower()),
            None,
        )
        if privacy_link and self.privacy_policy_url_found is None:
            try:
                self.privacy_policy_url_found = urljoin(record.url, privacy_link["href"])
            except ValueError:
                self.privacy_policy_url_found = privacy_link["href"]

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
                    if self._url_allowed(normalized):
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
