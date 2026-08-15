"""Risk flags — the "things a Business Analyst needs to know before
scoping a SOW" category: mixed content, signs this might be a
staging/dev environment that's publicly indexed, forms that look like
they collect PII without a detectable link to a privacy policy anywhere
in the crawl, and (separately, see check_ssl_expiry) certificate expiry.

Every flag here is a heuristic pattern match on what the crawl actually
saw — not a security audit, and not a legal compliance determination.
"""
from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

from models import PageRecord

STAGING_HOSTNAME_HINTS = ("staging", "stage.", "dev.", "test.", "uat.", "sandbox.", "preprod")


def run_risk_analysis(pages: dict[str, PageRecord], start_url: str, privacy_policy_url_found: str | None) -> dict:
    real_pages = {u: r for u, r in pages.items() if r.status_code and r.status_code < 400}

    mixed_content_pages = [u for u, r in real_pages.items() if r.has_mixed_content]
    noindex_pages = [u for u, r in real_pages.items() if r.has_noindex]
    pii_form_pages = [u for u, r in real_pages.items() if r.has_pii_form]

    hostname = urlparse(start_url).netloc.lower()
    hostname_looks_like_staging = any(hint in hostname for hint in STAGING_HOSTNAME_HINTS)
    noindex_ratio = len(noindex_pages) / max(len(real_pages), 1)
    looks_like_exposed_staging = hostname_looks_like_staging or noindex_ratio > 0.8

    pii_without_privacy_policy = bool(pii_form_pages) and not privacy_policy_url_found

    recommendations = []
    if mixed_content_pages:
        recommendations.append({
            "text": f"{len(mixed_content_pages)} page(s) served over HTTPS load at least one resource over plain HTTP (mixed content) — browsers block or warn on this, and it's usually a quick fix (update the resource URL to https://).",
            "effort_bucket": "config", "personas": ["business", "ux"],
        })
    if looks_like_exposed_staging:
        reason = "the hostname itself" if hostname_looks_like_staging else f"{round(noindex_ratio*100,1)}% of pages are marked noindex"
        recommendations.append({
            "text": f"This crawl shows signs of being a staging/non-production environment ({reason}) that's nonetheless publicly reachable and crawlable — worth confirming this isn't meant to be access-restricted.",
            "effort_bucket": "config", "personas": ["business"],
        })
    if pii_without_privacy_policy:
        recommendations.append({
            "text": f"{len(pii_form_pages)} page(s) have a form that looks like it collects personal information (email, name, phone, address, etc.), but no link to a privacy policy was found anywhere in this crawl.",
            "effort_bucket": "config", "personas": ["business"],
        })

    return {
        "mixed_content_count": len(mixed_content_pages),
        "mixed_content_pages": mixed_content_pages[:15],
        "noindex_count": len(noindex_pages),
        "looks_like_exposed_staging": looks_like_exposed_staging,
        "pii_form_count": len(pii_form_pages),
        "pii_form_pages": pii_form_pages[:15],
        "privacy_policy_found": privacy_policy_url_found,
        "pii_without_privacy_policy": pii_without_privacy_policy,
        "recommendations": recommendations,
    }


def check_ssl_expiry(start_url: str, timeout: float = 5.0) -> dict | None:
    """Direct TLS handshake against the domain to read the certificate's
    expiry date — no browser, no extra HTTP request library, just the
    standard-library ssl/socket modules. Returns None for a plain http://
    site (nothing to check) or if the handshake itself fails for any
    reason (that failure is itself worth surfacing, done here as a
    distinct 'reachable: false' result rather than silently vanishing).
    """
    parsed = urlparse(start_url)
    if parsed.scheme != "https":
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    port = parsed.port or 443

    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter")
        if not not_after:
            return {"reachable": True, "expires_at": None, "days_until_expiry": None, "error": "Certificate had no expiry field"}
        expiry_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (expiry_dt - datetime.now(timezone.utc)).days
        return {
            "reachable": True,
            "expires_at": expiry_dt.isoformat(),
            "days_until_expiry": days_left,
            "expiring_soon": days_left < 30,
            "error": None,
        }
    except Exception as exc:
        return {"reachable": False, "expires_at": None, "days_until_expiry": None, "error": str(exc)}
