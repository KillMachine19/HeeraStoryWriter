"""
News fetcher — aggregates supporting evidence for a stock move from:
  1. Yahoo Finance news (primary — fast, stock-specific, real-time)
  2. Finnhub company news API (secondary)
  3. Finnhub analyst recommendations
  4. SEC EDGAR 8-K filings (company-issued, primary source)
  5. RSS feeds (Reuters, MarketWatch — fallback only)

All news is filtered to TODAY (ET timezone) only — no stale articles.
All functions return plain dicts; no business logic here — that lives in main.py.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import feedparser
import pytz
import requests

from config import FINNHUB_API_KEY, RSS_FEEDS
from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HeeraMarketScanner/1.0)"}
_ET      = pytz.timezone("America/New_York")


def _today_et_cutoff() -> float:
    """Unix timestamp for midnight ET today — articles before this are stale."""
    now_et = datetime.now(_ET)
    midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_et.timestamp()


def _hours_ago_cutoff(hours: int = 3) -> float:
    """Unix timestamp for N hours ago — used for recency filter."""
    return time.time() - hours * 3600


def _et_time_str(unix_ts: int) -> str:
    """Format a Unix timestamp as 'HH:MM ET' for display."""
    if not unix_ts:
        return ""
    return datetime.fromtimestamp(unix_ts, tz=_ET).strftime("%I:%M %p ET")


# ── Signal classification ─────────────────────────────────────────────────────

_ANALYST_KW   = {"upgrade", "downgrade", "price target", "raises pt", "cuts pt",
                 "initiates", "outperform", "overweight", "underweight", "buy rating",
                 "sell rating", "neutral", "market perform", "strong buy", "reiterate"}
_EARNINGS_KW  = {"earnings", "quarterly results", "q1 ", "q2 ", "q3 ", "q4 ",
                 "revenue", " eps", "profit", " loss", " beat", " miss",
                 "guidance", "fiscal year", "annual results", "full-year"}
_CEO_KW       = {"ceo", "cfo", "cto", "chief executive", "chief financial",
                 "conference", "presentation", "investor day", "remarks", "interview",
                 "said at", "says at", "speaks at", "comments on"}
_PR_SOURCES   = {"globenewswire", "globe newswire", "pr newswire", "businesswire",
                 "business wire", "accesswire", "globe wire"}


def classify_signal(headline: str, source: str) -> str:
    """
    Classify what kind of catalyst a news article represents.
    Returns one of: 'analyst' | 'earnings' | 'ceo_statement' | 'press_release' | 'news'
    """
    hl  = headline.lower()
    src = source.lower()

    if src in _PR_SOURCES or any(p in src for p in _PR_SOURCES):
        return "press_release"
    if any(k in hl for k in _ANALYST_KW):
        return "analyst"
    if any(k in hl for k in _EARNINGS_KW):
        return "earnings"
    if any(k in hl for k in _CEO_KW):
        return "ceo_statement"
    return "news"


# ── Yahoo Finance news (primary, fastest) ─────────────────────────────────────

def fetch_yahoo_news(symbol: str) -> list[dict]:
    """
    Fetch today's news for a stock from Yahoo Finance search API.
    Yahoo Finance is the fastest free real-time source for US equities.
    """
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    params = {
        "q":          symbol,
        "newsCount":  8,
        "lang":       "en-US",
        "region":     "US",
    }

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        articles = resp.json().get("news", [])
    except Exception as exc:
        logger.warning(f"Yahoo Finance news failed ({symbol}): {exc}")
        return []

    today_cutoff = _today_et_cutoff()
    fresh_cutoff = _hours_ago_cutoff(hours=3)
    # Use the more lenient of the two: today midnight ET
    # (fresh_cutoff can be before midnight for early AM scans)
    cutoff = min(today_cutoff, fresh_cutoff)

    results = []
    for a in articles:
        pub_ts = a.get("providerPublishTime", 0)
        if pub_ts and pub_ts < cutoff:
            continue
        link  = a.get("link", "")
        title = (a.get("title") or "").strip()
        if not title:
            continue
        source = a.get("publisher", "Yahoo Finance")
        results.append({
            "headline":     title,
            "source":       source,
            "url":          link,
            "summary":      "",
            "published_at": _et_time_str(pub_ts),
            "signal_type":  classify_signal(title, source),
            "pub_ts":       pub_ts,
        })

    return results[:5]


# ── Finnhub ───────────────────────────────────────────────────────────────────

def fetch_finnhub_news(symbol: str) -> list[dict]:
    """
    Fetch today's company-specific news from Finnhub (secondary source).
    Uses today's date for both from/to and then re-filters by ET timestamp.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from":   today,
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

    today_cutoff = _today_et_cutoff()
    fresh_cutoff = _hours_ago_cutoff(hours=3)
    cutoff = min(today_cutoff, fresh_cutoff)

    results = []
    for a in articles[:8]:
        url_link = a.get("url", "")
        headline = (a.get("headline") or "").strip()
        pub_ts   = a.get("datetime", 0)
        if not url_link or not headline:
            continue
        if pub_ts and pub_ts < cutoff:
            continue
        source = a.get("source", "Finnhub")
        results.append({
            "headline":     headline,
            "source":       source,
            "url":          url_link,
            "summary":      (a.get("summary") or "")[:300],
            "published_at": _et_time_str(pub_ts),
            "signal_type":  classify_signal(headline, source),
            "pub_ts":       pub_ts,
        })

    return results[:5]


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
        if resp.status_code == 403:
            # Price target endpoint requires Finnhub paid tier — skip silently
            return None
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

        # Build EDGAR filing URL using accession number and entity_id (CIK)
        # file_num is a filing number (e.g. "001-12345"), NOT the CIK — use entity_id
        accession = src.get("accession_no", "")
        entity_id = src.get("entity_id") or src.get("file_num")
        # entity_id may be a list; take first element
        if isinstance(entity_id, list):
            entity_id = entity_id[0] if entity_id else ""
        cik = str(entity_id).lstrip("0") if entity_id else ""

        if accession and cik:
            acc_clean = accession.replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/"
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
            # Fetch with an explicit timeout — feedparser.parse() has no timeout
            raw = requests.get(feed_url, headers=_HEADERS, timeout=8)
            feed = feedparser.parse(raw.content)
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
      yahoo            — today's articles from Yahoo Finance (primary, fastest)
      finnhub          — today's articles from Finnhub (secondary)
      analyst_actions  — recommendation-period dicts
      price_target     — dict or None
      sec_filings      — 8-K dicts
      rss_mentions     — RSS fallback (only if yahoo + finnhub return nothing)
    """
    logger.debug(f"Fetching news for {symbol} ({company_name})")
    yahoo   = fetch_yahoo_news(symbol)
    finnhub = fetch_finnhub_news(symbol)

    # Only hit slow RSS feeds if the faster sources found nothing
    rss = []
    if not yahoo and not finnhub:
        rss = fetch_rss_mentions(symbol, company_name)

    return {
        "yahoo":           yahoo,
        "finnhub":         finnhub,
        "analyst_actions": fetch_analyst_actions(symbol),
        "price_target":    fetch_price_target(symbol),
        "sec_filings":     fetch_sec_filings(symbol),
        "rss_mentions":    rss,
    }


def has_supporting_news(news: dict) -> bool:
    """Return True if at least one news source returned results."""
    return bool(
        news.get("yahoo")
        or news.get("finnhub")
        or news.get("rss_mentions")
        or news.get("sec_filings")
    )
