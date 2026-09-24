"""
Stocktwits retail-interest filter.

Used as a PRE-FILTER before flagging a stock:
  - If the stock is on Stocktwits' trending list → flag it
  - If not trending but the symbol has recent message activity → flag it
  - Otherwise → skip (only institutions are watching, user already has Stocktwits feed)

Trending list is fetched once per scan cycle and cached for 20 minutes.
Individual stream checks are done lazily only for notable movers not in trending.
"""

from __future__ import annotations

import time

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HeeraMarketScanner/1.0)"}

# ── Trending cache (shared across the scan cycle) ─────────────────────────────
_trending_symbols: set[str] = set()
_trending_fetched_at: float  = 0.0
_TRENDING_TTL = 20 * 60  # 20 minutes


def _fetch_trending() -> set[str]:
    """Fetch the Stocktwits trending tickers list (1 API call, ~30 symbols)."""
    global _trending_symbols, _trending_fetched_at

    if time.time() - _trending_fetched_at < _TRENDING_TTL:
        return _trending_symbols

    try:
        r = requests.get(
            "https://api.stocktwits.com/api/2/trending/symbols.json",
            headers=_HEADERS,
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        syms = {s["symbol"].upper() for s in data.get("symbols", [])}
        _trending_symbols    = syms
        _trending_fetched_at = time.time()
        logger.debug(f"Stocktwits trending: {sorted(syms)}")
        return syms
    except Exception as exc:
        logger.warning(f"Stocktwits trending fetch failed: {exc}")
        return _trending_symbols  # return stale cache rather than empty


def _check_stream_activity(symbol: str, min_messages: int = 5) -> bool:
    """
    Check if a symbol has recent Stocktwits activity.
    Returns True if the last page has at least `min_messages` posts.
    One API call per symbol — only used as fallback for non-trending stocks.
    """
    try:
        r = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
            headers=_HEADERS,
            timeout=8,
        )
        if r.status_code == 404:
            return False
        r.raise_for_status()
        messages = r.json().get("messages", [])
        return len(messages) >= min_messages
    except Exception as exc:
        logger.debug(f"Stocktwits stream check failed ({symbol}): {exc}")
        return False


def has_retail_interest(symbol: str, check_stream: bool = False) -> bool:
    """
    Return True if the stock has meaningful retail investor interest on Stocktwits.

    Pass check_stream=True for notable movers that aren't in the trending list —
    it fires an extra API call to verify activity via the symbol stream.
    """
    trending = _fetch_trending()
    if symbol.upper() in trending:
        return True
    if check_stream:
        return _check_stream_activity(symbol.upper())
    return False
