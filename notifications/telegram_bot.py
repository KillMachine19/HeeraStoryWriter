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

    # ── News headlines (Yahoo first — fastest & most current) ──────────────
    # Merge Yahoo + Finnhub; deduplicate by URL; show publish time
    seen_urls: set[str] = set()
    top_news: list[dict] = []
    for article in (news.get("yahoo") or []) + (news.get("finnhub") or []) + (news.get("rss_mentions") or []):
        u = article.get("url", "")
        if u and u in seen_urls:
            continue
        if u:
            seen_urls.add(u)
        top_news.append(article)
        if len(top_news) == 3:
            break

    if top_news:
        lines.append("📰 *Today's News:*")
        for item in top_news:
            headline  = _esc(item.get("headline", "")[:90])
            url       = item.get("url", "")
            source    = _esc(item.get("source", ""))
            pub_time  = _esc(item.get("published_at", ""))
            time_tag  = f" _{pub_time}_" if pub_time else ""
            if url:
                lines.append(f"• [{headline}]({url}) _\\({source}\\)_{time_tag}")
            else:
                lines.append(f"• {headline} _\\({source}\\)_{time_tag}")
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
    has_any = top_news or sec or recs or news.get("yahoo")
    if not has_any:
        lines.append("⚠️ _No supporting news found in monitored sources\\. Verify on Bloomberg/Reuters manually\\._")

    _send_message("\n".join(lines))
    logger.info(f"Alert sent: {symbol} {pct:+.2f}%")


def send_cycle_divider(session: str, cycle_num: int | None = None) -> None:
    """
    Compact one-line divider sent at the start of every 35-minute scan cycle.
    Gives the channel a clear visual boundary between runs.
    """
    import pytz
    from datetime import datetime
    ET = pytz.timezone("America/New_York")
    time_str = datetime.now(ET).strftime("%I:%M %p ET").lstrip("0")
    label = session_label(session)
    num_tag = f" \\#{_esc(str(cycle_num))}" if cycle_num else ""
    _send_message(f"🔄 *{_esc(time_str)} — {_esc(label)}{num_tag}*")


def send_session_start(session: str) -> None:
    """
    Startup text sent ONCE at the beginning of each trading session.
    Fires before any scan alerts so the channel always has a clear marker.
    """
    from config import WATCHLIST, WATCHLIST_ALL
    from datetime import datetime
    import pytz

    ET     = pytz.timezone("America/New_York")
    now_et = datetime.now(ET)
    date_str = now_et.strftime("%B %d, %Y")
    time_str = now_et.strftime("%I:%M %p ET")

    session_windows = {
        "pre_market":   "4:00 AM – 9:30 AM ET  \\(1:30 PM – 7:00 PM IST\\)",
        "market_hours": "9:30 AM – 4:00 PM ET  \\(7:00 PM – 1:30 AM IST\\)",
    }
    window = session_windows.get(session, "")

    sector_summary = "  ".join(
        f"{_esc(sector)} \\({len(syms)}\\)"
        for sector, syms in WATCHLIST.items()
    )

    lines = [
        f"🚀 *HEERA SCANNER — {_esc(session_label(session).upper())}*",
        f"📅 {_esc(date_str)}  \\|  Started {_esc(time_str)}",
        f"⏱ {window}",
        "",
        f"*Universe:* {_esc(str(len(WATCHLIST_ALL)))} stocks · \\$250M\\+ market cap",
        f"_{sector_summary}_",
        "",
        "*Flags individual cards for:*",
        "🏦 Analyst upgrade / downgrade / price target",
        "📊 Earnings beat / miss / guidance",
        "🎤 CEO\\-CFO conference remarks",
        "📋 Company press releases",
        "📰 Notable moves \\(≥1\\.5%\\) with retail Stocktwits interest",
        "",
        "Scan interval: every 35 minutes\\. News ≤ 3h old only\\.",
    ]
    _send_message("\n".join(lines))


_SECTOR_EMOJI = {
    "Space":       "🚀",
    "Pharma":      "💊",
    "Biotech":     "🧬",
    "Consumer":    "🛍",
    "Real Estate": "🏢",
}

_SIGNAL_EMOJI = {
    "analyst":       "🏦",
    "earnings":      "📊",
    "ceo_statement": "🎤",
    "press_release": "📋",
    "news":          "📰",
    "none":          "📰",
}

_SIGNAL_LABEL = {
    "analyst":       "Analyst Action",
    "earnings":      "Earnings",
    "ceo_statement": "CEO/CFO Statement",
    "press_release": "Press Release",
    "news":          "News",
    "none":          "News",
}


def send_stock_card(item: dict, session: str) -> None:
    """
    Send an individual stock card for a flaggable watchlist stock.

    Format:
      {sector_emoji} *{TICKER}* · {Sector} · {arrow} {pct}%
      ${price} · {session}

      {signal_emoji} *{Signal Label}*
      • [Headline](url)  _(source · time)_
      • [Headline](url)  _(source · time)_
    """
    sym    = item["symbol"]
    sector = item.get("sector", "")
    pct    = item.get("pct_change", 0)
    price  = item.get("current_price", 0)
    sig    = item.get("signal_type", "news")
    news   = item.get("news", [])

    sect_e  = _SECTOR_EMOJI.get(sector, "📌")
    sig_e   = _SIGNAL_EMOJI.get(sig, "📰")
    sig_lbl = _SIGNAL_LABEL.get(sig, "News")
    arrow   = "📈" if pct >= 0 else "📉"
    pct_str = f"{pct:+.2f}%"
    sess_lbl = session_label(session)
    yf_url  = f"https://finance.yahoo.com/quote/{sym}/news"

    lines: list[str] = [
        f"{sect_e} *[{_esc(sym)}]({yf_url})* · {_esc(sector)} · {arrow} *{_esc(pct_str)}*",
        f"\\${_esc(str(price))} · {_esc(sess_lbl)}",
        "",
        f"{sig_e} *{_esc(sig_lbl)}*",
    ]

    if news:
        for article in news[:3]:
            headline = _esc(article.get("headline", "")[:90])
            url      = article.get("url", "")
            source   = _esc(article.get("source", ""))
            pub_time = _esc(article.get("published_at", ""))
            time_tag = f" · _{pub_time}_" if pub_time else ""
            art_sig  = article.get("signal_type", "news")
            art_e    = _SIGNAL_EMOJI.get(art_sig, "")

            if url:
                lines.append(f"• {art_e} [{headline}]({url})")
            else:
                lines.append(f"• {art_e} {headline}")
            if source or pub_time:
                lines.append(f"  _\\({source}\\){time_tag}_")
    else:
        lines.append(f"_No news in last 3h — [check Yahoo Finance]({yf_url})_")

    _send_message("\n".join(lines))
    logger.info(f"Stock card sent: {sym} {pct:+.2f}% [{sig}]")


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
