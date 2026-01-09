"""
Global application state.
"""

from typing import Dict, List, Any
from config import settings

# Trading state
capital_sim = settings.CAPITAL_SIM
positions: List[Dict[str, Any]] = []
closed_trades: List[Dict[str, Any]] = []
wins = 0
losses = 0
realized_pnl = 0.0

# Balance cache
_last_balance_ts = 0.0
_last_balance_val = settings.CAPITAL_SIM
_last_portfolio_value_ts = 0.0
_last_portfolio_value_val = None

# Session tracking
SESSION_START_BAL = None
SESSION_START_PORTFOLIO_VALUE = None
SESSION_START_TIME = None

# Algorithm control
algorithm_running = False
algorithm_paused = False

# Metrics
METRICS = {
    "orders_placed": 0,
    "orders_filled": 0,
    "orders_partial_filled": 0,
    "orders_cancelled": 0,
    "orders_timeout_cancel": 0,
    "avg_slippage_bps_sum": 0.0,
    "avg_slippage_bps_n": 0,
    "skip_counts": {},
}

# Active matches for strategy
active_matches: List[Dict[str, Any]] = []