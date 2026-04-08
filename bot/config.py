# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/config.py — Single Source of Truth
# Version 3.7 — Exit-type based cooldown
# =============================================================================
# CHANGES FROM v3.1:
# - COOLDOWN_CANDLES: Replaces SL_COOLDOWN_CANDLES and timeframe-based cooldown.
#   Cooldown is now exit-type aware, not timeframe-aware:
#     stop_loss:   4 candles — market moved against you, wait longer
#     time_stop:   2 candles — setup stale, wait for fresh conditions
#     take_profit: 1 candle  — setup worked, minimal pause before re-entry
#   This applies uniformly across all timeframes.
#   Old SL_COOLDOWN_CANDLES (1h:4, 4h:6, 1d:3) removed.
#   FILTERS["cooldown"]["candles_after_sl"] updated to match.
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
RESERVE_PCT      = 0.05      # 5% always kept as reserve
MAX_DEPLOYED_PCT = 0.95      # Maximum 95% capital in trades at any time
CAPITAL_PER_SLOT = 0.10      # Each trade slot uses 10% of capital

# Leverage
MIN_LEVERAGE     = 1
DEFAULT_LEVERAGE = 2          # Default leverage for all tiers
MAX_LEVERAGE     = 2          # Hard maximum — never exceed

# =============================================================================
# 4. TIER CONFIGURATION
# =============================================================================

TIERS = {
    "tier1": {
        "name":               "High Confidence",
        "score_percentile":   75,
        "max_slots":          4,
        "capital_pct":        0.40,
        "min_rrr":            2.0,
        "btc_filter":         "strict",
        "min_confluence":     4,
        "asian_session_size": 0.5,
        "leverage":           2,
    },
    "tier2": {
        "name":               "Medium Confidence",
        "score_percentile":   50,
        "max_slots":          3,
        "capital_pct":        0.30,
        "min_rrr":            1.75,
        "btc_filter":         "strict",
        "min_confluence":     4,
        "asian_session_size": 0.5,
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
        "asian_session_size": 0.0,
        "leverage":           1,
    },
}

MIN_CONFLUENCE_ANY = 3

# =============================================================================
# 5. MULTI-TIMEFRAME CONFIGURATION
# =============================================================================

TIMEFRAMES = ["1h", "4h", "1d"]

MTF_CONFIG = {
    "macro_tf":     "1d",
    "confirm_tf":   "4h",
    "entry_tf":     "1h",
    "all_required": True,
}

TIMEFRAME_TIEBREAKER_PCT  = 0.05
TIMEFRAME_PRIORITY        = ["1h", "4h", "1d"]

TIME_STOP_CANDLES = {
    "1h": 30,
    "4h": 12,
    "1d": 7,
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
    "oversold":   35,
    "overbought": 65,
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
    "period":         20,
    "min_multiplier": 1.1,
}

# =============================================================================
# 7. RISK MANAGEMENT
# =============================================================================

SL = {
    "atr_period":     14,
    "atr_multiplier": 1.5,
    "min_pct":        0.015,
    "max_pct":        0.03,
}

TRAILING_SL = {
    "breakeven_at": 1.0,
    "trail_at":     1.5,
    "trail_lock":   0.5,
}

TP = {
    "tier1_close_pct":      0.40,
    "tier1_rrr":            1.5,
    "tier2_close_pct":      0.30,
    "tier2_rrr":            2.0,
    "trail_pct":            0.30,
    "trail_atr_multiplier": 1.5,
}

ENTRY = {
    "leg1_pct":           0.60,
    "leg2_pct":           0.40,
    "leg2_candle_window": 3,
}

# =============================================================================
# COOLDOWN CONFIGURATION
# FIX v3.7: Cooldown is now exit-type based, not timeframe-based.
# Different exits tell you different things about market conditions:
#   stop_loss:   4 candles — market moved against you hard, stay out longer
#   time_stop:   2 candles — setup went nowhere, wait for fresh candle conditions
#   take_profit: 1 candle  — setup worked, minimal pause before re-entry
# Applied uniformly across all timeframes (1H/4H/1D).
# Old SL_COOLDOWN_CANDLES (1h:4, 4h:6, 1d:3) removed.
# =============================================================================

COOLDOWN_CANDLES = {
    "stop_loss":   4,
    "time_stop":   2,
    "take_profit": 1,
}

# =============================================================================
# 8. DRAWDOWN CIRCUIT BREAKERS
# =============================================================================

DRAWDOWN = {
    "alert_pct": 0.20,
    "pause_pct": 0.35,
    "stop_pct":  0.50,
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
        "enabled":                 True,
        "extreme_fear_threshold":  20,
        "extreme_greed_threshold": 80,
        "api_url": "https://api.alternative.me/fng/",
    },

    "correlation": {
        "enabled":               True,
        "lookback_days":         90,
        "max_correlated_trades": 2,
        "correlation_threshold": 0.85,
    },

    "volume": {
        "enabled":         True,
        "min_multiplier":  1.1,
        "lookback_period": 20,
    },

    "session": {
        "enabled": True,
        "us_session":    {"start": 13, "end": 21, "size_multiplier": 1.0},
        "euro_session":  {"start": 7,  "end": 13, "size_multiplier": 1.0},
        "asian_session": {
            "start": 0, "end": 7,
            "tier1_size": 0.5,
            "tier2_size": 0.5,
            "tier3_size": 0.0,
        },
    },

    "candle_close": {
        "enabled": True,
    },

    "cooldown": {
        "enabled": True,
        # Exit-type based cooldown candles (v3.7)
        # stop_loss=4, time_stop=2, take_profit=1
        # See COOLDOWN_CANDLES above — this is the single source of truth
        "candles": COOLDOWN_CANDLES,
    },

    "mtf_alignment": {
        "enabled":     True,
        "require_all": True,
    },

    "rsi_zone": {
        "enabled":       True,
        "long_max_rsi":  60,
        "short_min_rsi": 28,
    },

    "price_position": {
        "enabled":          True,
        "max_ema_distance": 0.08,
    },
}

# =============================================================================
# 10. BTC TREND FILTER
# =============================================================================

BTC_FILTER = {
    "enabled":        True,
    "symbol":         "BTC/USDT:USDT",
    "trend_ema_fast":  20,
    "trend_ema_slow":  50,
    "require_mtf":    True,
}

# =============================================================================
# 11. ROLLING PERFORMANCE MONITOR
# =============================================================================

PERFORMANCE_MONITOR = {
    "enabled":         True,
    "lookback_trades": 20,
    "min_expectancy":  0.0,
    "min_win_rate":    0.40,
}

# =============================================================================
# 12. APEX BACKTEST CONFIGURATION
# =============================================================================

BACKTEST = {
    "lookback_years":     1,
    "train_months":       8,
    "validate_months":    4,
    "batch_size":         10,
    "min_trades": {
        "1h": 15,
        "4h": 8,
        "1d": 5,
    },
    "max_overfitting_gap": 0.25,
}

SCORING_WEIGHTS = {
    "expectancy":    0.35,
    "win_rate":      0.25,
    "max_drawdown":  0.15,
    "profit_factor": 0.15,
    "sharpe_ratio":  0.10,
}

SCORING_MINIMUMS = {
    "expectancy":    0.0,
    "win_rate":      0.35,
    "max_drawdown":  0.30,
    "profit_factor": 1.05,
    "sharpe_ratio":  0.25,
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
        "trade_open":         True,
        "trade_close":        True,
        "stop_loss_hit":      True,
        "take_profit_hit":    True,
        "drawdown_alert":     True,
        "drawdown_pause":     True,
        "drawdown_stop":      True,
        "rebalance_complete": True,
        "token_added":        True,
        "token_removed":      True,
        "performance_pause":  True,
        "bot_error":          True,
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
    "daily_target_pct":    0.8,
    "monthly_target_pct":  20.0,
    "monthly_stretch_pct": 30.0,
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
        "TIME_STOP_CANDLES":   TIME_STOP_CANDLES,
        "COOLDOWN_CANDLES":    COOLDOWN_CANDLES,
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
