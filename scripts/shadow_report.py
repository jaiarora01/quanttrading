"""Shadow-mode readiness report — the evidence you review before going auto.

    python scripts/shadow_report.py

Summarises what the autonomous engine WOULD have traded (from the shadow run):
its track record, and the last broker-ready orders it logged but never sent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root on path

from options.shadow_executor import (SHADOW_ORDERS, SHADOW_POS, ShadowExecutor)
from options.data import OptionsData


def main() -> None:
    ex = ShadowExecutor(OptionsData.__new__(OptionsData))  # stats/reads need no live data
    st = ex.stats()

    print("\n" + "=" * 56)
    print("  SHADOW-MODE READINESS REPORT")
    print("  (what auto-mode WOULD have done — zero orders sent)")
    print("=" * 56)
    print(f"  Completed shadow cycles : {st['trades']}")
    if st["trades"]:
        print(f"  Win rate                : {st['win_rate']:.0f}%   (backtest ~78%)")
        print(f"  Would-be realized P&L   : ₹{st['realized']:,.0f}")
        print(f"  Best / worst            : ₹{st.get('best',0):,.0f} / ₹{st.get('worst',0):,.0f}")
    else:
        print("  No completed shadow cycles yet — run: python scripts/run_live_daemon.py --mode shadow")

    pos = ex.current()
    if pos:
        print(f"\n  Open shadow position    : {pos['structure']} · exp {pos['expiry']} "
              f"· credit ₹{pos['credit']:,.0f}")

    if SHADOW_ORDERS.exists():
        orders = [json.loads(l) for l in SHADOW_ORDERS.read_text().splitlines() if l.strip()]
        print(f"\n  Broker-ready orders logged: {len(orders)}  (in {SHADOW_ORDERS.name}, none sent)")
        for o in orders[-4:]:
            p = o["payload"]
            side = "SELL" if p["side"] < 0 else "BUY "
            print(f"    [{o['phase']:<16}] {side} {p['qty']} {p['symbol']}")

    print("\n  ✅ Ready-to-go check: when these orders look right AND the win rate")
    print("     holds over ~20 cycles, THEN register an Algo-ID and wire FyersBroker.")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    main()
