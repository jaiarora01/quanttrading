# Live Trading Rules — read this before every trade

**Account: ₹1,50,000 · 1 lot max · manual execution · take profit at 50%**

> Realistic expectation: **~₹4,800/month (~38%/yr on margin)** *if* it behaves like
> the backtest. That is not a promise — it's a 5.4-year simulation, and you have
> barely any live cycles yet.

## What you're trading and why

The one edge this project found is **selling index option premium** — India VIX
runs ~26% above realized volatility, and you get paid for that gap.

At ₹1.5L you can margin **the validated strategy**: a short ~1-SD NIFTY strangle
with a 2× stop, closed at 50% of credit. That's the version with 5.4 years behind
it (38.4%/yr, Sharpe 1.08). Smaller accounts can't margin it and fall back to a
defined-risk iron condor — the advisor picks automatically.

## The rules (non-negotiable)

1. **1 lot maximum.** Never add lots — not to scale up, not to recover a loss.
2. **One position at a time.** Never stack trades.
3. **Only when the advisor says OK.** If it refuses, you skip the cycle. The
   refusal *is* the value — it's protecting you from a bad-sized trade.
4. **Take profit at 50% of credit.** Validated: this beats holding to expiry by a
   wide margin (38.4%/yr vs 21.9%). The last of the premium is where the tail
   lives — don't reach for it.
5. **Respect the 2× stop.** Don't "wait for it to come back."
6. **Never widen a loser** or roll to avoid taking a loss. Take the loss.
7. **Halt at −20% of account (−₹30,000).** Stop trading entirely. Review, don't
   revenge-trade. The advisor enforces this automatically.
8. **Only sell when VIX ≥ 13.** Cheap premium isn't worth the tail.
9. **Never sell into a scheduled event.** The advisor auto-vetoes Budget (hard)
   and RBI/election-class events (within 3 days). Keep `config/events.json`
   current — verify RBI MPC dates at rbi.org.in.
10. **Record every real fill.** That's how we learn what actually happens vs the model.

### ⚠️ A naked strangle's stop is a plan, not a guarantee

At ₹1.5L the advisor offers the **naked** strangle. Your −₹18,000 stop is 12% of
the account — survivable. But an overnight gap can blow straight through it: the
backtest's worst single loss was **−₹37,000 (25% of your account)**, and a real
crash could be worse. Never leave a naked position unmonitored.

**₹1.5L is exactly one lot of margin — you have zero buffer.** Margin requirements
spike in volatile stretches, precisely when you're losing. **₹1.8–2L would let the
same trade breathe.**

## Pre-trade checklist (every single time)

- [ ] Token refreshed today (`python scripts/fyers_auth.py`)
- [ ] No position currently open
- [ ] Advisor returned a **ticket**, not a refusal
- [ ] Max loss shown is **≤ 12% of account** (~₹18,000)
- [ ] I can afford to lose that **entirely, today**, without it affecting my life
- [ ] I placed **every leg** shown on the ticket
- [ ] I recorded my **actual** fill (`--record`, or let the daemon auto-detect)

## The daily flow

**Automated (recommended)** — one command watches all day and pings you:
```bash
python scripts/fyers_auth.py                      # 1. refresh token (each morning)
python scripts/run_live_daemon.py --mode alert    # 2. leave it running
#    → macOS notification when it's time to place or close
#    → you click in the Fyers app; it auto-detects and records your real fills
```

**Manual (step-by-step):**
```bash
python scripts/live_advisor.py               # see the ticket — or a refusal
python scripts/live_advisor.py --record      # log your ACTUAL fill
python scripts/live_monitor.py               # check it (--watch to poll)
python scripts/live_monitor.py --close 1850  # record the exit (₹ debit paid)
```

### Daemon modes
| Mode | What it does |
|---|---|
| `shadow` | Decides and logs only — silent. Builds the validation record. **Default.** |
| `alert` | Also fires a macOS notification when action is due. **Run this.** |
| `auto` | Would place real orders — **refused**: needs a registered Algo-ID (below). |

Decisions log to `state/decisions.jsonl` — after a few weeks that's your evidence
for whether the automation actually makes the right calls.

## What this software will NOT do

- **It never places an order.** Verified: the only broker calls in the whole
  codebase are `optionchain`, `history`, `quotes`, `positions` — all read-only.
- **It can't predict.** ~78–91% win rate means losses still come, and they're
  bigger than wins. That's the deal you're accepting.

## The road to full automation (and where you are on it)

Automated order placement has required a **SEBI-registered Algo-ID** since April
2026. Running an unregistered algo risks regulatory action and broker suspension.
So full auto is gated behind real steps, in order:

```
0. Prove it in SHADOW mode          ← YOU ARE HERE
1. Register an Algo-ID with Fyers    ← paperwork only you can do; the legal gate
2. Implement FyersBroker(Broker)     ← wire real orders (payloads already built)
3. Kill-switches + daily-loss cap
4. Flip to auto — real money
```

### Step 0 — shadow mode (running now, zero orders)
```bash
python scripts/run_live_daemon.py --mode shadow   # full autonomous dry-run
python scripts/shadow_report.py                    # the readiness evidence
```
Shadow mode runs the **entire** auto loop — enter, manage, take profit at 50%,
stop, expiry — deciding exactly what auto-mode would, and writing the
**broker-ready order payloads** to `state/shadow_orders.jsonl`. It places nothing.
When the win rate holds over ~20 cycles **and** the logged orders look right,
you've earned the right to consider steps 1–4.

`--mode auto` refuses to start until you have a real Algo-ID (step 1).

## Honest expectations

| | Reality |
|---|---|
| Win rate | ~78–91% (losses still come) |
| Typical loss | **bigger than a typical win** |
| Worst backtested | −₹37,000 (25% of your account) |
| Monthly | ~₹4,800 — *if* it behaves like the backtest |
| Live validation | **barely started** — one completed paper cycle |

**If you find yourself checking it every 10 minutes, or wanting to add lots after
a loss — stop trading. That instinct is what empties accounts, not the strategy.**

> Research/educational tooling. Not investment advice.
