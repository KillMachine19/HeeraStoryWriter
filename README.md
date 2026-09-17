# Heera Market Scanner

Automated US stock market scanner that flags significant pre-market and market-hours movers,
sends real-time Telegram alerts, and generates a post-market PDF brief.

## What it does

| Phase | Time (ET) | Action |
|-------|-----------|--------|
| Pre-Market | 04:00 – 09:29 | Flags stocks ±2%+ from prior close with supporting news |
| Market Hours | 09:30 – 15:59 | Applies both pre-market and market-hours rules |
| Post-Market | 16:00 | Generates a PDF brief and sends it to Telegram |

**Eligibility criteria:** US-listed · Market cap > $250M · ±2% move minimum  
**News sources:** Finnhub · Reuters RSS · MarketWatch RSS · Benzinga RSS · SEC EDGAR (8-K)

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/your-username/heera-market-scanner.git
cd heera-market-scanner
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
# Fill in your keys — see below
```

**Keys needed:**

| Variable | Where to get it |
|----------|----------------|
| `TELEGRAM_BOT_TOKEN` | Message `@BotFather` on Telegram → `/newbot` |
| `TELEGRAM_CHANNEL_ID` | Your channel username (`@YourChannel`) or numeric ID |
| `FINNHUB_API_KEY` | Free signup at [finnhub.io](https://finnhub.io) |

### 3. Add your bot to the channel

In Telegram: open your channel → Add Members → search your bot → give it **admin** (post messages) rights.

### 4. Add GitHub Secrets (for Actions)

Go to your repo → Settings → Secrets and variables → Actions → add:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `FINNHUB_API_KEY`

---

## Running locally

```bash
# Auto-detect current market session
python main.py

# Force a specific session (useful for testing)
python main.py --force-session pre_market
python main.py --force-session market_hours

# Generate today's PDF immediately
python main.py --pdf-only
```

---

## GitHub Actions

Two workflows run automatically:

| Workflow | Schedule | What it does |
|----------|----------|-------------|
| `scanner.yml` | Every 10 min, Mon–Fri, 08:00–21:00 UTC | Scans for movers, sends Telegram alerts |
| `post_market.yml` | 20:15 & 21:15 UTC, Mon–Fri | Generates PDF, sends to Telegram, uploads as artifact |

You can also trigger both manually from the **Actions** tab.

---

## Project structure

```
heera-market-scanner/
├── main.py                      Entry point (local + GitHub Actions)
├── config.py                    All env vars and constants
├── scanner/
│   ├── price_scanner.py         Yahoo Finance screener → filter movers
│   └── news_fetcher.py          Finnhub + RSS + SEC EDGAR news aggregator
├── notifications/
│   └── telegram_bot.py          Telegram Bot API integration
├── reports/
│   └── pdf_generator.py         Post-market PDF builder (fpdf2)
├── state/
│   └── state_manager.py         Daily deduplication (JSON file per day)
├── utils/
│   ├── market_hours.py          Session detection (ET timezone)
│   └── logger.py                Structured logging to console + file
└── .github/workflows/
    ├── scanner.yml
    └── post_market.yml
```

---

## Notes

- **Free tier limits:** Finnhub free tier allows 60 API calls/minute. The scanner respects this.
- **News lag:** Free RSS feeds can have 10–30 minute lag. Sufficient for flagging; verify breaking stories on Bloomberg/Reuters directly.
- **No write-ups generated:** The tool surfaces data and source links. Editorial judgment and article writing remain with you.
- **GitHub Actions scheduling:** Cron jobs on GitHub Actions can lag up to 15 minutes under load. Acceptable for a 10-minute interval scanner.
