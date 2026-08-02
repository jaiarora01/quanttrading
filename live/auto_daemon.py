"""Autonomous live daemon — decides and alerts. It never places an order.

Each cycle it: syncs the real broker position (read-only), then either asks the
advisor for a condor to place, or asks the monitor whether the open one should be
closed — and notifies the human when action is due.

Modes (`settings.live_mode`):
  * ``shadow`` — decide + log only. Builds the validation record. (default)
  * ``alert``  — also fire a macOS notification when action is needed.
  * ``auto``   — place real orders. **Refused**: requires a SEBI-registered
                 Algo-ID (mandatory since Apr 2026) and is not implemented here.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from config.settings import STATE_DIR, Settings, get_settings
from live.notify import Deduper, notify
from options.advisor import LiveCondorAdvisor
from options.data import NIFTY_LOT, OptionsData, parse_expiry
from options.live_monitor import LiveMonitor
from options.real_ledger import RealLedger
from options.shadow_executor import ShadowExecutor

DECISIONS = STATE_DIR / "decisions.jsonl"
VALID_MODES = ("shadow", "alert", "auto")


def resolve_mode(s: Settings) -> str:
    """Validate the execution mode, refusing `auto` without an Algo-ID."""
    mode = (s.live_mode or "shadow").lower()
    if mode not in VALID_MODES:
        raise ValueError(f"live_mode must be one of {VALID_MODES}, got {mode!r}")
    if mode == "auto":
        if not s.live_algo_id:
            raise RuntimeError(
                "live_mode='auto' needs a SEBI-registered Algo-ID. Automated order "
                "placement without exchange registration has not been permitted since "
                "April 2026. Register algo trading with Fyers, set QT_LIVE_ALGO_ID in "
                ".env, or use live_mode='alert' (you place the orders)."
            )
        raise NotImplementedError(
            "Automated order placement is intentionally not implemented. With an "
            "Algo-ID registered, add a FyersBroker(Broker) and wire it here."
        )
    return mode


def _is_nifty_option(symbol: str) -> bool:
    return "NIFTY" in symbol.upper() and (symbol.upper().endswith("CE") or
                                          symbol.upper().endswith("PE"))


class LiveAutoDaemon:
    def __init__(self, data: OptionsData, settings: Settings | None = None,
                 ledger: RealLedger | None = None, notifier=notify) -> None:
        self.s = settings or get_settings()
        self.mode = resolve_mode(self.s)
        self.d = data
        self.ledger = ledger or RealLedger()
        self.notifier = notifier
        self.deduper = Deduper(self.s.live_notify_repeat_s)
        self.monitor = LiveMonitor(data, self.ledger, self.s)
        self.shadow = ShadowExecutor(data, self.s)
        self._last_debit: float | None = None

    # --- one decision cycle (unit-testable) --------------------------------
    def cycle(self, now: pd.Timestamp | None = None) -> dict:
        now = now or pd.Timestamp.now(tz="Asia/Kolkata")
        if self.mode == "shadow":
            rec = self._shadow_cycle(now)      # full autonomous dry-run, zero orders
        else:
            rec = self._alert_cycle(now)       # decide + notify; human places the click
        rec.update({"ts": now.isoformat(timespec="seconds"), "mode": self.mode})
        self._log(rec)
        return rec

    def _alert_cycle(self, now: pd.Timestamp) -> dict:
        self._sync_position()
        return self._manage_open() if self.ledger.open_position() else self._look_for_entry()

    def _shadow_cycle(self, now: pd.Timestamp) -> dict:
        """Simulate exactly what auto-mode would do — enter, manage, exit — while
        placing nothing. Logs broker-ready order payloads for later use."""
        pos = self.shadow.current()
        if pos is None:
            advisor = LiveCondorAdvisor(self.d, self.s, realized_pnl=self.shadow.stats()["realized"])
            t = advisor.advise()
            if not t.ok:
                return {"decision": "no-trade", "reason": t.reason}
            return self.shadow.open(t, now)
        reason, pnl = self.shadow.decide(pos, now)
        if reason:
            return self.shadow.close(pos, reason, pnl, now)
        return {"decision": "hold", "pnl": round(pnl),
                "dte": (pd.to_datetime(pos["expiry"]).date() - now.date()).days}

    # --- read-only position sync -------------------------------------------
    def _sync_position(self) -> None:
        """Detect that the human actually placed or closed the trade."""
        try:
            legs = [p for p in self.d.p.positions() if _is_nifty_option(p["symbol"] or "")]
        except Exception:  # noqa: BLE001 — never let a sync hiccup kill the daemon
            return
        known = self.ledger.open_position()

        if legs and not known:
            # Credit = money received on shorts (qty<0) minus paid on longs (qty>0).
            credit = sum(-p["qty"] * p["avg_price"] for p in legs)
            recorded = [{"action": "SELL" if p["qty"] < 0 else "BUY ",
                         "symbol": p["symbol"], "strike": 0, "type": "",
                         "ltp": p["avg_price"]} for p in legs]
            width = self.s.live_wing_points
            # Recover the expiry from the option symbols so expiry alerts still work.
            expiries = [e for e in (parse_expiry(p["symbol"]) for p in legs) if e]
            expiry = str(min(expiries)) if expiries else "unknown"
            self.ledger.record_entry(recorded, credit, expiry=expiry,
                                     max_loss=width * NIFTY_LOT - credit)
        elif known and not legs:
            # Position closed at the broker — record it using the last mark.
            debit = self._last_debit if self._last_debit is not None else known["credit"]
            self.ledger.record_exit(debit, "closed at broker (estimated debit)")
            self.deduper.reset()

    # --- decisions ----------------------------------------------------------
    def _look_for_entry(self) -> dict:
        advisor = LiveCondorAdvisor(self.d, self.s, realized_pnl=self.ledger.realized_pnl())
        t = advisor.advise()
        if not t.ok:
            return {"decision": "no-trade", "reason": t.reason}
        legs = ", ".join(f"{l['action'].strip()} {l['strike']}{l['type']}" for l in t.legs)
        self._alert(
            key=f"enter:{t.expiry}:{t.legs[0]['strike']}:{t.legs[1]['strike']}",
            title="📈 Place iron condor — NIFTY",
            message=(f"Credit ₹{t.net_premium:,.0f} · max loss ₹{t.max_loss:,.0f} "
                     f"({t.max_loss_pct*100:.0f}%) · exp {t.expiry}"),
        )
        return {"decision": "enter", "reason": "all gates passed", "legs": legs,
                "credit": round(t.net_premium), "max_loss": round(t.max_loss),
                "expiry": str(t.expiry)}

    def _manage_open(self) -> dict:
        m = self.monitor.mark()
        if m is None:
            return {"decision": "flat", "reason": "no position"}
        self._last_debit = m["debit_to_close"]
        alerts = self.monitor.alerts(m)
        if not alerts:
            return {"decision": "hold", "pnl": round(m["pnl"]), "dte": m["dte"]}
        self._alert(
            key=f"exit:{alerts[0][:24]}",
            title="🛑 Close the condor",
            message=f"{alerts[0]}  (P&L ₹{m['pnl']:,.0f})",
        )
        return {"decision": "exit", "reason": alerts[0], "pnl": round(m["pnl"])}

    # --- helpers ------------------------------------------------------------
    def _alert(self, key: str, title: str, message: str) -> None:
        """Notify the human — only in `alert` mode, and only if not a repeat."""
        if self.mode != "alert":
            return
        if self.deduper.should_fire(key):
            self.notifier(title, message)

    @staticmethod
    def _log(rec: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(DECISIONS, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
