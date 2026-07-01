"""
Positive-catalyst keyword filter — a flag, never a gate.

`keyword_hit` reports whether any growth/catalyst phrase appears in an article
(title + body). Every article is classified by the model regardless; this hit
is passed to the classifier only as context, it never drops anything.

Phrases are lowercase and matched as substrings, so multi-word phrases work
as-is.
"""

from __future__ import annotations

from .models import NewsItem

GOOD_NEWS_KEYWORDS = [
    # deals / partnerships / business wins
    "partnership",
    "partners with",
    "strategic alliance",
    "acquisition",
    "acquires",
    "to acquire",
    "merger",
    "investment in",
    "invests in",
    "stake in",
    "licensing deal",
    "joint venture",
    "supply agreement",
    "multi-year deal",
    "contract win",
    "signs deal",
    "collaboration with",
    "teams up with",

    # product / tech improvements
    "breakthrough",
    "launches",
    "unveils",
    "new model",
    "upgrade",
    "next-gen",
    "patent",
    "beta release",
    "expands",
    "improved accuracy",
    "outperforms",
    "milestone",
    "state of the art",
    "state-of-the-art",
    "industry-leading",
    "record speed",

    # financial performance / growth
    "beats estimates",
    "tops estimates",
    "raises guidance",
    "raises funding",
    "funding round",
    "record revenue",
    "surges",
    "soars",
    "rallies",
    "strong demand",
    "demand surge",
    "backlog grows",
    "order surge",
    "upgraded by analysts",
    "price target raised",

    # adoption / momentum
    "adoption surges",
    "rolls out",
    "expands deployment",
    "scales up",
    "wins contract",
    "selected by",
    "chosen by",
]


def keyword_hit(item: NewsItem) -> bool:
    """True if any keyword appears as a substring of the article's title+body."""
    text = item.search_text()  # already lowercased
    return any(kw in text for kw in GOOD_NEWS_KEYWORDS)
