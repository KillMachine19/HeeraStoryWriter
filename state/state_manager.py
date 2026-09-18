"""
Daily state manager — tracks which stocks have already triggered a
Telegram alert today to prevent spam on repeated 10-minute scans.

State is persisted as a JSON file named by UTC date:
  state/YYYY-MM-DD.json

Structure:
  {
    "pre_market": {
      "AAPL": { "first_seen": "...", "pct_change": -3.1, ... }
    },
    "market_hours": { ... }
  }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import STATE_DIR
from utils.logger import get_logger

logger = get_logger(__name__)

_state_dir = Path(STATE_DIR)
_state_dir.mkdir(exist_ok=True)


def _today_file() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _state_dir / f"{date_str}.json"


def load_state() -> dict:
    path = _today_file()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("State file corrupted — starting fresh")
    return {}


def _save_state(state: dict) -> None:
    with open(_today_file(), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_already_notified(symbol: str, session: str) -> bool:
    """Return True if this symbol was already alerted in this session today."""
    state = load_state()
    return symbol in state.get(session, {})


def mark_notified(symbol: str, session: str, stock_data: dict, news: dict | None = None) -> None:
    """
    Record that we've sent an alert for this symbol in this session.
    Pass `news` to cache it alongside the stock data so the PDF generator
    can read it without making additional API calls.
    """
    state = load_state()
    if session not in state:
        state[session] = {}
    entry = {
        "first_seen": datetime.now(timezone.utc).isoformat(),
        **stock_data,
    }
    if news:
        entry["_news"] = news
    state[session][symbol] = entry
    _save_state(state)
    logger.debug(f"Marked {symbol} as notified for {session}")


def get_all_flagged_today() -> dict:
    """Return the full state for today — used by the PDF generator."""
    return load_state()


# ── Session startup tracking ──────────────────────────────────────────────────

def is_session_started(session: str) -> bool:
    """Return True if the startup message for this session was already sent today."""
    return load_state().get("_sessions_started", {}).get(session, False)


def mark_session_started(session: str) -> None:
    """Record that the startup message for this session has been sent."""
    state = load_state()
    if "_sessions_started" not in state:
        state["_sessions_started"] = {}
    state["_sessions_started"][session] = True
    _save_state(state)


def increment_cycle(session: str) -> int:
    """Bump the scan-cycle counter for this session and return the new value."""
    state = load_state()
    key = f"_cycle_{session}"
    state[key] = state.get(key, 0) + 1
    _save_state(state)
    return state[key]


# ── Watchlist news deduplication ──────────────────────────────────────────────

def get_seen_news_urls() -> set[str]:
    """Return the set of news article URLs already sent to Telegram today."""
    return set(load_state().get("_seen_news_urls", []))


def save_seen_news_urls(urls: set[str]) -> None:
    """Persist the updated set of seen news URLs."""
    state = load_state()
    state["_seen_news_urls"] = list(urls)
    _save_state(state)


# ── Watchlist results cache ───────────────────────────────────────────────────

def save_watchlist_results(results: dict) -> None:
    """Cache the latest watchlist scan results for use in PDF generation."""
    state = load_state()
    state["_watchlist"] = results
    _save_state(state)


def get_watchlist_results() -> dict:
    """Retrieve the cached watchlist results (empty dict if not yet scanned)."""
    return load_state().get("_watchlist", {})
