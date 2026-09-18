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

from config import FINNHUB_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, WATCHLIST
from notifications.telegram_bot import (
    send_stock_alert,
    send_session_start,
    send_cycle_divider,
    send_pdf_report,
    send_watchlist_sector_update,
    send_watchlist_quiet,
)
from reports.pdf_generator import generate_pdf
from scanner.news_fetcher import get_all_news, has_supporting_news  # noqa: F401
from scanner.price_scanner import enrich_and_filter, get_movers
from scanner.watchlist_scanner import build_watchlist_results
from state.state_manager import (
    get_all_flagged_today,
    get_seen_news_urls,
    increment_cycle,
    is_already_notified,
    is_session_started,
    mark_notified,
    mark_session_started,
    save_seen_news_urls,
    save_watchlist_results,
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
      3. Sort by absolute % move, keep top 7 new stocks per cycle
      4. Skip stocks already notified this session
      5. Fetch news/analyst data from Finnhub + RSS + SEC EDGAR
      6. Send Telegram alert (with or without news — flagged either way)
      7. Persist to daily state for deduplication
    """
    MAX_ALERTS_PER_CYCLE = 7

    logger.info(f"─── Scan cycle: {session_label(session)} ───────────────────────")

    raw_movers = get_movers(session)
    qualified  = enrich_and_filter(raw_movers, session)

    if not qualified:
        logger.info("No qualifying movers this cycle")
        return

    # Sort by biggest move first, then cap to top 7 *new* stocks
    qualified.sort(key=lambda s: abs(s["pct_change"]), reverse=True)

    news_cache: dict = {}
    alerts_sent = 0

    for stock in qualified:
        symbol = stock["symbol"]

        # ── Cap: stop once we've sent 7 new alerts this cycle ─────────────
        if alerts_sent >= MAX_ALERTS_PER_CYCLE:
            logger.info(f"Reached {MAX_ALERTS_PER_CYCLE}-alert cap for this cycle")
            break

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

        # ── Persist state (news cached so PDF needs no re-fetch) ───────────
        mark_notified(symbol, session, stock, news=news)
        alerts_sent += 1

    return news_cache


# ── Watchlist scan ────────────────────────────────────────────────────────────

def run_watchlist_scan(session: str) -> None:
    """
    Scan the fixed watchlist universe every cycle.
    Results are grouped by sector and sent to Telegram.
    Saves results to state for PDF inclusion.
    """
    logger.info("Watchlist scan starting")

    seen_urls = get_seen_news_urls()
    results   = build_watchlist_results(session, seen_urls)

    for sector in WATCHLIST:
        items = results.get(sector, [])
        active = [i for i in items if i.get("has_news") or i.get("has_notable_move")]
        if active:
            send_watchlist_sector_update(sector, items)
        else:
            send_watchlist_quiet(sector)

    save_watchlist_results(results)
    save_seen_news_urls(seen_urls)
    logger.info("Watchlist scan complete")


# ── Post-market PDF ───────────────────────────────────────────────────────────

def run_post_market() -> None:
    """
    Generate the daily PDF and send it to the Telegram channel.
    News was cached in the state file during each scan alert, so no re-fetching needed.
    """
    logger.info("Post-market: generating PDF report")

    # News is already embedded in state entries via mark_notified(news=...)
    # generate_pdf() reads state and uses _news key from each entry directly
    pdf_path = generate_pdf()

    if pdf_path:
        state    = get_all_flagged_today()
        total    = sum(len(v) for k, v in state.items() if not k.startswith("_"))
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
    p.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously every 10 minutes (for local testing without GitHub Actions)",
    )
    return p.parse_args()


def _run_once(session: str) -> None:
    """Execute one full scan cycle for the given session."""
    if not is_session_started(session):
        send_session_start(session)
        mark_session_started(session)

    cycle_num = increment_cycle(session)
    send_cycle_divider(session, cycle_num)

    run_watchlist_scan(session)
    run_scan(session)


def main() -> None:
    import time as _time

    args = _parse_args()

    if not _check_config():
        sys.exit(1)

    # ── PDF-only mode ──────────────────────────────────────────────────────
    if args.pdf_only:
        run_post_market()
        return

    # ── Loop mode (local testing) — runs every 10 min until Ctrl-C ────────
    if args.loop:
        session = args.force_session
        logger.info("Loop mode: running every 10 minutes. Press Ctrl-C to stop.")
        while True:
            current = session or get_market_session()
            if current is None:
                logger.info("Outside market hours — waiting 10 minutes")
            elif current == "post_market":
                run_post_market()
            else:
                _run_once(current)
            logger.info("Sleeping 10 minutes until next cycle …")
            _time.sleep(600)

    # ── Single run (GitHub Actions / manual) ──────────────────────────────
    session = args.force_session or get_market_session()

    if session is None:
        logger.info("Outside active trading hours (or weekend) — nothing to do")
        sys.exit(0)

    if session == "post_market":
        run_post_market()
        return

    _run_once(session)


if __name__ == "__main__":
    main()
