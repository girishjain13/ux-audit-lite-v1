"""Infers several distinct, persona-based journey maps from the crawled
site's structure.

Same honesty note as before, worth repeating because it matters: none of
this is real behavioral data. A crawler has no access to analytics, session
recordings, or task-completion rates. What follows is an inference — where
the site's structure most likely puts each stage of a goal-driven visit for
a given persona, and how discoverable (click depth) that stage actually is.
Treat it as "here's what the structure suggests," worth validating against
real analytics if you have them, not a replacement for that data.

Not every persona will be relevant to every site — a B2B SaaS product and a
hospital system don't share a "Job Seeker" journey shape, but both likely
have one, so it's included for all sites rather than guessed at per-industry.
A persona with zero matching stages just means that funnel likely isn't a
priority for this particular site, which is itself a useful signal.
"""
from __future__ import annotations

JOURNEYS = [
    {
        "id": "prospective_customer", "name": "Prospective Customer",
        "description": "A new visitor evaluating whether to become a customer — the classic top-of-funnel to conversion path.",
        "stages": [
            {"id": "awareness", "name": "Awareness",
             "description": "Content someone finds before they know your product/company — top-of-funnel, informational.",
             "keywords": ("blog", "article", "insight", "guide", "resource", "news", "learn")},
            {"id": "consideration", "name": "Consideration",
             "description": "Pages that help someone evaluate fit — services, products, case studies.",
             "keywords": ("about", "service", "product", "feature", "solution", "case-study", "case_study", "portfolio", "work")},
            {"id": "decision", "name": "Decision",
             "description": "Pages aimed at someone close to choosing — pricing, plans, demos, comparisons.",
             "keywords": ("pricing", "plans", "demo", "trial", "quote", "compare", "vs")},
            {"id": "action", "name": "Action / Conversion",
             "description": "Where the actual conversion happens — signup, checkout, booking, contact.",
             "keywords": ("signup", "sign-up", "register", "checkout", "cart", "book", "apply", "contact", "buy", "shop")},
        ],
    },
    {
        "id": "job_seeker", "name": "Job Seeker",
        "description": "Someone evaluating the company as a potential employer and trying to apply.",
        "stages": [
            {"id": "discover_careers", "name": "Discover Careers",
             "description": "The landing point for anyone checking whether the company is hiring at all.",
             "keywords": ("careers", "jobs", "join-us", "join_us", "work-with-us", "we-are-hiring")},
            {"id": "browse_openings", "name": "Browse Openings",
             "description": "Actual job listings — specific open positions, not just a generic careers page.",
             "keywords": ("position", "opening", "vacancy", "job-listing", "job_listing", "openings")},
            {"id": "apply", "name": "Apply",
             "description": "Where someone actually submits an application.",
             "keywords": ("apply", "application", "submit-resume", "submit_resume")},
        ],
    },
    {
        "id": "existing_customer", "name": "Existing Customer / Support",
        "description": "Someone who already has a relationship with the company and needs to sign in, self-serve, or get help.",
        "stages": [
            {"id": "sign_in", "name": "Sign In",
             "description": "Where a returning user authenticates.",
             "keywords": ("login", "signin", "sign-in", "account", "my-account", "my_account")},
            {"id": "self_service", "name": "Self-Service Help",
             "description": "Documentation, FAQs, or a knowledge base someone can use without contacting a human.",
             "keywords": ("faq", "help", "docs", "documentation", "knowledge-base", "knowledgebase", "kb")},
            {"id": "contact_support", "name": "Contact Support",
             "description": "Where someone goes when self-service isn't enough and they need a real person.",
             "keywords": ("support", "help-desk", "helpdesk", "ticket", "contact-support")},
        ],
    },
    {
        "id": "press_investor", "name": "Press / Investor",
        "description": "Journalists, analysts, or investors researching the company rather than its product.",
        "stages": [
            {"id": "company_info", "name": "Company Info",
             "description": "Background on who the company is — leadership, mission, history.",
             "keywords": ("about", "company", "who-we-are", "leadership", "our-team", "our_team")},
            {"id": "news_press", "name": "News & Press",
             "description": "Press releases, media coverage, or company announcements.",
             "keywords": ("press", "media", "newsroom", "press-release", "press_release", "announcement")},
            {"id": "investor_relations", "name": "Investor Relations",
             "description": "Financial reports, shareholder information — relevant only to publicly-relevant or funded companies.",
             "keywords": ("investor", "investors", "shareholder", "financial-report", "financial_report", "/ir/", "ir-")},
            {"id": "media_contact", "name": "Media Contact",
             "description": "A dedicated way for press/investors to reach out, distinct from general customer contact.",
             "keywords": ("media-contact", "media_contact", "press-contact", "press_contact")},
        ],
    },
]


def _matches_stage(url: str, title: str, keywords: tuple) -> bool:
    haystack = f"{url} {title}".lower()
    return any(kw in haystack for kw in keywords)


def parse_custom_personas(text: str) -> list[dict]:
    """Parses a small text DSL for user-defined personas — the same
    keyword-matching engine as the built-in JOURNEYS above, but for
    personas the user names themselves. Entirely rule-based: no AI call,
    no API key needed, so this works even when ANTHROPIC_API_KEY isn't set.

    Format (blank line separates personas):

        Freelance Designer
        Awareness: blog, guide, resource
        Consideration: portfolio, case-study, pricing
        Decision: contact, quote, hire

    First line of each block is the persona name. Each following
    "Stage: kw1, kw2, kw3" line becomes one journey stage, matched against
    each crawled page's URL and title the same way the built-in personas
    are. Malformed lines are skipped rather than raising — a typo in one
    persona definition shouldn't break the whole feature.
    """
    personas = []
    for block in text.strip().split("\n\n"):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        name = lines[0]
        stages = []
        for line in lines[1:]:
            if ":" not in line:
                continue
            stage_name, _, kw_text = line.partition(":")
            keywords = tuple(k.strip().lower() for k in kw_text.split(",") if k.strip())
            if not stage_name.strip() or not keywords:
                continue
            stages.append({
                "id": stage_name.strip().lower().replace(" ", "_"),
                "name": stage_name.strip(),
                "description": f"Custom stage — pages matching: {', '.join(keywords)}.",
                "keywords": keywords,
            })
        if stages:
            personas.append({
                "id": "custom_" + name.strip().lower().replace(" ", "_"),
                "name": f"{name.strip()} (custom)",
                "description": "User-defined persona — matched by keyword against crawled URLs/titles, the same way the built-in personas above are.",
                "stages": stages,
            })
    return personas


def _build_single_journey(journey_def: dict, pages: dict, click_depths: dict) -> dict:
    stages_out = []
    for stage in journey_def["stages"]:
        matches = [
            (url, rec) for url, rec in pages.items()
            if rec.status_code and rec.status_code < 400
            and _matches_stage(url, rec.title, stage["keywords"])
        ]
        if not matches:
            stages_out.append({
                "id": stage["id"], "name": stage["name"], "description": stage["description"],
                "present": False, "page_count": 0, "example_url": None, "click_depth": None,
            })
            continue

        def depth_of(item):
            d = click_depths.get(item[0])
            return d if d is not None else 999

        best_url, best_rec = min(matches, key=depth_of)
        stages_out.append({
            "id": stage["id"], "name": stage["name"], "description": stage["description"],
            "present": True, "page_count": len(matches),
            "example_url": best_url, "example_title": best_rec.title,
            "click_depth": click_depths.get(best_url),
        })

    present_count = sum(1 for s in stages_out if s["present"])
    missing = [s["name"] for s in stages_out if not s["present"]]
    deep_stages = [s for s in stages_out if s["present"] and (s["click_depth"] or 0) > 3]

    notes = []
    if present_count == 0:
        notes.append("No content matched any stage of this journey — it likely isn't a priority for this site, or uses very different wording than the keywords checked here.")
    elif missing:
        notes.append(
            f"No content matched for: {', '.join(missing)}. That doesn't necessarily mean it doesn't "
            f"exist — it may just use different wording — but worth confirming a visitor could actually find it."
        )
    if deep_stages:
        names = ", ".join(s["name"] for s in deep_stages)
        notes.append(f"{names} sit more than 3 clicks from the homepage — a visitor on this journey would need to work to get there.")
    if present_count == len(journey_def["stages"]) and not deep_stages:
        notes.append("Every stage has findable, shallow content — the structure supports this journey well.")

    return {
        "id": journey_def["id"], "name": journey_def["name"], "description": journey_def["description"],
        "stages": stages_out,
        "stages_present": present_count,
        "stages_total": len(journey_def["stages"]),
        "notes": notes,
    }


def build_journey_map(pages: dict, click_depths: dict, custom_personas: list[dict] | None = None) -> dict:
    all_journeys = JOURNEYS + (custom_personas or [])
    journeys = [_build_single_journey(jd, pages, click_depths) for jd in all_journeys]
    return {
        "journeys": journeys,
        "journeys_with_any_presence": sum(1 for j in journeys if j["stages_present"] > 0),
        "journeys_total": len(journeys),
    }
