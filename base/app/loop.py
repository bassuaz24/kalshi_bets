"""
Main application loop for the trading bot.
"""

import os
import sys
import time
import json
from typing import List, Dict, Any, Optional
from pathlib import Path

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from app import state
from config import settings
from core.time import now_utc
from kalshi.markets import get_kalshi_markets
from kalshi.balance import get_kalshi_balance, get_kalshi_portfolio_value
from kalshi.positions import get_live_positions
from positions.io import resolve_positions_file, load_positions, save_positions
from positions.reconcile import reconcile_positions
from positions.metrics import calculate_unrealized_pnl, get_position_summary
from execution.positions import normalize_loaded_positions, deduplicate_positions
from execution.settlement import realize_if_settled
from strategy.engine import run_engine
from risk.stop_loss import check_stop_losses
from data_collection.oddsapi_client import collect_data_running
from bot_logging.csv_logger import log_metrics
from bot_logging.daily_reports import generate_daily_report


def main():
    """Main application loop."""
    print("🚀 Trading bot starting...")
    
    # Initialize positions file
    POSITIONS_FILE = resolve_positions_file()
    
    if POSITIONS_FILE.exists():
        print(f"📂 Found existing {POSITIONS_FILE}")
        try:
            positions = load_positions() or []
            normalize_loaded_positions()
            deduplicate_positions()
            print(f"💾 Loaded {len(positions)} existing positions")
        except Exception as e:
            print(f"⚠️ Failed to load positions: {e}")
            positions = []
    else:
        print(f"📭 No existing {POSITIONS_FILE} found — will create if needed")
        positions = []
        with open(POSITIONS_FILE, "w") as f:
            json.dump([], f, indent=2)
    
    # Initialize session tracking
    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
        state.SESSION_START_BAL = get_kalshi_balance(force=True)
        state.SESSION_START_PORTFOLIO_VALUE = get_kalshi_portfolio_value(force=True)
    else:
        state.SESSION_START_BAL = settings.CAPITAL_SIM
        state.SESSION_START_PORTFOLIO_VALUE = None
    
    state.SESSION_START_TIME = now_utc()
    print(f"🕒 Session started at {state.SESSION_START_TIME.isoformat()} | "
          f"Starting balance: ${state.SESSION_START_BAL:.2f}")
    
    # Reset metrics
    for k in state.METRICS:
        if isinstance(state.METRICS[k], dict):
            state.METRICS[k].clear()
        else:
            state.METRICS[k] = 0 if not isinstance(state.METRICS[k], float) else 0.0
    
    # Main loop
    last_discovery_ts = 0.0
    next_discovery_ts = 0.0
    active_matches: List[Dict[str, Any]] = []
    last_data_collection_ts = 0.0
    last_metrics_log_ts = 0.0
    last_report_generation_ts = 0.0
    
    # Set algorithm running before entering loop
    state.algorithm_running = True
    
    try:
        while state.algorithm_running:
            try:
                now = time.time()
                
                # Collect market data periodically
                if now - last_data_collection_ts >= settings.DATA_COLLECTION_INTERVAL:
                    print("📡 Collecting market data...")
                    try:
                        collect_data_running()
                        last_data_collection_ts = now
                    except Exception as e:
                        print(f"⚠️ Error collecting data: {e}")
                
                # Discover new markets periodically
                if now >= next_discovery_ts:
                    print("🔎 Discovering new markets...")
                    # TODO: Implement market discovery from OddsAPI and Kalshi
                    # For now, we'll use a placeholder that gets markets from active events
                    
                    # Update active_matches with current market data
                    # This is a placeholder - actual implementation would:
                    # 1. Fetch events from OddsAPI
                    # 2. Match them with Kalshi events
                    # 3. Get market data for each
                    
                    discovery_ts = time.time()
                    last_discovery_ts = discovery_ts
                    next_discovery_ts = discovery_ts + settings.NO_OVERLAP_SLEEP_SECS
                    
                    if not active_matches:
                        print(f"😴 No active matches found — sleeping for {settings.NO_OVERLAP_SLEEP_SECS}s")
                        time.sleep(settings.NO_OVERLAP_SLEEP_SECS)
                        continue
                
                # Check stop losses and take profits
                if active_matches:
                    check_stop_losses()
                
                # Reconcile positions with live Kalshi data
                if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
                    reconcile_positions()
                    realize_if_settled()
                
                # Run strategy engine on active matches
                if active_matches:
                    run_engine(active_matches)
                
                # Reconcile again after engine run
                if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
                    reconcile_positions()
                    realize_if_settled()
                
                # Save positions
                if state.positions:
                    save_positions()
                
                # Log metrics periodically
                if now - last_metrics_log_ts >= 300:  # Every 5 minutes
                    log_metrics()
                    last_metrics_log_ts = now
                
                # Generate daily report at end of day
                if now - last_report_generation_ts >= 86400:  # Once per day
                    try:
                        generate_daily_report()
                        last_report_generation_ts = now
                    except Exception as e:
                        print(f"⚠️ Error generating daily report: {e}")
                
                # Sleep based on whether we have active positions
                sleep_time = settings.REFRESH_ACTIVE if state.positions else settings.REFRESH_IDLE
                time.sleep(sleep_time)
            
            except Exception as loop_err:
                print(f"⚠️ Error in main loop iteration: {loop_err}")
                if settings.VERBOSE:
                    import traceback
                    traceback.print_exc()
                try:
                    save_positions()
                except Exception:
                    pass
                time.sleep(30)
                continue
    
    except KeyboardInterrupt:
        print("🛑 Bot stopped by user.")
        state.algorithm_running = False
        try:
            save_positions()
            print("💾 Positions saved before exit.")
        except Exception as e:
            print(f"⚠️ Failed to save positions on exit: {e}")
        
        # Print summary
        summary = get_position_summary()
        print(f"\n📊 Session Summary:")
        print(f"   Total PnL: ${summary.get('total_pnl', 0.0):.2f}")
        print(f"   Realized: ${summary.get('realized_pnl', 0.0):.2f}")
        print(f"   Unrealized: ${summary.get('unrealized_pnl', 0.0):.2f}")
        print(f"   Wins: {summary.get('wins', 0)}")
        print(f"   Losses: {summary.get('losses', 0)}")
    
    except Exception as e:
        print(f"❌ Unexpected error in main loop: {e}")
        if settings.VERBOSE:
            import traceback
            traceback.print_exc()
        state.algorithm_running = False
        try:
            save_positions()
        except Exception:
            pass


if __name__ == "__main__":
    main()