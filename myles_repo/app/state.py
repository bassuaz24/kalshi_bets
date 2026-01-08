from typing import Dict, List, Any
from config import settings

capital_sim = settings.CAPITAL_SIM
positions = []
closed_trades = []
wins = 0
losses = 0
realized_pnl = 0.0
_snapshot_scan_counter = 0

_active_matches_for_api: List[Dict[str, Any]] = []
_game_ticks_history: Dict[str, List[Dict[str, Any]]] = {}

_last_snapshot_write_per_match = {}

_last_balance_ts = 0.0
_last_balance_val = settings.CAPITAL_SIM
_last_portfolio_value_ts = 0.0
_last_portfolio_value_val = None

_odds_cache_events = []
_odds_cache_ts = 0.0
_last_odds_request_ts = 0.0
_odds_prev_snapshot: Dict[str, Dict[str, Dict[str, float]]] = {}
_odds_snapshot_loaded = False

_PEAK_PROFITS: Dict[str, dict] = {}

_LAST_PROCESSED_ODDS: Dict[str, Dict[str, float]] = {}

_FIRST_DETECTION_TIMES = {}
_FIRST_DETECTION_TIMES_LOADED = False

SESSION_START_BAL = None
SESSION_START_PORTFOLIO_VALUE = None
SESSION_START_TIME = None

METRICS = {
    "orders_placed": 0,
    "orders_filled": 0,
    "orders_timeout_cancel": 0,
    "avg_slippage_bps_sum": 0.0,
    "avg_slippage_bps_n": 0,
    "missed_hedge_band": 0,
    "missed_hedge_cap": 0,
    "missed_hedge_kelly": 0,
    "missed_wide_spread": 0,
    "skip_counts": {},
}
