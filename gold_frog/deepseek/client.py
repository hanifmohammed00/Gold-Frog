"""
DeepSeek API wrapper.

Three calls power the bot: classify_news() reads one article's tone,
parse_intent() turns a chat message into a structured command, and summarize()
writes the free-text overviews and alerts.

Cache discipline: the classification system prompt is a constant from
prompts.py and is never mutated — all variable content rides in the user
message. Keeping the system block byte-identical is what earns DeepSeek's
cheaper prompt-cache pricing.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

from .. import config
from ..models import NewsClassification
from .prompts import NEWS_SYSTEM_PROMPT, build_news_user_message


class DeepSeekError(RuntimeError):
    pass


def _strip_code_fences(text: str) -> str:
    """Defensively remove ```json ... ``` wrappers if the model adds them."""
    t = text.strip()
    if t.startswith("```"):
        # drop the first fence line (``` or ```json) and a trailing fence
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _post_chat(system_prompt: str, user_message: str) -> str:
    """Single-shot chat completion. No tools, no multi-turn — by design."""
    if not config.DEEPSEEK_API_KEY:
        raise DeepSeekError("DEEPSEEK_API_KEY is not set")

    url = f"{config.DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.0,
        "stream": False,
    }

    last_err: Optional[Exception] = None
    for attempt in range(config.DEEPSEEK_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url, headers=headers, json=payload,
                timeout=config.DEEPSEEK_TIMEOUT_SECONDS,
            )
            # Rate limited — back off longer (honoring Retry-After) so a burst of
            # parallel classifications self-heals instead of all failing.
            if resp.status_code == 429 and attempt < config.DEEPSEEK_MAX_RETRIES:
                wait = float(resp.headers.get("Retry-After", 5 * (attempt + 1)))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as e:
            last_err = e
            if attempt < config.DEEPSEEK_MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise DeepSeekError(f"DeepSeek request failed: {last_err}")


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise DeepSeekError(f"Could not parse DeepSeek JSON: {e}\nraw: {raw!r}")


def classify_news(ticker: str, keyword_hit: bool, article_text: str) -> NewsClassification:
    """Classify one article's tone for a ticker. keyword_hit flags whether the
    rule-based keyword filter matched — passed to the model as context."""
    keyword_flag = "hit" if keyword_hit else "miss"
    raw = _post_chat(NEWS_SYSTEM_PROMPT, build_news_user_message(ticker, keyword_flag, article_text))
    d = _parse_json(raw)
    return NewsClassification(
        tone=d.get("tone", "neutral"),
        confidence=d.get("confidence", "low"),
        reason=d.get("reason", ""),
        insufficient_data=bool(d.get("insufficient_data", False)),
    )


_INTENT_SYSTEM = """You parse a user's chat message to a stock bot into JSON. Respond with ONLY a JSON object, no markdown:
{"ticker": <uppercase stock ticker or null>, "tickers": <array of uppercase tickers or null>, "intent": "news" | "price" | "compare" | "advice", "price_metric": "now" | "since_open" | "5d" | "1mo" | "1y" | null, "news_days": <integer or null>}

Rules:
- intent: "advice" if the user asks for a buy/sell/hold/short recommendation or whether they should trade (e.g. "should I short Apple", "is NVDA a buy", "should I sell"). "compare" if they want two or more stocks compared (e.g. "GME vs AMC", "compare NVDA and AMD"). "price" if they ask about price, quote, "trading at", or how much it is up/down. Otherwise "news".
- tickers: for intent "compare", the list of UPPERCASED tickers mentioned; otherwise null.
- ticker: the single stock the user means, UPPERCASED (e.g. "gme" -> "GME"). If they say "it", "the stock", "that one", or give no symbol, use the LAST_TICKER provided. null for compare.
- price_metric (only when intent is price): "now" = current price; "since_open" = change since today's open; "5d" = past 5 days / past week; "1mo" = past month; "1y" = past year. null otherwise.
- news_days (only when intent is news): 0 if they say "today" or "latest"/"latest news"/"most recent"; null if they give NO timeframe and do not say "latest" (e.g. just "news on GME"); 1 = yesterday; 7 = last week / this week; or the number of days mentioned ("past 3 days" -> 3, "past month" -> 30). null otherwise.

Respond with the JSON object only."""


def parse_intent(message: str, last_ticker: Optional[str]) -> dict:
    """Turn a free-text chat message into {ticker, intent, price_metric,
    news_days}, using last_ticker for pronouns/omitted symbols. {} on failure."""
    raw = _post_chat(_INTENT_SYSTEM, f"LAST_TICKER: {last_ticker or 'none'}\nMESSAGE: {message}")
    try:
        d = json.loads(_strip_code_fences(raw))
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def summarize(system_prompt: str, user_text: str) -> str:
    """Free-form text completion for the Overall Overview and real-time alerts.
    Returns plain text, or "" if the call fails."""
    try:
        return _post_chat(system_prompt, user_text).strip()
    except DeepSeekError:
        return ""

