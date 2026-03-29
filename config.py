import os
from dotenv import load_dotenv

load_dotenv()

# --- API KEYS ---
API_KEY = os.getenv("BINANCE_API_KEY")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

# --- MODE ---
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # paper or live

# --- EXCHANGE SETTINGS ---
TESTNET = False
DEMO_URL = "https://demo-fapi.binance.com"

# --- UNIVERSE SETTINGS ---
TOP_N_COINS = 100
STABLE_COINS = ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD"]
BLACKLIST = []  # add token symbols here you want to exclude e.g. ["XYZ", "ABC"]

# --- STRATEGY SETTINGS ---
TIMEFRAME = "4h"
EMA_FAST = 21
EMA_SLOW = 50
RSI_PERIOD = 14
RSI_LONG_MIN = 45
RSI_LONG_MAX = 70
RSI_SHORT_MIN = 30
RSI_SHORT_MAX = 55
ADX_PERIOD = 14
ADX_THRESHOLD = 25
ATR_PERIOD = 14
VOLUME_MULTIPLIER = 1.5

# --- RISK MANAGEMENT ---
INITIAL_CAPITAL = 1000.0
RISK_PER_TRADE_PCT = 0.02       # 2% risk per trade
MAX_DRAWDOWN_PCT = 0.20         # stop trading at 20% drawdown
LEVERAGE = 3                    # 3x leverage max
ATR_STOP_MULTIPLIER = 1.5       # stop loss = 1.5x ATR
ATR_TRAIL_MULTIPLIER = 1.0      # trailing stop = 1x ATR
MAX_OPEN_TRADES = 5             # max simultaneous positions
TIME_STOP_HOURS = 48            # exit trade if no movement in 48 hours

# --- FEES (Binance Futures) ---
TAKER_FEE = 0.0005              # 0.05% per side
SLIPPAGE = 0.0005               # simulated slippage 0.05%

# --- PATHS ---
DB_PATH = "data/trades.db"
LOG_PATH = "logs/bot.log"