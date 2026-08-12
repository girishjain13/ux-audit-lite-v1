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
