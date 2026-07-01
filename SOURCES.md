# Data Sources

| Source | Responsibility | Notes |
|--------|----------------|-------|
| **TradingView** | News article feed (full body) | `adapters/tradingview.py`. Unofficial scraper for headlines + the `v3/story` endpoint for full body. No key needed; optional cookie fallback for CAPTCHA. |
| **Finnhub** | Exchange lookup + live quote | `adapters/finnhub.py`. `profile2` → exchange (NASDAQ/NYSE/AMEX) so TradingView gets the right symbol; `quote` → current price + today's open. Free tier, 60 req/min. |
| **yfinance** | Multi-day price history | `adapters/yfinance_adapter.py`. 5d / 1mo / 1y % change. |
| **DeepSeek** | Article classification, intent parsing, summaries | `deepseek/client.py`. |
