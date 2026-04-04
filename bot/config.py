# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/config.py — Single Source of Truth
# Version 3.0 — Multi-Timeframe Confirmation Strategy
# =============================================================================
# HOW TO USE THIS FILE:
# Every parameter for the entire system lives here.
# To change any behaviour, change it here ONLY.
# Never hardcode values anywhere else in the codebase.
# =============================================================================

import os
from pathlib import Path

# =============================================================================
# 1. ENVIRONMENT
# =============================================================================

PAPER_TRADING = True

BASE_DIR = Path(__file__).resolve().parent

# =============================================================================
# 2. EXCHANGE CONFIGURATION
# =============================================================================

EXCHANGE = {
    "name": "binanceusdm",

    "paper": {
        "api_key":    os.getenv("BINANCE_API_KEY", ""),
        "api_secret": os.getenv("BINANCE_API_SECRET", ""),
        "urls": {
            "api": {
                "public":       "https://demo-fapi.binance.com/fapi/v1",
                "private":      "https://demo-fapi.binance.com/fapi/v1",
                "fapiPublic":   "https://demo-fapi.binance.com/fapi/v1",
                "fapiPrivate":  "https://demo-fapi.binance.com/fapi/v1",
                "fapiPublicV2": "https://demo-fapi.binance.com/fapi/v2",
                "fapiPrivateV2":"https://demo-fapi.binance.com/fapi/v2",
            }
        },
    },

    "live": {
        "api_key":    os.getenv("BINANCE_LIVE_API_KEY", ""),
        "api_secret": os.getenv("BINANCE_LIVE_API_SECRET", ""),
    },
}

# =============================================================================
# 3. CAPITAL CONFIGURATION
# =============================================================================

INITIAL_CAPITAL  = 10000.0   # Starting capital in USDT
RESERVE_PCT      = 0.05     # 5% always kept as reserve
MAX_DEPLOYED_PCT = 0.95     # Maximum 95% capital in trades at any time
CAPITAL_PER_SLOT = 0.10     # Each trade slot uses 10% of capital

# Leverage
MIN_LEVERAGE     = 1
DEFAULT_LEVERAGE = 2        # Default leverage for all tiers
MAX_LEVERAGE     = 2        # Hard maximum — never exceed

# =============================================================================
# 4. TIER CONFIGURATION
# =============================================================================
# Tiers define signal confidence, slot count, RRR.
# To add a new tier: add an entry here. Nothing else needs to change.

TIERS = {
    "tier1": {
        "name":               "High Confidence",
        "score_percentile":   75,        # Top 25% backtest scores
        "max_slots":          4,
        "capital_pct":        0.40,
        "min_rrr":            1.5,       # 1:1.5 RRR for faster TP hits
        "btc_filter":         "strict",  # All tiers use strict BTC filter
        "min_confluence":     4,         # 4 of 6 indicators must agree
        "asian_session_size": 0.0,       # Skip Asian session entirely
        "leverage":           2,         # 2x leverage for Tier 1
    },
    "tier2": {
        "name":               "Medium Confidence",
        "score_percentile":   50,
        "max_slots":          3,
        "capital_pct":        0.30,
        "min_rrr":            1.5,
        "btc_filter":         "strict",
        "min_confluence":     4,
        "asian_session_size": 0.0,
        "leverage":           2,
    },
    "tier3": {
        "name":               "Low Confidence",
        "score_percentile":   25,
        "max_slots":          2,
        "capital_pct":        0.25,
        "min_rrr":            1.5,
        "btc_filter":         "strict",
        "min_confluence":     3,
        "asian_session_size": 0.0,       # Skip Asian session entirely
        "leverage":           1,         # Conservative 1x for Tier 3
    },
}

MIN_CONFLUENCE_ANY = 3

# =============================================================================
# 5. MULTI-TIMEFRAME CONFIGURATION
# =============================================================================
# Core change from v2.0: All tokens use multi-timeframe confirmation.
# 1D sets macro direction → 4H confirms → 1H triggers entry.
# All 3 must align before any trade fires.

TIMEFRAMES = ["1h", "4h", "1d"]

# Multi-timeframe confirmation hierarchy
MTF_CONFIG = {
    "macro_tf":     "1d",    # Sets overall trend direction
    "confirm_tf":   "4h",    # Confirms intermediate trend
    "entry_tf":     "1h",    # Triggers actual entry
    "all_required": True,    # All 3 timeframes must agree — no exceptions
}

# Tiebreaker still applies for backtest scoring
TIMEFRAME_TIEBREAKER_PCT  = 0.05
TIMEFRAME_PRIORITY        = ["1h", "4h", "1d"]

# Time stop rules per timeframe (based on entry timeframe = 1H)
TIME_STOP_CANDLES = {
    "1h": 24,    # 24 hours max per trade
    "4h": 12,    # 2 days max
    "1d": 7,     # 7 days max
}

# =============================================================================
# 6. INDICATOR CONFIGURATION
# =============================================================================

MANDATORY_INDICATORS    = ["ema", "vwap_or_200ema"]
CONFIRMATION_INDICATORS = ["rsi", "volume", "macd", "bollinger"]

EMA = {
    "fast":  20,
    "slow":  50,
    "macro": 200,
}

VWAP_TIMEFRAMES     = ["1h"]
EMA_200_TIMEFRAMES  = ["4h", "1d"]

RSI = {
    "period":     14,
    "oversold":   35,    # Slightly relaxed from 30 for more signals
    "overbought": 65,    # Slightly relaxed from 70 for more signals
}

MACD = {
    "fast":   12,
    "slow":   26,
    "signal": 9,
}

BOLLINGER = {
    "period":  20,
    "std_dev": 2.0,
}

VOLUME = {
    "period":          20,
    "min_multiplier":  1.1,    # Relaxed from 1.2 — slightly lower volume bar
}

# =============================================================================
# 7. RISK MANAGEMENT
# =============================================================================

SL = {
    "atr_period":     14,
    "atr_multiplier": 1.5,
}

TRAILING_SL = {
    "breakeven_at": 1.0,    # Move SL to breakeven at 1x risk in profit
    "trail_at":     1.5,    # Start trailing at 1.5x risk in profit
    "trail_lock":   0.5,    # Lock in 0.5x risk as minimum profit
}

TP = {
    "tier1_close_pct":      0.40,   # Close 40% at 1.5x RRR target
    "tier1_rrr":            1.5,    # First exit at 1.5x risk
    "tier2_close_pct":      0.30,   # Close another 30% at 2x RRR target
    "tier2_rrr":            2.0,    # Second exit at 2x risk
    "trail_pct":            0.30,   # Remaining 30% trails with ATR
    "trail_atr_multiplier": 1.5,    # Trail with 1.5x ATR — gives room to breathe
}

ENTRY = {
    "leg1_pct":           0.60,   # Enter 60% at signal candle close
    "leg2_pct":           0.40,   # Enter 40% on pullback
    "leg2_candle_window": 3,      # Pullback must happen within 3 candles
}

SL_COOLDOWN_CANDLES = {
    "1h": 4,    # 4 hours = one full 4H confirmation candle
    "4h": 6,    # 24 hours = one full 1D confirmation candle
    "1d": 3,    # 3 days = weekly trend reassessment
}

# =============================================================================
# 8. DRAWDOWN CIRCUIT BREAKERS
# =============================================================================

DRAWDOWN = {
    "alert_pct": 0.15,   # 15% — alert only
    "pause_pct": 0.25,   # 25% — pause new entries
    "stop_pct":  0.40,   # 40% — full stop
}

# =============================================================================
# 9. FILTER CONFIGURATION
# =============================================================================

FILTERS = {

    "liquidity": {
        "enabled":              True,
        "min_daily_volume_usd": 10_000_000,
    },

    "funding_rate": {
        "enabled":   True,
        "max_long":  0.001,
        "min_short": -0.001,
    },

    "fear_greed": {
        "enabled":                  True,
        "extreme_fear_threshold":   20,
        "extreme_greed_threshold":  80,
        "api_url": "https://api.alternative.me/fng/",
    },

    "correlation": {
        "enabled":               True,
        "lookback_days":         90,
        "max_correlated_trades": 2,
        "correlation_threshold": 0.85,
    },

    "volume": {
        "enabled":          True,
        "min_multiplier":   1.1,
        "lookback_period":  20,
    },

    "session": {
        "enabled": True,
        # Times in UTC — Asian session skipped entirely for all tiers
        "us_session":    {"start": 13, "end": 21, "size_multiplier": 1.0},
        "euro_session":  {"start": 7,  "end": 13, "size_multiplier": 1.0},
        "asian_session": {"start": 0,  "end": 7,  "size_multiplier": 0.0},
    },

    "candle_close": {
        "enabled": True,
    },

    "cooldown": {
        "enabled":          True,
        "candles_after_sl": {
            "1h": 4,
            "4h": 6,
            "1d": 3,
        },
    },

    # NEW: Multi-timeframe alignment filter
    "mtf_alignment": {
        "enabled":      True,
        "require_all":  True,   # All 3 timeframes must agree
    },

    # NEW: RSI zone filter — only enter when RSI is in favorable zone
    "rsi_zone": {
        "enabled":          True,
        "long_max_rsi":     60,  # Don't enter long if RSI already above 60
        "short_min_rsi":    40,  # Don't enter short if RSI already below 40
    },

    # NEW: Price position filter — only enter near EMA, not extended
    "price_position": {
        "enabled":          True,
        "max_ema_distance": 0.03,  # Price must be within 3% of EMA fast
    },
}

# =============================================================================
# 10. BTC TREND FILTER
# =============================================================================
# Strict for ALL tiers in v3.0 — no soft overrides
# Multi-timeframe BTC confirmation required

BTC_FILTER = {
    "enabled":       True,
    "symbol":        "BTC/USDT:USDT",
    "trend_ema_fast": 20,
    "trend_ema_slow": 50,
    "require_mtf":   True,   # BTC trend confirmed on both 1H and 4H
    # All tiers strict — defined in TIERS config above
}

# =============================================================================
# 11. ROLLING PERFORMANCE MONITOR
# =============================================================================

PERFORMANCE_MONITOR = {
    "enabled":         True,
    "lookback_trades": 20,
    "min_expectancy":  0.0,
    "min_win_rate":    0.40,   # Pause if rolling win rate drops below 40%
}

# =============================================================================
# 12. APEX BACKTEST CONFIGURATION
# =============================================================================

BACKTEST = {
    "lookback_years":     1,
    "train_months":       8,
    "validate_months":    4,
    "batch_size":         10,
    "min_trades": {          # Per-timeframe minimum trades in validation window
        "1h": 15,
        "4h": 8,
        "1d": 5,
    },
    "max_overfitting_gap": 0.25,  # Relaxed from 0.10
}

# Scoring weights — must sum to 1.0
SCORING_WEIGHTS = {
    "expectancy":    0.35,   # Increased — most important metric
    "win_rate":      0.25,   # Increased — we want high win rate
    "max_drawdown":  0.15,
    "profit_factor": 0.15,
    "sharpe_ratio":  0.10,
}

# Minimum thresholds — strategy fails if ANY not met
SCORING_MINIMUMS = {
    "expectancy":    0.0,    # Must be positive
    "win_rate":      0.38,   # 38% minimum — expectancy is the real guard
    "max_drawdown":  0.25,   # Allow up to 25% drawdown in backtest
    "profit_factor": 1.1,    # Minimum 1.1
    "sharpe_ratio":  0.3,    # Minimum 0.3
}

# =============================================================================
# 13. REBALANCING SCHEDULE
# =============================================================================

REBALANCE = {
    "weekly": {
        "enabled": True,
        "day":     "sunday",
        "hour":    0,
        "minute":  0,
    },
    "monthly": {
        "enabled": True,
        "day":     1,
        "hour":    1,
        "minute":  0,
    },
}

# =============================================================================
# 14. TELEGRAM CONFIGURATION
# =============================================================================

TELEGRAM = {
    "enabled":   True,
    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "chat_id":   os.getenv("TELEGRAM_CHAT_ID", ""),

    "alerts": {
        "trade_open":          True,
        "trade_close":         True,
        "stop_loss_hit":       True,
        "take_profit_hit":     True,
        "drawdown_alert":      True,
        "drawdown_pause":      True,
        "drawdown_stop":       True,
        "rebalance_complete":  True,
        "token_added":         True,
        "token_removed":       True,
        "performance_pause":   True,
        "bot_error":           True,
    },
}

# =============================================================================
# 15. DATABASE CONFIGURATION
# =============================================================================

DB = {
    "trades": str(BASE_DIR.parent / "data" / "trades.db"),
    "apex":   str(BASE_DIR.parent / "data" / "apex.db"),
}

# =============================================================================
# 16. LOGGING CONFIGURATION
# =============================================================================

LOGS = {
    "bot":       str(BASE_DIR.parent / "logs" / "bot.log"),
    "apex":      str(BASE_DIR.parent / "logs" / "apex.log"),
    "streamlit": str(BASE_DIR.parent / "logs" / "streamlit.log"),
    "level":     "INFO",
    "max_bytes":    10 * 1024 * 1024,
    "backup_count": 5,
}

# =============================================================================
# 17. DASHBOARD CONFIGURATION
# =============================================================================

DASHBOARD = {
    "port":            8501,
    "host":            "0.0.0.0",
    "refresh_seconds": 60,
    "title":           "AUTO-TRADING AI AGENT",
}

# =============================================================================
# 18. GO-LIVE CRITERIA
# =============================================================================

GO_LIVE_CRITERIA = {
    "min_closed_trades":   100,
    "max_win_rate_gap":    0.10,
    "min_expectancy":      0.0,
    "max_drawdown_alerts": 2,
}

# =============================================================================
# 19. PROFIT TARGETS
# =============================================================================

PROFIT_TARGETS = {
    "daily_target_pct":    0.8,    # ~0.8% per day = ~20% monthly
    "monthly_target_pct":  20.0,   # 20% monthly target
    "monthly_stretch_pct": 30.0,   # 30% stretch goal in bull markets
}

# =============================================================================
# 20. STABLECOIN & EXCLUDED TOKEN LIST
# =============================================================================

EXCLUDED_TOKENS = [
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD",
    "WBTC", "WETH", "WBNB",
    "STETH", "RETH", "CBETH",
]

# =============================================================================
# END OF CONFIG
# =============================================================================

# ── APEX Event Logger ────────────────────────────────────────────────────────
# Initialized here so every module can do: from bot.config import apex_logger
from bot.logger import APEXLogger as _APEXLogger
APEX_LOG_DIR = str(BASE_DIR.parent / "logs" / "apex_events")
apex_logger  = _APEXLogger(APEX_LOG_DIR)

def get_config_dict() -> dict:
    """Return current config as a snapshot dict for structured logging."""
    return {
        "PAPER_TRADING":       PAPER_TRADING,
        "INITIAL_CAPITAL":     INITIAL_CAPITAL,
        "RESERVE_PCT":         RESERVE_PCT,
        "MAX_DEPLOYED_PCT":    MAX_DEPLOYED_PCT,
        "CAPITAL_PER_SLOT":    CAPITAL_PER_SLOT,
        "MAX_LEVERAGE":        MAX_LEVERAGE,
        "DRAWDOWN":            DRAWDOWN,
        "GO_LIVE_CRITERIA":    GO_LIVE_CRITERIA,
        "PERFORMANCE_MONITOR": PERFORMANCE_MONITOR,
        "SCORING_MINIMUMS":    SCORING_MINIMUMS,
        "BTC_FILTER_ENABLED":  BTC_FILTER.get("enabled", True),
        "FILTERS_ENABLED": {
            k: v.get("enabled", True)
            for k, v in FILTERS.items()
            if isinstance(v, dict)
        },
    }

# __APEX_LOGGER_V1__
