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

import logging
import time
from typing import Any

import yfinance as yf

# Suppress yfinance's own noisy warnings for delisted/missing symbols
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

from config import FINNHUB_API_KEY, WATCHLIST, WATCHLIST_ALL
from scanner.news_fetcher import fetch_finnhub_news, fetch_yahoo_news
from utils.logger import get_logger

logger = get_logger(__name__)

# Minimum move to flag a price change even without news
_NOTABLE_MOVE_PCT = 1.5


# ── Price fetch ───────────────────────────────────────────────────────────────

def fetch_watchlist_prices(market_session: str) -> dict[str, dict]:
    """
    Fetch price data for all watchlist symbols via yfinance (handles Yahoo
    auth/TLS fingerprinting automatically).  Returns a dict keyed by symbol.
    """
    tickers = yf.Tickers(" ".join(WATCHLIST_ALL))
    results: dict[str, dict] = {}

    for symbol in WATCHLIST_ALL:
        try:
            t  = tickers.tickers[symbol]
            fi = t.fast_info

            last_price = float(fi.last_price or 0)
            prev_close = float(fi.previous_close or 0)
            market_cap = int(getattr(fi, "market_cap", 0) or 0)
            exchange   = str(getattr(fi, "exchange", "") or "")

            pct_change = 0.0
            if prev_close:
                pct_change = (last_price - prev_close) / prev_close * 100

            results[symbol] = {
                "symbol":        symbol,
                "name":          symbol,   # fast_info has no name; ticker is sufficient
                "current_price": round(last_price, 2),
                "prev_close":    round(prev_close, 2),
                "pct_change":    round(pct_change, 2),
                "market_cap":    market_cap,
                "exchange":      exchange,
            }
        except Exception as exc:
            logger.debug(f"{symbol}: price fetch skipped — {exc}")

    logger.info(f"Watchlist prices fetched: {len(results)}/{len(WATCHLIST_ALL)} symbols")
    return results


# ── News fetch (with rate-limit spacing) ──────────────────────────────────────

def fetch_watchlist_news(
    seen_urls: set[str],
) -> dict[str, list[dict]]:
    """
    Fetch today's news for every watchlist symbol.
    Yahoo Finance is checked first (fastest); Finnhub fills any gaps.
    Only articles with URLs not seen today are returned (deduplication).

    `seen_urls` is updated in-place — caller persists it back to state.
    """
    news_by_symbol: dict[str, list[dict]] = {}

    for symbol in WATCHLIST_ALL:
        combined: list[dict] = []
        local_seen: set[str] = set()

        # Yahoo Finance first — most up-to-date
        for a in fetch_yahoo_news(symbol):
            u = a.get("url", "")
            if u and u not in seen_urls and u not in local_seen:
                combined.append(a)
                local_seen.add(u)

        # Finnhub as secondary
        for a in fetch_finnhub_news(symbol):
            u = a.get("url", "")
            if u and u not in seen_urls and u not in local_seen:
                combined.append(a)
                local_seen.add(u)

        if combined:
            news_by_symbol[symbol] = combined
            seen_urls.update(local_seen)

        # Respect Finnhub free tier: ~60 calls/min
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
