"""
News fetcher — aggregates supporting evidence for a stock move from:
  1. Finnhub company news API
  2. Finnhub analyst recommendations
  3. SEC EDGAR 8-K filings (company-issued, primary source)
  4. RSS feeds (Reuters, MarketWatch, Benzinga, Investing.com)

All functions return plain dicts; no business logic here — that lives in main.py.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import feedparser
import requests

from config import FINNHUB_API_KEY, RSS_FEEDS, NEWS_LOOKBACK_HOURS
from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {"User-Agent": "HeeraMarketScanner/1.0 contact@heera.app"}


# ── Finnhub ───────────────────────────────────────────────────────────────────

def fetch_finnhub_news(symbol: str) -> list[dict]:
    """
    Fetch the most recent company-specific news articles from Finnhub.
    Looks back NEWS_LOOKBACK_HOURS hours.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    from_date = (datetime.utcnow() - timedelta(hours=NEWS_LOOKBACK_HOURS)).strftime("%Y-%m-%d")

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from":   from_date,
        "to":     today,
        "token":  FINNHUB_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        articles = resp.json()
    except Exception as exc:
        logger.warning(f"Finnhub news failed ({symbol}): {exc}")
        return []

    results = []
    for a in articles[:6]:  # cap at 6 most recent
        url_link = a.get("url", "")
        headline = a.get("headline", "").strip()
        if not url_link or not headline:
            continue
        results.append({
            "headline":     headline,
            "source":       a.get("source", "Finnhub"),
            "url":          url_link,
            "summary":      (a.get("summary") or "")[:300],
            "published_at": datetime.utcfromtimestamp(
                a.get("datetime", 0)
            ).strftime("%Y-%m-%d %H:%M UTC"),
        })

    return results


def fetch_analyst_actions(symbol: str) -> list[dict]:
    """
    Fetch the most recent analyst recommendation trend from Finnhub.
    Returns the last 2 periods (current month + prior month).
    """
    url = "https://finnhub.io/api/v1/stock/recommendation"
    params = {"symbol": symbol, "token": FINNHUB_API_KEY}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning(f"Finnhub analyst failed ({symbol}): {exc}")
        return []

    return data[:2] if data else []


def fetch_price_target(symbol: str) -> dict | None:
    """
    Fetch the current consensus price target from Finnhub.
    Returns None if unavailable or API error.
    """
    url = "https://finnhub.io/api/v1/stock/price-target"
    params = {"symbol": symbol, "token": FINNHUB_API_KEY}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("targetMean"):
            return {
                "mean":   round(data["targetMean"], 2),
                "high":   round(data.get("targetHigh", 0), 2),
                "low":    round(data.get("targetLow", 0), 2),
                "count":  data.get("numberOfAnalysts", 0),
                "last_updated": data.get("lastUpdated", ""),
            }
    except Exception as exc:
        logger.warning(f"Finnhub price target failed ({symbol}): {exc}")

    return None


# ── SEC EDGAR ─────────────────────────────────────────────────────────────────

def fetch_sec_filings(symbol: str) -> list[dict]:
    """
    Search SEC EDGAR full-text search for recent 8-K filings mentioning
    the ticker. Restricts to the last 2 days to surface fresh catalysts
    like convertible note offerings, earnings, etc.

    Source is always the company itself via SEC — qualifies as primary source.
    """
    from_dt = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
    to_dt   = datetime.utcnow().strftime("%Y-%m-%d")

    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q":         f'"{symbol}"',
        "forms":     "8-K",
        "dateRange": "custom",
        "startdt":   from_dt,
        "enddt":     to_dt,
    }

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
    except Exception as exc:
        logger.warning(f"SEC EDGAR search failed ({symbol}): {exc}")
        return []

    results = []
    for hit in hits[:3]:
        src = hit.get("_source", {})
        entity_name = src.get("display_names") or src.get("entity_name", "")
        filed = src.get("file_date", "")
        form  = src.get("form_type", "8-K")

        # Build a direct EDGAR filing link using accession number
        accession = src.get("accession_no", "").replace("-", "")
        cik = src.get("file_num", "").lstrip("0") if src.get("file_num") else ""
        if accession and cik:
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
        else:
            filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={symbol}&type=8-K&count=5"

        results.append({
            "form":        form,
            "filed":       filed,
            "entity":      entity_name,
            "url":         filing_url,
            "description": src.get("period_of_report", ""),
        })

    return results


# ── RSS feeds ─────────────────────────────────────────────────────────────────

def fetch_rss_mentions(symbol: str, company_name: str) -> list[dict]:
    """
    Scan configured RSS feeds for articles mentioning the ticker symbol
    or the company name in the headline. Only returns recent entries
    (within NEWS_LOOKBACK_HOURS).
    """
    mentions: list[dict] = []
    # Normalise for case-insensitive matching
    sym_upper  = symbol.upper()
    name_words = [w for w in company_name.upper().split() if len(w) > 3]

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.warning(f"RSS parse failed ({source_name}): {exc}")
            continue

        for entry in feed.entries[:30]:
            title   = (entry.get("title") or "").upper()
            summary = entry.get("summary") or entry.get("description") or ""
            link    = entry.get("link", "")

            # Match on ticker or significant words from company name
            hit = sym_upper in title or any(w in title for w in name_words)
            if not hit:
                continue

            mentions.append({
                "headline":     entry.get("title", "").strip(),
                "source":       source_name,
                "url":          link,
                "summary":      summary[:300],
                "published_at": entry.get("published", ""),
            })

        # Small delay to be a polite RSS consumer
        time.sleep(0.2)

    return mentions[:5]


# ── Aggregator ────────────────────────────────────────────────────────────────

def get_all_news(symbol: str, company_name: str) -> dict[str, Any]:
    """
    Gather all news signals for a stock and return them as a single dict.

    Keys:
      finnhub          — list of news articles
      analyst_actions  — list of recommendation-period dicts
      price_target     — dict or None
      sec_filings      — list of 8-K dicts
      rss_mentions     — list of RSS article dicts
    """
    logger.debug(f"Fetching news for {symbol} ({company_name})")
    return {
        "finnhub":         fetch_finnhub_news(symbol),
        "analyst_actions": fetch_analyst_actions(symbol),
        "price_target":    fetch_price_target(symbol),
        "sec_filings":     fetch_sec_filings(symbol),
        "rss_mentions":    fetch_rss_mentions(symbol, company_name),
    }


def has_supporting_news(news: dict) -> bool:
    """Return True if at least one news source returned results."""
    return bool(
        news.get("finnhub")
        or news.get("rss_mentions")
        or news.get("sec_filings")
    )
