"""
Watchlist scanner — tracks a fixed universe of stocks across four sectors:
  Space · Pharma · Biotech · Consumer

Differences from the screener scanner:
  - No minimum % move threshold (any news or move is flagged)
  - All 52 stocks fetched in a single Yahoo Finance API call
  - Finnhub news fetched per stock; new articles deduplicated via state
  - Results always included in PDF, with "no news" note if nothing found
"""

from __future__ import annotations

import time
from typing import Any

import requests

from config import FINNHUB_API_KEY, WATCHLIST, WATCHLIST_ALL
from scanner.news_fetcher import fetch_finnhub_news, fetch_rss_mentions
from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HeeraMarketScanner/1.0)"}

# Minimum move to flag a price change even without news
_NOTABLE_MOVE_PCT = 1.5


# ── Price fetch ───────────────────────────────────────────────────────────────

def fetch_watchlist_prices(session: str) -> dict[str, dict]:
    """
    Fetch price data for all watchlist symbols in a single Yahoo Finance
    v7 quote API call.  Returns a dict keyed by symbol.
    """
    symbols_csv = ",".join(WATCHLIST_ALL)
    url = (
        "https://query1.finance.yahoo.com/v7/finance/quote"
        f"?symbols={symbols_csv}&formatted=false&lang=en-US&region=US"
    )

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        quotes = resp.json().get("quoteResponse", {}).get("result", [])
    except Exception as exc:
        logger.warning(f"Watchlist price fetch failed: {exc}")
        return {}

    results: dict[str, dict] = {}
    for q in quotes:
        symbol = q.get("symbol", "")
        if not symbol:
            continue

        if session == "pre_market":
            pct_change = q.get("preMarketChangePercent") or 0.0
            current_price = q.get("preMarketPrice") or q.get("regularMarketPrice")
        else:
            pct_change = q.get("regularMarketChangePercent") or 0.0
            current_price = q.get("regularMarketPrice")

        results[symbol] = {
            "symbol":        symbol,
            "name":          q.get("shortName") or q.get("longName") or symbol,
            "current_price": round(float(current_price or 0), 2),
            "prev_close":    round(float(q.get("regularMarketPreviousClose") or 0), 2),
            "pct_change":    round(float(pct_change), 2),
            "market_cap":    int(q.get("marketCap") or 0),
            "exchange":      q.get("fullExchangeName", ""),
        }

    logger.info(f"Watchlist prices fetched: {len(results)}/{len(WATCHLIST_ALL)} symbols")
    return results


# ── News fetch (with rate-limit spacing) ──────────────────────────────────────

def fetch_watchlist_news(
    seen_urls: set[str],
) -> dict[str, list[dict]]:
    """
    Fetch Finnhub news for every watchlist symbol.
    Returns only articles whose URL has NOT been seen before (deduplication).

    `seen_urls` is the set of URLs already sent to Telegram today — it is
    updated in-place so the caller can persist it back to state.
    """
    news_by_symbol: dict[str, list[dict]] = {}

    for symbol in WATCHLIST_ALL:
        articles = fetch_finnhub_news(symbol)
        fresh = [a for a in articles if a.get("url") and a["url"] not in seen_urls]

        if fresh:
            news_by_symbol[symbol] = fresh
            for a in fresh:
                seen_urls.add(a["url"])

        # Respect Finnhub free tier: ~60 calls/min → space them gently
        time.sleep(0.15)

    return news_by_symbol


# ── Assemble sector results ───────────────────────────────────────────────────

def build_watchlist_results(
    session: str,
    seen_urls: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """
    Combine price data and news for the full watchlist, grouped by sector.

    Each item in a sector list:
      symbol, name, current_price, prev_close, pct_change, market_cap,
      exchange, news (list), has_news (bool), has_notable_move (bool)
    """
    prices   = fetch_watchlist_prices(session)
    news_map = fetch_watchlist_news(seen_urls)

    results: dict[str, list[dict]] = {}

    for sector, symbols in WATCHLIST.items():
        sector_items = []
        for symbol in symbols:
            price = prices.get(symbol, {
                "symbol": symbol, "name": symbol,
                "current_price": 0, "prev_close": 0,
                "pct_change": 0, "market_cap": 0, "exchange": "",
            })
            news = news_map.get(symbol, [])

            sector_items.append({
                **price,
                "news":             news,
                "has_news":         bool(news),
                "has_notable_move": abs(price.get("pct_change", 0)) >= _NOTABLE_MOVE_PCT,
            })
        results[sector] = sector_items

    return results
