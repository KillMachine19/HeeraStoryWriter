"""
Telegram notifications — sends formatted alerts and summaries to the channel.

Uses the Telegram Bot API directly (no library dependency) so we stay lean.
All messages use MarkdownV2 parse mode.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
from utils.logger import get_logger
from utils.market_hours import session_label

logger = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_DOC = "https://api.telegram.org/bot{token}/sendDocument"


# ── Markdown escaping ─────────────────────────────────────────────────────────

_ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"

def _esc(text: str) -> str:
    """Escape special characters for MarkdownV2."""
    return re.sub(r"([" + re.escape(_ESCAPE_CHARS) + r"])", r"\\\1", str(text))


# ── Core send helpers ─────────────────────────────────────────────────────────

def _send_message(text: str) -> bool:
    """Post a MarkdownV2-formatted message to the channel. Returns success bool."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("Telegram credentials missing — check .env")
        return False

    url = _TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id":                  TELEGRAM_CHANNEL_ID,
        "text":                     text,
        "parse_mode":               "MarkdownV2",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if not resp.ok:
            logger.warning(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as exc:
        logger.error(f"Telegram send failed: {exc}")
        return False


def _send_document(file_path: str, caption: str = "") -> bool:
    """Send a file (PDF) to the channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("Telegram credentials missing — check .env")
        return False

    url = _TELEGRAM_DOC.format(token=TELEGRAM_BOT_TOKEN)
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption},
                files={"document": f},
                timeout=60,
            )
        return resp.ok
    except Exception as exc:
        logger.error(f"Telegram document send failed: {exc}")
        return False


# ── Public notification functions ─────────────────────────────────────────────

def send_stock_alert(stock: dict[str, Any], news: dict[str, Any]) -> None:
    """
    Send a rich stock-move alert.

    Stock dict keys: symbol, name, exchange, market_cap, pct_change,
                     current_price, prev_close, session
    News dict keys:  finnhub, analyst_actions, price_target,
                     sec_filings, rss_mentions
    """
    symbol    = stock["symbol"]
    name      = stock["name"]
    pct       = stock["pct_change"]
    price     = stock["current_price"]
    mkt_cap_m = round(stock["market_cap"] / 1_000_000, 1)
    session   = session_label(stock["session"])
    arrow     = "🔴" if pct < 0 else "🟢"

    lines: list[str] = [
        f"{arrow} *{_esc(symbol)}* — {_esc(name)}",
        f"Session: {_esc(session)}",
        f"Move: *{_esc(f'{pct:+.2f}%')}*  \\|  Price: \\${_esc(price)}",
        f"Market Cap: \\${_esc(mkt_cap_m)}M  \\|  Prev Close: \\${_esc(stock.get('prev_close', 'N/A'))}",
        "",
    ]

    # ── News headlines ──────────────────────────────────────────────────────
    top_news = (news.get("finnhub") or [])[:2] + (news.get("rss_mentions") or [])[:1]
    if top_news:
        lines.append("📰 *News:*")
        for item in top_news[:3]:
            headline = _esc(item.get("headline", "")[:90])
            url      = item.get("url", "")
            source   = _esc(item.get("source", ""))
            if url:
                lines.append(f"• [{headline}]({url}) _\\({source}\\)_")
            else:
                lines.append(f"• {headline} _\\({source}\\)_")
        lines.append("")

    # ── SEC filing ──────────────────────────────────────────────────────────
    sec = (news.get("sec_filings") or [])[:1]
    if sec:
        f = sec[0]
        lines.append("📋 *SEC Filing \\(Primary Source\\):*")
        lines.append(
            f"• {_esc(f['form'])} filed {_esc(f['filed'])} — [EDGAR]({f['url']})"
        )
        lines.append("")

    # ── Analyst consensus ───────────────────────────────────────────────────
    recs = news.get("analyst_actions") or []
    if recs:
        r = recs[0]
        lines.append(
            f"📊 *Analyst Consensus \\({_esc(r.get('period',''))}\\):* "
            f"Buy {_esc(r.get('buy',0))} \\| Hold {_esc(r.get('hold',0))} \\| Sell {_esc(r.get('sell',0))}"
        )

    # ── Price target ───────────────────────────────────────────────────────
    pt = news.get("price_target")
    if pt:
        lines.append(
            f"🎯 *Price Target:* Mean \\${_esc(pt['mean'])}  "
            f"\\(H: \\${_esc(pt['high'])} / L: \\${_esc(pt['low'])}\\) — {_esc(pt['count'])} analysts"
        )

    # ── No news fallback ───────────────────────────────────────────────────
    has_any = top_news or sec or recs
    if not has_any:
        lines.append("⚠️ _No supporting news found in monitored sources\\. Verify on Bloomberg/Reuters manually\\._")

    _send_message("\n".join(lines))
    logger.info(f"Alert sent: {symbol} {pct:+.2f}%")


def send_session_start(session: str, scan_count: int = 0) -> None:
    """Broadcast a brief message when a new session begins."""
    label = _esc(session_label(session))
    msg = (
        f"📡 *{label} Scanner Active*\n"
        f"Monitoring ±{_esc(str(2))}%\\+ movers \\(market cap \\>${_esc('250M')}\\)\n"
        f"Scan interval: every 10 minutes"
    )
    _send_message(msg)


def send_pdf_report(pdf_path: str, date_str: str, total_flagged: int) -> None:
    """Send the post-market PDF report as a document to the channel."""
    caption = f"📄 Post-Market Report — {date_str} | {total_flagged} stocks flagged today"
    ok = _send_document(pdf_path, caption=caption)
    if ok:
        logger.info(f"PDF report sent: {pdf_path}")
    else:
        logger.error("Failed to send PDF report via Telegram")


def send_error_alert(message: str) -> None:
    """Send a simple error/warning message to the channel."""
    _send_message(f"⚠️ *Scanner Warning*\n{_esc(message)}")
