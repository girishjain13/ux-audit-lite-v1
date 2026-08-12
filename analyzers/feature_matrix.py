"""Detects whether common website features are present, via pattern
matching against the HTML actually crawled — not guesses, real signals:
form field types, link targets, schema.org types, and known third-party
widgets (reusing what analyzers/integrations.py already found).

Presence is a lower bound, not a guarantee of absence: a feature that
exists only behind a login wall, or that's implemented in a way this
tool's patterns don't recognize, will show as "not detected" even though
it exists. Treat "not detected" as "worth double-checking manually,"
not "confirmed absent."
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

FEATURES = [
    {
        "id": "search", "name": "Site Search",
        "url_patterns": ("/search",),
        "text_patterns": (r'type=["\']search["\']', r'role=["\']search["\']', r'name=["\']q["\']'),
    },
    {
        "id": "login", "name": "User Login / Account",
        "url_patterns": ("/login", "/signin", "/sign-in", "/account", "/my-account"),
        "text_patterns": (r'\blog[\s-]?in\b', r'\bsign[\s-]?in\b', r'\bmy account\b'),
    },
    {
        "id": "registration", "name": "User Registration / Signup",
        "url_patterns": ("/signup", "/sign-up", "/register"),
        "text_patterns": (r'\bsign[\s-]?up\b', r'\bregister\b', r'\bcreate an account\b'),
    },
    {
        "id": "ecommerce", "name": "E-commerce (cart / checkout)",
        "url_patterns": ("/cart", "/checkout", "/shop"),
        "text_patterns": (r'\badd to cart\b', r'\bcheckout\b', r'\bbuy now\b', r'"@type"\s*:\s*"Product"'),
    },
    {
        "id": "newsletter", "name": "Newsletter Signup",
        "url_patterns": (),
        "text_patterns": (r'\bnewsletter\b', r'\bsubscribe\b.{0,40}email', r'email.{0,40}\bsubscribe\b'),
    },
    {
        "id": "blog", "name": "Blog / Articles",
        "url_patterns": ("/blog", "/articles", "/news", "/insights"),
        "text_patterns": (),
    },
    {
        "id": "faq", "name": "FAQ / Help Center",
        "url_patterns": ("/faq", "/help", "/support"),
        "text_patterns": (r'\bfrequently asked questions\b', r'\bfaq\b'),
    },
    {
        "id": "pricing", "name": "Pricing / Plans",
        "url_patterns": ("/pricing", "/plans"),
        "text_patterns": (r'\bpricing\b', r'\bchoose a plan\b'),
    },
    {
        "id": "careers", "name": "Careers / Jobs",
        "url_patterns": ("/careers", "/jobs"),
        "text_patterns": (r"\bwe're hiring\b", r'\bjoin our team\b', r'\bopen positions\b'),
    },
    {
        "id": "multilingual", "name": "Multi-language Support",
        "url_patterns": (),
        "text_patterns": (r'hreflang=', r'\ben\s*\|\s*fr\b', r'\ben\s*\|\s*de\b', r'language[\s-]?switch'),
    },
    {
        "id": "video", "name": "Video Content",
        "url_patterns": (),
        "text_patterns": (r'<video', r'youtube\.com/embed', r'player\.vimeo\.com'),
    },
    {
        "id": "testimonials", "name": "Testimonials / Reviews",
        "url_patterns": ("/testimonials", "/reviews"),
        "text_patterns": (r'\btestimonial', r'"@type"\s*:\s*"Review"', r'"aggregateRating"'),
    },
    {
        "id": "downloads", "name": "Downloadable Resources",
        "url_patterns": (),
        "text_patterns": (r'href=["\'][^"\']+\.pdf', r'href=["\'][^"\']+\.docx?', r'href=["\'][^"\']+\.xlsx?'),
    },
    {
        "id": "contact_form", "name": "Contact Form",
        "url_patterns": ("/contact",),
        "text_patterns": (r'<form',),
    },
    {
        "id": "locations", "name": "Store/Office Locations",
        "url_patterns": ("/locations", "/find-us", "/stores"),
        "text_patterns": (r'\bfind a (store|location)\b', r'\bnear you\b'),
    },
]

# live chat is already detected by analyzers/integrations.py — surfaced
# separately in run_feature_matrix() below rather than duplicating patterns
CHAT_INTEGRATION_NAMES = {"Intercom", "Drift", "Zendesk"}


def scan_page_features(url: str, html: str) -> set[str]:
    """Returns the set of feature ids found on this one page's raw HTML."""
    found = set()
    path = urlparse(url).path.lower()
    for feature in FEATURES:
        if any(p in path for p in feature["url_patterns"]):
            found.add(feature["id"])
            continue
        if any(re.search(p, html, re.IGNORECASE) for p in feature["text_patterns"]):
            found.add(feature["id"])
    return found


def run_feature_matrix(feature_hits: dict, integration_detected: list) -> dict:
    """feature_hits: {feature_id: set of page urls} — built incrementally
    during the crawl (see crawler.py), same pattern as keyword/integration
    tallies, to avoid holding every page's raw HTML around afterward.
    """
    has_chat = any(d["name"] in CHAT_INTEGRATION_NAMES for d in integration_detected)

    rows = []
    for feature in FEATURES:
        pages = feature_hits.get(feature["id"], set())
        rows.append({
            "id": feature["id"], "name": feature["name"],
            "present": bool(pages), "page_count": len(pages),
            "example_url": next(iter(pages), None),
        })
    rows.append({
        "id": "live_chat", "name": "Live Chat Widget",
        "present": has_chat, "page_count": None,
        "example_url": None,
    })

    present_count = sum(1 for r in rows if r["present"])
    return {"rows": rows, "present_count": present_count, "total_count": len(rows)}
