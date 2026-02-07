"""
Configuration settings for the trading bot.
Loads from environment variables and .env file.
"""

import os
from pathlib import Path

# Calculate base directory first (before loading .env, in case BASE_DIR is in .env)
_CONFIG_DIR = Path(os.path.dirname(os.path.abspath(__file__)))  # config/ directory
_BASE_DIR = _CONFIG_DIR.parent  # base/ directory

# Load environment variables from .env if present
# Explicitly specify the path to ensure we load from the base directory
try:
    from dotenv import load_dotenv
    env_path = _BASE_DIR / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        # Fallback: search from current directory (for backward compatibility)
        load_dotenv()
except ImportError:
    pass

# Base directories (can be overridden by BASE_DIR env var)
BASE_DIR = Path(os.getenv("BASE_DIR", str(_BASE_DIR)))
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data_collection" / "data_curr"))
KALSHI_DATA_DIR = Path(os.getenv("KALSHI_DATA_DIR", BASE_DIR / "data_collection" / "kalshi_data"))
POSITIONS_FILE = BASE_DIR / "positions.json"

# Kalshi API settings
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
KALSHI_WS_URL = os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2")
API_KEY_ID = os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_ACCESS_KEY_ID")
PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", BASE_DIR / "kalshi_private_key.pem")

# OddsAPI settings
ODDS_API_KEY = os.getenv("ODDS_API_KEY") or os.getenv("API_BET_API")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Trading settings
PLACE_LIVE_KALSHI_ORDERS = os.getenv("PLACE_LIVE_KALSHI_ORDERS", "NO")
CAPITAL_SIM = float(os.getenv("CAPITAL_SIM", "10000.0"))
VERBOSE = os.getenv("VERBOSE", "False").lower() == "true"

# Main strategy loop timing (seconds)
STRATEGY_LOOP_INTERVAL = float(os.getenv("STRATEGY_LOOP_INTERVAL", "30.0"))  # Fixed interval for strategy execution

# Stop loss monitoring timing (seconds)
STOP_LOSS_CHECK_INTERVAL = float(os.getenv("STOP_LOSS_CHECK_INTERVAL", "2.0"))  # High-frequency stop loss checks

# UI/Performance update timing (seconds)
UI_UPDATE_INTERVAL = float(os.getenv("UI_UPDATE_INTERVAL", "1.0"))  # Frequent UI updates

# Position reconciliation timing (seconds)
RECONCILE_INTERVAL = float(os.getenv("RECONCILE_INTERVAL", "10.0"))  # Full reconciliation interval

# Legacy settings (kept for backward compatibility, but not used in new architecture)
REFRESH_ACTIVE = float(os.getenv("REFRESH_ACTIVE", "10.0"))
REFRESH_IDLE = float(os.getenv("REFRESH_IDLE", "60.0"))
NO_OVERLAP_SLEEP_SECS = float(os.getenv("NO_OVERLAP_SLEEP_SECS", "300.0"))

# Data collection settings
DATA_COLLECTION_INTERVAL = float(os.getenv("DATA_COLLECTION_INTERVAL", "60.0"))  # Legacy, not used in new architecture
DATA_SEPARATE_BY_LEAGUE = os.getenv("DATA_SEPARATE_BY_LEAGUE", "True").lower() == "true"

# WebSocket settings
WEBSOCKET_ENABLED = os.getenv("WEBSOCKET_ENABLED", "True").lower() == "true"
WEBSOCKET_RECONNECT_DELAY = float(os.getenv("WEBSOCKET_RECONNECT_DELAY", "5.0"))  # Initial reconnect delay (exponential backoff)
WEBSOCKET_MAX_RECONNECT_DELAY = float(os.getenv("WEBSOCKET_MAX_RECONNECT_DELAY", "60.0"))  # Max reconnect delay
WEBSOCKET_PRICE_CACHE_STALE_SECS = float(os.getenv("WEBSOCKET_PRICE_CACHE_STALE_SECS", "60.0"))  # Consider price stale after N seconds

# Market discovery settings
MIN_TRADING_VOLUME_PER_EVENT = int(os.getenv("MIN_TRADING_VOLUME_PER_EVENT", "0"))  # Minimum volume threshold (0 = no filter)

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
    "NBA": "basketball_nba",
    "CBBM": "basketball_ncaab",
    "CBBW": "basketball_wncaab",
    "ATP": ["tennis_atp_aus_open_singles", "tennis_atp_canadian_open", "tennis_atp_china_open", "tennis_atp_cincinnati_open",
    "tennis_atp_dubai", "tennis_atp_french_open", "tennis_atp_indian_wells", "tennis_atp_italian_open", "tennis_atp_madrid_open",
    "tennis_atp_miami_open", "tennis_atp_monte_carlo_masters", "tennis_atp_paris_masters", "tennis_atp_qatar_open", "tennis_atp_us_open", "tennis_atp_wimbledon"],
    "WTA": ["tennis_wta_aus_open_singles", "tennis_wta_canadian_open", "tennis_wta_china_open", "tennis_wta_cincinnati_open",
    "tennis_wta_dubai", "tennis_wta_french_open", "tennis_wta_indian_wells", "tennis_wta_italian_open", "tennis_wta_madrid_open",
    "tennis_wta_miami_open", "tennis_wta_qatar_open", "tennis_wta_us_open", "tennis_wta_wimbledon", "tennis_wta_wuhan_open"]
}

# Markets to fetch from OddsAPI
ODDS_API_MARKETS = "h2h,spreads,totals"
ODDS_API_REGION = "us"
ODDS_API_BOOKMAKERS = ["fanduel", "pinnacle", "betus", "betonlineag"]

# Kalshi collector settings (KALSHI_COLLECTOR_RUNTIME in seconds; empty/absent = indefinite)
_runtime = os.getenv("KALSHI_COLLECTOR_RUNTIME", "").strip()
KALSHI_COLLECTOR_RUNTIME = float(_runtime) if _runtime else None

# OddsAPI integration settings
ODDS_API_FETCH_INTERVAL = float(os.getenv("ODDS_API_FETCH_INTERVAL", "1800.0"))  # Fetch interval in seconds (default: 30 minutes)

# Bookmaker weights for weighted average calculation (weights should sum to 1.0)
# Keys should match bookmaker names from OddsAPI (case-insensitive matching)
ODDS_API_BOOKMAKER_WEIGHTS = {
    "Pinnacle": 0.7,
    "BetOnline.ag": 0.1,
    "BetUS": 0.1,
    "FanDuel": 0.1
    # Add more bookmakers as needed
}