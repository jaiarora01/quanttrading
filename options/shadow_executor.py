"""Shadow executor — a full autonomous trading loop that places ZERO orders.

This is the dry-run that proves auto-mode is ready. For every decision it writes
the exact **broker-ready order payload** a real `FyersBroker` would send (to
`state/shadow_orders.jsonl`), and it simulates the fill at live prices so the
shadow position has a realistic lifecycle: enter → manage → exit. Nothing touches
the exchange.

When you have a registered Algo-ID, going live is mechanical: replace
``_log_order`` with a real ``fyers.place_order(payload)`` — the payloads are
already correct.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

from config.settings import STATE_DIR, Settings, get_settings
from options.data import NIFTY_LOT, OptionsData

SHADOW_POS = STATE_DIR / "shadow_position.json"
SHADOW_ORDERS = STATE_DIR / "shadow_orders.jsonl"
SHADOW_FILLS = STATE_DIR / "shadow_fills.jsonl"


def _fyers_payload(leg: dict, phase: str) -> dict:
    """The exact dict a real FyersBroker.place_order would send (F&O, 1 lot)."""
    action = leg["action"].strip()
    side = -1 if action == "SELL" else 1          # Fyers: 1=buy, -1=sell
    if phase == "close":
        side = -side                               # reverse to flatten
    return {
        "symbol": leg["symbol"], "qty": NIFTY_LOT, "type": 2,   # 2 = market order
        "side": side, "productType": "MARGIN",                  # carry F&O
        "limitPrice": 0, "stopPrice": 0, "validity": "DAY",
        "disclosedQty": 0, "offlineOrder": False,
    }


class ShadowExecutor:
    def __init__(self, data: OptionsData, settings: Settings | None = None) -> None:
        self.d = data
        self.s = settings or get_settings()

    # --- state --------------------------------------------------------------
    def current(self) -> dict | None:
        if not SHADOW_POS.exists():
            return None
        return json.loads(SHADOW_POS.read_text()) or None

    # --- open ---------------------------------------------------------------
    def open(self, ticket, now: pd.Timestamp) -> dict:
        payloads = [_fyers_payload(leg, "open") for leg in ticket.legs]
        self._log_orders(payloads, "ENTRY", now)
        pos = {
            "entry_date": str(now.date()), "expiry": str(ticket.expiry),
            "structure": ticket.structure, "legs": ticket.legs,
            "credit": ticket.net_premium, "max_loss": ticket.max_loss,
        }
        SHADOW_POS.write_text(json.dumps(pos, indent=2, default=str))
        return {"decision": "shadow-enter", "structure": ticket.structure,
                "credit": round(ticket.net_premium), "max_loss": round(ticket.max_loss),
                "expiry": str(ticket.expiry),
                "would_place": f"{len(payloads)} orders (logged, none sent)"}

    # --- mark / exit decision ----------------------------------------------
    def mark(self, pos: dict) -> float:
        """Unrealized ₹ P&L from live quotes (credit − current cost to close)."""
        q = self.d.p.quote([l["symbol"] for l in pos["legs"]])
        shorts = sum(q.get(l["symbol"], l["ltp"]) for l in pos["legs"] if l["action"].strip() == "SELL")
        longs = sum(q.get(l["symbol"], l["ltp"]) for l in pos["legs"] if l["action"].strip() == "BUY")
        close_cost = (shorts - longs) * NIFTY_LOT
        return pos["credit"] - close_cost

    def decide(self, pos: dict, now: pd.Timestamp) -> tuple[str | None, float]:
        """Return (exit_reason or None, unrealized_pnl)."""
        pnl = self.mark(pos)
        credit = pos["credit"]
        expiry = pd.to_datetime(pos["expiry"]).date()
        if now.date() >= expiry:
            return "expiry", pnl
        if pnl >= self.s.live_profit_target * credit:
            return "profit-target", pnl
        if pnl <= -self.s.live_stop_mult * credit:
            return "stop-loss", pnl
        return None, pnl

    # --- close --------------------------------------------------------------
    def close(self, pos: dict, reason: str, pnl: float, now: pd.Timestamp) -> dict:
        payloads = [_fyers_payload(leg, "close") for leg in pos["legs"]]
        self._log_orders(payloads, f"EXIT ({reason})", now)
        self._append(SHADOW_FILLS, {"event": "EXIT", "exit_date": str(now.date()),
                                    "reason": reason, "pnl": round(pnl),
                                    "entry_date": pos["entry_date"]})
        SHADOW_POS.write_text(json.dumps({}))
        return {"decision": "shadow-exit", "reason": reason, "pnl": round(pnl)}

    # --- track record -------------------------------------------------------
    def stats(self) -> dict:
        if not SHADOW_FILLS.exists():
            return {"trades": 0, "wins": 0, "win_rate": 0.0, "realized": 0.0}
        exits = [json.loads(l) for l in SHADOW_FILLS.read_text().splitlines()
                 if l.strip() and json.loads(l).get("event") == "EXIT"]
        if not exits:
            return {"trades": 0, "wins": 0, "win_rate": 0.0, "realized": 0.0}
        pnls = [e["pnl"] for e in exits]
        wins = [p for p in pnls if p > 0]
        return {"trades": len(pnls), "wins": len(wins),
                "win_rate": len(wins) / len(pnls) * 100, "realized": sum(pnls),
                "best": max(pnls), "worst": min(pnls)}

    # --- helpers ------------------------------------------------------------
    def _log_orders(self, payloads: list[dict], phase: str, now: pd.Timestamp) -> None:
        for p in payloads:
            self._append(SHADOW_ORDERS, {"phase": phase, "SHADOW": True,
                                         "note": "logged only — NOT sent to broker",
                                         "payload": p})

    @staticmethod
    def _append(path, rec: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
