"""
Price scanner — fetches movers from Yahoo Finance screener and applies
our eligibility filters (US-listed, market cap >$250M, ±2% move).

During pre_market  : filters on preMarketChangePercent
During market_hours: filters on regularMarketChangePercent
"""

from __future__ import annotations

import requests
from typing import Optional
from utils.logger import get_logger
from config import (
    MIN_MARKET_CAP,
    MIN_PRICE_CHANGE_PCT,
    MAX_MOVERS_TO_FETCH,
    US_EXCHANGES,
)

logger = get_logger(__name__)

# Yahoo Finance screener IDs to pull
_SCREENER_IDS = ["day_gainers", "day_losers", "most_actives"]

_SCREENER_URL = (
    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    "?formatted=false&lang=en-US&region=US&scrIds={scr_id}&count={count}"
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HeeraMarketScanner/1.0)",
    "Accept": "application/json",
}


def _fetch_screener(scr_id: str) -> list[dict]:
    """Pull raw quotes from one Yahoo Finance screener."""
    url = _SCREENER_URL.format(scr_id=scr_id, count=MAX_MOVERS_TO_FETCH)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return (
            data.get("finance", {})
                .get("result", [{}])[0]
                .get("quotes", [])
        )
    except Exception as exc:
        logger.warning(f"Screener fetch failed ({scr_id}): {exc}")
        return []


def get_movers(session: str) -> list[dict]:
    """
    Fetch and deduplicate movers across all screener lists.
    Returns raw Yahoo Finance quote objects.
    """
    seen: set[str] = set()
    movers: list[dict] = []

    for scr_id in _SCREENER_IDS:
        for quote in _fetch_screener(scr_id):
            symbol = quote.get("symbol", "")
            if symbol and symbol not in seen:
                seen.add(symbol)
                movers.append(quote)

    logger.info(f"Fetched {len(movers)} unique raw quotes")
    return movers


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def enrich_and_filter(movers: list[dict], session: str) -> list[dict]:
    """
    Apply eligibility rules to raw Yahoo Finance quotes and return
    a clean list of dicts ready for downstream processing.

    During pre_market   → uses preMarketChangePercent
    During market_hours → uses regularMarketChangePercent
    """
    qualified: list[dict] = []

    for quote in movers:
        symbol: str = quote.get("symbol", "")
        if not symbol:
            continue

        # Use short code for filtering; keep full name for display
        exchange: str = quote.get("exchange", "")
        exchange_display: str = quote.get("fullExchangeName", exchange)
        market_cap: float = _safe_float(quote.get("marketCap"))
        name: str = quote.get("shortName") or quote.get("longName") or symbol

        # Determine the relevant price-change field for this session
        if session == "pre_market":
            pct_change = _safe_float(quote.get("preMarketChangePercent"))
            current_price: Optional[float] = quote.get("preMarketPrice")
        else:
            pct_change = _safe_float(quote.get("regularMarketChangePercent"))
            current_price = quote.get("regularMarketPrice")

        prev_close: Optional[float] = quote.get("regularMarketPreviousClose")

        # ── Eligibility gates ──────────────────────────────────────────────
        if exchange not in US_EXCHANGES:
            continue
        if market_cap < MIN_MARKET_CAP:
            continue
        if abs(pct_change) < MIN_PRICE_CHANGE_PCT:
            continue
        if not current_price:
            continue

        qualified.append({
            "symbol":       symbol,
            "name":         name,
            "exchange":     exchange_display,
            "market_cap":   int(market_cap),
            "pct_change":   round(pct_change, 2),
            "current_price": round(_safe_float(current_price), 2),
            "prev_close":   round(_safe_float(prev_close), 2) if prev_close else None,
            "session":      session,
        })

    logger.info(f"Qualified after filters: {len(qualified)} stocks")
    return qualified
