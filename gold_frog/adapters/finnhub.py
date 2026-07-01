"""
Finnhub adapter — exchange lookup + live quote. Free tier: 60 req/min.

Role (post-audit): tell us which exchange a ticker trades on (so TradingView
gets the right exchange-qualified symbol) and give a live quote (current price
+ today's open). Historical multi-day change comes from yfinance — Finnhub's
candle endpoint is premium-gated (403) on the free tier.
"""

from __future__ import annotations

from typing import Optional

import requests

from .. import config

_BASE = "https://finnhub.io/api/v1"


def _get(path: str, params: dict) -> Optional[dict]:
    if not config.FINNHUB_API_KEY:
        return None
    params = dict(params, token=config.FINNHUB_API_KEY)
    try:
        resp = requests.get(f"{_BASE}/{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


_exchange_cache: dict[str, str] = {}


def get_exchange(symbol: str) -> str:
    """TradingView exchange code for a ticker (NASDAQ / NYSE / AMEX), cached —
    a listing doesn't change between requests.

    Finnhub's profile2 returns a verbose exchange name; we map it to the short
    code TradingView expects. Defaults to NASDAQ when unknown so a lookup never
    hard-fails — callers retry the alternate exchange if the news comes back
    empty (see telegram_bot.handle_news)."""
    if symbol in _exchange_cache:
        return _exchange_cache[symbol]
    name = ((_get("stock/profile2", {"symbol": symbol}) or {}).get("exchange") or "").upper()
    if "NASDAQ" in name:
        code = "NASDAQ"
    elif "AMERICAN" in name or "AMEX" in name or "ARCA" in name:
        code = "AMEX"
    elif "NEW YORK STOCK EXCHANGE" in name or "NYSE" in name:
        code = "NYSE"
    else:
        code = "NASDAQ"
    _exchange_cache[symbol] = code
    return code


def get_quote(symbol: str) -> Optional[dict]:
    """Live quote: {price, open, prev_close} or None. Free tier."""
    q = _get("quote", {"symbol": symbol})
    if not q or not q.get("c"):
        return None
    return {"price": q.get("c"), "open": q.get("o"), "prev_close": q.get("pc")}
