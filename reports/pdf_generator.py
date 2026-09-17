"""
Post-market PDF report generator.

Layout:
  Page 1 — Cover: date, session stats, total stocks flagged
  Page 2+ — Summary table: Ticker | Move | Session | Trigger | Time
  Remaining pages — Per-stock detail sections

Each stock section contains:
  - Company name, ticker, move %, market cap
  - Session timeline (first seen pre-market vs market hours)
  - Top 3 news headlines + source links
  - SEC filings if any
  - Analyst consensus + price target
  - "Story angles" bullet points to help write next-day article

Uses fpdf2 (lightweight, no C dependencies, works on GitHub Actions).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

from config import PDF_OUTPUT_DIR, STATE_DIR
from state.state_manager import get_all_flagged_today
from utils.logger import get_logger
from utils.market_hours import session_label

logger = get_logger(__name__)

_OUTPUT_DIR = Path(PDF_OUTPUT_DIR)
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Colour palette ────────────────────────────────────────────────────────────
_BLACK  = (15,  15,  15)
_WHITE  = (255, 255, 255)
_ACCENT = (0,   82,  204)   # deep blue
_GREEN  = (0,  153,  76)
_RED    = (204,  0,   0)
_LIGHT  = (245, 245, 248)
_GRAY   = (120, 120, 120)


class MarketReport(FPDF):
    """Custom FPDF subclass with shared header/footer and helper methods."""

    def __init__(self, report_date: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.report_date = report_date
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(left=16, top=16, right=16)

    # ── FPDF overrides ────────────────────────────────────────────────────────

    def header(self):
        if self.page_no() == 1:
            return  # Cover page has its own full header
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*_GRAY)
        self.cell(0, 6, f"Heera Market Scanner  |  {self.report_date}", align="L")
        self.ln(6)
        self.set_draw_color(*_ACCENT)
        self.set_line_width(0.3)
        self.line(16, self.get_y(), 194, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_GRAY)
        self.cell(0, 5, f"Page {self.page_no()}  |  For editorial use only. Not investment advice.", align="C")

    # ── Shared drawing helpers ────────────────────────────────────────────────

    def filled_rect(self, x, y, w, h, color):
        self.set_fill_color(*color)
        self.rect(x, y, w, h, "F")

    def h1(self, text: str, color=_BLACK):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*color)
        self.multi_cell(0, 8, text)
        self.ln(2)

    def h2(self, text: str, color=_ACCENT):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*color)
        self.cell(0, 7, text, ln=True)
        self.ln(1)

    def body(self, text: str, color=_BLACK):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*color)
        self.multi_cell(0, 5, text)

    def small(self, text: str, color=_GRAY):
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*color)
        self.multi_cell(0, 4.5, text)

    def divider(self):
        self.set_draw_color(*_LIGHT)
        self.set_line_width(0.3)
        self.line(16, self.get_y(), 194, self.get_y())
        self.ln(4)


# ── Cover page ────────────────────────────────────────────────────────────────

def _cover(pdf: MarketReport, state: dict) -> None:
    pdf.add_page()

    # Background band at top
    pdf.filled_rect(0, 0, 210, 50, _ACCENT)

    pdf.set_y(14)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*_WHITE)
    pdf.cell(0, 10, "Heera Market Scanner", align="C", ln=True)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, "Post-Market Daily Brief", align="C", ln=True)

    pdf.set_y(60)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*_BLACK)
    pdf.cell(0, 8, pdf.report_date, align="C", ln=True)

    # ── Stats row ─────────────────────────────────────────────────────────
    pdf.ln(8)
    pre_count    = len(state.get("pre_market", {}))
    mkt_count    = len(state.get("market_hours", {}))
    total        = pre_count + mkt_count

    col_w = 58
    x_start = 16
    labels = [
        ("Pre-Market", str(pre_count)),
        ("Market Hours", str(mkt_count)),
        ("Total Flagged", str(total)),
    ]

    for i, (label, value) in enumerate(labels):
        x = x_start + i * col_w
        pdf.filled_rect(x, pdf.get_y(), col_w - 4, 22, _LIGHT)
        pdf.set_xy(x, pdf.get_y() + 3)
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(col_w - 4, 8, value, align="C")
        pdf.set_xy(x, pdf.get_y() + 9)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_GRAY)
        pdf.cell(col_w - 4, 5, label, align="C")

    pdf.ln(32)
    pdf.divider()

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*_GRAY)
    pdf.multi_cell(
        0, 5,
        "This report is generated for editorial use only. All stock movements and "
        "news sources are flagged for further verification. This is not investment advice."
    )


# ── Summary table ─────────────────────────────────────────────────────────────

def _summary_table(pdf: MarketReport, state: dict) -> None:
    pdf.add_page()
    pdf.h2("Summary Table — All Flagged Stocks")
    pdf.ln(2)

    # Table header
    headers  = ["Ticker", "Company", "Session", "Move %", "Price", "Mkt Cap"]
    col_widths = [22, 62, 28, 20, 22, 24]

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(*_ACCENT)
    pdf.set_text_color(*_WHITE)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, border=0, fill=True, align="C")
    pdf.ln()

    # Table rows
    pdf.set_font("Helvetica", "", 8)
    fill = False
    for session_key in ("pre_market", "market_hours"):
        for symbol, data in state.get(session_key, {}).items():
            pct   = data.get("pct_change", 0)
            color = _GREEN if pct >= 0 else _RED

            bg = _LIGHT if fill else _WHITE
            pdf.set_fill_color(*bg)
            pdf.set_text_color(*_BLACK)

            row = [
                symbol,
                (data.get("name") or "")[:30],
                session_label(session_key),
                f"{pct:+.2f}%",
                f"${data.get('current_price', 0)}",
                f"${round(data.get('market_cap', 0) / 1_000_000, 0):.0f}M",
            ]

            for i, cell in enumerate(row):
                if i == 3:  # Move % column — colour coded
                    pdf.set_text_color(*color)
                else:
                    pdf.set_text_color(*_BLACK)
                pdf.cell(col_widths[i], 6, cell, fill=True, align="C")
            pdf.ln()
            fill = not fill

    pdf.ln(4)


# ── Per-stock detail ──────────────────────────────────────────────────────────

def _stock_section(pdf: MarketReport, symbol: str, data: dict, session_key: str) -> None:
    """Render one stock's full detail section."""
    pdf.add_page()

    pct   = data.get("pct_change", 0)
    color = _GREEN if pct >= 0 else _RED
    arrow = "▲" if pct >= 0 else "▼"

    # ── Stock header ──────────────────────────────────────────────────────
    pdf.filled_rect(16, pdf.get_y(), 178, 18, _LIGHT)
    pdf.set_xy(18, pdf.get_y() + 2)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(60, 7, symbol)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_BLACK)
    pdf.cell(80, 7, (data.get("name") or "")[:45])
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*color)
    pdf.cell(0, 7, f"{arrow} {pct:+.2f}%", align="R")
    pdf.ln(12)

    # ── Key metrics row ───────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_GRAY)
    mkt_cap_m = round(data.get("market_cap", 0) / 1_000_000, 1)
    metrics = (
        f"Session: {session_label(session_key)}   |   "
        f"Price: ${data.get('current_price', 0)}   |   "
        f"Prev Close: ${data.get('prev_close', 'N/A')}   |   "
        f"Mkt Cap: ${mkt_cap_m}M   |   "
        f"Exchange: {data.get('exchange', 'N/A')}   |   "
        f"First Flagged: {data.get('first_seen', 'N/A')[:16]} UTC"
    )
    pdf.multi_cell(0, 5, metrics)
    pdf.ln(3)
    pdf.divider()

    news = data.get("_news", {})

    # ── News / Catalyst ───────────────────────────────────────────────────
    pdf.h2("News & Catalyst")

    finnhub_news = (news.get("finnhub") or [])[:3]
    rss_news     = (news.get("rss_mentions") or [])[:2]
    all_news     = finnhub_news + rss_news

    if all_news:
        for item in all_news[:4]:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*_BLACK)
            headline = (item.get("headline") or "")[:110]
            pdf.multi_cell(0, 5, f"• {headline}")
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_GRAY)
            source  = item.get("source", "")
            pub_at  = item.get("published_at", "")
            url     = item.get("url", "")
            meta    = f"  Source: {source}   |   {pub_at}"
            if url:
                meta += f"   |   URL: {url[:80]}"
            pdf.multi_cell(0, 4, meta)
            pdf.ln(1)
    else:
        pdf.body(
            "No news found in monitored sources (Finnhub, Reuters, MarketWatch, Benzinga).\n"
            "Recommend manual verification on Bloomberg or Dow Jones Newswires.",
            color=_RED,
        )

    pdf.ln(3)

    # ── SEC filings ───────────────────────────────────────────────────────
    sec = news.get("sec_filings") or []
    if sec:
        pdf.h2("SEC Filings (Primary Source)")
        for f in sec[:2]:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*_BLACK)
            pdf.cell(0, 5, f"• {f.get('form','8-K')} — Filed: {f.get('filed','')}", ln=True)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_GRAY)
            pdf.multi_cell(0, 4, f"  EDGAR: {f.get('url','')[:90]}")
            pdf.ln(1)
        pdf.ln(2)

    # ── Analyst consensus ─────────────────────────────────────────────────
    recs = news.get("analyst_actions") or []
    pt   = news.get("price_target")
    if recs or pt:
        pdf.h2("Analyst Data")
        if recs:
            r = recs[0]
            pdf.body(
                f"Consensus ({r.get('period','latest')}): "
                f"Buy {r.get('buy',0)}  Hold {r.get('hold',0)}  "
                f"Sell {r.get('sell',0)}  Strong Buy {r.get('strongBuy',0)}"
            )
        if pt:
            pdf.body(
                f"Price Target — Mean: ${pt['mean']}  "
                f"High: ${pt['high']}  Low: ${pt['low']}  "
                f"({pt['count']} analysts, updated {pt.get('last_updated','')})"
            )
        pdf.ln(3)

    # ── Story angles ──────────────────────────────────────────────────────
    pdf.h2("Story Angles for Tomorrow")
    angles = _build_story_angles(symbol, data, news, session_key)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_BLACK)
    for angle in angles:
        pdf.multi_cell(0, 5, f"  → {angle}")
        pdf.ln(0.5)


def _build_story_angles(symbol: str, data: dict, news: dict, session_key: str) -> list[str]:
    """Generate bullet-point story angles based on available data."""
    angles = []
    pct  = data.get("pct_change", 0)
    name = data.get("name", symbol)
    mkt  = round(data.get("market_cap", 0) / 1_000_000, 0)

    direction = "surged" if pct > 0 else "fell"
    angles.append(
        f"{name} ({symbol}) {direction} {abs(pct):.1f}% in {session_label(session_key).lower()} "
        f"— what drove the move?"
    )

    sec = news.get("sec_filings") or []
    if sec:
        angles.append(f"Company filed an 8-K: confirm whether it relates to the price action.")

    recs = news.get("analyst_actions") or []
    if recs:
        r = recs[0]
        total = sum([r.get("buy",0), r.get("hold",0), r.get("sell",0)])
        if total > 0:
            angles.append(
                f"Analyst standing: {r.get('buy',0)}/{total} buys — "
                f"has Street sentiment shifted after today's move?"
            )

    pt = news.get("price_target")
    if pt and data.get("current_price"):
        upside = round((pt["mean"] - data["current_price"]) / data["current_price"] * 100, 1)
        angles.append(
            f"Consensus price target ${pt['mean']} implies {upside:+.1f}% from today's close "
            f"— does the move change the thesis?"
        )

    finnhub_news = news.get("finnhub") or []
    if finnhub_news:
        angles.append(
            f"Latest news headline: '{(finnhub_news[0].get('headline',''))[:80]}' "
            f"— verify this is the primary catalyst."
        )

    angles.append(
        f"${mkt:.0f}M market cap — check if move is outsized relative to float and average volume."
    )

    return angles


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_pdf(news_cache: dict | None = None) -> str | None:
    """
    Build the full post-market PDF and return the output file path.

    news_cache: optional dict keyed by symbol with pre-fetched news data
                (used when main.py passes cached news to avoid re-fetching).
    """
    state = get_all_flagged_today()
    total_stocks = sum(len(v) for v in state.values())

    if total_stocks == 0:
        logger.info("No stocks flagged today — skipping PDF generation")
        return None

    today_et = datetime.now(timezone.utc).strftime("%B %d, %Y")
    filename  = f"heera_report_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf"
    filepath  = str(_OUTPUT_DIR / filename)

    pdf = MarketReport(report_date=today_et)

    _cover(pdf, state)
    _summary_table(pdf, state)

    for session_key in ("pre_market", "market_hours"):
        for symbol, data in state.get(session_key, {}).items():
            # Attach cached news if provided
            if news_cache and symbol in news_cache:
                data["_news"] = news_cache[symbol]
            _stock_section(pdf, symbol, data, session_key)

    pdf.output(filepath)
    logger.info(f"PDF generated: {filepath}  ({total_stocks} stocks)")
    return filepath
