"""Reframes the automated action-plan findings as a heuristic evaluation —
the format UX designers actually review things in — using Nielsen's 10
usability heuristics and his 0-4 severity scale.

Important honesty note: a static-HTML crawl can only speak to some of these
heuristics. Heuristics like "visibility of system status" or "flexibility
and efficiency of use" need interaction testing a crawler can't do. Rather
than fabricate a score for those, we mark them "not assessed" and say why —
an under-claiming report is more useful to a designer than an over-claiming
one.
"""
from __future__ import annotations

SEVERITY_LABELS = {
    0: "Not a problem",
    1: "Cosmetic",
    2: "Minor",
    3: "Major",
    4: "Catastrophic",
}

_PRIORITY_TO_SEVERITY = {"high": 3, "medium": 2, "low": 1}

NIELSEN_HEURISTICS = [
    {
        "id": "h1", "name": "Visibility of system status",
        "blurb": "Does the site keep people informed about what's happening (loading states, confirmations, progress)?",
        "assessable": False,
        "why_not": "Requires watching real interactions (loading spinners, form submits) — a static crawl only sees the HTML a page returns, not what happens after a click.",
    },
    {
        "id": "h2", "name": "Match between system and the real world",
        "blurb": "Does the site speak the visitor's language, with familiar conventions and real-world logic?",
        "assessable": False,
        "why_not": "This is a judgment call about wording and mental models — needs a human reader, not a crawler.",
    },
    {
        "id": "h3", "name": "User control and freedom",
        "blurb": "Can people easily undo actions, back out of a flow, or escape somewhere they didn't mean to go?",
        "assessable": False,
        "why_not": "Needs testing actual flows (forms, checkout, wizards) — outside what a link crawl can observe.",
    },
    {
        "id": "h4", "name": "Consistency and standards",
        "blurb": "Do pages follow the same conventions as each other, so learning one page transfers to the next?",
        "assessable": True,
        "keywords": ("title", "heading", "duplicate", "canonical"),
    },
    {
        "id": "h5", "name": "Error prevention",
        "blurb": "Does the design stop mistakes before they happen — clear form fields, sensible defaults?",
        "assessable": True,
        "keywords": ("form", "label", "input"),
    },
    {
        "id": "h6", "name": "Recognition rather than recall",
        "blurb": "Can people find what they need by browsing, without having to remember where something was?",
        "assessable": True,
        "keywords": ("orphan", "click depth", "clicks from"),
    },
    {
        "id": "h7", "name": "Flexibility and efficiency of use",
        "blurb": "Does the site work well for both first-time and power users (shortcuts, filters, saved state)?",
        "assessable": False,
        "why_not": "Needs usage data or task-based testing — not visible from a page's markup alone.",
    },
    {
        "id": "h8", "name": "Aesthetic and minimalist design",
        "blurb": "Is every page focused, with no clutter or filler diluting the content that matters?",
        "assessable": True,
        "keywords": ("thin content", "word", "images", "alt text"),
    },
    {
        "id": "h9", "name": "Help recognize, diagnose, and recover from errors",
        "blurb": "When something goes wrong (a broken link, a bad search), does the site help people recover?",
        "assessable": True,
        "keywords": ("http_4", "http_5", "broken", "error", "robots"),
    },
    {
        "id": "h10", "name": "Help and documentation",
        "blurb": "Is help easy to find and understand when someone genuinely gets stuck?",
        "assessable": False,
        "why_not": "Whether help content is actually clear and easy to find is a qualitative read, not a crawl signal.",
    },
]


def classify_into_heuristics(action_plan: list[dict]) -> list[dict]:
    """Bucket action-plan findings under the heuristic(s) they best match."""
    results = []
    for h in NIELSEN_HEURISTICS:
        entry = {
            "id": h["id"],
            "name": h["name"],
            "blurb": h["blurb"],
            "assessable": h["assessable"],
            "why_not": h.get("why_not", ""),
            "findings": [],
            "max_severity": 0,
        }
        if h["assessable"]:
            for item in action_plan:
                text = item["action"].lower()
                if any(kw in text for kw in h["keywords"]):
                    sev = _PRIORITY_TO_SEVERITY.get(item["priority"], 1)
                    entry["findings"].append({"text": item["action"], "severity": sev,
                                               "severity_label": SEVERITY_LABELS[sev], "area": item["area"]})
                    entry["max_severity"] = max(entry["max_severity"], sev)
        results.append(entry)
    return results


def heuristic_summary(heuristics: list[dict]) -> dict:
    assessed = [h for h in heuristics if h["assessable"]]
    clean = [h for h in assessed if not h["findings"]]
    flagged = [h for h in assessed if h["findings"]]
    return {
        "assessed_count": len(assessed),
        "not_assessed_count": len(heuristics) - len(assessed),
        "clean_count": len(clean),
        "flagged_count": len(flagged),
        "highest_severity": max((h["max_severity"] for h in flagged), default=0),
    }
