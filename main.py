#!/usr/bin/env python3
"""
Heera Market Scanner — entry point.

Works in two modes:
  Automated  : triggered by GitHub Actions cron (every 10 min, Mon–Fri)
  Manual     : run locally with optional --force-session or --pdf-only flags

Usage:
  python main.py                              # auto-detect session
  python main.py --force-session pre_market  # force a specific session (testing)
  python main.py --pdf-only                  # generate today's PDF immediately
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from config import FINNHUB_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
from notifications.telegram_bot import send_stock_alert, send_session_start, send_pdf_report
from reports.pdf_generator import generate_pdf
from scanner.news_fetcher import get_all_news, has_supporting_news
from scanner.price_scanner import enrich_and_filter, get_movers
from state.state_manager import (
    get_all_flagged_today,
    is_already_notified,
    mark_notified,
)
from utils.logger import get_logger
from utils.market_hours import get_market_session, session_label

logger = get_logger(__name__)

# ── Config validation ─────────────────────────────────────────────────────────

def _check_config() -> bool:
    """Warn if required env variables are missing."""
    ok = True
    for name, val in [
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHANNEL_ID", TELEGRAM_CHANNEL_ID),
        ("FINNHUB_API_KEY",     FINNHUB_API_KEY),
    ]:
        if not val:
            logger.error(f"Missing required env variable: {name}")
            ok = False
    return ok


# ── Scan cycle ────────────────────────────────────────────────────────────────

def run_scan(session: str) -> None:
    """
    One 10-minute scan cycle.

    Steps:
      1. Fetch raw movers from Yahoo Finance screener
      2. Apply eligibility filters (US-listed, mkt cap, % move)
      3. Skip stocks already notified this session
      4. Fetch news/analyst data from Finnhub + RSS + SEC EDGAR
      5. Send Telegram alert (with or without news — flagged either way)
      6. Persist to daily state for deduplication
    """
    logger.info(f"─── Scan cycle: {session_label(session)} ───────────────────────")

    raw_movers = get_movers(session)
    qualified  = enrich_and_filter(raw_movers, session)

    if not qualified:
        logger.info("No qualifying movers this cycle")
        return

    news_cache: dict = {}

    for stock in qualified:
        symbol = stock["symbol"]

        # ── Deduplication: skip if we've already alerted this session ──────
        if is_already_notified(symbol, session):
            logger.debug(f"{symbol}: already notified in {session}, skipping")
            continue

        # ── Fetch all news signals ─────────────────────────────────────────
        news = get_all_news(symbol, stock["name"])
        news_cache[symbol] = news

        # ── Send alert regardless — flag no-news stocks with a warning ─────
        send_stock_alert(stock, news)

        if not has_supporting_news(news):
            logger.info(
                f"{symbol} ({stock['pct_change']:+.2f}%): "
                "alerted but no supporting news found in free sources"
            )
        else:
            logger.info(
                f"{symbol} ({stock['pct_change']:+.2f}%): alerted with news"
            )

        # ── Persist state ──────────────────────────────────────────────────
        mark_notified(symbol, session, stock)

    return news_cache


# ── Post-market PDF ───────────────────────────────────────────────────────────

def run_post_market() -> None:
    """Generate the daily PDF and send it to the Telegram channel."""
    logger.info("Post-market: generating PDF report")

    # Re-fetch news for all flagged stocks to enrich the PDF
    state    = get_all_flagged_today()
    news_map: dict = {}

    for session_key in ("pre_market", "market_hours"):
        for symbol, data in state.get(session_key, {}).items():
            if symbol not in news_map:
                news_map[symbol] = get_all_news(symbol, data.get("name", symbol))

    pdf_path = generate_pdf(news_cache=news_map)

    if pdf_path:
        total = sum(len(v) for v in state.values())
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        send_pdf_report(pdf_path, date_str=date_str, total_flagged=total)
    else:
        logger.info("No stocks flagged today — no PDF generated")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Heera Market Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--force-session",
        choices=["pre_market", "market_hours", "post_market"],
        metavar="SESSION",
        help="Override market session detection (pre_market | market_hours | post_market)",
    )
    p.add_argument(
        "--pdf-only",
        action="store_true",
        help="Skip scanning; generate and send the post-market PDF immediately",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if not _check_config():
        sys.exit(1)

    # ── PDF-only mode ──────────────────────────────────────────────────────
    if args.pdf_only:
        run_post_market()
        return

    # ── Determine session ──────────────────────────────────────────────────
    session = args.force_session or get_market_session()

    if session is None:
        logger.info("Outside active trading hours (or weekend) — nothing to do")
        sys.exit(0)

    if session == "post_market":
        run_post_market()
        return

    # ── Run scan ───────────────────────────────────────────────────────────
    run_scan(session)


if __name__ == "__main__":
    main()
