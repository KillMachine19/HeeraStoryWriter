"""
Watchlist scanner — tracks a fixed universe across five sectors:
  Space · Pharma · Biotech · Consumer · Real Estate

A stock is flagged (marked as 'flaggable') when ALL of:
  1. Market cap ≥ $250M
  2. Has a notable price move (≥ 1.5%) OR key signal news
  3. Has retail interest on Stocktwits (trending OR active stream)
     — waived for high-priority catalysts: analyst, earnings, ceo_statement

Key signals that bypass the Stocktwits gate:
  'analyst'       — upgrade / downgrade / price target change
  'earnings'      — quarterly results / beat / miss / guidance
  'ceo_statement' — CEO/CFO remarks at conference or press interview
"""

from __future__ import annotations

import logging
import time
from typing import Any

import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

from config import MIN_MARKET_CAP, WATCHLIST, WATCHLIST_ALL
from scanner.news_fetcher import fetch_finnhub_news, fetch_yahoo_news
from scanner.stocktwits_filter import has_retail_interest
from utils.logger import get_logger

logger = get_logger(__name__)

_NOTABLE_MOVE_PCT = 1.5
_HIGH_PRIORITY    = {"analyst", "earnings", "ceo_statement", "press_release"}


# ── Price fetch ───────────────────────────────────────────────────────────────

def fetch_watchlist_prices(market_session: str) -> dict[str, dict]:
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
                "name":          symbol,
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


# ── News fetch ────────────────────────────────────────────────────────────────

def fetch_watchlist_news(seen_urls: set[str]) -> dict[str, list[dict]]:
    news_by_symbol: dict[str, list[dict]] = {}

    for symbol in WATCHLIST_ALL:
        combined: list[dict] = []
        local_seen: set[str] = set()

        for a in fetch_yahoo_news(symbol):
            u = a.get("url", "")
            if u and u not in seen_urls and u not in local_seen:
                combined.append(a)
                local_seen.add(u)

        for a in fetch_finnhub_news(symbol):
            u = a.get("url", "")
            if u and u not in seen_urls and u not in local_seen:
                combined.append(a)
                local_seen.add(u)

        if combined:
            news_by_symbol[symbol] = combined
            seen_urls.update(local_seen)

        time.sleep(0.15)

    return news_by_symbol


# ── Flagging logic ────────────────────────────────────────────────────────────

def _top_signal(news: list[dict]) -> str:
    """Return the highest-priority signal type from a list of articles."""
    priority = ["earnings", "analyst", "ceo_statement", "press_release", "news"]
    found = {a.get("signal_type", "news") for a in news}
    for p in priority:
        if p in found:
            return p
    return "news"


def _is_flaggable(
    symbol: str,
    market_cap: int,
    pct_change: float,
    news: list[dict],
) -> tuple[bool, str]:
    """
    Return (should_flag, reason_string).
    Applies market cap, movement, signal, and Stocktwits filters.
    """
    if market_cap < MIN_MARKET_CAP:
        return False, "below_market_cap"

    has_move    = abs(pct_change) >= _NOTABLE_MOVE_PCT
    top_sig     = _top_signal(news) if news else "none"
    is_key_sig  = top_sig in _HIGH_PRIORITY

    if not has_move and not is_key_sig:
        return False, "no_move_no_signal"

    # High-priority catalyst bypasses Stocktwits gate
    if is_key_sig:
        return True, top_sig

    # Notable move: require Stocktwits retail interest
    # check_stream=True fires an extra API call only for notable movers
    if has_move:
        if has_retail_interest(symbol, check_stream=True):
            return True, f"move_{top_sig}"
        return False, "no_retail_interest"

    return False, "filtered"


# ── Assemble sector results ───────────────────────────────────────────────────

def build_watchlist_results(
    session: str,
    seen_urls: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """
    Combine price data and news for the full watchlist, grouped by sector.

    Each item in a sector list includes:
      symbol, name, current_price, prev_close, pct_change, market_cap,
      exchange, news, has_news, has_notable_move, flaggable, signal_type
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

            pct    = price.get("pct_change", 0)
            mktcap = price.get("market_cap", 0)

            flaggable, flag_reason = _is_flaggable(symbol, mktcap, pct, news)
            top_sig = _top_signal(news) if news else "none"

            sector_items.append({
                **price,
                "sector":           sector,
                "news":             news,
                "has_news":         bool(news),
                "has_notable_move": abs(pct) >= _NOTABLE_MOVE_PCT,
                "flaggable":        flaggable,
                "signal_type":      top_sig,
                "flag_reason":      flag_reason,
            })
        results[sector] = sector_items

    flagged_total = sum(
        1 for items in results.values() for item in items if item["flaggable"]
    )
    logger.info(f"Watchlist scan: {flagged_total} stocks flaggable across {len(results)} sectors")
    return results
