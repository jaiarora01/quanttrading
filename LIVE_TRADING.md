# Live Trading Rules — read this before every trade

**Account: ₹50,000 · 1 lot max · manual execution · take profit at 50%**

> Realistic expectation: **~₹500–1,500/month**. At ₹50k this is tuition, not income
> — 4-leg costs eat ~20% of gross premium. That is not a promise either; it's an
> estimate for a structure with **no 5.4-year backtest behind it** (see below).

## What you're trading and why

The one edge this project found is **selling index option premium** — India VIX
runs ~26% above realized volatility, and you get paid for that gap.

### ⚠️ At ₹50k you are NOT trading the validated strategy

The 5.4-year result (38.4%/yr, Sharpe 1.08) is for a short ~1-SD NIFTY **strangle**,
which needs ~₹1.5L of margin. **₹50k cannot margin it.** The advisor therefore falls
back to a defined-risk **iron condor** — automatically, at
[`advisor.py:109`](options/advisor.py#L109).

That fallback is the *sane* choice at this size (your loss is capped by the wings
instead of open-ended), but be clear about what it costs you:

| | Strangle (₹1.5L) | Condor (₹50k) |
|---|---|---|
| Backtested over 5.4 yrs | ✅ Yes | ❌ **No** |
| Legs / costs | 2 legs | 4 legs, ~20% of gross premium |
| Max loss | Open-ended past the stop | Capped by wings (~₹5,300) |
| Expectation | ~₹4,800/mo | ~₹500–1,500/mo |

The VRP edge it harvests is the same, but the wings sell away much of the premium,
and **the condor variant's returns are modelled, not validated.** Treat early
months as calibration, not proof.

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
7. **Halt at −20% of account (−₹10,000).** Stop trading entirely. Review, don't
   revenge-trade. The advisor enforces this automatically. Note that's only ~2
   max-loss trades — at ₹50k the halt arrives fast.
8. **Only sell when VIX ≥ 13.** Cheap premium isn't worth the tail.
9. **Never sell into a scheduled event.** The advisor auto-vetoes Budget (hard)
   and RBI/election-class events (within 3 days). Keep `config/events.json`
   current — verify RBI MPC dates at rbi.org.in.
10. **Record every real fill.** That's how we learn what actually happens vs the model.

### ⚠️ What the wings do and don't protect

The condor's wings make your max loss **knowable in advance** (~₹5,300, ~11% of
account) — that's a real improvement over a naked stop, which an overnight gap can
blow straight through. This is why the small account gets the safer structure.

But the wings are only 100 points wide, and that width is **load-bearing**:

- At 100 pts, max loss ≈ ₹5,268 → **11% of ₹50k** → advisor says OK
- At 150 pts, max loss ≈ ₹8,063 → **16% of ₹50k** → advisor **REFUSES**

So do not widen the wings to collect more credit. It will simply stop producing
tradeable tickets, and if you widen the risk cap instead you've removed the one
thing making ₹50k survivable.

**The honest sizing note:** ₹50k is below what this strategy really wants. Two
max-loss trades hit the −20% halt. If you can fund ₹1.5L+ you get the *validated*
strangle instead of an unvalidated condor; ₹1.8–2L gives that trade margin buffer.
Trading ₹50k is defensible for learning the mechanics with money you can lose —
not for generating income.

## Pre-trade checklist (every single time)

- [ ] Token refreshed today (`python scripts/fyers_auth.py`)
- [ ] No position currently open
- [ ] Advisor returned a **ticket**, not a refusal
- [ ] Max loss shown is **≤ 12% of account** (~₹6,000)
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
| Win rate | ~78–91% *for the strangle* — the condor is unvalidated |
| Typical loss | **bigger than a typical win** |
| Worst case per trade | ~−₹5,300, capped by the wings (~11% of ₹50k) |
| Monthly | ~₹500–1,500 — modelled, not backtested |
| Costs | ~20% of gross premium (4 legs) |
| Live validation | **barely started** — one completed paper cycle, at ₹1.5L basis |

**If you find yourself checking it every 10 minutes, or wanting to add lots after
a loss — stop trading. That instinct is what empties accounts, not the strategy.**

> Research/educational tooling. Not investment advice.
