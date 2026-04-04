import os
from dotenv import load_dotenv

load_dotenv()

# --- API KEYS ---
API_KEY = os.getenv("BINANCE_API_KEY")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

# --- MODE ---
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# --- EXCHANGE SETTINGS ---
TESTNET = False
DEMO_URL = "https://demo-fapi.binance.com"

# --- VALIDATED TOKEN UNIVERSE (from backtesting) ---
# Only DEPLOY tokens — walk-forward validated, 5 years data
DEPLOY_TOKENS = [
    "ADA/USDT:USDT",
    "BCH/USDT:USDT",
    "TAO/USDT:USDT",
    "KAS/USDT:USDT",
    "RENDER/USDT:USDT",
    "WLD/USDT:USDT",
    "ENA/USDT:USDT",
    "SUI/USDT:USDT",
    "POL/USDT:USDT",
    "XRP/USDT:USDT",
    "NEAR/USDT:USDT",
    "LINK/USDT:USDT",
    "TON/USDT:USDT",
    "FET/USDT:USDT",
    "ATOM/USDT:USDT",
]

# Per-token strategy mapping from backtesting
TOKEN_STRATEGIES = {
    "ADA/USDT:USDT":    "RSI_Pullback_VWAP",
    "BCH/USDT:USDT":    "RSI_Pullback_VWAP",
    "TAO/USDT:USDT":    "Stoch_EMA_Volume",
    "KAS/USDT:USDT":    "Stoch_EMA_Volume",
    "RENDER/USDT:USDT": "Stoch_EMA_Volume",
    "WLD/USDT:USDT":    "Stoch_EMA_Volume",
    "ENA/USDT:USDT":    "RSI_Pullback_VWAP",
    "SUI/USDT:USDT":    "Stoch_EMA_Volume",
    "POL/USDT:USDT":    "Stoch_EMA_Volume",
    "XRP/USDT:USDT":    "RSI_Pullback_VWAP",
    "NEAR/USDT:USDT":   "Stoch_EMA_Volume",
    "LINK/USDT:USDT":   "Stoch_EMA_Volume",
    "TON/USDT:USDT":    "Stoch_EMA_Volume",
    "FET/USDT:USDT":    "Stoch_EMA_Volume",
    "ATOM/USDT:USDT":   "Stoch_EMA_Volume",
}

# --- UNIVERSE SETTINGS ---
TOP_N_COINS = 100
STABLE_COINS = ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD"]
BLACKLIST = []

# --- STRATEGY SETTINGS ---
TIMEFRAME = "1h"
EMA_FAST = 21
EMA_SLOW = 50
RSI_PERIOD = 14
RSI_LONG_MIN = 35
RSI_LONG_MAX = 50
RSI_SHORT_MIN = 50
RSI_SHORT_MAX = 65
ADX_PERIOD = 14
ADX_THRESHOLD = 25
ATR_PERIOD = 14
VOLUME_MULTIPLIER = 1.5
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3

# --- RISK MANAGEMENT ---
INITIAL_CAPITAL = 1000.0
RISK_PER_TRADE_PCT = 0.02
MAX_DRAWDOWN_PCT = 0.20
LEVERAGE = 3
ATR_STOP_MULTIPLIER = 1.5
ATR_TRAIL_MULTIPLIER = 1.0
MAX_OPEN_TRADES = 5
TIME_STOP_HOURS = 48

# --- FEES ---
TAKER_FEE = 0.0005
SLIPPAGE = 0.0005

# --- PATHS ---
DB_PATH = "data/trades.db"
LOG_PATH = "logs/bot.log"