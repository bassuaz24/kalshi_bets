"""
Multi-threaded main application loop for the trading bot.
Separates strategy execution, stop loss monitoring, and UI updates.
"""

import os
import sys
import time
import json
import threading
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
from kalshi.websocket_client import start_websocket_client, stop_websocket_client, get_websocket_client


# Thread-safe locks
_state_lock = threading.RLock()


def strategy_loop_thread():
    """Main strategy loop thread - runs at fixed STRATEGY_LOOP_INTERVAL."""
    print(f"🔄 Strategy loop thread started (interval: {settings.STRATEGY_LOOP_INTERVAL}s)")
    
    last_data_collection_ts = 0.0
    last_reconcile_ts = 0.0
    active_matches: List[Dict[str, Any]] = []
    
    while state.algorithm_running and not state.algorithm_paused:
        try:
            loop_start = time.time()
            
            # 1. Collect market data (before strategy computation)
            if loop_start - last_data_collection_ts >= settings.STRATEGY_LOOP_INTERVAL:
                print(f"📡 [{threading.current_thread().name}] Collecting market data...")
                try:
                    collect_data_running()
                    last_data_collection_ts = loop_start
                except Exception as e:
                    print(f"⚠️ Error collecting data: {e}")
                    if settings.VERBOSE:
                        import traceback
                        traceback.print_exc()
            
            # 2. Market discovery (placeholder - implement based on your strategy)
            # TODO: Implement market discovery from OddsAPI and Kalshi
            # For now, we'll use a placeholder that gets markets from active events
            if not active_matches or (loop_start - last_reconcile_ts) >= settings.RECONCILE_INTERVAL:
                print(f"🔎 [{threading.current_thread().name}] Discovering markets...")
                # Placeholder: actual implementation would fetch from OddsAPI + Kalshi
                # active_matches = discover_markets()
                last_reconcile_ts = loop_start
            
            # 3. Reconcile positions (periodically)
            if settings.PLACE_LIVE_KALSHI_ORDERS == "YES" and (loop_start - last_reconcile_ts) >= settings.RECONCILE_INTERVAL:
                print(f"🔄 [{threading.current_thread().name}] Reconciling positions...")
                with _state_lock:
                    reconcile_positions()
                    realize_if_settled()
                    save_positions()
                
                # Sync WebSocket subscriptions when positions change
                ws_client = get_websocket_client()
                if ws_client:
                    ws_client.sync_subscriptions_sync()
                
                last_reconcile_ts = loop_start
            
            # 4. Run strategy engine on active matches
            if active_matches:
                print(f"⚙️  [{threading.current_thread().name}] Running strategy engine...")
                with _state_lock:
                    run_engine(active_matches)
                    save_positions()
                
                # Sync WebSocket subscriptions after new positions created
                ws_client = get_websocket_client()
                if ws_client:
                    ws_client.sync_subscriptions_sync()
            
            # 5. Reconcile again after engine run (to catch any immediate fills)
            if settings.PLACE_LIVE_KALSHI_ORDERS == "YES" and active_matches:
                with _state_lock:
                    reconcile_positions()
                    realize_if_settled()
                    save_positions()
                
                ws_client = get_websocket_client()
                if ws_client:
                    ws_client.sync_subscriptions_sync()
            
            # Calculate sleep time to maintain fixed interval
            loop_duration = time.time() - loop_start
            sleep_time = max(0, settings.STRATEGY_LOOP_INTERVAL - loop_duration)
            
            if sleep_time > 0:
                # Use interruptible sleep - break out of sleep early if algorithm stopped
                sleep_end = time.time() + sleep_time
                while time.time() < sleep_end and state.algorithm_running and not state.algorithm_paused:
                    time.sleep(min(0.5, sleep_end - time.time()))
            elif settings.VERBOSE:
                print(f"⚠️ Strategy loop took {loop_duration:.2f}s (exceeds {settings.STRATEGY_LOOP_INTERVAL}s interval)")
        
        except KeyboardInterrupt:
            # Allow keyboard interrupt to propagate
            raise
        except Exception as e:
            print(f"❌ Error in strategy loop: {e}")
            if settings.VERBOSE:
                import traceback
                traceback.print_exc()
            # Use interruptible sleep
            sleep_end = time.time() + 5
            while time.time() < sleep_end and state.algorithm_running and not state.algorithm_paused:
                time.sleep(min(0.5, sleep_end - time.time()))
    
    print(f"🛑 Strategy loop thread stopped")


def stop_loss_monitoring_thread():
    """Stop loss monitoring thread - runs at high frequency STOP_LOSS_CHECK_INTERVAL."""
    print(f"🛡️  Stop loss monitoring thread started (interval: {settings.STOP_LOSS_CHECK_INTERVAL}s)")
    
    while state.algorithm_running and not state.algorithm_paused:
        try:
            loop_start = time.time()
            
            # Check stop losses and take profits
            with _state_lock:
                check_stop_losses()
                save_positions()
            
            # Sync subscriptions if positions changed (e.g., exit orders placed)
            ws_client = get_websocket_client()
            if ws_client:
                ws_client.sync_subscriptions_sync()
            
            # Calculate sleep time to maintain fixed interval
            loop_duration = time.time() - loop_start
            sleep_time = max(0, settings.STOP_LOSS_CHECK_INTERVAL - loop_duration)
            
            if sleep_time > 0:
                # Use interruptible sleep
                sleep_end = time.time() + sleep_time
                while time.time() < sleep_end and state.algorithm_running and not state.algorithm_paused:
                    time.sleep(min(0.5, sleep_end - time.time()))
            elif settings.VERBOSE:
                print(f"⚠️ Stop loss check took {loop_duration:.2f}s (exceeds {settings.STOP_LOSS_CHECK_INTERVAL}s interval)")
        
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"❌ Error in stop loss monitoring: {e}")
            if settings.VERBOSE:
                import traceback
                traceback.print_exc()
            # Use interruptible sleep
            sleep_end = time.time() + 1
            while time.time() < sleep_end and state.algorithm_running and not state.algorithm_paused:
                time.sleep(min(0.5, sleep_end - time.time()))
    
    print(f"🛑 Stop loss monitoring thread stopped")


def ui_update_thread():
    """UI/Performance update thread - runs at high frequency UI_UPDATE_INTERVAL."""
    print(f"📊 UI update thread started (interval: {settings.UI_UPDATE_INTERVAL}s)")
    
    last_metrics_log_ts = 0.0
    last_report_generation_ts = 0.0
    
    while state.algorithm_running:
        try:
            loop_start = time.time()
            
            # Update performance metrics (for UI consumption)
            # State is already thread-safe with locks, so we can read it directly
            # The UI server reads from state directly, so this thread mainly ensures
            # metrics are calculated and cached
            
            # Log metrics periodically (every 5 minutes)
            if loop_start - last_metrics_log_ts >= 300:
                try:
                    log_metrics()
                    last_metrics_log_ts = loop_start
                except Exception as e:
                    if settings.VERBOSE:
                        print(f"⚠️ Error logging metrics: {e}")
            
            # Generate daily report (once per day)
            if loop_start - last_report_generation_ts >= 86400:
                try:
                    generate_daily_report()
                    last_report_generation_ts = loop_start
                except Exception as e:
                    if settings.VERBOSE:
                        print(f"⚠️ Error generating daily report: {e}")
            
            # Calculate sleep time to maintain fixed interval
            loop_duration = time.time() - loop_start
            sleep_time = max(0, settings.UI_UPDATE_INTERVAL - loop_duration)
            
            if sleep_time > 0:
                # Use interruptible sleep
                sleep_end = time.time() + sleep_time
                while time.time() < sleep_end and state.algorithm_running:
                    time.sleep(min(0.5, sleep_end - time.time()))
        
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"❌ Error in UI update thread: {e}")
            if settings.VERBOSE:
                import traceback
                traceback.print_exc()
            # Use interruptible sleep
            sleep_end = time.time() + 1
            while time.time() < sleep_end and state.algorithm_running:
                time.sleep(min(0.5, sleep_end - time.time()))
    
    print(f"🛑 UI update thread stopped")


def main():
    """Main application entry point - starts all threads."""
    print("🚀 Trading bot starting with multi-threaded architecture...")
    
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
    with _state_lock:
        for k in state.METRICS:
            if isinstance(state.METRICS[k], dict):
                state.METRICS[k].clear()
            else:
                state.METRICS[k] = 0 if not isinstance(state.METRICS[k], float) else 0.0
    
    # Start WebSocket client (for real-time price updates)
    if settings.WEBSOCKET_ENABLED:
        start_websocket_client()
        time.sleep(2)  # Give WebSocket time to connect
    
    # Set algorithm running before starting threads
    state.algorithm_running = True
    
    # Start worker threads
    threads = []
    
    strategy_thread = threading.Thread(target=strategy_loop_thread, name="StrategyLoop", daemon=True)
    strategy_thread.start()
    threads.append(strategy_thread)
    
    stop_loss_thread = threading.Thread(target=stop_loss_monitoring_thread, name="StopLossMonitor", daemon=True)
    stop_loss_thread.start()
    threads.append(stop_loss_thread)
    
    ui_thread = threading.Thread(target=ui_update_thread, name="UIUpdate", daemon=True)
    ui_thread.start()
    threads.append(ui_thread)
    
    print(f"✅ Started {len(threads)} worker threads")
    print(f"   - Strategy loop: {settings.STRATEGY_LOOP_INTERVAL}s interval")
    print(f"   - Stop loss monitoring: {settings.STOP_LOSS_CHECK_INTERVAL}s interval")
    print(f"   - UI updates: {settings.UI_UPDATE_INTERVAL}s interval")
    
    try:
        # Main thread waits for interruption
        while state.algorithm_running:
            time.sleep(1)
            # Check if threads are still alive
            for thread in threads:
                if not thread.is_alive():
                    print(f"⚠️ Thread {thread.name} died unexpectedly!")
    
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user.")
        state.algorithm_running = False
    
    except Exception as e:
        print(f"❌ Unexpected error in main: {e}")
        if settings.VERBOSE:
            import traceback
            traceback.print_exc()
        state.algorithm_running = False
    
    finally:
        # Cleanup
        print("🧹 Cleaning up...")
        state.algorithm_running = False
        
        # Stop WebSocket client
        if settings.WEBSOCKET_ENABLED:
            stop_websocket_client()
        
        # Wait for threads to finish (with timeout)
        # Give threads a chance to see algorithm_running = False and exit
        import time
        time.sleep(0.5)  # Brief pause for threads to check the flag
        
        for thread in threads:
            thread.join(timeout=3.0)
            if thread.is_alive():
                print(f"⚠️ Thread {thread.name} did not terminate gracefully (this is okay for daemon threads)")
        
        # Save positions
        try:
            with _state_lock:
                save_positions()
            print("💾 Positions saved before exit.")
        except Exception as e:
            print(f"⚠️ Failed to save positions on exit: {e}")
        
        # Print summary
        try:
            summary = get_position_summary()
            print(f"\n📊 Session Summary:")
            print(f"   Total PnL: ${summary.get('total_pnl', 0.0):.2f}")
            print(f"   Realized: ${summary.get('realized_pnl', 0.0):.2f}")
            print(f"   Unrealized: ${summary.get('unrealized_pnl', 0.0):.2f}")
            print(f"   Wins: {summary.get('wins', 0)}")
            print(f"   Losses: {summary.get('losses', 0)}")
        except Exception as e:
            print(f"⚠️ Error generating summary: {e}")


if __name__ == "__main__":
    main()
