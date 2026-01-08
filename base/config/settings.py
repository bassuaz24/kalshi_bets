"""
Configuration settings for the trading bot.
Loads from environment variables and .env file.
"""

import os
from pathlib import Path

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Base directories
BASE_DIR = Path(os.getenv("BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data_collection" / "data_curr"))
POSITIONS_FILE = BASE_DIR / "positions.json"

# Kalshi API settings
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://trading-api.kalshi.com")
API_KEY_ID = os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_ACCESS_KEY_ID")
PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", BASE_DIR / "private_key.pem")

# OddsAPI settings
ODDS_API_KEY = os.getenv("ODDS_API_KEY") or os.getenv("API_BET_API")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Trading settings
PLACE_LIVE_KALSHI_ORDERS = os.getenv("PLACE_LIVE_KALSHI_ORDERS", "NO")
CAPITAL_SIM = float(os.getenv("CAPITAL_SIM", "10000.0"))
VERBOSE = os.getenv("VERBOSE", "False").lower() == "true"

# Refresh intervals (seconds)
REFRESH_ACTIVE = float(os.getenv("REFRESH_ACTIVE", "10.0"))
REFRESH_IDLE = float(os.getenv("REFRESH_IDLE", "60.0"))
NO_OVERLAP_SLEEP_SECS = float(os.getenv("NO_OVERLAP_SLEEP_SECS", "300.0"))

# Data collection settings
DATA_COLLECTION_INTERVAL = float(os.getenv("DATA_COLLECTION_INTERVAL", "60.0"))
DATA_SEPARATE_BY_LEAGUE = os.getenv("DATA_SEPARATE_BY_LEAGUE", "True").lower() == "true"

# Order settings
ORDER_FILL_TIME = float(os.getenv("ORDER_FILL_TIME", "30.0"))
BALANCE_CACHE_SECS = float(os.getenv("BALANCE_CACHE_SECS", "10.0"))

# Strategy settings
MIN_BET_SIZE = float(os.getenv("MIN_BET_SIZE", "1.0"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "0.05"))  # 5% max stake per trade

# Risk management
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "0.30"))  # 30% max total exposure
MAX_EXPOSURE_PER_EVENT_PCT = float(os.getenv("MAX_EXPOSURE_PER_EVENT_PCT", "0.10"))  # 10% per event

# Logging settings
WRITE_TRADES_CSV = os.getenv("WRITE_TRADES_CSV", "True").lower() == "true"
WRITE_DAILY_REPORTS = os.getenv("WRITE_DAILY_REPORTS", "True").lower() == "true"
WRITE_SESSION_METRICS = os.getenv("WRITE_SESSION_METRICS", "True").lower() == "true"

# UI settings
UI_PORT = int(os.getenv("UI_PORT", "8080"))
UI_HOST = os.getenv("UI_HOST", "127.0.0.1")

# Kalshi price formatting
TICK = 1  # Kalshi prices in cents, so tick is 1

# Sport keys for OddsAPI
SPORT_KEYS = {
    "NFL": "americanfootball_nfl",
    "CFB": "americanfootball_ncaaf",
    "NBA": "basketball_nba",
    "CBBM": "basketball_ncaab",
    "CBBW": "basketball_wncaab",
}

# Markets to fetch from OddsAPI
ODDS_API_MARKETS = "h2h,spreads,totals"
ODDS_API_REGION = "us"
ODDS_API_BOOKMAKERS = ["fanduel", "pinnacle", "betus", "betonlineag"]