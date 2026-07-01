"""
TradingView adapter — real-time per-symbol news with full article body.

Headlines come from the tradingview-scraper library; the full body comes from
TradingView's v3 story endpoint, which returns the article as an AST we flatten
to text. No API key needed — the cookie (config.TRADINGVIEW_COOKIE) is optional
and only sent if set, as a fallback if TradingView returns CAPTCHA challenges.

Note: this is an unofficial scraper and can break if TradingView changes their
endpoints.
"""

from __future__ import annotations

import warnings
from datetime import date, datetime, timezone
from typing import Optional

import requests

from .. import config
from ..models import NewsItem

warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

_STORY_URL = "https://news-headlines.tradingview.com/v3/story"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _flatten(node) -> str:
    """Recursively pull text out of TradingView's astDescription tree."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_flatten(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "symbol":  # inline ticker token e.g. NASDAQ:AAPL
            return (node.get("params") or {}).get("text", "")
        return _flatten(node.get("children", []))
    return ""


def _story_body(story_id: str) -> tuple[str, str]:
    """Returns (full_body, short_description) for one story id. Empty on failure."""
    headers = dict(_HEADERS)
    if config.TRADINGVIEW_COOKIE:
        headers["Cookie"] = config.TRADINGVIEW_COOKIE
    try:
        d = requests.get(
            _STORY_URL, params={"id": story_id, "lang": "en"},
            headers=headers, timeout=10,
        ).json()
    except (requests.RequestException, ValueError):
        return "", ""
    return _flatten(d.get("astDescription")), d.get("shortDescription", "") or ""


def fetch_news(
    symbol: str, exchange: str = "NASDAQ",
    limit: int = 5, since: Optional[date] = None, until: Optional[date] = None,
) -> list[NewsItem]:
    """News items for symbol on exchange, each with full body.

    Default: latest `limit` items. If `since` (a local date) is given, return
    every item published in [since, until] instead (limit ignored; `until`
    defaults to no upper bound). Headlines are filtered first so we only fetch
    bodies for the ones we keep.
    """
    from tradingview_scraper.symbols.news import NewsScraper

    headlines = NewsScraper(export_result=False).scrape_headlines(
        symbol=symbol, exchange=exchange, sort="latest",
    )
    if not isinstance(headlines, list):
        return []

    if since is not None:
        # Compare in LOCAL time: "today"/"yesterday" must mean the user's
        # calendar day, not UTC's. fromtimestamp() w/o tz = local.
        def _in_range(r):
            if not r.get("published"):
                return False
            d = datetime.fromtimestamp(r["published"]).date()
            return d >= since and (until is None or d <= until)
        rows = [r for r in headlines if _in_range(r)]
    else:
        rows = headlines[:limit]

    out: list[NewsItem] = []
    for row in rows:
        body, short = _story_body(row.get("id", ""))
        published = row.get("published")
        out.append(NewsItem(
            ticker=symbol,
            headline=row.get("title", "") or "",
            body_text=body or short,  # fall back to summary if AST body is empty
            published_at=datetime.fromtimestamp(published, tz=timezone.utc) if published else None,
            source_name=f"tradingview:{row.get('provider', '')}",
            url=row.get("link"),
        ))
    return out
