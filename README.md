# quanttrading — Nifty 50 Momentum (India)

A broker-agnostic quant trading system for Indian markets. First build: an
**intraday breakout scanner** across the Nifty 50 — many small trades within the
hour, each entering on a short-term breakout and exiting on a small target/stop
(all squared off by end of day). Researched in a vectorized intraday backtester
with realistic Indian per-trade costs, then run live with **simulated (paper)
fills** fed by real-time Fyers data.

> ⚠️ Reality check: at many trades/hour, transaction costs (STT, brokerage,
> exchange fees, slippage) dominate P&L. The backtest models every cost per
> trade so results are honest — a scalping edge must clear that cost floor on
> *every* trade to be real.

Data access and order execution sit behind interfaces (`data/interfaces.py`,
`execution/interfaces.py`), so going live later = adding one `FyersBroker`
adapter, and adding F&O / stock futures = new symbols, not a rewrite.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is provisioned
automatically (pinned in `.python-version`).

```bash
uv sync                      # create venv + install deps
cp .env.example .env         # then fill in Fyers credentials
```

## Fyers authentication

1. Create an app at <https://myapi.fyers.in/dashboard/> to get `APP_ID` and
   `SECRET_ID`; set the redirect URI to match `FYERS_REDIRECT_URI`.
2. Put `FYERS_APP_ID`, `FYERS_SECRET_ID`, `FYERS_REDIRECT_URI` in `.env`.
3. Fyers access tokens **expire daily**. Regenerate before a session:

   ```bash
   uv run scripts/fyers_auth.py     # opens login, writes FYERS_ACCESS_TOKEN to .env
   ```

## Commands

All commands run from the project root. **Use `python` with the venv activated**
(this is the reliable path — `uv run` rebuilds on every call and can hang):

```bash
source .venv/bin/activate     # activate once per terminal — then use `python …`
```
> `uv run scripts/X.py` also works *if* it doesn't hang. If it prints nothing /
> never starts, use `python scripts/X.py` (venv active) or `uv run --no-sync scripts/X.py`.
> Throughout this file, `python …` assumes the venv is active.

### 1. One-time setup
```bash
uv sync                               # create venv + install all dependencies
cp .env.example .env                  # then paste your Fyers APP_ID / SECRET_ID into .env
```

### 2. Every day you trade (Fyers token expires daily)
```bash
python scripts/fyers_auth.py          # log in, refresh token → writes .env
```
> Run once each morning, or whenever you see `valid token` / `authenticate (-15/-16)`.

### 3. ⭐ The live options strategy (the real edge) + widget
```bash
python scripts/run_options_paper.py   # one manual step: manage/enter the strangle + track record
python scripts/run_options_daemon.py  # OR run continuously: auto-manages in market hours, logs P&L
python scripts/pnl_widget.py          # floating Apple-Stocks-style P&L card (reads the daemon log)
```
Typical setup — two Terminal tabs: the **daemon** in one, the **widget** in the
other. Stop with `Ctrl+C`, or `pkill -f run_options_daemon.py` / `pkill -f pnl_widget.py`.

### 3b. 💰 REAL MONEY — advisor / monitor / autonomous daemon
```bash
python scripts/run_live_daemon.py --mode alert  # ⭐ watches all day, macOS-notifies when to act
python scripts/run_live_daemon.py --mode shadow # full autonomous dry-run: logs broker-ready orders, places NONE
python scripts/shadow_report.py                 # readiness evidence (would-be track record + logged orders)
python scripts/live_advisor.py                  # manual: show the trade to place (or refusal)
python scripts/live_advisor.py --record         # log your actual fill
python scripts/live_monitor.py [--watch]        # track the open position
python scripts/live_monitor.py --close 1850     # record the exit (₹ debit paid)
```
> **Never places an order** — the only broker calls are read-only. At ₹1.5L the
> advisor offers the **validated naked strangle** (2× stop, take profit at 50%);
> smaller accounts fall back to defined-risk condors. Hard gates: ≤12% risk/trade,
> VIX≥13, **event veto** (no selling into Budget/RBI — `config/events.json`),
> halt at −20%. Read **[LIVE_TRADING.md](LIVE_TRADING.md)** first.
>
> Event veto also accepts external flags at `state/event_flags.json` (same schema)
> — a hook for a cron job or LLM news-checker to add vetoes. It can only *remove*
> trades, never add them.

### 4. The share-scalper (learning tool — this approach loses to costs)
```bash
python scripts/run_scalper_daemon.py  # autonomous dip-buy/rip-sell; shows GROSS − COSTS = NET
```
> Deliberately the falsified strategy — run it to *watch* transaction costs drain NET below GROSS.

### 5. Research & backtests
```bash
python scripts/run_options.py                                                 # options-selling backtest
python scripts/run_cycling_study.py                                           # ⭐ profit-target study (why we take 50%)
python scripts/download_data.py --resolution D --days 1825 --universe nifty200 # daily bars → cache
python scripts/run_ml.py --universe nifty200 --top-k 20                        # ML factor model
python scripts/run_ml_paper.py --top-k 20                                      # ML equity paper rebalance
python scripts/run_backtest.py                                                 # intraday breakout backtest
python scripts/run_paper.py                                                    # intraday breakout paper runner (early experiment)
python -m pytest -q                                                            # 36 unit tests
```

### 6. Save & sync to GitHub (private repo)
```bash
git add -A && git commit -m "your message"   # .env stays ignored automatically
git push                                       # push to the private repo
# fresh machine:
git clone https://github.com/aroraabeer-collab/quanttrading.git
cd quanttrading && uv sync && cp .env.example .env   # re-enter Fyers creds (not in the repo)
```

## Layout

| Path          | Purpose                                                        |
| ------------- | -------------------------------------------------------------- |
| `config/`     | Settings (pydantic) + Nifty 50 / Nifty 200 universes           |
| `data/`       | `DataProvider` interface, Fyers provider (backoff), parquet cache |
| `options/`    | ⭐ Options: pricing, vol-premium backtest, live paper trader    |
| `ml/`         | Cross-sectional ML factor model + risk-based portfolios        |
| `strategy/`   | Intraday strategies (breakout, mean-reversion, ORB)            |
| `backtest/`   | Cost models (intraday/delivery/options), vectorbt engine       |
| `execution/`  | `Broker` interface + `PaperBroker`                             |
| `live/`       | NSE market clock, paper runners, options daemon                |
| `scripts/`    | All entrypoints (see Commands above)                           |

See **[FINDINGS.md](FINDINGS.md)** for the full scorecard of what works and what doesn't.

## Status & scope

The one strategy with a real edge is **index option selling** (see FINDINGS.md):
a ~1-SD NIFTY strangle, 2× stop, **closed at 50% of credit** — 38.4%/yr and
Sharpe 1.08 over 5.4 years. Everything price-based (intraday + ML) was tested and
shown not to beat passive.

**Current state:** paper validation, ₹1.5L basis. **1 completed cycle** (+₹5,576).
The number that matters isn't the win — it's getting to ~20 cycles, including
losses, to see whether the backtest holds up live.

> Research/educational software. Not investment advice. Paper trading only —
> no real orders are placed by this codebase.
