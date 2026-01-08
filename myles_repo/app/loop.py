
from app import state
from config import settings
from core.time import now_utc
from odds_feed.betsapi import fetch_event_moneyline, _fetch_odds_feed_live_events
from odds_feed.overlaps import get_overlapping_matches
from math_calculations.ev import devig_proportional, devig_shin_two_way
from kalshi.markets import get_kalshi_markets
from kalshi.balance import get_kalshi_balance, get_kalshi_portfolio_value
from kalshi.positions import get_live_positions
from positions.io import resolve_positions_file, load_positions, save_positions
from positions.first_detection import load_first_detection_times, record_first_detection_time, FIRST_DETECTION_TIMES_FILE
from positions.maintenance import refresh_position_tracking, purge_old_positions, purge_stale_positions, purge_stale_live_positions
from positions.reconcile import reconcile_positions
from positions.queries import event_key
from execution.positions import normalize_loaded_positions, deduplicate_positions
from execution.settlement import realize_if_settled
from bot_logging.csv_logger import log_snapshot_scan, _metrics_flush_periodic
from bot_logging.snapshot_email import send_positions_email
from risk.locks import persist_event_locks, prune_event_locks
from risk.stop_loss import persist_stop_lossed_events
from strategy.engine import run_engine
import os
import time
from datetime import datetime
closed_trades = state.closed_trades

METRICS = state.METRICS
SESSION_START_BAL = None
SESSION_START_PORTFOLIO_VALUE = None
SESSION_START_TIME = None
_active_matches_for_api = []

BASE_DIR = settings.BASE_DIR
PLACE_LIVE_KALSHI_ORDERS = settings.PLACE_LIVE_KALSHI_ORDERS
CAPITAL_SIM = settings.CAPITAL_SIM
PRESERVE_MANUAL_POSITIONS = settings.PRESERVE_MANUAL_POSITIONS
NO_OVERLAP_SLEEP_SECS = settings.NO_OVERLAP_SLEEP_SECS
REFRESH_ACTIVE = settings.REFRESH_ACTIVE
REFRESH_IDLE = settings.REFRESH_IDLE
MIN_LOCKOUT_PERIOD = settings.MIN_LOCKOUT_PERIOD
SEND_EMAIL_TURN_ON = settings.SEND_EMAIL_TURN_ON
EMAIL_INTERVAL_SECS = settings.EMAIL_INTERVAL_SECS
USE_SHIN_DEVIG = settings.USE_SHIN_DEVIG


def main():
    print("🚀 Arbitrage bot starting...")
    
    # Start API server in a separate thread
    try:
        from api.api_server import start_api_server
        api_port = int(os.getenv("API_PORT", "8000"))
        api_thread = start_api_server(port=api_port)
    except Exception as e:
        print(f"⚠️ Failed to start API server: {e}")
        print("   Continuing without API server...")

    # ✅ Resolve which positions.json we should keep in sync with
    POSITIONS_FILE = resolve_positions_file()

    # 🧭 Auto-skip user prompt for AWS/unattended deployment
    # Note: Position cleanup happens automatically in the main loop every 5 minutes
    if os.path.exists(POSITIONS_FILE):
        print(f"📂 Found existing {POSITIONS_FILE} — will keep existing positions and auto-cleanup old ones.")
    else:
        print(f"📭 No existing {POSITIONS_FILE} found — a new one will be created if needed.")    

    # ✅ Load existing positions once at startup
    if os.path.exists(POSITIONS_FILE):
        try:
            positions = load_positions() or []

            print(f"💾 Loaded {len(positions)} existing positions from {POSITIONS_FILE}")
            # 🔧 NEW: normalize and deduplicate for hedge recognition
            normalize_loaded_positions()
            deduplicate_positions()  # optional, but strongly recommended
            
            # ✅ Sync with live Kalshi positions at startup to catch any manual trades or fills
            if PLACE_LIVE_KALSHI_ORDERS == "YES":
                print("🔄 Syncing positions with live Kalshi data at startup...")
                reconcile_positions()
                open_count = len([p for p in positions if not p.get('settled', False)])
                print(f"📊 Positions after sync: {open_count} open")
        except Exception as e:
            print(f"⚠️ Failed to load {POSITIONS_FILE}: {e}")
            positions = []
    else:
        print(f"📭 No existing {POSITIONS_FILE} found — creating new file.")
        positions = []
        # Optional: create an empty positions.json if it doesn't exist
        with open(POSITIONS_FILE, "w") as f:
            json.dump([], f, indent=2)
        
        # ✅ Sync with live Kalshi positions even if no local file exists (in case of manual trades)
        if PLACE_LIVE_KALSHI_ORDERS == "YES":
            print("🔄 Syncing with live Kalshi positions at startup...")
            reconcile_positions()
            open_count = len([p for p in positions if not p.get('settled', False)])
            print(f"📊 Positions after sync: {open_count} open")
    
    # ✅ Load first detection times at startup
    print(f"📂 Using first detection times file: {FIRST_DETECTION_TIMES_FILE}")
    loaded_times = load_first_detection_times()
    if loaded_times:
        print(f"⏱️ Loaded {len(loaded_times)} first detection times from {FIRST_DETECTION_TIMES_FILE}")
    else:
        print(f"📭 No existing first detection times found — will create on first match.")
    
    # 🧭 Restore event locks between runs
    try:
        event_locks_path = os.path.join(BASE_DIR, "event_locks.json")
        if os.path.exists(event_locks_path):
            with open(event_locks_path, "r") as f:
                EVENT_LOCKED_TILL_HEDGE = {event_key(t) for t in json.load(f)}
            print(f"🔒 Restored {len(EVENT_LOCKED_TILL_HEDGE)} locked events from file.")
            prune_event_locks()
        else:
            EVENT_LOCKED_TILL_HEDGE = set()
    except Exception as e:
        print(f"⚠️ Could not load event locks: {e}")
        EVENT_LOCKED_TILL_HEDGE = set()
    
    # 🧭 Stop-lossed events tracking with timestamps and entry prices (allows re-entry if price recovers)
    EVENT_STOP_LOSSED = {}  # Dict: {event_key: {"timestamp": ..., "entry_price": ...}}
    try:
        event_stop_lossed_path = os.path.join(BASE_DIR, "event_stop_lossed.json")
        if os.path.exists(event_stop_lossed_path):
            with open(event_stop_lossed_path, "r") as f:
                data = json.load(f)
                # Handle old format (list) and convert to new format
                if isinstance(data, list):
                    # Old format: convert to new format with current time
                    print(f"⚠️ Converting old event_stop_lossed.json format to new timestamp format")
                    current_time = time.time()
                    EVENT_STOP_LOSSED = {event_key(t): {"timestamp": current_time, "entry_price": None} for t in data}
                    persist_stop_lossed_events()  # Save in new format
                elif isinstance(data, dict):
                    # New format: load timestamps and entry prices
                    for key, value in data.items():
                        try:
                            if isinstance(value, dict):
                                # New format with timestamp and entry_price
                                timestamp_val = value.get("timestamp")
                                entry_price_val = value.get("entry_price")
                                
                                if isinstance(timestamp_val, (int, float)):
                                    EVENT_STOP_LOSSED[key] = {"timestamp": timestamp_val, "entry_price": entry_price_val}
                                elif isinstance(timestamp_val, str):
                                    # Parse ISO format
                                    dt = datetime.fromisoformat(timestamp_val.replace('Z', '+00:00'))
                                    EVENT_STOP_LOSSED[key] = {"timestamp": dt.timestamp(), "entry_price": entry_price_val}
                                else:
                                    print(f"⚠️ Could not parse timestamp for {key}: {timestamp_val}")
                            elif isinstance(value, (int, float)):
                                # Old format: just timestamp, convert to new format
                                EVENT_STOP_LOSSED[key] = {"timestamp": value, "entry_price": None}
                            elif isinstance(value, str):
                                # Old format: ISO string timestamp, convert to new format
                                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                                EVENT_STOP_LOSSED[key] = {"timestamp": dt.timestamp(), "entry_price": None}
                        except Exception as e:
                            print(f"⚠️ Could not parse stop loss data for {key}: {e}")
                    
                    # Clean up expired entries (older than cooldown period)
                    current_time = time.time()
                    expired_keys = [
                        key for key, data in EVENT_STOP_LOSSED.items()
                        if isinstance(data, dict) and (current_time - data.get("timestamp", 0)) >= (MIN_LOCKOUT_PERIOD * 60)
                    ]
                    for key in expired_keys:
                        del EVENT_STOP_LOSSED[key]
                    if expired_keys:
                        persist_stop_lossed_events()
                    print(f"🚫 Restored {len(EVENT_STOP_LOSSED)} stop-lossed events from file (cooldown active until price recovers).")
                else:
                    EVENT_STOP_LOSSED = {}
        else:
            EVENT_STOP_LOSSED = {}
    except Exception as e:
        print(f"⚠️ Could not load stop-lossed events: {e}")
        EVENT_STOP_LOSSED = {}
    
    # ✅ Load 7% exited events at startup
    event_7pct_exited_path = os.path.join(BASE_DIR, "event_7pct_exited.json")
    if os.path.exists(event_7pct_exited_path):
        try:
            with open(event_7pct_exited_path, "r") as f:
                EVENT_7PCT_EXITED = {event_key(t) for t in json.load(f)}
            print(f"🚫 Restored {len(EVENT_7PCT_EXITED)} 7% exited events from file (no new entries allowed).")
        except Exception as e:
            print(f"⚠️ Could not load 7% exited events: {e}")
            EVENT_7PCT_EXITED = set()
    else:
        EVENT_7PCT_EXITED = set()


    # 📊 Verification block — show loaded trades
    print(f"📂 Using positions file: {POSITIONS_FILE}")
    if positions:
        # ✅ Only show open (non-settled) positions
        open_positions = [p for p in positions if not p.get("settled", False)]
        if open_positions:
            print(f"📊 Loaded {len(open_positions)} open positions:")
            for p in open_positions:
                print(f"   - {p['match']} | {p['side'].upper()} x{p['stake']} @ {p['entry_price']:.2%}")
        else:
            print("📭 No open positions found in file.")
    else:
        print("📭 No existing positions found in file.")

    # ✅ Just announce mode — don't reload positions again
    if PLACE_LIVE_KALSHI_ORDERS == "YES":
        print("🔒 Live mode — preserving loaded positions.")
    else:
        print("🧪 Simulation mode — preserving loaded positions.")

    # ✅ Do NOT reset or reload positions again here
    # load_positions()      # ❌ REMOVE
    # reconcile_positions() # ❌ REMOVE

    # ✅ Start session with clean balance and portfolio value
    if PLACE_LIVE_KALSHI_ORDERS == "YES":
        SESSION_START_BAL = get_kalshi_balance(force=True)
        SESSION_START_PORTFOLIO_VALUE = get_kalshi_portfolio_value(force=True)
    else:
        SESSION_START_BAL = CAPITAL_SIM
        SESSION_START_PORTFOLIO_VALUE = None

    SESSION_START_TIME = now_utc()
    portfolio_msg = f" | starting portfolio value: ${SESSION_START_PORTFOLIO_VALUE:.2f}" if SESSION_START_PORTFOLIO_VALUE is not None else ""
    print(f"🕒 Session baseline set at {SESSION_START_TIME.isoformat()} — "
          f"starting balance: ${SESSION_START_BAL:.2f}{portfolio_msg}")

    last_positions_email_ts = 0.0
    if SEND_EMAIL_TURN_ON:
        # At startup, we don't have active_matches yet, so send without games
        # The email will be sent again with games once they're discovered
        send_positions_email(reason="startup / running now", live_games=[])
        last_positions_email_ts = time.time()

    # Reset metrics at session start
    for k in METRICS:
        if isinstance(METRICS[k], dict):
            METRICS[k].clear()
        else:
            METRICS[k] = 0 if not isinstance(METRICS[k], float) else 0.0

    # === Main loop: Check existing matches every 10s, discover new games every 5m ===
    ACTIVE_MATCH_REFRESH = 10 * 60  # 5 minutes for discovering new games
    LAST_DISCOVERY_TS = 0.0
    next_discovery_ts = 0.0
    active_matches: List[Dict[str, Any]] = []
    latest_raw_events: Optional[List[Dict[str, Any]]] = None
    
    # Global state for API is already initialized at module level
    # We'll update _active_matches_for_api in the loop

    try:
        while True:
            try:
                now = time.time()
                just_discovered = False  # Initialize flag for each iteration

                # 🔄 Every 5 minutes, discover new overlaps
                if now >= next_discovery_ts:
                    print("🔎 Refreshing active matches from Odds API + Kalshi...")
                    latest_raw_events = _fetch_odds_feed_live_events()
                    active_matches = get_overlapping_matches(preloaded_events=latest_raw_events)
                    # Update global state for API
                    _active_matches_for_api = active_matches.copy()
                    discovery_ts = time.time()
                    LAST_DISCOVERY_TS = discovery_ts
                    if not active_matches:
                        next_discovery_ts = discovery_ts + NO_OVERLAP_SLEEP_SECS
                        minutes = int(NO_OVERLAP_SLEEP_SECS / 60)
                        print(f"😴 No overlapping matches found — sleeping for {minutes} minutes before next scan.")
                        time.sleep(NO_OVERLAP_SLEEP_SECS)
                        active_matches = []
                        _active_matches_for_api = []
                        continue
                    if active_matches:
                        for match in active_matches:
                            match["discovered_ts"] = discovery_ts
                            match["last_seen_ts"] = discovery_ts
                            # ✅ Record first detection time for FIRST_TRADE_WINDOW_MINUTES constraint
                            ticker = match.get("ticker", "")
                            if ticker:
                                record_first_detection_time(ticker, now_utc())
                        # 🧭 Refresh tracking of open positions against current active matches
                        refresh_position_tracking(active_matches)
                    next_discovery_ts = discovery_ts + ACTIVE_MATCH_REFRESH

                    # 🧩 Keep local positions clean unless manual-preserve is enabled
                    if PRESERVE_MANUAL_POSITIONS:
                        print("⚠️ Position purge skipped — preserving manual positions.json entries.")
                    else:
                        purge_old_positions()
                        purge_stale_positions(hours=4, active_matches=active_matches)
                        purge_stale_live_positions(hours=12)

                    # 🔄 Always reconcile local positions with live Kalshi feed
                    if PLACE_LIVE_KALSHI_ORDERS == "YES":
                        live_positions = get_live_positions()
                        if live_positions:
                            print(f"📊 Found {len(live_positions)} live Kalshi positions:")
                            for lp in live_positions:
                                print(f"   - {lp['ticker']} | {lp['side'].upper()} {lp['contracts']} @ {lp['avg_price']:.2%}")
                        else:
                            print("📭 No live positions currently on Kalshi.")

                        # ✅ Replace local positions if they got lost
                        if not positions and live_positions:
                            print("🔄 Rebuilding local positions list from Kalshi...")
                            for lp in live_positions:
                                positions.append({
                                    "match": lp["ticker"],  # placeholder; you can refine mapping
                                    "side": lp["side"],
                                    "event_ticker": (lp.get("event_ticker") or "").upper(),
                                    "market_ticker": lp["ticker"],
                                    "entry_price": lp["avg_price"],
                                    "entry_time": now_utc().isoformat(),
                                    "stake": lp["contracts"],
                                    "effective_entry": lp["avg_price"],
                                    "odds_prob": 0.5,  # unknown, neutral placeholder
                                })
                    
                    # 🚀 Mark that we just discovered new matches
                    # Don't run engine yet - we'll refresh markets first then run engine once
                    just_discovered = True
                    print(f"🚀 Discovered {len(active_matches)} new matches, will evaluate after market refresh...")
                    for match in active_matches:
                        log_snapshot_scan(match)
                else:
                    just_discovered = False

                if SEND_EMAIL_TURN_ON and (time.time() - last_positions_email_ts) >= EMAIL_INTERVAL_SECS:
                    send_positions_email(reason="hourly", live_games=active_matches)
                    last_positions_email_ts = time.time()

                # 🔄 Every 10 seconds: Re-evaluate all active matches (refresh odds/score and Kalshi markets)
                # This happens on every loop iteration, regardless of whether we discovered new matches
                if active_matches:
                    # Update global state for API
                    _active_matches_for_api = active_matches.copy()
                    print(f"🔄 Checking {len(active_matches)} existing overlapping matches...")
                    # Refresh odds/score data and Kalshi markets for all active matches
                    for match in active_matches:
                        # Refresh odds, score, and clock data from BetsAPI
                        evt_id = match.get("id")
                        match_name = match.get("match", "Unknown")
                        if evt_id:
                            try:
                                # Always fetch fresh odds from BetsAPI (no caching)
                                # Cache-busting is handled in _betsapi_request
                                moneyline = fetch_event_moneyline(str(evt_id))
                                if moneyline:
                                    match.setdefault("odds_feed", {})
                                    
                                    # Get old values for comparison
                                    old_home_odds = match["odds_feed"].get("home_odds")
                                    old_away_odds = match["odds_feed"].get("away_odds")
                                    
                                    # Update odds (convert to float)
                                    home_odds_new = float(moneyline.get("home_odds", 0))
                                    away_odds_new = float(moneyline.get("away_odds", 0))
                                    
                                    if home_odds_new > 0 and away_odds_new > 0:
                                        # Convert decimal odds → implied probabilities
                                        implied_home = 1.0 / home_odds_new
                                        implied_away = 1.0 / away_odds_new
                                        
                                        # Proportional devig (simple)
                                        fair_prop_home, fair_prop_away = devig_proportional([implied_home, implied_away])
                                        
                                        # Shin devig (advanced)
                                        fair_shin_home, fair_shin_away = devig_shin_two_way(home_odds_new, away_odds_new)
                                        
                                        # Use Shin devig by default (same as discovery)
                                        if USE_SHIN_DEVIG:
                                            home_prob_new = fair_shin_home
                                            away_prob_new = fair_shin_away
                                        else:
                                            home_prob_new = fair_prop_home
                                            away_prob_new = fair_prop_away
                                        
                                        # Always update odds and probabilities (even if same values)
                                        match["odds_feed"]["home_odds"] = home_odds_new
                                        match["odds_feed"]["away_odds"] = away_odds_new
                                        match["odds_feed"]["home_prob"] = home_prob_new
                                        match["odds_feed"]["away_prob"] = away_prob_new
                                        
                                        # Debug: show if odds changed
                                        if old_home_odds is not None and (abs(old_home_odds - home_odds_new) > 0.01 or abs(old_away_odds - away_odds_new) > 0.01):
                                            print(f"   📊 {match_name}: Odds updated | Home: {old_home_odds:.2f}→{home_odds_new:.2f} | Away: {old_away_odds:.2f}→{away_odds_new:.2f}")
                                    else:
                                        print(f"   ⚠️ {match_name}: Invalid odds from BetsAPI (home={home_odds_new}, away={away_odds_new})")
                                    
                                    # Update score and clock
                                    match["odds_feed"]["score_snapshot"] = moneyline.get("score_snapshot")
                                    match["odds_feed"]["period_clock"] = moneyline.get("period_clock")
                                    
                                    # Update last_update timestamp
                                    match["odds_feed"]["last_update_ts"] = time.time()
                                    match["odds_feed"]["last_update_iso"] = datetime.utcnow().isoformat() + "Z"
                                else:
                                    print(f"   ⚠️ {match_name}: No odds returned from BetsAPI (using cached)")
                                
                                # Small delay to avoid rate limiting
                                time.sleep(0.1)
                            except Exception as e:
                                # Log fetch failures instead of silently ignoring
                                print(f"   ❌ {match_name}: Error fetching odds: {e} (using cached)")
                        
                        # Always refresh Kalshi markets to get latest prices
                        kalshi_markets = get_kalshi_markets(match["ticker"], force_live=True)
                        # Handle rate limiting (None) or filter active markets
                        if kalshi_markets:
                            match["kalshi"] = [
                                m for m in kalshi_markets
                                if m.get("status") == "active" and (m.get("yes_bid") or m.get("yes_ask"))
                            ]
                        else:
                            # Rate limited or no markets - keep existing or set to empty
                            match["kalshi"] = match.get("kalshi", [])
                    # Always re-evaluate all active matches every 10 seconds
                    for match in active_matches:
                        log_snapshot_scan(match)
                    
                    # ✅ Reconcile positions BEFORE running engine to ensure latest trades are visible
                    # This ensures positions placed by the bot (or manually) are synced into local state
                    # BEFORE the engine evaluates what to do next
                    if PLACE_LIVE_KALSHI_ORDERS == "YES":
                        reconcile_positions()
                    
                    run_engine(active_matches)

                # ✅ Reconcile again AFTER engine to catch any new fills from orders placed during engine run
                # This ensures positions placed by the bot (or manually) are synced into local state
                if PLACE_LIVE_KALSHI_ORDERS == "YES":
                    reconcile_positions()
                    realize_if_settled()
                else:
                    print("🛡️ Skipping reconcile_positions() and realize_if_settled() in SIM mode")
                
                # 🛡️ Don't write to file if list is empty (prevents wiping manual JSON)
                if not positions:
                    print("⚠️ positions list empty — skipping save to protect manual positions.json")
                else:
                    print(f"💾 Saving {len(positions)} open positions")
                    save_positions()

                show_book()

                # 🚨 Kill-switch check
                current_balance = get_kalshi_balance()
                if SESSION_START_BAL and SESSION_START_BAL > 0:
                    session_pnl = current_balance - SESSION_START_BAL
                    session_roi = session_pnl / SESSION_START_BAL
               
                _metrics_flush_periodic()

                time.sleep(REFRESH_ACTIVE if positions else REFRESH_IDLE)
            
            except Exception as loop_err:
                # Catch errors within loop iteration to prevent crash
                print(f"⚠️ Error in main loop iteration: {loop_err}")
                import traceback
                traceback.print_exc()
                # Save positions on error
                try:
                    save_positions()
                    print("💾 Positions saved after loop error.")
                except Exception as save_err:
                    print(f"⚠️ Failed to save positions: {save_err}")
                # Wait before retrying to avoid rapid error loops
                print("⏳ Waiting 30 seconds before retrying loop...")
                time.sleep(30)
                continue  # Continue to next iteration

    except KeyboardInterrupt:
        print("🛑 Bot stopped by user.")
        # Save positions before exiting
        try:
            save_positions()
            print("💾 Positions saved before exit.")
        except Exception as e:
            print(f"⚠️ Failed to save positions on exit: {e}")
        for trade in closed_trades:
            print(f"📋 {trade['match']} | PnL: ${trade['pnl']:.2f}")
    except Exception as e:
        # Catch any unexpected errors to prevent crash
        print(f"❌ Unexpected error in main loop: {e}")
        import traceback
        traceback.print_exc()
        # Save positions before continuing
        try:
            save_positions()
            print("💾 Positions saved after error.")
        except Exception as save_err:
            print(f"⚠️ Failed to save positions after error: {save_err}")
        # Wait a bit before retrying to avoid rapid error loops
        print("⏳ Waiting 60 seconds before retrying...")
        time.sleep(60)


if __name__ == "__main__":
    main()
