"""Shared data models for the audit engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AuditStatus(str, Enum):
    QUEUED = "queued"
    CRAWLING = "crawling"
    ANALYZING = "analyzing"
    DONE = "done"
    ERROR = "error"


@dataclass
class PageRecord:
    url: str
    status_code: Optional[int] = None
    content_type: str = ""
    depth: int = 0  # click depth from the crawl start (BFS)
    path_depth: int = 0  # number of "/" segments in the URL path
    redirected_from: Optional[str] = None
    error: Optional[str] = None
    fetch_ms: float = 0.0

    title: str = ""
    meta_description: str = ""
    canonical: str = ""
    h1_list: list = field(default_factory=list)
    heading_sequence: list = field(default_factory=list)  # e.g. ["h1","h2","h2","h3"]
    word_count: int = 0
    text_hash: str = ""

    og_tags: dict = field(default_factory=dict)
    has_schema_org: bool = False
    schema_types: list = field(default_factory=list)
    lang: str = ""

    images_total: int = 0
    images_missing_alt: int = 0
    forms_total: int = 0
    inputs_missing_label: int = 0
    aria_landmark_count: int = 0
    script_count: int = 0
    external_script_count: int = 0

    internal_links_out: list = field(default_factory=list)
    external_links_out_count: int = 0
    rendered_height_estimate: int = 0  # proxy for scroll depth (px)
    reading_time_seconds: int = 0

    is_thin_content: bool = False
    is_duplicate_of: Optional[str] = None

    # A structural fingerprint of the page's HTML skeleton (tag/class shape,
    # with repeated sibling blocks collapsed so a list of 5 products and a
    # list of 50 look the same) — used to cluster pages into "templates"
    # rather than by URL pattern or title, which can be misleading. See
    # analyzers/templates.py.
    template_fingerprint: str = ""

    # URL structure & redirect complexity (see analyzers/url_health.py) —
    # how many hops it took to reach this page's final URL, and the actual
    # hop-by-hop path, so a chain vs. a genuine loop can be told apart.
    redirect_chain_length: int = 0
    redirect_chain: list = field(default_factory=list)

    # Content freshness (see analyzers/freshness.py) — from the HTTP
    # Last-Modified header when the server sends one, falling back to
    # sitemap.xml's <lastmod> for this URL when it doesn't. Either way
    # it's a string as received (HTTP-date or ISO-date format) — parsing
    # happens in the analyzer, not here, since a missing/malformed date
    # is itself useful information (not every CMS exposes this at all).
    last_modified: Optional[str] = None

    # 0-100 Flesch Reading Ease (see crawler.py's _flesch_reading_ease) —
    # only set for pages with enough words to make the estimate meaningful.
    readability_score: Optional[float] = None

    # hreflang alternates declared on this page: {locale_code: url} — feeds
    # the locale coverage matrix in analyzers/locale.py
    hreflang_links: dict = field(default_factory=dict)

    # Risk flags (see analyzers/risk.py)
    has_pii_form: bool = False
    has_mixed_content: bool = False
    has_noindex: bool = False

    # Optional browser-rendered UX signals. These are populated only when
    # CrawlConfig.render_js is enabled; keeping them on PageRecord lets the
    # existing analyzers/report continue to work without a second data model.
    rendered: bool = False
    render_ms: float = 0.0
    rendered_title: str = ""
    rendered_height: int = 0
    viewport_width: int = 0
    viewport_height: int = 0
    horizontal_overflow: bool = False
    rendered_button_count: int = 0
    rendered_form_count: int = 0
    rendered_input_count: int = 0
    rendered_cta_count: int = 0
    rendered_cta_labels: list = field(default_factory=list)
    rendered_nav_link_count: int = 0
    rendered_dialog_count: int = 0
    rendered_tab_count: int = 0
    rendered_accordion_count: int = 0
    js_errors: list = field(default_factory=list)
    console_errors: list = field(default_factory=list)
    screenshot_path: Optional[str] = None
    render_error: Optional[str] = None
    # Evidence quality / analysis eligibility. These prevent a loading shell,
    # PDF, image, or failed request from being treated as a normal HTML page.
    resource_type: str = "html"
    analysis_eligible: bool = True
    analysis_confidence: str = "medium"
    rendered_text_length: int = 0
    rendered_dom_complete: bool = False


@dataclass
class CrawlProgress:
    status: AuditStatus = AuditStatus.QUEUED
    pages_crawled: int = 0
    pages_queued: int = 0
    pages_errored: int = 0
    max_pages: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current_url: str = ""
    elapsed_seconds: float = 0.0
    avg_page_seconds: float = 0.0
    eta_seconds: Optional[float] = None
    log: list = field(default_factory=list)  # rolling list of recent events

    def note(self, message: str, cap: int = 200):
        self.log.append(message)
        if len(self.log) > cap:
            self.log = self.log[-cap:]
