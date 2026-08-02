"""Live iron-condor ADVISOR — tells you what to place. It never places orders.

Why an advisor and not a bot: automated order placement requires a SEBI-registered
Algo-ID (mandatory since Apr 2026), and this strategy's entire risk is its tail —
a human circuit-breaker is a feature, not a limitation.

Why iron condors and not the backtested strangle: a ₹50k account cannot margin a
naked NIFTY strangle (~₹1.5L needed), and even a standard 1SD/2SD condor risks
₹26k–58k per trade — 50–117% of the account. Wings are therefore **mandatory and
tight** (100 pts keeps max loss ~11% of ₹50k; 150 pts breaches the gate at ~16%),
and a hard gate refuses any trade risking more than `live_max_risk_pct` of the
account.

**The condor is NOT the validated strategy.** The 5.4-year result (38.4%/yr,
Sharpe 1.08) belongs to the strangle; the condor harvests the same VRP edge but
sells much of it away to buy the wings, and its returns are modelled, not
backtested. Above `MARGIN_PER_LOT` this advisor switches to the strangle.

Honest framing: at ₹50k this is tuition, not income. Expect ~₹500–1,500/month,
with 4-leg costs eating ~20% of gross premium.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import sqrt

import pandas as pd

from backtest.costs import GST_RATE
from config.settings import Settings, get_settings
from options.data import NIFTY_LOT, OptionsData, atm_strike
from options.event_veto import EventVeto
from options.regime_veto import RegimeVeto
from options.vol_backtest import MARGIN_PER_LOT


def _leg_cost(n_legs: int, premium_value: float) -> float:
    """Round-trip cost for an n-leg options structure (brokerage + STT + GST)."""
    brokerage = n_legs * 2 * 20.0
    statutory = 0.001 * premium_value          # STT/exchange/SEBI on premium, approx
    return brokerage + statutory + GST_RATE * brokerage


@dataclass
class Ticket:
    """A concrete, placeable 4-leg iron condor — or a refusal."""

    ok: bool
    reason: str = ""
    structure: str = "condor"       # "strangle" (validated) or "condor" (defined-risk)
    expiry: date | None = None
    spot: float = 0.0
    vix: float = 0.0
    dte: int = 0
    legs: list[dict] = field(default_factory=list)   # [{action, strike, type, symbol, price}]
    net_premium: float = 0.0        # ₹ collected (credit), per lot
    max_loss: float = 0.0           # ₹ worst case, capped by the wings
    max_loss_pct: float = 0.0       # as a fraction of the account
    est_costs: float = 0.0


class LiveCondorAdvisor:
    def __init__(self, data: OptionsData, settings: Settings | None = None,
                 realized_pnl: float = 0.0) -> None:
        self.d = data
        self.s = settings or get_settings()
        self.realized_pnl = realized_pnl   # cumulative live P&L, for the drawdown halt

    def advise(self, today: date | None = None, target_dte: int = 25) -> Ticket:
        today = today or date.today()
        s = self.s

        # --- Gate 0: account drawdown halt -----------------------------------
        dd = -self.realized_pnl / s.live_account if s.live_account else 0.0
        if dd >= s.live_halt_drawdown:
            return Ticket(ok=False, reason=(
                f"HALT — account is down {dd*100:.0f}% (limit {s.live_halt_drawdown*100:.0f}%). "
                "Stop trading and review. Do not 'trade back' losses."))

        chain = self.d.live_chain(strikecount=30, target_dte=target_dte)
        spot, vix, expiry, opts = chain["spot"], chain["vix"], chain["expiry"], chain["options"]
        dte = max((expiry - today).days, 1)

        # --- Gate 1: only sell when volatility is rich ------------------------
        if vix < s.vix_min:
            return Ticket(ok=False, spot=spot, vix=vix, expiry=expiry, dte=dte, reason=(
                f"NO TRADE — VIX {vix:.1f} is below {s.vix_min}. Premium is too cheap "
                "to justify the tail risk. Wait for richer volatility."))

        # --- Gate 1.5: never sell premium into a known binary event -----------
        veto = EventVeto(s).check(today, expiry)
        if veto:
            return Ticket(ok=False, spot=spot, vix=vix, expiry=expiry, dte=dte, reason=veto)

        # --- Gate 1.6: regime veto (OFF by default — backtested to hurt) -------
        regime = RegimeVeto(s).check_live(self.d)
        if regime:
            return Ticket(ok=False, spot=spot, vix=vix, expiry=expiry, dte=dte, reason=regime)

        # --- Short ~1SD strangle (the validated core) ---
        move = spot * (vix / 100.0) * sqrt(dte / 365.0)
        kc, kp = atm_strike(spot + move), atm_strike(spot - move)
        short_ce = self._pick(opts, "CE", kc)
        short_pe = self._pick(opts, "PE", kp)
        if not (short_ce and short_pe):
            return Ticket(ok=False, spot=spot, vix=vix, expiry=expiry, dte=dte, reason=(
                "NO TRADE — could not find the short strikes in the live chain."))

        # Prefer the VALIDATED naked strangle when the account can margin it AND
        # absorb its stop-loss within the risk limit; otherwise fall back to the
        # defined-risk condor.
        strangle_prem = (short_ce["ltp"] + short_pe["ltp"]) * NIFTY_LOT
        stop_risk = s.live_stop_mult * strangle_prem
        if s.live_account >= MARGIN_PER_LOT and stop_risk <= s.live_max_risk_pct * s.live_account:
            legs = [
                {"action": "SELL", **{k: short_ce[k] for k in ("strike", "symbol", "ltp")}, "type": "CE"},
                {"action": "SELL", **{k: short_pe[k] for k in ("strike", "symbol", "ltp")}, "type": "PE"},
            ]
            return Ticket(ok=True, structure="strangle", expiry=expiry, spot=spot, vix=vix,
                          dte=dte, legs=legs, net_premium=strangle_prem,
                          max_loss=stop_risk, max_loss_pct=stop_risk / s.live_account,
                          est_costs=_leg_cost(2, strangle_prem))

        # --- Defined-risk condor: add tight protective wings ---
        wing = s.live_wing_points
        long_ce = self._pick(opts, "CE", kc + wing)
        long_pe = self._pick(opts, "PE", kp - wing)
        if not (long_ce and long_pe):
            return Ticket(ok=False, spot=spot, vix=vix, expiry=expiry, dte=dte, reason=(
                "NO TRADE — could not find the wing strikes in the live chain "
                "(illiquid or too far out). Try again nearer the money."))

        credit = (short_ce["ltp"] + short_pe["ltp"]) - (long_ce["ltp"] + long_pe["ltp"])
        net_premium = credit * NIFTY_LOT
        # Wings cap the loss: widest spread minus the credit already received.
        width = max(long_ce["strike"] - short_ce["strike"], short_pe["strike"] - long_pe["strike"])
        max_loss = width * NIFTY_LOT - net_premium
        costs = _leg_cost(4, abs(net_premium))
        max_loss_pct = max_loss / s.live_account if s.live_account else 1.0

        legs = [
            {"action": "SELL", **{k: short_ce[k] for k in ("strike", "symbol", "ltp")}, "type": "CE"},
            {"action": "SELL", **{k: short_pe[k] for k in ("strike", "symbol", "ltp")}, "type": "PE"},
            {"action": "BUY ", **{k: long_ce[k] for k in ("strike", "symbol", "ltp")}, "type": "CE"},
            {"action": "BUY ", **{k: long_pe[k] for k in ("strike", "symbol", "ltp")}, "type": "PE"},
        ]
        t = Ticket(ok=True, expiry=expiry, spot=spot, vix=vix, dte=dte, legs=legs,
                   net_premium=net_premium, max_loss=max_loss,
                   max_loss_pct=max_loss_pct, est_costs=costs)

        # --- Gate 2: the trade must be survivable at this account size --------
        if net_premium <= 0:
            t.ok, t.reason = False, ("NO TRADE — the wings cost more than the premium "
                                     "collected (no credit). Not worth the risk.")
        elif max_loss_pct > s.live_max_risk_pct:
            t.ok, t.reason = False, (
                f"NO TRADE — max loss ₹{max_loss:,.0f} is {max_loss_pct*100:.0f}% of your "
                f"₹{s.live_account:,.0f} account (limit {s.live_max_risk_pct*100:.0f}%). "
                f"Tighten the wings (currently {wing} pts) or skip this cycle.")
        elif net_premium <= costs * 2:
            t.ok, t.reason = False, (
                f"NO TRADE — credit ₹{net_premium:,.0f} is too small against ₹{costs:,.0f} "
                "of costs. The edge would be eaten by fees.")
        return t

    @staticmethod
    def _pick(opts: pd.DataFrame, typ: str, target: int):
        sub = opts[opts["type"] == typ]
        if sub.empty:
            return None
        row = sub.iloc[(sub["strike"] - target).abs().argmin()]
        # Reject if the chain had nothing near the strike we wanted.
        if abs(int(row["strike"]) - target) > 200:
            return None
        return {"strike": int(row["strike"]), "symbol": row["symbol"], "ltp": float(row["ltp"])}


def format_ticket(t: Ticket, s: Settings | None = None) -> str:
    """Human-readable order ticket (or refusal) for manual placement."""
    s = s or get_settings()
    if not t.ok:
        return f"\n🛑 {t.reason}\n"
    name = ("SHORT STRANGLE (validated)" if t.structure == "strangle"
            else "IRON CONDOR (defined risk)")
    lines = [
        "",
        "=" * 62,
        f"  {name} — place manually in Fyers   (expiry {t.expiry}, {t.dte} DTE)",
        "=" * 62,
        f"  NIFTY spot {t.spot:,.0f}   ·   VIX {t.vix:.1f}   ·   1 lot ({NIFTY_LOT})",
        "",
    ]
    for leg in t.legs:
        lines.append(f"   {leg['action']}  {leg['strike']}{leg['type']}   "
                     f"@ ~{leg['ltp']:.1f}   {leg['symbol']}")
    lines += [
        "",
        f"  Credit received : ₹{t.net_premium:,.0f}   ← your max profit",
        f"  MAX LOSS        : ₹{t.max_loss:,.0f}   ({t.max_loss_pct*100:.0f}% of account)"
        + ("   ← at your stop" if t.structure == "strangle" else "   ← capped by wings"),
        f"  Est. costs      : ₹{t.est_costs:,.0f}   ({len(t.legs)} legs, round trip)",
        f"  Close at        : ₹{t.net_premium * s.live_profit_target:,.0f} profit "
        f"({s.live_profit_target*100:.0f}% of credit) — validated: this beats holding to expiry",
        "",
    ]
    if t.structure == "strangle":
        lines += [
            f"  ⚠  NAKED position — your {s.live_stop_mult:g}x stop is a PLAN, not a guarantee.",
            "     An overnight gap can blow through it. Never leave this unmonitored.",
        ]
    else:
        lines += [
            "  ⚠  Place ALL FOUR legs. The two BUY legs are your safety net —",
            "     without them one bad move can exceed your whole account.",
        ]
    lines += ["=" * 62, ""]
    return "\n".join(lines)
