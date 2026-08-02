"""Central configuration via pydantic-settings.

Values load from environment variables / a local `.env` file. Secrets (Fyers
credentials) live only in `.env`; strategy params have sane defaults here and
can be overridden per-run via `QT_*` env vars.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
STATE_DIR = PROJECT_ROOT / "state"
REPORTS_DIR = PROJECT_ROOT / "reports"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Fyers credentials (secrets) ---
    fyers_app_id: str = Field(default="", alias="FYERS_APP_ID")
    fyers_secret_id: str = Field(default="", alias="FYERS_SECRET_ID")
    fyers_redirect_uri: str = Field(
        default="https://127.0.0.1:5000/callback", alias="FYERS_REDIRECT_URI"
    )
    fyers_access_token: str = Field(default="", alias="FYERS_ACCESS_TOKEN")

    # Active strategy: "orb", "breakout", or "meanrev" (intraday mean-reversion).
    strategy: str = Field(default="orb", alias="QT_STRATEGY")

    # Trading universe: "nifty50" or "nifty200".
    universe: str = Field(default="nifty50", alias="QT_UNIVERSE")

    # Only sell option premium when implied vol is rich enough (validated:
    # adding this filter lifted backtest Sharpe from 1.04 -> 1.40).
    vix_min: float = Field(default=13.0, alias="QT_VIX_MIN")

    # --- Live daemon execution mode ---
    # "shadow" = decide + log only · "alert" = notify the human to place it
    # "auto"   = place REAL orders — requires a SEBI-registered Algo-ID (see
    #            live_algo_id). Refused outright when that is unset.
    live_mode: str = Field(default="shadow", alias="QT_LIVE_MODE")
    live_algo_id: str = Field(default="", alias="QT_LIVE_ALGO_ID")
    # Re-notify the same alert only after this many seconds (anti-spam).
    live_notify_repeat_s: float = Field(default=1800.0, alias="QT_LIVE_NOTIFY_REPEAT")

    # --- REAL-MONEY guardrails (enforced by LiveCondorAdvisor, not just docs) ---
    # Small account => defined-risk iron condors ONLY. Never a naked position.
    live_account: float = Field(default=50_000.0, alias="QT_LIVE_ACCOUNT")
    # Stop-loss on a naked strangle, as a multiple of premium collected (validated: 2x).
    live_stop_mult: float = Field(default=2.0, alias="QT_LIVE_STOP_MULT")
    live_max_risk_pct: float = Field(default=0.12, alias="QT_LIVE_MAX_RISK")   # max loss <=12% of account
    live_max_lots: int = Field(default=1, alias="QT_LIVE_MAX_LOTS")
    live_halt_drawdown: float = Field(default=0.20, alias="QT_LIVE_HALT_DD")   # stop trading at -20%
    # Wing distance. At a ₹50k account this is load-bearing, not cosmetic: 100 pts
    # keeps max loss ~11% of account (inside live_max_risk_pct) across VIX 13-28 and
    # 7-21 DTE. Widening to 150 pts pushes it to ~16% and the advisor REFUSES the
    # trade. Do not raise this without re-checking the gate at your account size.
    live_wing_points: int = Field(default=100, alias="QT_LIVE_WING_PTS")       # protective wing distance
    # Event-risk veto: skip selling premium into Budget/RBI/election-class events.
    live_event_veto: bool = Field(default=True, alias="QT_EVENT_VETO")
    event_entry_buffer_days: int = Field(default=3, alias="QT_EVENT_BUFFER")
    # Regime veto: OFF by default — backtested and it HURT (Sharpe 0.88->0.79,
    # bigger drawdown). Kept for experimentation; the technical/vol signals don't
    # predict hostile cycles. See FINDINGS.md. Enable with QT_REGIME_VETO=true.
    live_regime_veto: bool = Field(default=False, alias="QT_REGIME_VETO")
    regime_trend_z: float = Field(default=2.0, alias="QT_REGIME_TREND_Z")       # price stretch from SMA50
    regime_momentum_mult: float = Field(default=2.0, alias="QT_REGIME_MOM_MULT")  # |20d ret| vs 20d vol
    regime_vix_spike: float = Field(default=0.25, alias="QT_REGIME_VIX_SPIKE")   # VIX +25% in 5 days
    live_profit_target: float = Field(default=0.50, alias="QT_LIVE_PROFIT_TGT")  # close at 50% of max premium

    # --- Mean-reversion (buy-the-dip toward VWAP) params ---
    mr_entry_dev: float = Field(default=0.004, alias="QT_MR_DEV")     # enter when >0.4% below VWAP
    mr_take_profit: float = Field(default=0.005, alias="QT_MR_TP")    # +0.5% snap-back
    mr_stop_loss: float = Field(default=0.004, alias="QT_MR_SL")      # -0.4%
    mr_max_hold_bars: int = Field(default=12, alias="QT_MR_MAX_HOLD")
    mr_window_start: str = Field(default="09:45", alias="QT_MR_START")
    mr_window_end: str = Field(default="15:00", alias="QT_MR_END")

    # --- Opening Range Breakout (ORB) params ---
    opening_range_minutes: int = Field(default=30, alias="QT_OR_MINUTES")
    orb_entry_end: str = Field(default="14:00", alias="QT_ORB_ENTRY_END")
    orb_take_profit: float = Field(default=0.02, alias="QT_ORB_TP")   # +2% cap
    orb_stop_loss: float = Field(default=0.008, alias="QT_ORB_SL")    # 0.8% (trailing)
    orb_trail: bool = Field(default=True, alias="QT_ORB_TRAIL")
    orb_max_hold_bars: int = Field(default=60, alias="QT_ORB_MAX_HOLD")  # ~5h, effectively EOD

    # --- Intraday scalping / backtest params ---
    # Bar resolution for the intraday scanner (Fyers codes: "1","3","5","15"...).
    bar_resolution: str = Field(default="5", alias="QT_BAR_RESOLUTION")
    # Breakout entry: go long when close breaks the high of the last N bars.
    breakout_lookback: int = Field(default=12, alias="QT_BREAKOUT_LOOKBACK")
    # Only take a breakout if current bar volume > this multiple of avg volume.
    volume_mult: float = Field(default=1.5, alias="QT_VOLUME_MULT")
    # Per-trade exits (fraction of entry price).
    take_profit: float = Field(default=0.004, alias="QT_TAKE_PROFIT")  # +0.4%
    stop_loss: float = Field(default=0.002, alias="QT_STOP_LOSS")      # -0.2%
    # Time-stop: force-exit a trade after this many bars if neither TP/SL hit.
    max_hold_bars: int = Field(default=6, alias="QT_MAX_HOLD_BARS")
    # Quality filters (trade less, but better):
    #   VWAP trend filter — only enter longs when price is above the day's VWAP.
    use_vwap_filter: bool = Field(default=True, alias="QT_USE_VWAP")
    #   Time-of-day window — only enter between these IST times (morning momentum).
    entry_window_start: str = Field(default="09:20", alias="QT_ENTRY_START")
    entry_window_end: str = Field(default="11:00", alias="QT_ENTRY_END")
    # Square off all open positions at/after this IST time (no overnight risk).
    square_off_time: str = Field(default="15:15", alias="QT_SQUARE_OFF")

    initial_capital: float = Field(default=1_000_000.0, alias="QT_INITIAL_CAPITAL")

    # --- Risk limits (intraday) ---
    max_concurrent_positions: int = Field(default=5, alias="QT_MAX_POSITIONS")
    capital_per_trade: float = Field(default=0.10, alias="QT_CAPITAL_PER_TRADE")  # 10% NAV
    max_trades_per_day: int = Field(default=40, alias="QT_MAX_TRADES_PER_DAY")
    daily_loss_limit: float = Field(default=0.03, alias="QT_DAILY_LOSS_LIMIT")  # halt at -3% NAV

    # --- Costs ---
    slippage_bps: float = Field(default=3.0, alias="QT_SLIPPAGE_BPS")  # per side

    @property
    def fyers_token_string(self) -> str:
        """Token in the `app_id:access_token` form the Fyers SDK expects."""
        return f"{self.fyers_app_id}:{self.fyers_access_token}"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
