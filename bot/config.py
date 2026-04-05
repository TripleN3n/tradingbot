# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/config.py — Single Source of Truth
# Version 3.1 — Parameter corrections and alignment to strategy spec
# =============================================================================
# CHANGES FROM v3.0:
# - TIME_STOP_CANDLES["1h"]: 24 → 30 (spec alignment)
# - TIERS min_rrr: tier1=2.0, tier2=1.75, tier3=1.5 (spec alignment)
# - DRAWDOWN thresholds: 15/25/40% → 20/35/50% (spec alignment)
# - RSI zone filter short_min_rsi: 40 → 28 (too aggressive in bearish markets)
# - Price position filter max_ema_distance: 3% → 8% (too tight for trends)
# - Session filter Asian session: now tier-aware per spec (50% for tier1/2)
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
# Tiers define signal confidence, slot count, RRR.
# To add a new tier: add an entry here. Nothing else needs to change.
#
# FIX: min_rrr now correctly differentiated by tier per strategy spec:
#   Tier1 (highest confidence) → 1:2.0 RRR — best signals deserve best targets
#   Tier2 (medium confidence)  → 1:1.75 RRR
#   Tier3 (lower confidence)   → 1:1.5 RRR — minimum acceptable RRR

TIERS = {
    "tier1": {
        "name":               "High Confidence",
        "score_percentile":   75,        # Top 25% backtest scores
        "max_slots":          4,
        "capital_pct":        0.40,
        "min_rrr":            2.0,       # 1:2 RRR — highest confidence, widest TP
        "btc_filter":         "strict",
        "min_confluence":     4,         # 4 of 6 indicators must agree
        "asian_session_size": 0.5,       # 50% position size in Asian session
        "leverage":           2,
    },
    "tier2": {
        "name":               "Medium Confidence",
        "score_percentile":   50,
        "max_slots":          3,
        "capital_pct":        0.30,
        "min_rrr":            1.75,      # 1:1.75 RRR
        "btc_filter":         "strict",
        "min_confluence":     4,
        "asian_session_size": 0.5,       # 50% position size in Asian session
        "leverage":           2,
    },
    "tier3": {
        "name":               "Low Confidence",
        "score_percentile":   25,
        "max_slots":          2,
        "capital_pct":        0.25,
        "min_rrr":            1.5,       # 1:1.5 RRR — minimum acceptable
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

TIMEFRAMES = ["1h", "4h", "1d"]

MTF_CONFIG = {
    "macro_tf":     "1d",
    "confirm_tf":   "4h",
    "entry_tf":     "1h",
    "all_required": True,
}

TIMEFRAME_TIEBREAKER_PCT  = 0.05
TIMEFRAME_PRIORITY        = ["1h", "4h", "1d"]

# Time stop rules per entry timeframe.
# FIX: 1H corrected from 24 to 30 candles per strategy spec (30 hours max).
# 4H: 12 candles = 2 days. 1D: 7 candles = 7 days.
TIME_STOP_CANDLES = {
    "1h": 30,    # 30 hours max — FIX: was 24, spec says 30
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
    "atr_multiplier": 1.5,      # SL = 1.5× ATR minimum distance
    "min_pct":        0.015,    # Hard floor: SL must be at least 1.5% of entry
    "max_pct":        0.03,     # Hard cap: SL cannot exceed 3% of entry
}

TRAILING_SL = {
    "breakeven_at": 1.0,    # Move SL to breakeven at 1x risk in profit
    "trail_at":     1.5,    # Start trailing at 1.5x risk in profit
    "trail_lock":   0.5,    # Lock in 0.5x risk as minimum profit
}

# Two-stage TP system (applied to all tiers):
# Stage 1: Close 40% of position at 1.5x RRR — quick profit capture
# Stage 2: Close 30% of position at 2.0x RRR — let winners run
# Remaining 30%: Trail with 1.5x ATR until time stop or trailing SL hit
TP = {
    "tier1_close_pct":      0.40,
    "tier1_rrr":            1.5,    # First partial exit at 1.5x risk
    "tier2_close_pct":      0.30,
    "tier2_rrr":            2.0,    # Second partial exit at 2.0x risk
    "trail_pct":            0.30,
    "trail_atr_multiplier": 1.5,
}

ENTRY = {
    "leg1_pct":           0.60,
    "leg2_pct":           0.40,
    "leg2_candle_window": 3,
}

SL_COOLDOWN_CANDLES = {
    "1h": 4,
    "4h": 6,
    "1d": 3,
}

# =============================================================================
# 8. DRAWDOWN CIRCUIT BREAKERS
# =============================================================================
# FIX: Thresholds corrected to match strategy spec (was 15/25/40%).

DRAWDOWN = {
    "alert_pct": 0.20,   # 20% — Telegram alert, bot continues trading
    "pause_pct": 0.35,   # 35% — Pause new entries, existing trades finish
    "stop_pct":  0.50,   # 50% — Full stop, all trades closed at market
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
        # Times in UTC
        # FIX: Asian session is now tier-aware per strategy spec.
        # Tier1/Tier2: 50% position size in Asian session (was blocked entirely).
        # Tier3: Skip Asian session entirely (unchanged).
        # 1D timeframe signals are exempt — daily candle is already closed.
        "us_session":    {"start": 13, "end": 21, "size_multiplier": 1.0},
        "euro_session":  {"start": 7,  "end": 13, "size_multiplier": 1.0},
        "asian_session": {
            "start": 0, "end": 7,
            "tier1_size": 0.5,    # 50% position size — spec compliant
            "tier2_size": 0.5,    # 50% position size — spec compliant
            "tier3_size": 0.0,    # Tier3 fully blocked in Asian session
        },
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

    "mtf_alignment": {
        "enabled":     True,
        "require_all": True,
    },

    # FIX: short_min_rsi reduced from 40 to 28.
    # At 40, the filter blocked ALL short signals in Extreme Fear conditions
    # where RSI is typically 20–35. Shorts in the 28–40 RSI range are valid
    # when trend confluence is confirmed on higher timeframes.
    "rsi_zone": {
        "enabled":      True,
        "long_max_rsi":  60,   # Don't enter long when RSI already above 60
        "short_min_rsi": 28,   # Don't enter short when RSI already below 28
    },

    # FIX: max_ema_distance increased from 3% to 8%.
    # In a trending market, price routinely sits 5–15% from the 20-period EMA.
    # 3% was blocking entries precisely when trend momentum was clearest.
    "price_position": {
        "enabled":          True,
        "max_ema_distance": 0.08,   # Price must be within 8% of fast EMA
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

# v3.2: relaxed thresholds — bear-market short strategies can dip just below strict values
SCORING_MINIMUMS = {
    "expectancy":    0.0,    # Must be positive
    "win_rate":      0.35,   # 35% minimum (was 38%)
    "max_drawdown":  0.30,   # 30% max allowed (was 25%)
    "profit_factor": 1.05,   # Minimum 1.05 (was 1.1)
    "sharpe_ratio":  0.25,   # Minimum 0.25 (was 0.3)
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
