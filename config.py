"""
Central configuration — all constants and env-var reads live here.
Import this module everywhere instead of calling os.getenv() directly.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")

# ── Finnhub ───────────────────────────────────────────────────────────────────
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

# ── Scanner thresholds ────────────────────────────────────────────────────────
MIN_MARKET_CAP: int = 250_000_000       # $250 million minimum
MIN_PRICE_CHANGE_PCT: float = 2.0       # ±2% minimum move
MAX_MOVERS_TO_FETCH: int = 100          # How many screener results to pull

# ── Yahoo Finance exchange codes that qualify as US-listed ────────────────────
US_EXCHANGES: set[str] = {"NMS", "NYQ", "ASE", "NGM", "NCM", "PCX", "BATS", "NYSEArca"}

# ── News sources: RSS feeds scraped for mentions ──────────────────────────────
RSS_FEEDS: dict[str, str] = {
    "Reuters":    "https://feeds.reuters.com/reuters/businessNews",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories",
    "Benzinga":   "https://www.benzinga.com/feed",
    "Investing.com": "https://www.investing.com/rss/news.rss",
}

# ── News lookback: how far back (hours) to search for supporting news ─────────
NEWS_LOOKBACK_HOURS: int = 24

# ── Watchlist — fixed universe tracked every scan cycle ──────────────────────
# No ±2% threshold for this list; any news or notable move is flagged.
WATCHLIST: dict[str, list[str]] = {
    "Space":    ["SPCX","RKLB","ASTS","LUNR","KRMN","IRDM","VOYG","FLY","BKSY","RDW","LMT","NOC","LHX"],
    "Pharma":   ["VRTX","AMLX","BBIO","PRAX","KNSA","TAK","RARE","CRNX","VKTX","INSM","IONS","DNLI","VNDA"],
    "Biotech":  ["KOD","CAPR","INVD","NUVL","KYMR","ARQT","BIVI","CNTB","ABCL","BEAM","ROIV","CGEM","ALT"],
    "Consumer": ["LEVI","WWW","KTB","CRI","LCII","NKE","WMT","AMZN","TSLA","CCL","WYNN","LVS","MCD","VICI"],
}

# Flat list for convenience
WATCHLIST_ALL: list[str] = [sym for syms in WATCHLIST.values() for sym in syms]

# ── State / deduplication ─────────────────────────────────────────────────────
STATE_DIR: str = "state"

# ── PDF output directory ──────────────────────────────────────────────────────
PDF_OUTPUT_DIR: str = "reports/output"
