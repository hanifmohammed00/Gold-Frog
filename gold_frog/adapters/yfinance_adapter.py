"""
yfinance adapter — multi-day price history only (5d / 1mo / 1y change).

Role (post-audit): the percentage-change-over-N source. Current price and
today's open come from Finnhub's live quote; yfinance fills the historical
windows Finnhub's free tier won't (its candle endpoint is premium-gated).
Degrades to None on any error.
"""

from __future__ import annotations

from typing import Optional

try:
    import yfinance as yf  # type: ignore
    _HAS_YF = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_YF = False


def price_change(symbol: str, period: str) -> Optional[dict]:
    """Change over a yfinance period ('5d','1mo','1y'). Returns
    {first, last, pct} or None. first = close at the window's start."""
    if not _HAS_YF:
        return None
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if len(hist) < 2:
            return None
        first = float(hist["Close"].iloc[0])
        last = float(hist["Close"].iloc[-1])
        if first == 0:
            return None
        return {"first": first, "last": last, "pct": (last - first) / first * 100.0}
    except Exception:
        return None
