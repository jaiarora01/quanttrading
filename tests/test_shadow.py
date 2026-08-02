"""Shadow executor: a full autonomous lifecycle that places ZERO real orders."""
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import get_settings
from options.shadow_executor import ShadowExecutor

IST = "Asia/Kolkata"


class Ticket:
    def __init__(self):
        self.ok, self.structure = True, "strangle"
        self.expiry = date.today() + timedelta(days=20)
        self.net_premium, self.max_loss = 9000.0, 18000.0
        self.legs = [
            {"action": "SELL", "strike": 25000, "type": "CE", "symbol": "CE_SYM", "ltp": 60.0},
            {"action": "SELL", "strike": 23400, "type": "PE", "symbol": "PE_SYM", "ltp": 60.0},
        ]


def _data(ce, pe):
    return SimpleNamespace(p=SimpleNamespace(quote=lambda syms: {"CE_SYM": ce, "PE_SYM": pe}))


def _executor(tmp_path, monkeypatch, ce=60.0, pe=60.0):
    for name in ("SHADOW_POS", "SHADOW_ORDERS", "SHADOW_FILLS"):
        monkeypatch.setattr(f"options.shadow_executor.{name}",
                            tmp_path / f"{name.lower()}.jsonl")
    return ShadowExecutor(_data(ce, pe), get_settings())


NOW = pd.Timestamp("2026-08-02 10:00", tz=IST)


def test_open_logs_broker_ready_orders_and_opens_position(tmp_path, monkeypatch):
    ex = _executor(tmp_path, monkeypatch)
    rec = ex.open(Ticket(), NOW)
    assert rec["decision"] == "shadow-enter"
    pos = ex.current()
    assert pos and pos["credit"] == 9000.0
    # Orders were logged as sell-to-open, market, F&O — and marked SHADOW.
    from options.shadow_executor import SHADOW_ORDERS
    orders = [__import__("json").loads(l) for l in SHADOW_ORDERS.read_text().splitlines()]
    assert len(orders) == 2
    assert all(o["SHADOW"] is True and "NOT sent" in o["note"] for o in orders)
    assert all(o["payload"]["side"] == -1 for o in orders)     # SELL to open
    assert all(o["payload"]["productType"] == "MARGIN" for o in orders)


def test_take_profit_at_50pct(tmp_path, monkeypatch):
    # Legs decayed to 30 each: close cost 60*75=4500, credit 9000 -> +4500 = 50%.
    ex = _executor(tmp_path, monkeypatch, ce=30.0, pe=30.0)
    ex.open(Ticket(), NOW)
    reason, pnl = ex.decide(ex.current(), NOW)
    assert reason == "profit-target" and pnl == pytest.approx(4500)


def test_stop_loss_fires(tmp_path, monkeypatch):
    # Legs blew out to 180 each: close 360*75=27000, credit 9000 -> -18000 = -2x.
    ex = _executor(tmp_path, monkeypatch, ce=180.0, pe=180.0)
    ex.open(Ticket(), NOW)
    reason, pnl = ex.decide(ex.current(), NOW)
    assert reason == "stop-loss" and pnl < 0


def test_hold_when_neither_hit(tmp_path, monkeypatch):
    ex = _executor(tmp_path, monkeypatch, ce=55.0, pe=55.0)   # small decay
    ex.open(Ticket(), NOW)
    reason, _ = ex.decide(ex.current(), NOW)
    assert reason is None


def test_close_records_reversing_orders_and_pnl(tmp_path, monkeypatch):
    ex = _executor(tmp_path, monkeypatch, ce=30.0, pe=30.0)
    ex.open(Ticket(), NOW)
    reason, pnl = ex.decide(ex.current(), NOW)
    ex.close(ex.current(), reason, pnl, NOW)
    assert ex.current() is None                               # flat again
    st = ex.stats()
    assert st["trades"] == 1 and st["wins"] == 1
    # Closing orders reverse the opens: BUY to close (+1).
    from options.shadow_executor import SHADOW_ORDERS
    orders = [__import__("json").loads(l) for l in SHADOW_ORDERS.read_text().splitlines()]
    close_orders = [o for o in orders if "EXIT" in o["phase"]]
    assert len(close_orders) == 2 and all(o["payload"]["side"] == 1 for o in close_orders)


def test_full_shadow_cycle_via_daemon_places_no_orders(tmp_path, monkeypatch):
    """End-to-end: the daemon's shadow cycle runs enter->exit and calls no
    order-placing broker method (only read-only quote())."""
    from live.auto_daemon import LiveAutoDaemon
    from test_advisor import FakeChain

    for name in ("SHADOW_POS", "SHADOW_ORDERS", "SHADOW_FILLS"):
        monkeypatch.setattr(f"options.shadow_executor.{name}", tmp_path / f"{name.lower()}.jsonl")
    monkeypatch.setattr("live.auto_daemon.DECISIONS", tmp_path / "decisions.jsonl")

    # FakeChain has no place_order/exit/cancel — any such call would AttributeError.
    data = FakeChain(vix=15.0)
    data.p = SimpleNamespace(
        positions=lambda: [],
        quote=lambda syms: {s: 20.0 for s in syms},   # cheap now -> profit target
    )
    s = get_settings().model_copy(update={"live_mode": "shadow", "live_account": 150_000})
    d = LiveAutoDaemon(data, s)
    r1 = d.cycle(NOW)
    assert r1["decision"] == "shadow-enter"
    r2 = d.cycle(NOW + pd.Timedelta(days=1))
    assert r2["decision"] in ("shadow-exit", "hold")
