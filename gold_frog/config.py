"""
Central configuration.

Secrets (API keys, the bot token) are read from environment variables. Real
values live in a gitignored .env file (see .env.example), loaded on import so
the bot just works when run from the project root.
"""

import os


def _load_dotenv(path=".env"):
    """Minimal stdlib .env loader — reads KEY=value lines into the environment
    without overriding anything already set. No dependency."""
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_dotenv()

# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Only this Telegram user ID is answered — everyone else is rejected.
TELEGRAM_ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0"))
# Local hour (0-23) to push the daily watchlist digest.
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "8"))

# ---------------------------------------------------------------------------
# DeepSeek API (article classification, intent parsing, summaries)
# ---------------------------------------------------------------------------

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_TIMEOUT_SECONDS = 30
DEEPSEEK_MAX_RETRIES = 2

# ---------------------------------------------------------------------------
# Data sources (see SOURCES.md)
# ---------------------------------------------------------------------------

# Finnhub: exchange lookup + live quote.
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# TradingView session cookie — optional. The public news endpoints work without
# it; set it only if TradingView starts returning CAPTCHA challenges.
TRADINGVIEW_COOKIE = os.getenv("TRADINGVIEW_COOKIE", "")
