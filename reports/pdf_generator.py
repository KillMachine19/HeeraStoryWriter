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
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

# ── Unicode safety ────────────────────────────────────────────────────────────
# fpdf2's built-in Helvetica only covers latin-1.  News headlines and company
# names can contain em dashes, curly quotes, etc.  Normalise them before every
# string that goes into a cell or multi_cell.

_CHAR_MAP = {
    "—": " - ",   # em dash
    "–": " - ",   # en dash
    "‘": "'",     # left single quote
    "’": "'",     # right single quote
    "“": '"',     # left double quote
    "”": '"',     # right double quote
    "…": "...",   # ellipsis
    "·": "-",     # middle dot
    "•": "*",     # bullet
    "™": "(TM)",  # trademark
    "®": "(R)",   # registered
    "©": "(C)",   # copyright
}

def _s(text) -> str:
    """Return a latin-1-safe version of text for fpdf2 Helvetica cells."""
    t = str(text)
    for ch, repl in _CHAR_MAP.items():
        t = t.replace(ch, repl)
    # Normalise remaining accented characters to their ASCII base
    t = unicodedata.normalize("NFKD", t)
    return t.encode("latin-1", errors="replace").decode("latin-1")

from config import PDF_OUTPUT_DIR, STATE_DIR, WATCHLIST
from state.state_manager import get_all_flagged_today, get_watchlist_results
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
        self.multi_cell(0, 8, _s(text))
        self.ln(2)

    def h2(self, text: str, color=_ACCENT):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*color)
        self.cell(0, 7, _s(text), ln=True)
        self.ln(1)

    def body(self, text: str, color=_BLACK):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*color)
        self.multi_cell(0, 5, _s(text))

    def small(self, text: str, color=_GRAY):
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*color)
        self.multi_cell(0, 4.5, _s(text))

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
                _s(symbol),
                _s((data.get("name") or "")[:30]),
                _s(session_label(session_key)),
                _s(f"{pct:+.2f}%"),
                _s(f"${data.get('current_price', 0)}"),
                _s(f"${round(data.get('market_cap', 0) / 1_000_000, 0):.0f}M"),
            ]

            for i, cell in enumerate(row):
                if i == 3:  # Move % column - colour coded
                    pdf.set_text_color(*color)
                else:
                    pdf.set_text_color(*_BLACK)
                pdf.cell(col_widths[i], 6, cell, fill=True, align="C")
            pdf.ln()
            fill = not fill

    pdf.ln(4)


# ── Per-stock detail ──────────────────────────────────────────────────────────

def _stock_section(pdf: MarketReport, symbol: str, data: dict, session_key: str) -> None:
    """
    Compact per-stock section — one page maximum per stock.
    Designed as a quick-reference brief, not a deep dive.
    """
    pct   = data.get("pct_change", 0)
    color = _GREEN if pct >= 0 else _RED
    arrow = "+" if pct >= 0 else "-"
    mkt_cap_m = round(data.get("market_cap", 0) / 1_000_000, 1)
    news = data.get("_news", {})

    # ── Stock header bar ──────────────────────────────────────────────────
    pdf.filled_rect(16, pdf.get_y(), 178, 14, _LIGHT)
    pdf.set_xy(18, pdf.get_y() + 2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(22, 6, _s(symbol))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_BLACK)
    pdf.cell(96, 6, _s((data.get("name") or "")[:50]))
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*color)
    pdf.cell(0, 6, _s(f"{arrow} {pct:+.2f}%"), align="R")
    pdf.ln(9)

    # ── One-line metrics ──────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*_GRAY)
    pdf.cell(
        0, 5,
        _s(
            f"{session_label(session_key)}  |  Price ${data.get('current_price',0)}  "
            f"|  Prev Close ${data.get('prev_close','N/A')}  |  Cap ${mkt_cap_m}M  "
            f"|  {data.get('exchange','N/A')}  |  Flagged {str(data.get('first_seen',''))[:16]} UTC"
        ),
        ln=True,
    )
    pdf.ln(2)
    pdf.divider()

    # ── News & catalyst (top 3) ───────────────────────────────────────────
    all_news = ((news.get("finnhub") or [])[:2] + (news.get("rss_mentions") or [])[:1])
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 5, "NEWS / CATALYST", ln=True)

    if all_news:
        for item in all_news[:3]:
            headline = _s((item.get("headline") or "")[:95])
            source   = _s(item.get("source", ""))
            url      = item.get("url", "")
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(*_BLACK)
            pdf.cell(0, 4.5, f"* {headline}", ln=True)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_GRAY)
            line = f"  {source}"
            if url:
                line += f"  -  {url[:65]}"
            pdf.cell(0, 3.5, _s(line), ln=True)
            pdf.ln(0.5)
    else:
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*_RED)
        pdf.cell(0, 5, "No news found - verify manually on Bloomberg / Reuters", ln=True)

    pdf.ln(2)

    # ── SEC filing (one line) ─────────────────────────────────────────────
    sec = (news.get("sec_filings") or [])[:1]
    if sec:
        f = sec[0]
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(0, 5, "SEC FILING (PRIMARY SOURCE)", ln=True)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*_BLACK)
        pdf.cell(0, 4, _s(f"  {f.get('form','8-K')} filed {f.get('filed','')}  -  {f.get('url','')[:65]}"), ln=True)
        pdf.ln(2)

    # ── Analyst snapshot (one line) ───────────────────────────────────────
    recs = news.get("analyst_actions") or []
    pt   = news.get("price_target")
    if recs or pt:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(0, 5, "ANALYST SNAPSHOT", ln=True)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*_BLACK)
        if recs:
            r = recs[0]
            pdf.cell(
                0, 4,
                _s(f"  Consensus ({r.get('period','')}):  Buy {r.get('buy',0)}  "
                   f"Hold {r.get('hold',0)}  Sell {r.get('sell',0)}  "
                   f"Strong Buy {r.get('strongBuy',0)}"),
                ln=True,
            )
        if pt:
            upside = ""
            if data.get("current_price"):
                up = round((pt["mean"] - data["current_price"]) / data["current_price"] * 100, 1)
                upside = f"  ({up:+.1f}% from close)"
            pdf.cell(
                0, 4,
                _s(f"  Price Target: Mean ${pt['mean']}  H ${pt['high']}  L ${pt['low']}{upside}"),
                ln=True,
            )
        pdf.ln(2)

    # ── Story angles ──────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 5, "STORY ANGLES", ln=True)
    angles = _build_story_angles(symbol, data, news, session_key)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*_BLACK)
    for angle in angles[:4]:   # cap at 4 for compactness
        pdf.cell(0, 4.5, _s(f"  -> {angle[:100]}"), ln=True)
    pdf.ln(3)


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


# ── Watchlist section ─────────────────────────────────────────────────────────

_SECTOR_EMOJI = {"Space": "Rocket", "Pharma": "Pharma", "Biotech": "Biotech", "Consumer": "Consumer"}


def _watchlist_section(pdf: MarketReport, watchlist_results: dict) -> None:
    """
    Watchlist universe section — always included in the PDF.
    Organized by sector; stocks with news/notable moves shown in detail.
    Quiet sectors get a single "no new developments" line.
    """
    pdf.add_page()
    pdf.h1("Watchlist Universe", color=_ACCENT)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*_GRAY)
    pdf.cell(0, 5, "Fixed universe — Space / Pharma / Biotech / Consumer. No 2% threshold.", ln=True)
    pdf.ln(2)
    pdf.divider()

    for sector in WATCHLIST:
        items = watchlist_results.get(sector, [])

        # ── Sector header ─────────────────────────────────────────────────
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(0, 6, _s(f"{sector} Sector"), ln=True)

        active = [i for i in items if i.get("has_news") or i.get("has_notable_move")]

        if not active:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_GRAY)
            pdf.cell(0, 5, "  No new developments today.", ln=True)
            pdf.ln(2)
            continue

        for item in active:
            if pdf.get_y() > 250:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 10)
                pdf.set_text_color(*_ACCENT)
                pdf.cell(0, 6, _s(f"{sector} Sector (continued)"), ln=True)

            sym   = item["symbol"]
            pct   = item.get("pct_change", 0)
            price = item.get("current_price", 0)
            color = _GREEN if pct >= 0 else _RED
            arrow = "+" if pct >= 0 else ""

            # Symbol + move on one line
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_text_color(*_BLACK)
            pdf.cell(22, 5, _s(sym))
            pdf.set_text_color(*color)
            pdf.cell(22, 5, _s(f"{arrow}{pct:.2f}%"))
            pdf.set_text_color(*_GRAY)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.cell(0, 5, _s(f"${price}"), ln=True)

            # News headlines
            for article in item.get("news", [])[:2]:
                headline = _s((article.get("headline") or "")[:90])
                source   = _s(article.get("source", ""))
                url      = article.get("url", "")
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*_BLACK)
                pdf.cell(0, 4, f"  * {headline}", ln=True)
                pdf.set_text_color(*_GRAY)
                line = f"    {source}"
                if url:
                    line += f"  -  {url[:60]}"
                pdf.cell(0, 3.5, _s(line), ln=True)

            pdf.ln(1.5)

        pdf.ln(2)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_pdf() -> str | None:
    """
    Build the full post-market PDF and return the output file path.
    News is read directly from each state entry's '_news' key,
    which was stored by mark_notified() during the scan — no API calls here.
    """
    state            = get_all_flagged_today()
    watchlist_results = get_watchlist_results()
    screener_stocks  = {k: v for k, v in state.items() if not k.startswith("_")}
    total_stocks     = sum(len(v) for v in screener_stocks.values())

    # Generate PDF even when only watchlist has data
    has_watchlist = bool(watchlist_results)
    if total_stocks == 0 and not has_watchlist:
        logger.info("No stocks flagged today — skipping PDF generation")
        return None

    today_et = datetime.now(timezone.utc).strftime("%B %d, %Y")
    filename  = f"heera_report_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf"
    filepath  = str(_OUTPUT_DIR / filename)

    pdf = MarketReport(report_date=today_et)

    _cover(pdf, state)
    if total_stocks:
        _summary_table(pdf, state)

    # Watchlist section first — always included when scan has run
    if has_watchlist:
        _watchlist_section(pdf, watchlist_results)

    for session_key in ("pre_market", "market_hours"):
        stocks = screener_stocks.get(session_key, {})
        if not stocks:
            continue
        pdf.add_page()
        pdf.h1(f"{'Pre-Market' if session_key == 'pre_market' else 'Market Hours'} - Detail", color=_ACCENT)
        pdf.divider()

        for symbol, data in stocks.items():
            if pdf.get_y() > 230:
                pdf.add_page()
            _stock_section(pdf, symbol, data, session_key)

    pdf.output(filepath)
    logger.info(f"PDF generated: {filepath}  ({total_stocks} stocks)")
    return filepath
