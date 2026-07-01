![Gold Frog](assets/banner.png)

A Telegram bot that summarizes stock news sentiment on demand. Ask it about a
ticker in plain English and it pulls recent articles, classifies each one, and
replies with a short per-article breakdown plus an overall Bullish / Bearish /
Neutral read. It also answers price questions, compares tickers, and pushes
real-time alerts and a daily digest for a watchlist.

## What it does

- **News** — "latest news on AAPL", "NVDA news yesterday", "TSLA last week", "GME past 3 days"
- **Price** — "what is GME trading at", "how much is AAPL up since open", "NVDA over the past month/year"
- **Compare** — "GME vs AMC"
- **Watchlist** — "watch add NVDA", "watch remove NVDA", "watch list"
- **Real-time alerts** — a richer push the moment a new article lands for any watchlist ticker
- **Daily digest** — a once-a-day sentiment summary of the watchlist

It understands free phrasing and remembers the last ticker, so "how much is it
up since open" works as a follow-up.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your keys
```

`.env` keys:

| Key | Where to get it |
|-----|-----------------|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_ALLOWED_USER_ID` | [@userinfobot](https://t.me/userinfobot) — only this user is answered |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com |
| `FINNHUB_API_KEY` | https://finnhub.io (free tier) |
| `TRADINGVIEW_COOKIE` | optional — only if TradingView starts returning CAPTCHAs |
| `DIGEST_HOUR` | optional — local hour (0–23) for the daily digest, default 8 |

## Run

```bash
python telegram_bot.py
```

Message your bot `/start` for the help card, then ask away.

## Customizing

Everything below is a plain edit — no framework config, no build step. Restart
the bot after any change.

### Watchlist (which tickers get alerts + digest)

Edit **`watchlist.txt`** — one ticker per line, `#` for comments. Also
editable live from chat: `watch add TICKER`, `watch remove TICKER`, `watch list`.

### Which Telegram account the bot answers

`TELEGRAM_ALLOWED_USER_ID` in `.env`. Every other user gets "Not authorized."
Get your numeric ID from [@userinfobot](https://t.me/userinfobot).

### Daily digest time

`DIGEST_HOUR` in `.env` — local hour, 0–23, default `8`.

### How often real-time alerts poll for new articles

`gold_frog/config.py` doesn't hold this one — it's the `interval_min=30`
default argument on `_alert_loop` in `telegram_bot.py`. Lower it to check more
often (more API calls), raise it to check less.

### DeepSeek model / timeouts / retries

`gold_frog/config.py`:
- `DEEPSEEK_MODEL` — the model name sent to the API.
- `DEEPSEEK_TIMEOUT_SECONDS` — per-request timeout.
- `DEEPSEEK_MAX_RETRIES` — retry attempts on failure/rate-limit before giving up.

### How many articles get classified / shown per news request

`telegram_bot.py`:
- `MAX_ARTICLES` — cap on articles sent to DeepSeek per request (cost/latency vs. coverage).
- `MAX_SHOWN` — how many of those make it into the reply (the rest still count toward the verdict).
- `_MAX_WORKERS` — how many classification calls run in parallel.

### The bot's tone / wording

`telegram_bot.py`:
- `GREETING` — the `/start` welcome message.
- `DISCLAIMER` — what's sent when someone asks for buy/sell/trade advice.
- `_OVERVIEW_SYSTEM` — system prompt for the "Overall Overview" paragraph on a news reply.
- `_ALERT_SYSTEM` — system prompt for real-time article alerts.

`gold_frog/deepseek/prompts.py`:
- `NEWS_SYSTEM_PROMPT` — the per-article classification prompt (tone/confidence rules).
  Keep it byte-identical across edits you don't intend, changing it invalidates
  DeepSeek's prompt cache (cheaper repeated calls) until it re-warms.

`gold_frog/deepseek/client.py`:
- `_INTENT_SYSTEM` — the prompt that turns a chat message into a structured
  command (ticker, intent, timeframe). Edit here to teach it new phrasing.

### Positive-catalyst keyword list

`gold_frog/keywords.py` — `GOOD_NEWS_KEYWORDS`, a plain list of substrings
(lowercase). This is only ever a *context flag* passed to the classifier, not
a filter — every article still gets classified regardless of a match.

### Which stock exchanges get tried

`telegram_bot.py` — `_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")`. The resolved
exchange (from Finnhub) is tried first, then the rest, so an unfamiliar ticker
doesn't read as "no news."

### Adding a new data source

Add a module under `gold_frog/adapters/` following the shape of
`finnhub.py` / `tradingview.py` / `yfinance_adapter.py`, then wire it into
`telegram_bot.py`. See [SOURCES.md](SOURCES.md) for what each existing source
is responsible for.

## Layout

```
telegram_bot.py        the bot: routing, commands, alerts, digest
gold_frog/
  config.py            settings + .env loading
  models.py            shared data shapes
  deepseek/            classification, intent parsing, summaries
  keywords.py          positive-catalyst keyword filter (context flag for the classifier)
  adapters/            tradingview (news), finnhub (exchange + quote), yfinance (history)
```

See [SOURCES.md](SOURCES.md) for what each external service is responsible for.
