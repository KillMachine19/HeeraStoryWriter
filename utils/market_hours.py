"""
Market session detection based on US Eastern Time.

Sessions:
  pre_market   — 04:00 to 09:29 ET, Mon–Fri
  market_hours — 09:30 to 15:59 ET, Mon–Fri
  post_market  — 16:00 to 19:59 ET, Mon–Fri (PDF generation window)
"""

from datetime import datetime, time
from typing import Literal, Optional
import pytz

ET = pytz.timezone("America/New_York")

MarketSession = Literal["pre_market", "market_hours", "post_market"]

_PRE_MARKET_START  = time(4,  0)
_PRE_MARKET_END    = time(9, 30)
_MARKET_OPEN       = time(9, 30)
_MARKET_CLOSE      = time(16, 0)
_POST_MARKET_END   = time(20, 0)


def now_et() -> datetime:
    """Current datetime in US/Eastern."""
    return datetime.now(ET)


def is_weekday() -> bool:
    return now_et().weekday() < 5  # Mon=0 … Fri=4


def get_market_session() -> Optional[MarketSession]:
    """
    Return the current US market session, or None when outside all sessions
    (weekends, overnight).
    """
    if not is_weekday():
        return None

    t = now_et().time()

    if _PRE_MARKET_START <= t < _PRE_MARKET_END:
        return "pre_market"
    if _MARKET_OPEN <= t < _MARKET_CLOSE:
        return "market_hours"
    if _MARKET_CLOSE <= t < _POST_MARKET_END:
        return "post_market"
    return None


def session_label(session: MarketSession) -> str:
    """Human-readable session label for notifications."""
    return {
        "pre_market":   "Pre-Market",
        "market_hours": "Market Hours",
        "post_market":  "Post-Market",
    }[session]
