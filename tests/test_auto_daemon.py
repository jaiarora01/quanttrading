"""The daemon must never place orders, never spam, and refuse unregistered auto mode."""
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import get_settings
from live.auto_daemon import LiveAutoDaemon, resolve_mode
from live.notify import Deduper


class FakeChain:
    def __init__(self, spot=24_000.0, vix=15.0, dte=25):
        self.spot, self.vix, self.dte = spot, vix, dte

    def live_chain(self, strikecount: int = 30, target_dte: int = 25) -> dict:
        rows = []
        for k in range(int(self.spot) - 2000, int(self.spot) + 2050, 50):
            ltp = max(2.0, 200 - abs(k - self.spot) * 0.15)
            for typ in ("CE", "PE"):
                rows.append({"strike": k, "type": typ, "ltp": ltp,
                             "symbol": f"NSE:NIFTYTEST{k}{typ}"})
        return {"expiry": date.today() + timedelta(days=self.dte), "spot": self.spot,
                "vix": self.vix, "options": pd.DataFrame(rows)}


class FakeData(FakeChain):
    """OptionsData stand-in: adds the read-only broker surface `.p`."""

    def __init__(self, positions=None, quotes=None, **kw):
        super().__init__(**kw)
        self.p = SimpleNamespace(positions=lambda: positions or [],
                                 quote=lambda syms: quotes or {})


class FakeLedger:
    def __init__(self, position=None):
        self._pos, self.entries, self.exits = position, [], []

    def open_position(self):
        return self._pos

    def realized_pnl(self):
        return 0.0

    def record_entry(self, legs, credit, expiry, max_loss, model_credit=0.0):
        self._pos = {"legs": legs, "credit": credit, "expiry": expiry, "max_loss": max_loss}
        self.entries.append(self._pos)
        return self._pos

    def record_exit(self, debit, reason):
        self.exits.append({"debit": debit, "reason": reason})
        self._pos = None


def _settings(**over):
    base = {"live_mode": "alert", "live_account": 50_000, "live_wing_points": 100,
            "live_max_risk_pct": 0.12}
    return get_settings().model_copy(update={**base, **over})


def _daemon(monkeypatch, tmp_path, mode="alert", positions=None, ledger=None, calls=None):
    monkeypatch.setattr("live.auto_daemon.DECISIONS", tmp_path / "decisions.jsonl")
    return LiveAutoDaemon(
        FakeData(positions=positions), _settings(live_mode=mode),
        ledger=ledger or FakeLedger(),
        notifier=lambda t, m: (calls if calls is not None else []).append((t, m)),
    )


# --- mode gating (safety-critical) -----------------------------------------
def test_auto_mode_refused_without_algo_id():
    with pytest.raises(RuntimeError, match="Algo-ID"):
        resolve_mode(_settings(live_mode="auto", live_algo_id=""))


def test_auto_mode_still_not_implemented_even_with_algo_id():
    with pytest.raises(NotImplementedError):
        resolve_mode(_settings(live_mode="auto", live_algo_id="ALGO123"))


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        resolve_mode(_settings(live_mode="yolo"))


# --- notification dedupe ----------------------------------------------------
def test_deduper_suppresses_repeats_but_allows_changes():
    d = Deduper(repeat_after=1800)
    assert d.should_fire("a", now=0)
    assert not d.should_fire("a", now=10)          # same alert, too soon
    assert d.should_fire("b", now=20)              # different alert
    assert d.should_fire("b", now=20 + 1800)       # enough time elapsed


# --- decision cycles --------------------------------------------------------
def test_alert_mode_notifies_once_for_the_same_setup(monkeypatch, tmp_path):
    calls = []
    d = _daemon(monkeypatch, tmp_path, mode="alert", calls=calls)
    rec = d.cycle()
    assert rec["decision"] == "enter"
    assert len(calls) == 1 and "condor" in calls[0][0].lower()
    d.cycle()                                       # identical setup again
    assert len(calls) == 1, "must not re-notify the same trade every poll"


def test_shadow_mode_decides_but_never_notifies(monkeypatch, tmp_path):
    for name in ("SHADOW_POS", "SHADOW_ORDERS", "SHADOW_FILLS"):
        monkeypatch.setattr(f"options.shadow_executor.{name}", tmp_path / f"{name.lower()}.jsonl")
    calls = []
    d = _daemon(monkeypatch, tmp_path, mode="shadow", calls=calls)
    rec = d.cycle()
    assert rec["decision"] == "shadow-enter"        # decides + simulates the fill
    assert calls == []                              # but stays silent (no notification)


def test_low_vix_produces_no_trade(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("live.auto_daemon.DECISIONS", tmp_path / "d.jsonl")
    d = LiveAutoDaemon(FakeData(vix=9.0), _settings(), ledger=FakeLedger(),
                       notifier=lambda t, m: calls.append((t, m)))
    rec = d.cycle()
    assert rec["decision"] == "no-trade"
    assert "vix" in rec["reason"].lower()
    assert calls == []


def test_decisions_are_logged(monkeypatch, tmp_path):
    log = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("live.auto_daemon.DECISIONS", log)
    LiveAutoDaemon(FakeData(), _settings(), ledger=FakeLedger(),
                   notifier=lambda t, m: None).cycle()
    assert log.exists() and log.read_text().strip()


def test_detects_position_placed_at_broker(monkeypatch, tmp_path):
    """User placed the condor manually -> daemon records it from broker positions."""
    legs = [
        {"symbol": "NSE:NIFTY24950CE", "qty": -75, "avg_price": 60.0},   # short
        {"symbol": "NSE:NIFTY23050PE", "qty": -75, "avg_price": 58.0},   # short
        {"symbol": "NSE:NIFTY25050CE", "qty": 75, "avg_price": 45.0},    # long wing
        {"symbol": "NSE:NIFTY22950PE", "qty": 75, "avg_price": 43.0},    # long wing
    ]
    ledger = FakeLedger()
    d = _daemon(monkeypatch, tmp_path, positions=legs, ledger=ledger)
    d.cycle()
    assert len(ledger.entries) == 1
    # credit = received on shorts - paid on longs = 75*(60+58) - 75*(45+43)
    assert ledger.entries[0]["credit"] == pytest.approx(75 * (60 + 58 - 45 - 43))
