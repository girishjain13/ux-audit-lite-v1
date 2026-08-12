"""Site-wide keyword and phrase frequency analysis.

Tokenization happens once, incrementally, inside the crawler as each page
is parsed (see crawler.py's use of `tokenize()` below) — that avoids
holding every page's full text in memory just to re-scan it afterwards.
This module just turns the resulting counters into a report-ready shape.
"""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = {
    "the","a","an","and","or","but","if","of","to","in","on","for","with","as","by","at","from",
    "is","are","was","were","be","been","being","this","that","these","those","it","its","it's",
    "you","your","yours","we","our","ours","they","their","theirs","he","she","his","her","him",
    "them","i","me","my","mine","us","not","no","yes","do","does","did","doing","done","have",
    "has","had","having","will","would","shall","should","can","could","may","might","must",
    "about","above","after","again","against","all","also","am","any","because","been","before",
    "below","between","both","during","each","few","further","here","how","into","just","more",
    "most","now","once","only","other","out","over","own","same","so","some","such","than","then",
    "there","through","too","under","until","up","very","what","when","where","which","while",
    "who","whom","why","without","one","two","three","new","get","got","use","used","using",
    "please","click","home","page","learn","today","us","let","need","want","make","made",
    "every","always","never","many","much","well","good","great","best","like","years","year",
    "day","days","way","ways","across","including","around","still","even","since","upon",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]{2,}")


def tokenize(text: str) -> list[str]:
    """Lowercase, alpha-only tokens (len >= 3), stopwords removed."""
    words = _WORD_RE.findall(text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) >= 3]


def bigrams(tokens: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]


def run_keyword_analysis(
    word_counts: Counter,
    bigram_counts: Counter,
    doc_freq: Counter,
    total_pages: int,
    top_n: int = 25,
) -> dict:
    top_keywords = [
        {
            "term": term,
            "count": count,
            "pages": doc_freq.get(term, 0),
            "pct_of_pages": round(100 * doc_freq.get(term, 0) / max(total_pages, 1), 1),
        }
        for term, count in word_counts.most_common(top_n)
    ]
    top_phrases = [
        {"term": term, "count": count}
        for term, count in bigram_counts.most_common(top_n)
        if count > 1  # single-occurrence bigrams are just noise at small crawl sizes
    ]
    return {
        "top_keywords": top_keywords,
        "top_phrases": top_phrases,
        "unique_keyword_count": len(word_counts),
        "unique_phrase_count": len(bigram_counts),
    }
