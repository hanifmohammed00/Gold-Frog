#!/usr/bin/env python3
"""Gold Frog Telegram bot.

Ask it about a stock — news (today / yesterday / last week / past N days) or
price (now / since open / 5d / 1mo / 1y). It resolves the ticker's exchange via
Finnhub, pulls news from TradingView, classifies + filters each article (in
parallel), and replies with short per-article tags + an Overall Overview. While
running it pushes a richer real-time alert when a brand-new article appears for
any ticker in watchlist.txt.

Only the allowlisted user (config.TELEGRAM_ALLOWED_USER_ID) is answered.
Long-polls the Telegram Bot API with requests — no extra dependency, no webhook.
Run:  python telegram_bot.py
"""

import difflib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

from gold_frog import config
from gold_frog.adapters import finnhub, tradingview
from gold_frog.adapters.yfinance_adapter import price_change
from gold_frog.deepseek.client import DeepSeekError, classify_news, parse_intent, summarize
from gold_frog import keywords

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

TONE_TAG = {"good": "Bullish", "bad": "Bearish", "neutral": "Neutral"}
SIGNAL_EMOJI = {"Bullish": "📈", "Bearish": "📉", "Neutral": "📊"}
MAX_SHOWN = 5       # process all relevant articles, but only show the best N
MAX_ARTICLES = 10   # cap classification cost/latency; we only show the best MAX_SHOWN anyway
_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
_MAX_WORKERS = 5    # parallel DeepSeek classifications per request (rate-limit friendly)

_OVERVIEW_SYSTEM = (
    "You are a financial news summarizer. You are given a ticker, an overall "
    "signal, and a numbered list of short per-article summaries. Write a 2-3 "
    "sentence Overall Overview of the aggregate sentiment and the specific "
    "recurring drivers/themes across the articles. Reference the concrete "
    "drivers, not just a one-word verdict. Plain text, no markdown, no preamble."
)
_ALERT_SYSTEM = (
    "You are a financial news alert writer. You are given one news article about "
    "a stock plus the stock's current price context. If the article is "
    "significant or breaking, write 3-4 sentences on what happened and why it "
    "matters to the stock. If it mostly repeats known info or is minor, keep it "
    "to 1-2 sentences. Briefly fold in the price/sentiment context. Plain text, "
    "no markdown, no preamble."
)


# --------------------------------------------------------------------------
# Persisted state — last ticker per chat survives restarts
# --------------------------------------------------------------------------

_STATE_FILE = "bot_state.json"


def _load_last_ticker():
    try:
        d = json.load(open(_STATE_FILE))
        return {int(k): v for k, v in d.get("last_ticker", {}).items()}
    except (FileNotFoundError, ValueError):
        return {}


_last_ticker = _load_last_ticker()  # {chat_id: TICKER}
_pending_news = {}  # {chat_id: TICKER} — chats we asked "today or this week?"; in-memory only


def _save_state():
    # Atomic write (tmp + replace) so a crash mid-write can't corrupt the file.
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"last_ticker": {str(k): v for k, v in _last_ticker.items()}}, f)
        os.replace(tmp, _STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Telegram I/O
# --------------------------------------------------------------------------

def _send(chat_id, text):
    for i in range(0, len(text), 4000):  # Telegram caps a message at 4096 chars
        try:
            requests.post(f"{_API}/sendMessage",
                          json={"chat_id": chat_id, "text": text[i:i + 4000]}, timeout=20)
        except requests.RequestException:
            pass


# --------------------------------------------------------------------------
# News: window, classify, dedup, rank, format
# --------------------------------------------------------------------------

def _news_window(news_days):
    """(since, until, label) from the parser's news_days. None/0 -> today."""
    today = datetime.now().date()
    n = news_days or 0
    if n <= 0:
        return today, today, "today"
    if n == 1:
        y = today - timedelta(days=1)
        return y, y, "yesterday"
    return today - timedelta(days=n), today, f"in the last {n} days"


def _is_duplicate(headline, kept_headlines):
    return any(difflib.SequenceMatcher(None, headline.lower(), k.lower()).ratio() > 0.7
               for k in kept_headlines)


_TONE_SIGN = {"good": 1, "bad": -1, "neutral": 0}


def _rank_key(row):
    """Best first: directional (non-neutral) articles, then higher confidence."""
    _h, _tag, _s, tone, conf = row
    return (tone != "neutral", _CONF_RANK.get(conf, 0))


def overall_signal(rows):
    """Confidence-weighted verdict: a high-confidence bad outweighs a couple of
    low-confidence goods, rather than a raw headcount."""
    score = sum(_TONE_SIGN.get(tone, 0) * _CONF_RANK.get(conf, 1) for *_, tone, conf in rows)
    return "Bullish" if score > 0 else "Bearish" if score < 0 else "Neutral"


def _classify(item):
    """One article -> (item, classification), or None if the model call fails."""
    try:
        hit = keywords.keyword_hit(item)
        c = classify_news(ticker=item.ticker, keyword_hit=hit,
                          article_text=f"{item.headline}\n{item.body_text}")
        return item, c
    except DeepSeekError:
        return None


def analyze_articles(ticker, items):
    """Classify up to MAX_ARTICLES (in parallel) -> drop irrelevant -> drop dupes.
    Returns (rows, overall, dropped); rows = [(headline, tag, summary, tone, conf)],
    dropped = count whose classification failed (e.g. rate limit)."""
    items = items[:MAX_ARTICLES]
    if not items:
        return [], "Neutral", 0
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(items))) as ex:
        results = list(ex.map(_classify, items))  # ex.map preserves input order

    rows, kept_headlines, dropped = [], [], 0
    for r in results:
        if r is None:                                         # classify failed (e.g. rate limit)
            dropped += 1
            continue
        item, c = r
        if c.insufficient_data:                               # not actually about this ticker
            continue
        if _is_duplicate(item.headline, kept_headlines):      # near-duplicate headline
            continue
        kept_headlines.append(item.headline)
        rows.append((item.headline, TONE_TAG.get(c.tone, "Neutral"), c.reason, c.tone, c.confidence))

    return rows, overall_signal(rows), dropped


def format_news_reply(ticker, all_rows, shown, overall, dropped=0):
    total = len(all_rows)
    head_count = f"{len(shown)} relevant articles" if total <= MAX_SHOWN else f"top {len(shown)} of {total}"
    lines = [f"{SIGNAL_EMOJI[overall]} {ticker} — {overall} ({head_count})", ""]
    for i, (headline, tag, summary, _tone, _c) in enumerate(shown, 1):
        lines.append(f"{i}. {headline} - {tag} {SIGNAL_EMOJI[tag]} {summary}")
    lines.append("──────────────────────────")
    numbered = "\n".join(f"{i}. {h} — {s}" for i, (h, _t, s, _to, _c) in enumerate(all_rows, 1))
    overview = summarize(_OVERVIEW_SYSTEM, f"Ticker: {ticker}\nOverall signal: {overall}\n{numbered}")
    lines.append(f"Overall Overview: {overview or 'No overview available.'}")
    if dropped:
        lines.append(f"⚠️ {dropped} article(s) couldn't be analyzed (service busy) — not counted.")
    return "\n".join(lines)


# Exchange codes to try, in order, when the resolved one returns nothing.
_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")


def _fetch_news_any_exchange(ticker, resolved, since, until):
    """Try the resolved exchange first, then the others — a wrong/unknown
    exchange guess shouldn't read as 'no news'. (Scraper itself untouched.)"""
    for exch in [resolved] + [e for e in _EXCHANGES if e != resolved]:
        items = tradingview.fetch_news(ticker, exchange=exch, since=since, until=until)
        if items:
            return items
    return []


def _send_news(chat_id, ticker, rows, overall, dropped, prefix=""):
    shown = sorted(rows, key=_rank_key, reverse=True)[:MAX_SHOWN]
    ctx = _price_context(ticker)  # combined price + news card
    reply = format_news_reply(ticker, rows, shown, overall, dropped)
    _send(chat_id, prefix + (f"{ctx}\n\n{reply}" if ctx else reply))


def handle_news(chat_id, ticker, news_days):
    exchange = finnhub.get_exchange(ticker)  # only news needs the exchange
    since, until, label = _news_window(news_days)
    _send(chat_id, f"Pulling {ticker} news ({label})…")
    items = _fetch_news_any_exchange(ticker, exchange, since, until)
    rows, overall, dropped = analyze_articles(ticker, items)
    if not rows and news_days == 0:  # nothing today — fall back to this week with a heads-up
        wsince, wuntil, _ = _news_window(7)
        witems = _fetch_news_any_exchange(ticker, exchange, wsince, wuntil)
        wrows, woverall, wdropped = analyze_articles(ticker, witems)
        if wrows:
            _send_news(chat_id, ticker, wrows, woverall, wdropped,
                       prefix=f"No news on {ticker} today — here's this week instead.\n\n")
            return
        items = witems  # both empty; fall through to the not-recognized / no-news message
    if not rows:
        if not items and finnhub.get_quote(ticker) is None:   # unknown symbol vs quiet day
            _send(chat_id, f"I don't recognize {ticker} — double-check the ticker symbol.")
        else:
            _send(chat_id, f"Sorry, no new news articles about {ticker} {label}.")
        return
    _send_news(chat_id, ticker, rows, overall, dropped)


# --------------------------------------------------------------------------
# Price commands
# --------------------------------------------------------------------------

def handle_price(chat_id, ticker, key):
    if key in ("now", "since_open"):
        q = finnhub.get_quote(ticker)
        if not q:
            _send(chat_id, f"Couldn't get a price for {ticker} right now.")
            return
        if key == "now":
            _send(chat_id, f"{ticker} is trading at ${q['price']:.2f}.")
            return
        chg = q["price"] - q["open"]
        pct = chg / q["open"] * 100 if q["open"] else 0.0
        word = "up" if chg >= 0 else "down"
        _send(chat_id, f"{ticker} is {word} ${abs(chg):.2f} ({pct:+.2f}%) since open "
                       f"(open ${q['open']:.2f}, now ${q['price']:.2f}).")
        return

    label = {"5d": "past 5 days", "1mo": "past month", "1y": "past year"}[key]
    ch = price_change(ticker, key)
    if not ch:
        _send(chat_id, f"Couldn't get {label} price data for {ticker}.")
        return
    word = "up" if ch["pct"] >= 0 else "down"
    _send(chat_id, f"{ticker} is {word} {ch['pct']:+.1f}% over the {label} "
                   f"(${ch['first']:.2f} → ${ch['last']:.2f}).")


# --------------------------------------------------------------------------
# Compare two+ tickers (today's sentiment, side by side)
# --------------------------------------------------------------------------

def handle_compare(chat_id, tickers):
    tickers = [t.upper() for t in tickers][:3]  # cap at 3 to bound cost
    if len(tickers) < 2:
        _send(chat_id, "Compare needs two tickers, e.g. 'GME vs AMC'.")
        return
    _send(chat_id, f"Comparing {', '.join(tickers)} (this week)…")
    today = datetime.now().date()
    since = today - timedelta(days=7)   # a week of news, so a quiet day isn't "no news"
    lines = []
    for t in tickers:
        ch = price_change(t, "5d")
        perf = f"{ch['pct']:+.1f}% 5d" if ch else "price n/a"
        items = _fetch_news_any_exchange(t, finnhub.get_exchange(t), since, today)
        rows, overall, _drop = analyze_articles(t, items)
        if not rows:
            lines.append(f"➖ {t} — no news this week ({perf})")
        else:
            top = sorted(rows, key=_rank_key, reverse=True)[0][2]  # best article's summary
            lines.append(f"{SIGNAL_EMOJI[overall]} {t} — {overall} ({len(rows)} articles, {perf}): {top}")
    _send(chat_id, "\n\n".join(lines))


# --------------------------------------------------------------------------
# Watchlist management from chat
# --------------------------------------------------------------------------

def _write_watchlist(tickers, path="watchlist.txt"):
    body = "# Gold Frog watchlist — one ticker per line.\n" + "\n".join(tickers) + "\n"
    open(path, "w").write(body)


def _handle_watch_cmd(chat_id, text):
    """Returns True if the message was a watchlist command and was handled."""
    parts = text.strip().lstrip("/").split()
    if not parts or parts[0].lower() != "watch":
        return False
    wl = _read_watchlist()
    action = parts[1].lower() if len(parts) > 1 else "list"
    if action == "list":
        _send(chat_id, "Watchlist: " + (", ".join(wl) if wl else "(empty)"))
        return True
    if action not in ("add", "remove") or len(parts) < 3:
        _send(chat_id, "Usage: watch add TICKER | watch remove TICKER | watch list.")
        return True
    ticker = parts[2].upper()
    if action == "add":
        if ticker in wl:
            _send(chat_id, f"{ticker} already in the watchlist.")
        else:
            wl.append(ticker); _write_watchlist(wl)
            _send(chat_id, f"Added {ticker}. Watchlist: {', '.join(wl)}")
    else:  # remove
        if ticker not in wl:
            _send(chat_id, f"{ticker} not in the watchlist.")
        else:
            wl.remove(ticker); _write_watchlist(wl)
            _send(chat_id, f"Removed {ticker}. Watchlist: {', '.join(wl) or '(empty)'}")
    return True


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

GREETING = (
    "👋 Welcome to Gold Frog — your stock news & sentiment bot.\n\n"
    "Just talk to me normally. For example:\n"
    "📰 News — \"latest news on AAPL\", \"NVDA news yesterday\", \"TSLA last week\"\n"
    "💵 Price — \"what is GME trading at\", \"how much is AAPL up since open\",\n"
    "        \"NVDA over the past month\"\n"
    "⚖️ Compare — \"GME vs AMC\"\n"
    "⭐ Watchlist — \"watch add NVDA\", \"watch remove NVDA\", \"watch list\"\n\n"
    "I'll also push real-time alerts and a daily digest for your watchlist.\n"
    "Send a ticker to get started."
)


DISCLAIMER = (
    "⚠️ I'm not a financial advisor and I don't make buy/sell calls. I'm a tool "
    "for analyzing market news and price action — ask me about a stock's news, "
    "price, or compare two, and decide for yourself."
)


def _timeframe_days(text):
    """A bare reply to 'today or this week?' -> news_days, or None if it isn't
    a timeframe (so we can fall back to normal parsing)."""
    t = text.strip().lower()
    if t in ("today", "now", "latest"):
        return 0
    if t == "yesterday":
        return 1
    if "week" in t:
        return 7
    if "month" in t:
        return 30
    m = re.search(r"(\d+)\s*day", t)
    return int(m.group(1)) if m else None


def handle(chat_id, text):
    if text.strip().lower().lstrip("/") in ("start", "help"):
        _send(chat_id, GREETING)
        return
    if _handle_watch_cmd(chat_id, text):   # "watch add/remove/list" — no LLM needed
        return
    pending = _pending_news.pop(chat_id, None)  # answering a "today or this week?" we asked
    if pending:
        days = _timeframe_days(text)
        if days is not None:
            handle_news(chat_id, pending, days)
            return
        # not a timeframe reply — treat as a fresh message
    parsed = parse_intent(text, _last_ticker.get(chat_id))  # NLU + remembered ticker
    if parsed.get("intent") == "advice":
        _send(chat_id, DISCLAIMER)
        return
    if parsed.get("intent") == "compare":
        handle_compare(chat_id, parsed.get("tickers") or [])
        return
    ticker = (parsed.get("ticker") or "").upper() or None
    if not ticker:
        _send(chat_id, "Which stock? e.g. 'latest news on GME' or 'what is GME trading at'.")
        return
    _last_ticker[chat_id] = ticker  # remember for follow-ups like "how much is it up"
    _save_state()
    if parsed.get("intent") == "price":
        handle_price(chat_id, ticker, parsed.get("price_metric") or "now")
    elif parsed.get("news_days") is None:
        _pending_news[chat_id] = ticker  # remember we asked, so the reply resolves to news
        _send(chat_id, f"News on {ticker} from when — today or this week?")
    else:
        handle_news(chat_id, ticker, parsed.get("news_days"))


# --------------------------------------------------------------------------
# Real-time new-article alert watcher
# --------------------------------------------------------------------------

def _read_watchlist(path="watchlist.txt"):
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return []
    return [ln.strip().upper() for ln in lines if ln.strip() and not ln.startswith("#")]


def _price_context(ticker):
    q = finnhub.get_quote(ticker)
    if not q:
        return ""
    pct = (q["price"] - q["open"]) / q["open"] * 100 if q["open"] else 0.0
    return f"Current: ${q['price']:.2f} ({pct:+.2f}% since open)."


def _alert_loop(chat_id, interval_min=30):
    """Push a richer alert the moment a NEW article appears for a watchlist
    ticker. A ticker's existing backlog is seeded silently the first time it's
    seen (process start OR newly added to the list), so adding a ticker never
    floods. `seen` is bounded so it can't grow forever."""
    seen, seeded = set(), set()
    while True:
        for ticker in _read_watchlist():
            try:
                items = tradingview.fetch_news(ticker, exchange=finnhub.get_exchange(ticker))
            except Exception:
                continue
            if ticker not in seeded:           # silent seed — no alerts for backlog
                seen.update(i.url for i in items)
                seeded.add(ticker)
                continue
            for item in items:
                if item.url in seen:
                    continue
                seen.add(item.url)
                ctx = _price_context(ticker)
                body = summarize(_ALERT_SYSTEM,
                                 f"Stock: {ticker}\nPrice context: {ctx}\n"
                                 f"Article: {item.headline}\n{item.body_text}")
                _send(chat_id, f"🔔 NEW — {ticker}\n\n{body}\n\n{ctx}")
        if len(seen) > 2000:                   # keep the de-dup set bounded
            seen = set(list(seen)[-1000:])
        time.sleep(interval_min * 60)


# --------------------------------------------------------------------------
# Daily digest
# --------------------------------------------------------------------------

def _seconds_until(hour):
    now = datetime.now()
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def _digest_loop(chat_id, hour):
    """Once a day at `hour` (local), push each watchlist ticker's sentiment."""
    while True:
        time.sleep(_seconds_until(hour))
        tickers = _read_watchlist()
        if not tickers:
            continue
        today = datetime.now().date()
        lines = [f"☀️ Daily digest — {today}"]
        for t in tickers:
            try:  # one bad ticker must not kill the whole digest
                items = _fetch_news_any_exchange(t, finnhub.get_exchange(t), today, today)
                rows, overall, _drop = analyze_articles(t, items)
                lines.append(f"{SIGNAL_EMOJI[overall]} {t} — {overall} ({len(rows)} articles)"
                             if rows else f"➖ {t} — no news")
            except Exception:
                lines.append(f"⚠️ {t} — lookup failed")
        _send(chat_id, "\n".join(lines))


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set.")
    # In a private chat, chat_id == user_id, so pushes go to the allowlisted user.
    uid = config.TELEGRAM_ALLOWED_USER_ID
    _send(uid, GREETING)  # greet on startup so the user knows the bot is live
    threading.Thread(target=_alert_loop, args=(uid,), daemon=True).start()
    threading.Thread(target=_digest_loop, args=(uid, config.DIGEST_HOUR), daemon=True).start()
    print(f"Gold Frog bot running — alerts on, daily digest at {config.DIGEST_HOUR}:00. Ctrl-C to stop.")
    offset = None
    while True:
        try:
            resp = requests.get(f"{_API}/getUpdates",
                                params={"timeout": 30, "offset": offset}, timeout=40).json()
        except requests.RequestException:
            continue
        if not resp.get("ok"):  # bad token / API error — surface it, don't spin silently
            print("Telegram getUpdates error:", resp.get("description", resp))
            time.sleep(5)
            continue
        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            user_id = (msg.get("from") or {}).get("id")
            chat_id = (msg.get("chat") or {}).get("id")
            if not chat_id:
                continue
            if user_id != config.TELEGRAM_ALLOWED_USER_ID:
                _send(chat_id, "Not authorized.")
                continue
            try:
                handle(chat_id, msg.get("text", ""))
            except Exception as e:  # never crash the loop — a crash drops the
                print("handle error:", e)  # offset ack and Telegram replays the update
                _send(chat_id, "Something went wrong handling that — try again.")


if __name__ == "__main__":
    main()
