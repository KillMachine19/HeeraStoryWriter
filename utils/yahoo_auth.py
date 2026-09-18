"""
Yahoo Finance authentication helper.

Yahoo requires a crumb + session cookies for most of its finance APIs.
This module fetches both once per process and reuses them for all calls.
"""

from __future__ import annotations

import requests
from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Module-level cache — one auth per process run
_session: requests.Session | None = None
_crumb:   str | None               = None


def get_yahoo_session() -> tuple[requests.Session, str]:
    """
    Return a (session, crumb) pair ready for Yahoo Finance API calls.
    Fetches once on first call; reuses thereafter.
    Append &crumb={crumb} to any query1/query2 URL that returns 401.
    """
    global _session, _crumb

    if _session is not None and _crumb is not None:
        return _session, _crumb

    _session = requests.Session()
    _session.headers.update(_HEADERS)

    try:
        # Step 1: hit the consent gateway to plant cookies
        _session.get("https://fc.yahoo.com", timeout=10)
        # Step 2: retrieve the crumb (needs the cookies from step 1)
        r = _session.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            timeout=10,
        )
        _crumb = r.text.strip()
        logger.debug(f"Yahoo crumb obtained ({len(_crumb)} chars)")
    except Exception as exc:
        logger.warning(f"Yahoo crumb fetch failed: {exc} — API calls may 401")
        _crumb = ""

    return _session, _crumb
