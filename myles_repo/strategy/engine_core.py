from config import settings
from app import state
from core.time import now_utc, parse_iso_utc
from kalshi.markets import get_kalshi_markets, get_event_total_volume, format_price, market_yes_mid
from kalshi.balance import get_kalshi_balance
from kalshi.orders import prepare_kalshi_order, safe_prepare_kalshi_order, _extract_order_id, wait_for_fill_or_cancel
from kalshi.positions import get_live_positions
from kalshi.fees import kalshi_fee_per_contract
from positions.reconcile import reconcile_positions
from positions.io import save_positions
from positions.first_detection import get_first_detection_time
from positions.queries import event_is_neutralized
from positions.metrics import _current_unrealized_and_equity, _roi_pct_from_equity
from positions.maintenance import check_time_based_exits
from positions.queries import is_neutralized
from risk.exposure import exposure_violation, max_qty_with_cap
from risk.stop_loss import is_event_in_stop_loss_cooldown, mark_event_stop_lossed
from risk.locks import update_event_lock, set_event_neutralization_flags, mark_event_7pct_exited
from risk.profit_protection import check_profit_protection
from risk.game_time import _should_block_trading_by_game_time, _should_block_early_game_trading
from strategy.hedge import hedge_qty_bounds_target_roi, hedge_outcome_rois, report_event_hedge_bands, log_hedge_band_preview
from math_calculations.ev import ev_exit_yes, ev_settlement_yes, ev_per_contract, kelly_yes_with_costs, choose_maker_vs_taker
from utils.names import normalize_tokens, expand_nba_abbreviations
from utils.tickers import event_key, normalize_event_ticker
from bot_logging.csv_logger import log_eval, log_backtest_feed, log_backtest_metrics, log_entry_row, log_exit_row, _bump_fill
import time

TICK = settings.TICK
CAPITAL_SIM = settings.CAPITAL_SIM
PLACE_LIVE_KALSHI_ORDERS = settings.PLACE_LIVE_KALSHI_ORDERS
VERBOSE = settings.VERBOSE
ENABLE_NBA_TRADING = settings.ENABLE_NBA_TRADING
USE_SHIN_DEVIG = settings.USE_SHIN_DEVIG
KALSHI_BASE_URL = settings.KALSHI_BASE_URL
TIME_BASED_EXITS_ENABLED = settings.TIME_BASED_EXITS_ENABLED
TIME_EXIT_THRESHOLD_MINUTES = settings.TIME_EXIT_THRESHOLD_MINUTES
MAX_EXPOSURE_PER_GAME = settings.MAX_EXPOSURE_PER_GAME
MIN_HEDGE_RETURN = settings.MIN_HEDGE_RETURN
EVENT_LOCKED_TILL_HEDGE = settings.EVENT_LOCKED_TILL_HEDGE
ALLOW_STOP_LOSS_PRICE_RECOVERY = settings.ALLOW_STOP_LOSS_PRICE_RECOVERY
MIN_LOCKOUT_PERIOD = settings.MIN_LOCKOUT_PERIOD
SEND_EMAIL_TURN_ON = settings.SEND_EMAIL_TURN_ON
EMAIL_INTERVAL_SECS = settings.EMAIL_INTERVAL_SECS
PRESERVE_MANUAL_POSITIONS = settings.PRESERVE_MANUAL_POSITIONS
KELLY_FRACTION = settings.KELLY_FRACTION
HEDGE_PRICE_MIN = settings.HEDGE_PRICE_MIN
HEDGE_PRICE_MAX = settings.HEDGE_PRICE_MAX
HEDGE_TRADE_FRACTIONAL_KELLY = settings.HEDGE_TRADE_FRACTIONAL_KELLY
MIN_TRADING_VOLUME_PER_EVENT = settings.MIN_TRADING_VOLUME_PER_EVENT
PRINT_MARKET_TABLE = settings.PRINT_MARKET_TABLE
USE_CONSERVATIVE_EV = settings.USE_CONSERVATIVE_EV
STOP_LOSS_ODDS_DIFF_THRESHOLD = settings.STOP_LOSS_ODDS_DIFF_THRESHOLD
STOP_LOSS_THRESHOLD_NO_EV = settings.STOP_LOSS_THRESHOLD_NO_EV
STOP_LOSS_THRESHOLD = settings.STOP_LOSS_THRESHOLD
EVENT_7PCT_EXITED_SIDE = settings.EVENT_7PCT_EXITED_SIDE
MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS = settings.MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS
HEDGING_ENABLED = settings.HEDGING_ENABLED
MIN_EV_THRESHOLD = settings.MIN_EV_THRESHOLD
FIRST_ENTRY_MIN_QTY = settings.FIRST_ENTRY_MIN_QTY
PYRAMIDING_ENABLED = settings.PYRAMIDING_ENABLED
ALLOW_PYRAMID_AFTER_HEDGE = settings.ALLOW_PYRAMID_AFTER_HEDGE
MAX_SPREAD = settings.MAX_SPREAD
MAX_PROFIT_THRESHOLD = settings.MAX_PROFIT_THRESHOLD
TRAILING_STOP_TIGHTEN_THRESHOLD = settings.TRAILING_STOP_TIGHTEN_THRESHOLD
TRAILING_STOP_TIGHTENED_PCT = settings.TRAILING_STOP_TIGHTENED_PCT
TRAILING_STOP_INITIAL_PCT = settings.TRAILING_STOP_INITIAL_PCT
EVENT_7PCT_EXITED = settings.EVENT_7PCT_EXITED
ODDS_FEED_EXIT_THRESHOLD = settings.ODDS_FEED_EXIT_THRESHOLD
ODDS_FEED_EXIT_THRESHOLD_MIN = settings.ODDS_FEED_EXIT_THRESHOLD_MIN
ODDS_FEED_EXIT_TIME_MINUTES = settings.ODDS_FEED_EXIT_TIME_MINUTES
ODDS_FEED_AGGRESSIVE_EXIT_ENABLED = settings.ODDS_FEED_AGGRESSIVE_EXIT_ENABLED
PROFIT_PROTECTION_ENABLED = settings.PROFIT_PROTECTION_ENABLED
PROFIT_PROTECTION_MIN_TIME_REMAINING = settings.PROFIT_PROTECTION_MIN_TIME_REMAINING
PROFIT_PROTECTION_MIN_HOLD_SECONDS = settings.PROFIT_PROTECTION_MIN_HOLD_SECONDS
PROFIT_PROTECTION_PYRAMIDING_WINDOW = settings.PROFIT_PROTECTION_PYRAMIDING_WINDOW
PROFIT_PROTECTION_MIN_MARGIN_ABOVE_SETTLEMENT = settings.PROFIT_PROTECTION_MIN_MARGIN_ABOVE_SETTLEMENT
PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT = settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT
PROFIT_PROTECTION_REQUIRE_NO_RECENT_GROWTH = settings.PROFIT_PROTECTION_REQUIRE_NO_RECENT_GROWTH
MAX_PROFIT_DETECTION_ENABLED = settings.MAX_PROFIT_DETECTION_ENABLED
TRAILING_STOP_ENABLED = settings.TRAILING_STOP_ENABLED
TRAILING_STOP_PCT = settings.TRAILING_STOP_PCT
MIN_PROFIT_FOR_TRAILING_STOP = settings.MIN_PROFIT_FOR_TRAILING_STOP
SPREAD_TIGHT = settings.SPREAD_TIGHT
FIRST_ENTRY_EV_THRESHOLD = settings.FIRST_ENTRY_EV_THRESHOLD
HEDGE_ENTRY_EV_THRESHOLD = settings.HEDGE_ENTRY_EV_THRESHOLD
FIRST_ENTRY_PRICE_MAX = settings.FIRST_ENTRY_PRICE_MAX
FIRST_ENTRY_PRICE_MIN = settings.FIRST_ENTRY_PRICE_MIN
FIRST_ENTRY_KALSHI_PRICE_MIN = settings.FIRST_ENTRY_KALSHI_PRICE_MIN
FIRST_ENTRY_KALSHI_PRICE_MAX = settings.FIRST_ENTRY_KALSHI_PRICE_MAX
FIRST_ENTRY_MIN_CAPITAL = settings.FIRST_ENTRY_MIN_CAPITAL
MAX_STAKE_PCT = settings.MAX_STAKE_PCT
HEDGE_MAX_STAKE_PCT = settings.HEDGE_MAX_STAKE_PCT
MAX_TOTAL_EXPOSURE_PCT = settings.MAX_TOTAL_EXPOSURE_PCT
MAX_TOTAL_EXPOSURE_HEDGE_PCT = settings.MAX_TOTAL_EXPOSURE_HEDGE_PCT
MAX_EXPOSURE_PER_GAME_PCT = settings.MAX_EXPOSURE_PER_GAME_PCT
REFRESH_ACTIVE = settings.REFRESH_ACTIVE
REFRESH_IDLE = settings.REFRESH_IDLE
NO_OVERLAP_SLEEP_SECS = settings.NO_OVERLAP_SLEEP_SECS
ORDER_FILL_TIME = settings.ORDER_FILL_TIME
MIN_KELLY = settings.MIN_KELLY
capital_sim = state.capital_sim
positions = state.positions
wins = state.wins
losses = state.losses
realized_pnl = state.realized_pnl


def run_engine(overlaps):

        # ✅ Helper: compute first-entry quantity (NON-HEDGE)
    def compute_first_entry_quantity(kelly_fraction, kalshi_price, odds_prob, capital):
        """
        Calculate first entry quantity matching testing_new_strategy.py KellyCalculator.calculate_bet_size()
        Kelly-based sizing is capped at MAX_STAKE_PCT of capital.
        """
        # kelly_fraction is already calculated (from kelly_yes_with_costs)
        # Apply KELLY_FRACTION multiplier (matching testing_new_strategy.py, now 1.0 = full Kelly)
        fractional_kelly = kelly_fraction * KELLY_FRACTION
        
        if fractional_kelly <= 0:
            return None
        
        # Calculate cost per contract (matching testing_new_strategy.py)
        # Determine if maker or taker based on price vs ask
        # For now, assume taker (conservative) - will be refined based on actual order type
        is_maker = False  # Default to taker for sizing calculation
        fee_per_contract = kalshi_fee_per_contract(kalshi_price, is_maker=is_maker)
        cost_per_contract = kalshi_price + fee_per_contract
        
        # Bet size = fractional_kelly * bankroll / cost_per_contract (matching testing_new_strategy.py)
        bet_size = (fractional_kelly * capital) / cost_per_contract
        
        # === Cap bet size at MAX_STAKE_PCT of capital ===
        # Calculate maximum quantity allowed based on MAX_STAKE_PCT
        # max_qty_with_cap expects the base price (kalshi_price) and calculates fees internally
        max_stake_dollars = capital * MAX_STAKE_PCT
        max_qty_by_stake = max_qty_with_cap(max_stake_dollars, kalshi_price)
        
        # If we can't afford even 1 contract with MAX_STAKE_PCT, don't place the bet
        if max_qty_by_stake < 1:
            return None
        
        # Cap bet_size at the maximum allowed quantity
        bet_size = min(bet_size, max_qty_by_stake)
        
        qty = max(0, int(bet_size))  # Round down, ensure non-negative
        return qty if qty >= 1 else None

    # ✅ Helper: compute hedge quantity (guarantee +ROI both sides)
    # ✅ Helper: compute hedge quantity (guarantee ≥ $0 profit both outcomes)
    def compute_hedge_quantity(existing_pos, hedge_price, odds_prob, capital):
        """
        Size the hedge using the actual entry price (kalshi_price) for sizing calculations.
        This ensures sizing matches the price we'll actually pay.
        
        For hedges, we skip Kelly checks - ROI bands ensure combined position profitability.
        Standalone Kelly is meaningless for hedges since we're not making a standalone trade.
        """
        pB = float(hedge_price)  # Use actual entry price (kalshi_price from aggressiveness logic)

        if not (HEDGE_PRICE_MIN <= pB <= HEDGE_PRICE_MAX):
            return None

        # Capital cap - primary constraint for hedges
        dollars_cap = capital * HEDGE_MAX_STAKE_PCT
        max_qty_capital = max_qty_with_cap(dollars_cap, pB)

        # For hedges, skip Kelly check - ROI bands ensure profitability
        # If Kelly is positive, use it for sizing guidance, but don't block if it's negative/low
        # Note: compute_hedge_quantity doesn't have market data, so default to taker (conservative)
        fee_pc = kalshi_fee_per_contract(pB, is_maker=False)
        kelly_h = kelly_yes_with_costs(odds_prob, pB, rt_cost=fee_pc)
        
        if kelly_h >= MIN_KELLY:
            # Kelly is positive - use it for sizing guidance
            kelly_dollars = capital * kelly_h * HEDGE_TRADE_FRACTIONAL_KELLY
            max_qty_kelly = max_qty_with_cap(kelly_dollars, pB)
            # Use the minimum of capital cap and Kelly-based sizing
            max_qty = min(max_qty_capital, max_qty_kelly)
        else:
            # Kelly is negative/low - just use capital cap (ROI bands will ensure profitability)
            max_qty = max_qty_capital

        # Divide by 2 to ensure minimum quantity of 2 contracts (scaling requirement)
        # This ensures we start with at least 1 contracts and scale from there
        return max(1, max_qty // 2)


    global capital_sim, positions, wins, losses, realized_pnl

    # ✅ ADD THIS HERE — so it's available anywhere inside run_engine()
    def _find_market_for(label: str, kalshi_markets=None):
        """
        Match odds-feed team name (label) to Kalshi market.
        Uses ticker suffix (e.g. '-AFA', '-LIU') and yes_sub_title ('Air Force', 'LIU')
        for robust mapping.

        Assumes exactly two YES markets per event (home/away moneyline). If Kalshi
        introduces alternates (e.g., spreads), callers must guard accordingly.
        """
        if not kalshi_markets:
            return None

        # Detect if this is an NBA event by checking ticker format
        is_nba_event = False
        for m in kalshi_markets:
            ticker = m.get("ticker", "").upper()
            if ticker.startswith("KXNBAGAME-"):
                is_nba_event = True
                break
        
        # Expand NBA abbreviations before normalization if NBA event
        if is_nba_event:
            label = expand_nba_abbreviations(label)
        
        label_norm = (label or "").lower().strip()
        label_tokens = normalize_tokens(label_norm)

        # Build a map of Kalshi market abbreviations and sub_titles
        for m in kalshi_markets:
            yes_sub = (m.get("yes_sub_title") or "").strip()
            
            # Expand NBA abbreviations in Kalshi market titles too
            if is_nba_event:
                yes_sub = expand_nba_abbreviations(yes_sub)
            
            yes_sub = yes_sub.lower().strip()
            ticker = m.get("ticker", "").lower()

            # Extract final team code from ticker suffix
            # Example: 'KXNCAAMBGAME-25NOV11LIUAFA-AFA' → 'afa'
            # Example: 'KXNBAGAME-25DEC24SAOKC-OKC' → 'okc'
            ticker_suffix = ticker.split("-")[-1] if "-" in ticker else ""
            ticker_suffix = ticker_suffix.strip().lower()

            yes_tokens = normalize_tokens(yes_sub)
            yes_tokens.add(ticker_suffix)  # ✅ include suffix as token

            # bidirectional match between label + Kalshi tokens
            if label_tokens & yes_tokens or yes_tokens & label_tokens:
                return m

        return None

    print(f"\n🔍 Evaluating {len(overlaps)} overlapping matches...")

    # Determine balance source
    if PLACE_LIVE_KALSHI_ORDERS == "YES":
        capital = get_kalshi_balance()
        print(f"💰 Using LIVE Kalshi balance for Kelly sizing: ${capital:.2f}")
    else:
       capital = capital_sim

    # Safety check: ensure capital is valid
    if capital is None or capital <= 0:
        print(f"⚠️ Invalid capital ({capital}) — skipping engine run")
        return

    if not overlaps:
        print("No new overlaps — continuing to monitor and will rescan.")
        return  # (we keep this return, but ensure main loop still runs quickly)

    for match in overlaps:
        ticker = match["ticker"]
        ticker_key = event_key(ticker)
        
        # Check if this is an NBA game with trading disabled (still process for monitoring/logging)
        is_nba_blocked = not ENABLE_NBA_TRADING and ticker and str(ticker).startswith("KXNBAGAME-")
        
        home = match["home"]
        away = match["away"]

        # Add delay between API calls to respect rate limits
        time.sleep(0.25)  # 250ms delay between matches (same as EVENT_ODDS_SLEEP)

        # ✅ Always refresh Kalshi markets in real time
        kalshi = get_kalshi_markets(ticker, force_live=True)
        # Handle rate limiting (None) or empty markets
        if not kalshi:  # None (rate limited) or [] (no markets)
            print(f"⚠️ No Kalshi markets returned for {ticker}. Retrying next loop.")
            continue

        match["kalshi"] = kalshi

        # ✅ Volume filter: Skip games with insufficient trading volume
        total_volume = get_event_total_volume(ticker, markets=kalshi)
        if total_volume is None:
            print(f"⚠️ Skipping {match['match']} — could not determine trading volume.")
            continue
        if total_volume < MIN_TRADING_VOLUME_PER_EVENT:
            print(f"📊 Skipping {match['match']} — volume {total_volume:,} < {MIN_TRADING_VOLUME_PER_EVENT:,} threshold (BLOCKED)")
            continue
        if VERBOSE:
            print(f"✅ {match['match']} — volume {total_volume:,} meets threshold ({MIN_TRADING_VOLUME_PER_EVENT:,})")

        odds_snapshot = match.get("odds_feed") or {}
        home_prob = odds_snapshot.get("home_prob")
        away_prob = odds_snapshot.get("away_prob")
        odds_last_ts = odds_snapshot.get("last_update_ts")
        score_snapshot = odds_snapshot.get("score_snapshot")
        period_clock = odds_snapshot.get("period_clock")

        if home_prob is None or away_prob is None:
            print(f"⚠️ Skipping {match['match']} — odds-feed probabilities unavailable.")
            continue

        # ✅ Check if odds have been updated on this turn before allowing trades
        # Compare both timestamp AND odds values to prevent duplicate trades when odds are unchanged
        odds_updated = False
        if odds_last_ts is not None and home_prob is not None and away_prob is not None:
            last_processed = _LAST_PROCESSED_ODDS.get(ticker_key)
            
            if last_processed is None:
                # First time processing this event - allow trading
                odds_updated = True
                _LAST_PROCESSED_ODDS[ticker_key] = {
                    "timestamp": odds_last_ts,
                    "home_prob": home_prob,
                    "away_prob": away_prob
                }
            else:
                # Check if timestamp is newer or equal (odds were freshly fetched in this cycle)
                last_ts = last_processed.get("timestamp", 0)
                timestamp_newer_or_equal = odds_last_ts >= last_ts
                timestamp_newer = odds_last_ts > last_ts
                
                # Check if odds values have changed (with tolerance for floating point)
                last_home = last_processed.get("home_prob")
                last_away = last_processed.get("away_prob")
                home_changed = (last_home is None or abs(home_prob - last_home) > ODDS_CHANGE_TOLERANCE)
                away_changed = (last_away is None or abs(away_prob - last_away) > ODDS_CHANGE_TOLERANCE)
                odds_values_changed = home_changed or away_changed
                
                # Odds are considered updated if:
                # (1) Timestamp is newer (different refresh cycle) - always allow (freshly fetched)
                # (2) Timestamp matches (same refresh cycle) - allow (same refresh, can process both sides)
                # (3) Timestamp is older - block (stale data from previous cycle)
                if timestamp_newer_or_equal:
                    # Timestamp is newer or same - this is a fresh or current refresh cycle
                    # Allow trading (can process both sides in same cycle)
                    odds_updated = True
                    _LAST_PROCESSED_ODDS[ticker_key] = {
                        "timestamp": odds_last_ts,
                        "home_prob": home_prob,
                        "away_prob": away_prob
                    }
                else:
                    # Timestamp is older - stale data from previous cycle, block
                    odds_updated = False
                    if VERBOSE:
                        time_since_update = time.time() - odds_last_ts
                        time_since_processed = time.time() - last_ts
                        print(f"⏸️ Skipping {match['match']} — odds timestamp is older than last processed "
                              f"(last processed: {time_since_processed:.1f}s ago, current: {time_since_update:.1f}s ago, "
                              f"values: home={home_prob:.4f}≈{last_home:.4f}, away={away_prob:.4f}≈{last_away:.4f})")
        else:
            # No timestamp or odds available - allow trading but warn
            if VERBOSE:
                print(f"⚠️ {match['match']} — no odds timestamp/values available, allowing trades (may be stale)")
            odds_updated = True
        
        # Set flag to skip new entries if odds haven't updated (hedging will bypass this)
        match["_skip_new_entries"] = not odds_updated

        # Stale odds check removed - user wants to use odds even if stale
        
        # Format score and time info for display
        score_time_info = ""
        if score_snapshot:
            score_time_info += f" | Score: {score_snapshot}"
        if period_clock:
            score_time_info += f" | Clock: {period_clock}"

        # Show odds-API probs for this overlap
        # === 2b. Market comparison table (REPLACE this whole block) ===
        if PRINT_MARKET_TABLE:
            print(f"\n🎾 {match['match']} ({ticker})")
            print("")
            print(f"{'Side':<25} {'Odds Prob':>14} {'Kalshi Price':>12} {'EV (% Ret)':>14} {'EV($/ct)':>10}")
            print("-" * 80)

            for label, prob in [(home, home_prob), (away, away_prob)]:
                # 🔒 Hard event lock: stop first-side repeats before hedge
                if ticker_key in EVENT_LOCKED_TILL_HEDGE and not event_is_neutralized(ticker):
                    if VERBOSE:
                        print(f"🚫 Event {ticker} locked — first-side already open, waiting for hedge.")
                    continue

                m = _find_market_for(label, kalshi)
                if not m:
                    continue

                yes_bid = format_price(m.get("yes_bid"))
                yes_ask = format_price(m.get("yes_ask"))
                if yes_bid is None and yes_ask is None:
                    continue

                yes_price = (
                    (yes_bid + yes_ask) / 2.0
                    if (yes_bid is not None and yes_ask is not None)
                    else (yes_ask if yes_ask is not None else yes_bid)
                )

                yb_raw = m.get("yes_bid")
                ya_raw = m.get("yes_ask")
                spread = (
                    max(0.0, ((ya_raw or 0) - (yb_raw or 0)) / 100.0)
                    if (ya_raw is not None and yb_raw is not None)
                    else 0.0
                )

                edge_yes = ((prob - yes_price) / max(yes_price, 1e-6)) - (0.5 * spread) / max(yes_price, 1e-6)
                ev_yes = ev_per_contract(prob, yes_price)

                print(f"{label:<25} {prob:>14.2%} {yes_price:>12.2%} {edge_yes:>10.2%} {ev_yes:>10.3f}")

            print("-" * 78)



        # Identify Kalshi YES markets for both sides
        # ✅ Identify Kalshi YES markets for both sides (alias-aware)
        home_market = _find_market_for(home, kalshi)
        away_market = _find_market_for(away, kalshi)

        if not home_market or not away_market:
            # Normalized debug view (uses the same canonical tokenizer for both)
            home_norm = list(normalize_tokens(home))
            away_norm = list(normalize_tokens(away))
            kalshi_titles = [list(normalize_tokens(m.get("yes_sub_title"))) for m in kalshi]

            print(f"⚠️ Skipping {match['match']} — could not align both Kalshi markets.")
            print(f"    → home_norm={home_norm}, away_norm={away_norm}, kalshi_titles={kalshi_titles}")
            continue

        # Evaluate both sides
        # Evaluate both sides – now allow YES entries on both home and away
        rt_ev = None
        
        # Format score/time info for match header
        score_time_str = ""
        if score_time_info:
            if "Score:" in score_time_info:
                score_part = score_time_info.split("Score:")[1].split("|")[0].strip()
                score_time_str += f"Score: {score_part} "
            if "Clock:" in score_time_info:
                clock_part = score_time_info.split("Clock:")[1].strip()
                score_time_str += f"| {clock_part}"
        
        # Print match header once
        match_header = f"{match['match']}"
        if score_time_str:
            match_header += f" ({score_time_str})"
        print(f"\n{'═' * 120}")
        print(f"📊 {match_header}")
        print(f"{'═' * 120}")
        
        # Print NBA trading status if disabled
        if is_nba_blocked:
            print(f"🚫 NBA trading is DISABLED (ENABLE_NBA_TRADING = False) - monitoring only, no trades will be placed")
        
        print(f"{'Side':<6} | {'Mode':<6} | {'Odds':<8} | {'Kalshi':<8} | {'Edge':<8} | {'EV':<8} | {'Kelly':<7} | {'Price':<8} | {'Spread':<8}")
        print(f"{'─' * 120}")
        
        for side, market, odds_prob in [
            (home, home_market, home_prob),
            (away, away_market, away_prob),
        ]:
            # ✅ MOVE position detection BEFORE cooldown check so we can display existing positions
            ticker_key = event_key(ticker)
            neutralized_evt = event_is_neutralized(ticker)
            evt_norm = normalize_event_ticker(ticker)
            
            if not market:
                continue

            # Check hedge context BEFORE lockout check (hedges should bypass lockout)
            event_locked = ticker_key in EVENT_LOCKED_TILL_HEDGE
            event_positions = [
                p for p in positions
                if normalize_event_ticker(p.get("event_ticker", "")) == evt_norm
                and not p.get("settled", False)
            ]
            has_event_position = bool(event_positions)
            held_tickers = {p.get("market_ticker") for p in event_positions}
            one_sided_exposure = any(
                event_key(p.get("event_ticker")) == ticker_key
                and p["market_ticker"] == market.get("ticker")
                and not p.get("neutralized", False)
                for p in positions
            )
            pending_same_side_block = (
                event_locked
                and not neutralized_evt
                and market.get("ticker") in held_tickers
            )
            
            # ✅ Check if we have an existing position on this side - if so, display it even in cooldown
            existing_pos_on_side = next(
                (p for p in event_positions 
                 if p.get("market_ticker") == market.get("ticker")
                 and p.get("stake", 0) > 0),
                None
            )
            
            # Get current market prices (needed for both position display and cooldown check)
            yes_bid = format_price(market.get("yes_bid"))
            yes_ask = format_price(market.get("yes_ask"))
            current_market_price = (yes_bid + yes_ask) / 2.0 if (yes_bid is not None and yes_ask is not None) else (yes_ask if yes_ask is not None else yes_bid)
            
            # ✅ Display existing position info even if in cooldown
            if existing_pos_on_side:
                # Use already calculated prices
                yb = yes_bid
                ya = yes_ask
                mid = current_market_price
                kalshi_prob = mid if (mid is not None) else existing_pos_on_side.get("entry_price", 0)
                edge_pct = (odds_prob - (kalshi_prob or 0.0)) * 100.0
                spread_dbg = (abs((ya or 0.0) - (yb or 0.0)) if (ya is not None and yb is not None) else 0.0)
                
                # Calculate current EV and Kelly for display
                entry_price = existing_pos_on_side.get("entry_price", 0)
                yes_ask_formatted = yes_ask
                cons_ev, fair_ev = ev_settlement_yes(odds_prob, entry_price, market.get("yes_ask"), yes_ask=yes_ask_formatted)
                rt_ev = cons_ev if USE_CONSERVATIVE_EV else fair_ev
                is_maker_provisional = (yes_ask_formatted is not None and kalshi_prob < yes_ask_formatted)
                provisional_fee_pc = kalshi_fee_per_contract(kalshi_prob, is_maker=is_maker_provisional)
                kelly_fraction = kelly_yes_with_costs(odds_prob, kalshi_prob, rt_cost=provisional_fee_pc)
                
                mode = "POSITION"  # Indicate this is an existing position
                print(
                    f"{side:<6} | {mode:<6} | {odds_prob:>7.2%} | {kalshi_prob:>7.2%} | "
                    f"{edge_pct:>7.2f}% | {rt_ev:>7.2%} | {kelly_fraction:>6.3f} | "
                    f"{kalshi_prob:>7.2%} | {spread_dbg:>7.2%} | Qty: {existing_pos_on_side.get('stake', 0):.0f} @ {entry_price:.2%}"
                )
            
            # Check if event is in stop-loss cooldown period - only block NEW entries
            # ✅ Pass current market price to check if price has recovered
            
            if is_event_in_stop_loss_cooldown(ticker, current_price=current_market_price, cooldown_minutes=MIN_LOCKOUT_PERIOD):
                if VERBOSE:
                    print(f"🚫 Event {ticker} is in stop-loss cooldown period - blocking entry to prevent immediate re-entry")
                # ✅ Only skip if we don't have an existing position to display/manage
                if not existing_pos_on_side:
                    continue
            
            # 🔒 Block entries after 7% exit - permanently prevent re-entry
            if ticker_key in EVENT_7PCT_EXITED:
                if VERBOSE:
                    print(f"🚫 Event {ticker} locked — 7% exit executed, no new entries allowed.")
                # ✅ Only skip if we don't have an existing position to display/manage
                if not existing_pos_on_side:
                    continue
            
            # fresh sizing state for this side
            quantity = None
            min_qty_required = None
            # ✅ Always evaluate this side as a YES entry (no skipping away side)
            side_choice = "yes"
            
            # ✅ Stop-loss check: If first trade (one-sided, not hedged) loses STOP_LOSS_THRESHOLD, sell immediately
            if one_sided_exposure and not neutralized_evt:
                current_pos = next(
                    (p for p in positions
                     if event_key(p.get("event_ticker")) == ticker_key
                     and p["market_ticker"] == market.get("ticker")
                     and not p.get("neutralized", False)),
                    None
                )
                # ✅ FIXED: Removed stop_loss_triggered check - continue evaluating until all contracts are sold
                if current_pos and current_pos.get("stake", 0) > 0:
                    entry_value = current_pos.get("entry_value")
                    if entry_value is None:
                        # Fallback: calculate from stake and entry_price
                        entry_value = current_pos.get("stake", 0) * current_pos.get("entry_price", 0)
                    
                    # Get current bid price for valuation
                    current_bid = format_price(market.get("yes_bid"))
                    if current_bid and entry_value > 0:
                        current_value = current_pos.get("stake", 0) * current_bid
                        loss_pct = (entry_value - current_value) / entry_value
                        
                        # ✅ Display stop-loss monitoring info when approaching threshold (loss > 10%)
                        if loss_pct > 0.10:
                            sportsbook_prob = odds_prob if odds_prob is not None else None
                            kalshi_prob = current_bid
                            entry_price = current_pos.get("entry_price", 0)
                            
                            # Calculate odds difference if data available
                            odds_diff_abs = None
                            if sportsbook_prob is not None and kalshi_prob is not None:
                                odds_diff_abs = abs(sportsbook_prob - kalshi_prob)
                            
                            status_line = f"📊 STOP-LOSS MONITORING: Loss {loss_pct:.1%} "
                            status_line += f"(Entry: {entry_price:.2%} → Current: {kalshi_prob:.2%})"
                            if sportsbook_prob is not None:
                                status_line += f" | Sportsbook: {sportsbook_prob:.2%}"
                            if odds_diff_abs is not None:
                                status_line += f" | Odds Diff: {odds_diff_abs:.1%}"
                                if odds_diff_abs > STOP_LOSS_ODDS_DIFF_THRESHOLD:
                                    status_line += f" (>{STOP_LOSS_ODDS_DIFF_THRESHOLD:.0%} - would BLOCK)"
                                else:
                                    status_line += f" (≤{STOP_LOSS_ODDS_DIFF_THRESHOLD:.0%} - would ALLOW)"
                            if loss_pct >= STOP_LOSS_THRESHOLD_NO_EV:
                                status_line += f" | ⚠️ HARD STOP ({STOP_LOSS_THRESHOLD_NO_EV:.0%})"
                            elif loss_pct >= STOP_LOSS_THRESHOLD:
                                status_line += f" | ⚠️ STOP LOSS ({STOP_LOSS_THRESHOLD:.0%})"
                            print(status_line)
                        
                        if loss_pct >= STOP_LOSS_THRESHOLD:  # Stop-loss threshold
                            # ✅ NEW: Check hard stop loss first (50% - always trigger regardless of odds)
                            if loss_pct >= STOP_LOSS_THRESHOLD_NO_EV:
                                # Hard stop loss - trigger regardless of sportsbook odds difference
                                if VERBOSE:
                                    print(f"🛑 HARD STOP-LOSS: {loss_pct:.1%} loss exceeds {STOP_LOSS_THRESHOLD_NO_EV:.0%} threshold - triggering regardless of odds difference")
                                # Continue to execute stop loss below...
                            
                            else:
                                # Normal stop loss (22.5%) - check if sportsbook agrees with Kalshi
                                # Calculate absolute difference between sportsbook probability and Kalshi probability
                                sportsbook_prob = odds_prob  # Already available in this context
                                kalshi_prob = current_bid    # Current Kalshi bid price
                                
                                if sportsbook_prob is not None and kalshi_prob is not None:
                                    # Calculate absolute difference (sportsbook - kalshi)
                                    odds_diff_abs = abs(sportsbook_prob - kalshi_prob)
                                    
                                    if odds_diff_abs > STOP_LOSS_ODDS_DIFF_THRESHOLD:
                                        # Sportsbook and Kalshi disagree by > 5% (absolute) - block stop loss
                                        # Reasoning: Sportsbook thinks true probability is different, price might recover
                                        if VERBOSE:
                                            print(f"🛡️ STOP-LOSS BLOCKED: Loss {loss_pct:.1%} but sportsbook/Kalshi absolute diff {odds_diff_abs:.1%} > {STOP_LOSS_ODDS_DIFF_THRESHOLD:.0%} "
                                                  f"(sportsbook: {sportsbook_prob:.2%}, Kalshi: {kalshi_prob:.2%}) - holding position")
                                        continue  # Skip stop loss, hold position
                                    else:
                                        # Sportsbook and Kalshi agree (≤ 5% absolute difference) - allow stop loss
                                        if VERBOSE:
                                            print(f"✅ STOP-LOSS ALLOWED: Loss {loss_pct:.1%} and sportsbook/Kalshi absolute diff {odds_diff_abs:.1%} ≤ {STOP_LOSS_ODDS_DIFF_THRESHOLD:.0%} "
                                                  f"(sportsbook: {sportsbook_prob:.2%}, Kalshi: {kalshi_prob:.2%}) - executing stop loss")
                                else:
                                    # Missing odds data - default to allowing stop loss (safe fallback)
                                    if VERBOSE:
                                        print(f"⚠️ STOP-LOSS: Missing sportsbook odds data (sportsbook_prob={sportsbook_prob}, kalshi_prob={kalshi_prob}), defaulting to allow stop loss")
                            
                            # ✅ Check if other side has already exited at 7% - if so, hold to the end and disable stop loss
                            evt_ticker_str = current_pos.get("event_ticker")
                            if evt_ticker_str:
                                evt_key_check = event_key(evt_ticker_str)
                                
                                # Check if a 7% exit happened on the other side
                                if evt_key_check in EVENT_7PCT_EXITED_SIDE:
                                    exited_market_ticker = EVENT_7PCT_EXITED_SIDE[evt_key_check]
                                    current_market_ticker = market.get("ticker")
                                    
                                    # If the OTHER market ticker already exited at 7%, skip stop loss and hold to the end
                                    if exited_market_ticker and current_market_ticker and current_market_ticker != exited_market_ticker:
                                        if VERBOSE:
                                            print(f"🛡️ STOP-LOSS BLOCKED: Other market ({exited_market_ticker}) already exited at 7% - holding {current_market_ticker} to the end (loss: {loss_pct:.1%})")
                                        continue  # Skip stop loss, hold to the end
                            
                            # Check minimum hold time before executing stop loss
                            entry_time_str = current_pos.get("entry_time")
                            hold_duration = None
                            if entry_time_str:
                                try:
                                    entry_time = parse_iso_utc(entry_time_str)
                                    if entry_time:
                                        entry_ts = entry_time.timestamp()
                                        current_ts = time.time()
                                        hold_duration = current_ts - entry_ts
                                        
                                        if hold_duration < MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS:
                                            remaining_seconds = int(MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS - hold_duration)
                                            if VERBOSE:
                                                print(f"🚫 STOP-LOSS BLOCKED: Loss {loss_pct:.1%} but only held for {hold_duration:.0f}s, need {MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS}s minimum ({remaining_seconds}s remaining)")
                                            continue  # Skip stop loss until 5 minutes have passed
                                except Exception as e:
                                    if VERBOSE:
                                        print(f"⚠️ Error checking entry time for stop-loss: {e}")
                                    # If we can't parse time, allow the stop loss (don't block on errors)
                            
                            # Execute stop-loss: sell entire position aggressively (price below bid to ensure immediate fill)
                            stake_to_sell = int(current_pos.get("stake", 0))
                            if stake_to_sell > 0:
                                # Price 2 ticks below bid for maximum aggressiveness - we want OUT immediately
                                # This ensures we get filled even if bid moves down slightly
                                aggressive_sell_price = max(0.01, current_bid - (2 * TICK))
                                was_triggered = current_pos.get("stop_loss_triggered", False)
                                status_tag = "CONTINUING" if was_triggered else "TRIGGERED"
                                
                                # ✅ Enhanced stop-loss execution display with all relevant info
                                entry_price = current_pos.get("entry_price", 0)
                                sportsbook_prob = odds_prob if odds_prob is not None else None
                                odds_diff_abs = None
                                if sportsbook_prob is not None and current_bid is not None:
                                    odds_diff_abs = abs(sportsbook_prob - current_bid)
                                
                                print(f"\n{'🛑' * 40}")
                                print(f"🛑 STOP-LOSS {status_tag}: {loss_pct:.1%} loss")
                                print(f"   Position: {stake_to_sell} contracts | Entry: {entry_price:.2%} → Current: {current_bid:.2%}")
                                print(f"   Value: ${entry_value:.2f} → ${current_value:.2f} (Loss: ${entry_value - current_value:.2f})")
                                if sportsbook_prob is not None:
                                    print(f"   Sportsbook Prob: {sportsbook_prob:.2%} | Kalshi Prob: {current_bid:.2%}")
                                    if odds_diff_abs is not None:
                                        print(f"   Odds Difference (absolute): {odds_diff_abs:.1%} (threshold: ≤{STOP_LOSS_ODDS_DIFF_THRESHOLD:.0%} to allow)")
                                print(f"   Selling {stake_to_sell} contracts at {aggressive_sell_price:.2%} (bid: {current_bid:.2%}, {2*TICK:.2%} below for immediate fill)")
                                print(f"{'🛑' * 40}\n")
                                
                                try:
                                    # 🛡️ SAFETY CHECK: Verify we actually have the position before selling
                                    if PLACE_LIVE_KALSHI_ORDERS == "YES":
                                        try:
                                            live_positions = get_live_positions()
                                            live_qty = sum(
                                                p["contracts"] for p in live_positions 
                                                if p["ticker"] == market.get("ticker") and p["side"] == "yes"
                                            )
                                            if live_qty == 0:
                                                print(f"⚠️ WARNING: No live position found on {market.get('ticker')}, but proceeding with stop-loss based on local position")
                                                # Don't block - proceed with sell based on local position
                                            elif live_qty < stake_to_sell:
                                                print(f"⚠️ Adjusting stop-loss sell quantity from {stake_to_sell} to {live_qty} (actual live position)")
                                                stake_to_sell = live_qty
                                        except Exception as e:
                                            print(f"⚠️ Could not verify live position before stop-loss sell: {e} - proceeding based on local position")
                                    
                                    # Block stop-loss for NBA if NBA trading is disabled
                                    event_ticker_pos = current_pos.get("event_ticker", "")
                                    if event_ticker_pos and str(event_ticker_pos).startswith("KXNBAGAME-") and not ENABLE_NBA_TRADING:
                                        if VERBOSE:
                                            print(f"🚫 Blocking stop-loss sell for {match['match']} {side} — NBA trading is disabled")
                                        continue  # Skip stop-loss order but continue to show evaluation
                                    
                                    # Place sell order at aggressive price (below bid) to ensure immediate fill
                                    sell_resp = prepare_kalshi_order(
                                        market_ticker=market.get("ticker"),
                                        side="yes",
                                        price=aggressive_sell_price,
                                        quantity=stake_to_sell,
                                        action="sell"
                                    )
                                    
                                    sell_order_id, sell_client_oid = _extract_order_id(sell_resp)
                                    if sell_order_id:
                                        # Wait for fill
                                        sell_status, sell_filled_qty = wait_for_fill_or_cancel(
                                            sell_order_id,
                                            client_order_id=sell_client_oid,
                                            timeout_s=ORDER_FILL_TIME,
                                            poll_s=1.0,
                                            expected_count=stake_to_sell,
                                            require_full=False,
                                            verify_ticker=market.get("ticker"),
                                            verify_side="yes"
                                        )
                                        
                                        if sell_filled_qty > 0:
                                            print(f"✅ Stop-loss executed: Sold {sell_filled_qty} contracts at {aggressive_sell_price:.2%} (immediate exit)")
                                            # Mark position as stop-loss triggered
                                            current_pos["stop_loss_triggered"] = True
                                            # Mark event as stop-lossed with timestamp and original entry price (allows re-entry if price recovers)
                                            original_entry_price = current_pos.get("entry_price")
                                            mark_event_stop_lossed(ticker, entry_price=original_entry_price)
                                            # Update stake (reduce by sold amount)
                                            current_pos["stake"] = max(0, current_pos.get("stake", 0) - sell_filled_qty)
                                            if current_pos["stake"] <= 0:
                                                current_pos["settled"] = True
                                            save_positions()
                                        else:
                                            print(f"⚠️ Stop-loss order not filled: {sell_status}")
                                    else:
                                        print(f"⚠️ Could not extract order ID from stop-loss sell order")
                                except Exception as e:
                                    print(f"⚠️ Error executing stop-loss: {e}")
                                
                                # Skip further processing for this side (stop-loss takes priority)
                                continue

            existing_opposite = next(
                (
                    p for p in event_positions
                    if p.get("market_ticker") != market.get("ticker")
                ),
                None
            )
            
            # ✅ SIMPLIFIED: Removed rebalancing logic - just check if we need to hedge
            # is_hedge_context is True only when we have opposite position (not based on "drift outside bounds")
            is_hedge_context = bool(existing_opposite and not one_sided_exposure) and HEDGING_ENABLED

            # === High-confidence lockout REMOVED ===
            # REMOVED: This conflicted with MIN_PRICE/MAX_PRICE strategy (60-70% Kalshi price range)
            # The MIN_PRICE/MAX_PRICE check at line ~6189 already filters by actual Kalshi execution price
            # Old check was: if not is_hedge_context and odds_prob <= FIRST_ENTRY_PRICE_MIN (0.75)
            # This was blocking valid trades where odds_prob < 75% but Kalshi price was in 60-70% range

            if (
                is_hedge_context
                and existing_opposite
                and existing_opposite.get("market_ticker") != market.get("ticker")
            ):
                log_hedge_band_preview(existing_opposite, market, match.get("match", ticker))

            # Guard against missing bid/ask
            # ✅ Extract prices fresh for each side-market evaluation
            yb = format_price(market.get("yes_bid"))
            ya = format_price(market.get("yes_ask"))

            # ✅ If both are missing → skip side safely
            if yb is None and ya is None:
                print(f"⏭️ Skip {match['match']} {side} — no bid/ask on Kalshi")
                METRICS["skip_counts"]["no_bid_ask"] = METRICS["skip_counts"].get("no_bid_ask", 0) + 1
                continue

            # --- Prices & spread (compute FIRST) ---
            # --- Compute aggressive YES entry between (bid - cushion) .. (ask - cushion) ---
            # --- Compute aggressive YES entry between (bid - cushion) .. (ask - cushion) ---
            # --- Compute aggressive YES entry between bid .. ask-1tick, with snapping rules ---
            yb_raw = market.get("yes_bid")
            ya_raw = market.get("yes_ask")
            yb = format_price(yb_raw)
            ya = format_price(ya_raw)

            if yb is None and ya is None:
                continue

            # Derive mid & spread (price space)
            if yb is not None and ya is not None:
                mid = (yb + ya) / 2.0
                spread_px = max(0.0, ya - yb)
            else:
                mid = ya if ya is not None else yb
                spread_px = 0.0

            spread_mid = spread_px
            volatility_mode = "slow"

            # Edge vs MID (as % return if you "pay" MID)
            ev_pct_vs_mid = 0.0
            if (mid is not None and mid > 0):
                ev_pct_vs_mid = (odds_prob - mid) / max(1e-6, mid)

            # Gates & aggressiveness mapping
            half_spread = 0.5 * spread_px
            gate = max(MIN_EV_THRESHOLD, half_spread)  # don't get aggressive if edge barely beats spread (matching testing_new_strategy.py)


            # For hedges and rebalancing, be aggressive to ensure fill - prioritize getting the hedge in place
            # is_hedge_context is True for: (1) initial hedges when we have opposite side, (2) rebalancing when outside bounds
            # Regular Kelly trades within bounds will have is_hedge_context = False and use normal pricing below
            if is_hedge_context:
                # For hedges and rebalancing, set maximum aggressiveness to ensure we get filled
                # This ensures we lock in the hedge quickly since we start making money once hedged
                # Also applies when positions drift outside bounds and need rebalancing
                a = 1.0  # Maximum aggressiveness for hedges and rebalancing
            else:
                # Base aggression in [0..1]: 0 near bid, 1 near ask-1tick
                a_raw = (ev_pct_vs_mid - gate) / max(1e-9, (EDGE_FOR_ASK - gate))
                a = _clip01(a_raw)

                # Spread damping: wide spreads → less aggression
                damp = min(1.0, SPREAD_TIGHT / max(spread_px, 1e-6))  # e.g., SPREAD_TIGHT = 0.02
                a *= damp

                # Mode nudge
                if volatility_mode == "scalp":
                    a *= 0.6
                elif volatility_mode == "volatile":
                    a = min(1.0, a * 1.15)

            # Snap rules:
            # 1) Low aggression → post exactly at BID (maker)
            if (yb is not None) and (a <= BID_POST_A) and not is_hedge_context:
                entry_price = yb

            # 2) Very high aggression → optionally hit ASK (taker)
            # For hedges, always be willing to cross the ask to ensure fill
            elif (ya is not None) and (is_hedge_context or (ALLOW_TAKER and (a >= TAKER_A))):
                entry_price = ya

            # 3) Otherwise, interpolate between BID and (ASK - 1 tick)
            else:
                lo = yb if yb is not None else (mid - 0.5 * spread_px)
                hi = _prev_tick(ya) if ya is not None else (mid + 0.5 * spread_px)

                # If anchors are inverted/missing, normalize around mid
                if (lo is None) or (hi is None) or (lo >= hi):
                    lo = mid - max(1*TICK, 0.25 * spread_px)
                    hi = mid + max(1*TICK, 0.25 * spread_px)

                entry_price = lo + a * (hi - lo)

            # Final clamp & quantize
            entry_price = _q(max(0.01, min(0.99, entry_price)))

            # Safety: if we shouldn't cross and quantization bumped us to/over ask, pull 1 tick below ask
            # For hedges, allow crossing the ask to ensure fill
            if (ya is not None) and (entry_price >= ya) and not (is_hedge_context or (ALLOW_TAKER and (a >= TAKER_A))):
                entry_price = _prev_tick(ya)

            ######

            # Always trade YES only (home or away)
            side_choice = "yes"
            kalshi_price = round(entry_price, 4)

            # 🔒 Skip 0% or 100% price levels (illiquid / broken market)
            if kalshi_price <= 0.01 or kalshi_price >= 0.99:
                print(f"⏭️ Skip {match['match']} {side} — invalid price {kalshi_price:.2%} (too extreme)")
                METRICS["skip_counts"]["invalid_price"] = METRICS["skip_counts"].get("invalid_price", 0) + 1
                continue

            # ✅ Price range validation for first entries (matching testing_new_strategy.py)
            if not is_hedge_context:
                if kalshi_price is not None:
                    if kalshi_price < MIN_PRICE or kalshi_price > MAX_PRICE:
                        print(f"⏭️ Skip {match['match']} {side} — price {kalshi_price:.2%} outside range [{MIN_PRICE:.2%}, {MAX_PRICE:.2%}]")
                        METRICS["skip_counts"]["price_out_of_range"] = METRICS["skip_counts"].get("price_out_of_range", 0) + 1
                        continue

            # --- Volatility mode (set ONLY here) ---
            volatility_mode = "slow"
            spread = abs(ya - yb) if (ya is not None and yb is not None) else spread_mid
            
            # --- EV calculation with fill probability (Phase 1) ---
            yes_ask_formatted_early = format_price(market.get("yes_ask"))
            yes_bid_formatted_early = format_price(market.get("yes_bid"))
            mid_price_for_ev = mid if mid is not None else ((yes_bid_formatted_early + yes_ask_formatted_early) / 2.0 if (yes_bid_formatted_early is not None and yes_ask_formatted_early is not None) else None)
            
            # Get period_clock and match_name for time-based fill probability adjustment
            odds_snapshot_for_ev = match.get("odds_feed") or {}
            period_clock_for_ev = odds_snapshot_for_ev.get("period_clock")
            match_name_for_ev = match.get("match")
            
            # Estimate quantity for fill probability calculation (use preliminary Kelly with mid price)
            estimated_qty = 25  # Default estimate
            if mid_price_for_ev is not None and odds_prob is not None:
                try:
                    # Quick preliminary Kelly calculation using mid price (maker assumption)
                    provisional_fee_est = kalshi_fee_per_contract(mid_price_for_ev, is_maker=True)
                    kelly_est = kelly_yes_with_costs(odds_prob, mid_price_for_ev, rt_cost=provisional_fee_est)
                    if kelly_est and kelly_est > 0:
                        fractional_kelly_est = kelly_est * KELLY_FRACTION
                        cost_per_contract_est = mid_price_for_ev + provisional_fee_est
                        bet_size_est = (fractional_kelly_est * capital) / cost_per_contract_est if cost_per_contract_est > 0 else 0
                        estimated_qty = max(1, int(bet_size_est))
                except Exception:
                    pass  # Use default if calculation fails
            
            # Choose maker vs taker using fill probability
            use_maker_from_ev, ev_buy, fill_prob_maker, order_price_suggestion = choose_maker_vs_taker(
                odds_prob=odds_prob,
                current_bid=yes_bid_formatted_early,
                current_ask=yes_ask_formatted_early,
                quantity=estimated_qty,
                mid_price=mid_price_for_ev,
                spread=spread,
                period_clock=period_clock_for_ev,
                match_name=match_name_for_ev
            )
            
            # For backward compatibility, also calculate individual EVs
            ev_taker_early = calculate_ev_buy(odds_prob, yes_ask_formatted_early, is_maker=False) if yes_ask_formatted_early is not None else None
            ev_maker_early = calculate_ev_buy(odds_prob, mid_price_for_ev, is_maker=True) if mid_price_for_ev is not None else None
            
            # ✅ Spread filtering (matching testing_new_strategy.py)
            spread_ok = True
            spread_reason = None
            if SPREAD_FILTER_ENABLED:
                if spread is None:
                    spread_ok = False
                    spread_reason = 'Spread unavailable'
                elif spread > MAX_SPREAD_ABSOLUTE:
                    spread_ok = False
                    spread_reason = 'Spread exceeds absolute limit'
                elif ev_buy is not None and ev_buy > 0 and spread > (ev_buy * MAX_SPREAD_EDGE_RATIO):
                    spread_ok = False
                    spread_reason = 'Spread consumes edge'
            
            if not spread_ok:
                bid_str = f"{yb:.2%}" if yb is not None else 'N/A'
                ask_str = f"{ya:.2%}" if ya is not None else 'N/A'
                print(f"⏭️ Skip {match['match']} {side} — spread filter: {spread_reason} "
                      f"(spread={spread:.2% if spread is not None else 'N/A'}, bid={bid_str}, ask={ask_str})")
                METRICS["missed_wide_spread"] += 1
                METRICS["skip_counts"]["spread_filter"] = METRICS["skip_counts"].get("spread_filter", 0) + 1
                continue
            
            # Legacy MAX_SPREAD check (only if SPREAD_FILTER_ENABLED is False, for backward compatibility)
            if not SPREAD_FILTER_ENABLED and spread is not None and spread > MAX_SPREAD:
                bid_str = f"{yb:.2%}" if yb is not None else 'N/A'
                ask_str = f"{ya:.2%}" if ya is not None else 'N/A'
                print(f"⏭️ Skip {match['match']} {side} — spread too wide: {spread:.2%} > {MAX_SPREAD:.2%} "
                      f"(bid={bid_str}, ask={ask_str})")
                METRICS["missed_wide_spread"] += 1
                METRICS["skip_counts"]["spread_too_wide"] = METRICS["skip_counts"].get("spread_too_wide", 0) + 1
                continue
            
            if spread >= 0.10:
                volatility_mode = "volatile"
            elif spread <= 0.02:
                volatility_mode = "scalp"

            existing_pos = next((p for p in positions if p["market_ticker"] == market.get("ticker")), None)
            last_price = existing_pos.get("last_price") if existing_pos else None

            # Jump/scalp overrides
            if spread >= SCALP_VOL_SPREAD:
                volatility_mode = "scalp"
            elif last_price is not None and mid is not None and abs(mid - last_price) >= SCALP_VOL_JUMP:
                volatility_mode = "scalp"

            # track last seen price if we hold this market
            if existing_pos and mid is not None:
                existing_pos["last_price"] = mid

            # --- EV calculation already done above for spread filtering ---
            # Reuse ev_buy and use_maker from choose_maker_vs_taker (with fill probability)
            yes_ask_formatted = yes_ask_formatted_early  # Reuse from earlier calculation
            use_maker = use_maker_from_ev  # Use decision from choose_maker_vs_taker (includes fill probability)
            
            # For backward compatibility, also calculate fair_ev using old method
            cons_ev, fair_ev = ev_settlement_yes(odds_prob, entry_price, market.get("yes_ask"), yes_ask=yes_ask_formatted)
            
            # -- Provisional Kelly for debug (computed early so we can print before any 'continue')
            is_maker_provisional = (yes_ask_formatted is not None and kalshi_price < yes_ask_formatted)
            # Fix: Maker orders still have fees (just lower), so always calculate fee properly
            provisional_fee_pc = kalshi_fee_per_contract(kalshi_price, is_maker=is_maker_provisional)

            kelly_fraction_dbg = kelly_yes_with_costs(odds_prob, kalshi_price, rt_cost=provisional_fee_pc)

            # -- Snapshot numbers for this side (initial evaluation - skip table format here, will show in detailed view)
            kalshi_prob = (mid if (mid is not None) else kalshi_price)
            edge_pct = (odds_prob - (kalshi_prob or 0.0)) * 100.0
            side_tag = "HOME" if side == home else "AWAY"
            # Don't print here - will show in detailed table below

            rt_ev = cons_ev if USE_CONSERVATIVE_EV else fair_ev

            # ✅ If we already have exposure on this event BUT no hedge yet → stop taking more first-side risk
            # Check for exposure on *this same side only* (same market_ticker)
            curr_side_exp = side_exposure_dollars(ticker, market.get("ticker"))
            side_cap_dollars = capital * MAX_STAKE_PCT
            remaining_cap = max(0.0, side_cap_dollars - curr_side_exp)
            remaining_qty_cap = max_qty_with_cap(remaining_cap, kalshi_price)
            allow_add_on_entry = False
            if one_sided_exposure and remaining_qty_cap >= 1:
                if not neutralized_evt:
                    # Before hedge: allow pyramiding only if price increased (averaging up on winners)
                    if ALLOW_PYRAMID_BEFORE_HEDGE:
                        allow_add_on_entry = True
                    elif ALLOW_PYRAMID_ON_WINNERS:
                        # Check if current price > entry price (price has increased)
                        current_pos = next(
                            (p for p in positions
                             if event_key(p.get("event_ticker")) == ticker_key
                             and p["market_ticker"] == market.get("ticker")
                             and not p.get("neutralized", False)),
                            None
                        )
                        if current_pos and kalshi_price is not None:
                            entry_price = current_pos.get("entry_price", 0)
                            # Use kalshi_price (actual execution price) for comparison
                            price_increase = kalshi_price - entry_price
                            if (entry_price > 0 and 
                                kalshi_price > entry_price and
                                price_increase >= PYRAMID_ON_WINNERS_MIN_INCREASE and
                                kalshi_price <= MAX_PRICE):
                                allow_add_on_entry = True
                                if VERBOSE:
                                    print(f"✅ Allowing pyramiding before hedge: price increased from {entry_price:.2%} to {kalshi_price:.2%} (+{price_increase:.2%}, ≥{PYRAMID_ON_WINNERS_MIN_INCREASE:.0%} threshold, below {MAX_PRICE:.0%} cap)")
                            elif VERBOSE:
                                # Debug why pyramiding was blocked
                                reasons = []
                                if entry_price <= 0:
                                    reasons.append(f"invalid entry_price ({entry_price})")
                                elif kalshi_price <= entry_price:
                                    reasons.append(f"price decreased ({entry_price:.2%} → {kalshi_price:.2%})")
                                elif price_increase < PYRAMID_ON_WINNERS_MIN_INCREASE:
                                    reasons.append(f"increase {price_increase:.2%} < {PYRAMID_ON_WINNERS_MIN_INCREASE:.0%} threshold")
                                elif kalshi_price > MAX_PRICE:
                                    reasons.append(f"price {kalshi_price:.2%} > {MAX_PRICE:.0%} cap")
                                if reasons:
                                    print(f"🚫 Pyramiding before hedge blocked for {side}: {', '.join(reasons)}")
                        elif VERBOSE:
                            if not current_pos:
                                print(f"🚫 Pyramiding before hedge blocked for {side}: could not find existing position (ticker={market.get('ticker')}, event={ticker_key})")
                            if kalshi_price is None:
                                print(f"🚫 Pyramiding before hedge blocked for {side}: kalshi_price is None")
                elif neutralized_evt and ALLOW_PYRAMID_AFTER_HEDGE:
                    # ✅ SAFETY CHECK: Validate that pyramiding maintains MIN_HEDGE_RETURN
                    # Get the opposite side position
                    opposite_side_ticker = existing_opposite.get("market_ticker") if existing_opposite else None
                    current_side_ticker = market.get("ticker")
                    
                    if opposite_side_ticker and current_side_ticker and kalshi_price and kalshi_price > 0:
                        # Aggregate current quantities and prices for both sides
                        event_positions = [p for p in positions if event_key(p.get("event_ticker")) == ticker_key and not p.get("settled", False)]
                        
                        # Current side (the one we're considering adding to)
                        qA_current, pA_current, _ = aggregate_positions_on_side(event_positions, current_side_ticker)
                        
                        # Opposite side
                        qB_current, pB_current, _ = aggregate_positions_on_side(event_positions, opposite_side_ticker)
                        
                        if qA_current > 0 and qB_current > 0 and pA_current > 0 and pB_current > 0:
                            # Calculate what NEW quantity would be after adding pyramid amount
                            # Estimate provisional add quantity based on current capital allocation
                            provisional_add_qty = max(1, int(capital * kelly_fraction_dbg / kalshi_price))
                            
                            # Proposed new quantities
                            qA_proposed = qA_current + provisional_add_qty
                            pA_weighted_new = (qA_current * pA_current + provisional_add_qty * kalshi_price) / qA_proposed
                            
                            # Check if proposed quantities maintain MIN_HEDGE_RETURN
                            roi_A_check, roi_B_check = hedge_outcome_rois(qA_proposed, pA_weighted_new, qB_current, pB_current)
                            
                            if roi_A_check >= MIN_HEDGE_RETURN and roi_B_check >= MIN_HEDGE_RETURN:
                                allow_add_on_entry = True
                                if VERBOSE:
                                    print(f"✅ Pyramid after hedge allowed: ROI A={roi_A_check:.2%}, B={roi_B_check:.2%} (min={MIN_HEDGE_RETURN:.2%})")
                            else:
                                if VERBOSE:
                                    print(f"🚫 Pyramid after hedge BLOCKED: Would break hedge ROI (A={roi_A_check:.2%}, B={roi_B_check:.2%}, min={MIN_HEDGE_RETURN:.2%})")
                                allow_add_on_entry = False
                        else:
                            # Can't validate - be conservative and block
                            if VERBOSE:
                                print(f"🚫 Pyramid after hedge BLOCKED: Could not validate hedge quantities (qA={qA_current}, qB={qB_current})")
                            allow_add_on_entry = False
                    else:
                        # Missing ticker info - be conservative
                        if VERBOSE:
                            print(f"🚫 Pyramid after hedge BLOCKED: Missing ticker/price information")
                        allow_add_on_entry = False

            # 🚫 Absolute block: never add to same side until event is hedged
            if pending_same_side_block and not allow_add_on_entry:
                if VERBOSE:
                    print(f"⚠️ {match['match']} {side} — waiting for opposite hedge before re-entering same side.")
                continue

            if one_sided_exposure and not allow_add_on_entry and not is_hedge_context:
                if VERBOSE:
                    print(f"🚫 {match['match']} — already long this side; skipping any add-ons until hedge exists.")
                continue

            # and can add more Home under the normal first-entry EV/Kelly rules + exposure caps.

            # --- EV gating: different rules for first-entry vs. hedge ---
            ###
            # --- EV gating (use ev_buy matching testing_new_strategy.py) ---
            if ev_buy is None:
                print(f"⏭️ Skip {match['match']} {side} — ev_buy is None (cannot calculate EV)")
                METRICS["skip_counts"]["ev_none"] = METRICS["skip_counts"].get("ev_none", 0) + 1
                continue

            hedge_band_for_new_pos = None

            hedge_ev_context = is_hedge_context

            if hedge_ev_context:  # HEDGE MODE
                # Skip EV check for hedges - ROI bands already ensure combined position profitability
                # Standalone EV is meaningless for hedges since we're not making a standalone trade
                # The ROI bands calculate based on total cost (both positions) and total payout
                if VERBOSE:
                    kalshi_prob = (mid if (mid is not None) else kalshi_price)
                    edge_pct = (odds_prob - (kalshi_prob or 0.0)) * 100.0
                    print(
                        f"ℹ️ Hedge mode — skipping standalone EV check (ROI bands ensure combined position profitability) "
                        f"(odds={odds_prob:.2%} kalshi={kalshi_prob:.2%} edge={edge_pct:.2f}% kelly={kelly_fraction_dbg:.3f})"
                    )
            else:  # FIRST ENTRY MODE
                # Use MIN_EV_THRESHOLD from testing_new_strategy.py (0.015 = 1.5%)
                ev_threshold_min = MIN_EV_THRESHOLD
                ev_threshold_max = MAX_EV_THRESHOLD
                if DYNAMIC_EV_ENABLED:
                    # Adjust threshold based on spread and volatility
                    spread_factor = min(1.0, spread / SCALP_VOL_SPREAD) if spread else 1.0
                    # Wider spreads require higher EV (but still capped at max)
                    ev_threshold_min = MIN_EV_THRESHOLD * (1.0 + spread_factor * 0.5)
                    ev_threshold_max = MAX_EV_THRESHOLD  # Keep max fixed regardless of spread
                    # Safety clamp: ensure min never exceeds max
                    ev_threshold_min = min(ev_threshold_min, ev_threshold_max)
                
                # Use ev_buy (matching testing_new_strategy.py) instead of fair_ev
                # Only trade when EV is between min and max thresholds
                # Note: ev_buy is already checked for None above at line 7006, so it cannot be None here
                if ev_buy < ev_threshold_min or ev_buy > ev_threshold_max:
                    kalshi_prob = (mid if (mid is not None) else kalshi_price)
                    edge_pct = (odds_prob - (kalshi_prob or 0.0)) * 100.0
                    ev_display = ev_buy
                    reason = "too low" if ev_buy < ev_threshold_min else "too high"
                    print(
                        f"⏭️ Skip {match['match']} {side} — EV {reason}: ev={ev_display} (must be between {ev_threshold_min:.2%} and {ev_threshold_max:.2%}) | "
                        f"odds={odds_prob:.2%} kalshi={kalshi_prob:.2%} edge={edge_pct:.2f}% kelly={kelly_fraction_dbg:.3f}{score_time_info}"
                    )
                    METRICS["skip_counts"]["ev_below_threshold"] = METRICS["skip_counts"].get("ev_below_threshold", 0) + 1
                    continue


            ####


            #####
            # Adjust edge for half-spread slippage
            yes_ask_formatted_here = format_price(market.get("yes_ask"))
            is_maker_entry = (yes_ask_formatted_here is not None and entry_price < yes_ask_formatted_here)
            cost_buffer = kalshi_fee_per_contract(entry_price, is_maker=is_maker_entry) + 0.5 * spread

            # Skip if round-trip EV is not positive enough
            # Stage 1: provisional fee → provisional Kelly
            # (We recompute more accurate fee after we know quantity.)
            is_maker_provisional_here = (yes_ask_formatted_here is not None and kalshi_price < yes_ask_formatted_here)
            # Fix: Maker orders still have fees (just lower), so always calculate fee properly
            provisional_fee_pc = kalshi_fee_per_contract(kalshi_price, is_maker=is_maker_provisional_here)
            if side_choice == "yes":
                kelly_fraction = kelly_yes_with_costs(odds_prob, kalshi_price, rt_cost=provisional_fee_pc)
            else:
                kelly_fraction = kelly_yes_with_costs(1 - odds_prob, 1 - kalshi_price, rt_cost=provisional_fee_pc)

            # 📊 Debug snapshot: odds prob, kalshi prob, edge, ev, kelly
            kalshi_prob = (mid if (mid is not None) else kalshi_price)
            edge_pct = (odds_prob - (kalshi_prob or 0.0)) * 100.0
            mode = "HEDGE" if is_hedge_context else "ENTRY"
            spread_dbg = (abs((ya or 0.0) - (yb or 0.0)) if (ya is not None and yb is not None) else 0.0)
            
            # Print formatted table row
            side_name = side  # Use actual team name
            # Use ev_buy for display (matching testing_new_strategy.py) instead of fair_ev
            ev_display = ev_buy if ev_buy is not None else (fair_ev if fair_ev is not None else 0.0)
            print(
                f"{side_name:<6} | {mode:<6} | {odds_prob:>7.2%} | {kalshi_prob:>7.2%} | "
                f"{edge_pct:>7.2f}% | {ev_display:>7.2%} | {kelly_fraction:>6.3f} | "
                f"{kalshi_price:>7.2%} | {spread_dbg:>7.2%}"
            )


            # ✅ Skip tiny Kelly values
            # ✅ At this point, we know:
            # - kelly_fraction is valid
            # - is_hedge_context tells us if we should hedge or first-enter

            if (not is_hedge_context) and (kelly_fraction < MIN_KELLY):
                print(f"⏭️ Skip {match['match']} {side} — Kelly too small: {kelly_fraction:.4f} < MIN_KELLY {MIN_KELLY:.4f}")
                METRICS["skip_counts"]["kelly_too_small"] = METRICS["skip_counts"].get("kelly_too_small", 0) + 1
                continue


            bands_dirty = False

            if is_hedge_context:
                if existing_opposite.get("market_ticker") == market.get("ticker"):
                    print(f"⏭️ Skip {match['match']} {side} — hedge requires opposite market (same market_ticker, waiting for other side quotes)")
                    METRICS["skip_counts"]["hedge_same_market"] = METRICS["skip_counts"].get("hedge_same_market", 0) + 1
                    continue
                # 💡 You already have one side → must calculate hedge quantity
                # Use kalshi_price (aggressive pricing) for hedge sizing to match actual entry price
                # kalshi_price is already calculated above using aggressiveness logic
                if kalshi_price is None or kalshi_price <= 0:
                    print(f"⏭️ Skip {match['match']} {side} — hedge impossible: kalshi_price is invalid ({kalshi_price})")
                    METRICS["skip_counts"]["hedge_invalid_price"] = METRICS["skip_counts"].get("hedge_invalid_price", 0) + 1
                    continue

                # Ensure price is in valid range for calculations (kalshi_price should already be in [0,1] from aggressiveness calc)
                hedge_price = max(0.01, min(0.99, float(kalshi_price)))
                
                # Check price range before proceeding (use original kalshi_price for consistency)
                if not (HEDGE_PRICE_MIN <= kalshi_price <= HEDGE_PRICE_MAX):
                    print(f"⏭️ Skip {match['match']} {side} — hedge price {kalshi_price:.2%} outside range [{HEDGE_PRICE_MIN:.2%}, {HEDGE_PRICE_MAX:.2%}]")
                    METRICS["missed_hedge_band"] += 1
                    METRICS["skip_counts"]["hedge_price_out_of_range"] = METRICS["skip_counts"].get("hedge_price_out_of_range", 0) + 1
                    continue

                # For hedges, skip Kelly check - ROI bands ensure combined position profitability
                # Standalone Kelly is meaningless for hedges since we're not making a standalone trade
                quantity = compute_hedge_quantity(
                    existing_opposite,
                    hedge_price,  # Use clamped price for safety in calculations
                    odds_prob,
                    capital
                )

                if quantity is None:
                    if VERBOSE:
                        pB = float(hedge_price)
                        # Check why it failed - could be price range or capital constraint
                        if not (HEDGE_PRICE_MIN <= pB <= HEDGE_PRICE_MAX):
                            print(f"🚫 Hedge price {pB:.2%} outside [{HEDGE_PRICE_MIN:.2%}, {HEDGE_PRICE_MAX:.2%}] — skip")
                            METRICS["missed_hedge_band"] += 1
                        else:
                            dollars_cap = capital * HEDGE_MAX_STAKE_PCT
                            max_qty_capital = max_qty_with_cap(dollars_cap, pB)
                            if max_qty_capital < 1:
                                print(f"🚫 Hedge impossible — insufficient capital (cap_qty={max_qty_capital:.1f}, capital=${capital:.2f})")
                                METRICS["missed_hedge_cap"] += 1
                            else:
                                print(f"🚫 Hedge impossible — unknown constraint (cap_qty={max_qty_capital:.1f}, capital=${capital:.2f})")
                                METRICS["missed_hedge_band"] += 1
                    else:
                        # Track even if not verbose
                        pB = float(hedge_price)
                        if not (HEDGE_PRICE_MIN <= pB <= HEDGE_PRICE_MAX):
                            METRICS["missed_hedge_band"] += 1
                        else:
                            dollars_cap = capital * HEDGE_MAX_STAKE_PCT
                            max_qty_capital = max_qty_with_cap(dollars_cap, pB)
                            if max_qty_capital < 1:
                                METRICS["missed_hedge_cap"] += 1
                            else:
                                METRICS["missed_hedge_band"] += 1
                    continue

            else:
                # 💡 No opposite side exists → this is a new first-entry attempt
                quantity = compute_first_entry_quantity(
                    kelly_fraction,
                    kalshi_price,
                    odds_prob,
                    capital
                )
                if quantity is None:
                    # Not good enough for first entry
                    print(f"⏭️ Skip {match['match']} {side} — first entry quantity is None (Kelly calculation failed)")
                    METRICS["skip_counts"]["quantity_none"] = METRICS["skip_counts"].get("quantity_none", 0) + 1
                    continue
                if (not has_event_position):
                    scale = max(1.0, capital / FIRST_ENTRY_MIN_CAPITAL)
                    min_qty_required = max(FIRST_ENTRY_MIN_QTY, int(math.ceil(FIRST_ENTRY_MIN_QTY * scale)))
                    if quantity < min_qty_required:
                        print(f"⏭️ Skip {match['match']} {side} — quantity too small: {quantity} < MIN {min_qty_required} contracts")
                        METRICS["skip_counts"]["quantity_too_small"] = METRICS["skip_counts"].get("quantity_too_small", 0) + 1
                        continue
            # Initialize variables for hedge context (needed for later calculations)
            total_hedge_stake_existing = 0.0
            total_hedge_cost_existing = 0.0
            
            # ✅ Final hedge clamp just before placing (quotes can change)
            if is_hedge_context:
                # Use kalshi_price for band calculation to match actual entry price
                price_for_band = kalshi_price
                if price_for_band is None:
                    if VERBOSE: print("🚫 Hedge impossible — kalshi_price is None")
                    continue
                
                # ✅ AGGREGATE all positions on the opposite side (not just one!)
                # Find all positions on the opposite side (same event, different market_ticker)
                opposite_side_positions = [
                    p for p in event_positions
                    if p.get("market_ticker") != market.get("ticker")
                    and not p.get("settled", False)
                ]
                
                if not opposite_side_positions:
                    print(f"⏭️ Skip {match['match']} {side} — no opposite side positions found for hedge")
                    METRICS["skip_counts"]["hedge_no_opposite"] = METRICS["skip_counts"].get("hedge_no_opposite", 0) + 1
                    continue
                
                # Aggregate opposite side: sum quantities and calculate weighted average entry price
                total_opp_stake = 0.0
                total_opp_cost = 0.0  # sum of (quantity * entry_price) for weighted average
                
                for opp_pos in opposite_side_positions:
                    try:
                        qty = float(opp_pos.get("stake", 0))
                        price = float(opp_pos.get("entry_price", 0))
                        if qty > 0 and price > 0:
                            total_opp_stake += qty
                            total_opp_cost += qty * price
                    except (TypeError, ValueError):
                        continue
                
                if total_opp_stake <= 0:
                    print(f"⏭️ Skip {match['match']} {side} — invalid aggregated opposite side stake: {total_opp_stake}")
                    METRICS["skip_counts"]["hedge_invalid_stake"] = METRICS["skip_counts"].get("hedge_invalid_stake", 0) + 1
                    continue
                
                # Calculate weighted average entry price for opposite side
                weighted_avg_opp_entry = total_opp_cost / total_opp_stake
                
                # ✅ AGGREGATE all positions on the hedge side (to account for mixed entry prices)
                # Find all positions on the hedge side (same event, same market_ticker)
                hedge_side_positions = [
                    p for p in event_positions
                    if p.get("market_ticker") == market.get("ticker")
                    and not p.get("settled", False)
                ]
                
                # Calculate weighted average entry price for existing hedge side positions
                total_hedge_stake_existing = 0.0
                total_hedge_cost_existing = 0.0
                
                for hedge_pos in hedge_side_positions:
                    try:
                        qty = float(hedge_pos.get("stake", 0))
                        price = float(hedge_pos.get("entry_price", 0))
                        if qty > 0 and price > 0:
                            total_hedge_stake_existing += qty
                            total_hedge_cost_existing += qty * price
                    except (TypeError, ValueError):
                        continue
                
                # Use aggregated values for hedge band calculation
                opp_stake = total_opp_stake
                opp_entry = weighted_avg_opp_entry
                
                # Calculate held quantity using aggregated value (accounts for multiple positions)
                held_qty_hedge_side = int(round(total_hedge_stake_existing))
                
                # Calculate bands using NEW entry price (this tells us what total we'd need at new price)
                # We'll validate with actual weighted average later
                band_now = hedge_qty_bounds_target_roi(
                    opp_stake,
                    opp_entry,
                    float(price_for_band),
                    r=MIN_HEDGE_RETURN
                )

                if not band_now or band_now[0] is None or band_now[1] is None:
                    print(f"⏭️ Skip {match['match']} {side} — hedge band vanished just before place")
                    METRICS["missed_hedge_band"] += 1
                    METRICS["skip_counts"]["hedge_band_vanished"] = METRICS["skip_counts"].get("hedge_band_vanished", 0) + 1
                    continue
                ql, qh = band_now
                ql_i, qh_i = int(math.ceil(ql)), int(math.floor(qh))
                
                # Handle invalid bands (qh_i < ql_i) - happens when one side is over-levered
                invalid_bands = (qh_i < ql_i)
                use_fallback_balance = False
                fallback_target_qty = None
                
                if invalid_bands:
                    # Check if we're clearly over-levered to one side and should still try to balance
                    # Use probability-weighted exposure to account for market probabilities
                    opp_exposure = total_opp_stake * weighted_avg_opp_entry
                    current_side_avg_price = (total_hedge_cost_existing / total_hedge_stake_existing) if total_hedge_stake_existing > 0 else kalshi_price
                    current_side_exposure = total_hedge_stake_existing * current_side_avg_price
                    
                    # Get opposite side's probability (if current side is home, opposite is away, and vice versa)
                    # odds_prob is the probability for the current side being evaluated
                    current_side_prob = odds_prob
                    # Opposite side probability is 1 - current_side_prob (since probabilities sum to 1)
                    opp_side_prob = 1.0 - current_side_prob
                    
                    # Calculate probability-weighted exposure (risk-weighted)
                    # Risk-weighted = exposure × probability of LOSING (expected loss if that side loses)
                    # Higher probability of winning = lower probability of losing = lower risk
                    # For opposite side: prob of losing = current_side_prob (if current wins, opposite loses)
                    # For current side: prob of losing = 1 - current_side_prob = opp_side_prob
                    opp_risk_weighted = opp_exposure * current_side_prob  # Expected loss if opposite side loses
                    current_risk_weighted = current_side_exposure * opp_side_prob  # Expected loss if current side loses
                    
                    # Calculate exposure ratio using risk-weighted values
                    if current_risk_weighted > 0:
                        exposure_ratio = opp_risk_weighted / current_risk_weighted
                    else:
                        exposure_ratio = float('inf') if opp_risk_weighted > 0 else 1.0
                    
                    # CRITICAL: Only buy more of the UNDER-levered side, never the over-levered side
                    # If current side has MORE risk-weighted exposure than opposite, skip buying more
                    # (We only want to buy more of the side that has LESS exposure to balance)
                    if current_risk_weighted > opp_risk_weighted:
                        if VERBOSE:
                            print(f"🚫 Invalid bands but current side is OVER-levered (risk-weighted ${current_risk_weighted:.2f} > ${opp_risk_weighted:.2f})")
                            print(f"   Skipping fallback - should buy more of opposite side instead")
                        METRICS["missed_hedge_band"] += 1
                        continue
                    
                    # If opposite side has >60% more risk-weighted exposure, use fallback balance strategy
                    # This means current side is UNDER-levered, so we should buy more of it
                    if exposure_ratio > 1.6:
                        use_fallback_balance = True
                        if VERBOSE:
                            print(f"⚠️ Invalid bands but over-leverage detected:")
                            print(f"   Dollar exposure: opposite ${opp_exposure:.2f} vs current ${current_side_exposure:.2f}")
                            print(f"   Risk-weighted: opposite ${opp_risk_weighted:.2f} (prob={opp_side_prob:.1%}) vs current ${current_risk_weighted:.2f} (prob={current_side_prob:.1%})")
                            print(f"   Risk-weighted ratio={exposure_ratio:.2f}")
                            print(f"   Using fallback balance strategy to reduce imbalance")
                        
                        # Fallback: target equal risk-weighted exposure (not just dollar exposure)
                        # Calculate target dollar exposure needed to match opposite side's risk-weighted exposure
                        # target_risk_weighted = opp_risk_weighted (equal risk)
                        # target_risk_weighted = target_exposure * opp_side_prob  # Risk-weighted for current side
                        # target_exposure = opp_risk_weighted / opp_side_prob
                        # But cap at 80% of opposite dollar exposure to avoid over-leveraging
                        # Note: opp_side_prob is already calculated above (line 5079)
                        target_exposure_by_risk = opp_risk_weighted / max(0.01, opp_side_prob) if opp_side_prob > 0 else opp_exposure * 0.8
                        target_exposure_by_dollar = opp_exposure * 0.8
                        target_exposure = min(target_exposure_by_risk, target_exposure_by_dollar)
                        # Safety check: ensure kalshi_price is valid for division
                        if kalshi_price <= 0.01 or kalshi_price >= 0.99:
                            if VERBOSE:
                                print(f"🚫 Fallback balance blocked — invalid kalshi_price {kalshi_price:.2%}")
                            METRICS["missed_hedge_band"] += 1
                            continue
                        target_qty = max(1, int(target_exposure / kalshi_price))
                        quantity_needed = max(0, target_qty - total_hedge_stake_existing)
                        
                        if quantity_needed > 0:
                            # Set up fake bands for the rest of the logic to work
                            ql_i = total_hedge_stake_existing  # Current position
                            qh_i = target_qty  # Target position
                            # Store the target quantity for later use
                            fallback_target_qty = target_qty
                            if VERBOSE:
                                print(f"   Fallback rebalance: current={total_hedge_stake_existing}, target={target_qty}, buying {quantity_needed} contracts")
                        else:
                            if VERBOSE:
                                print("🚫 Band invalid and already balanced enough — skip")
                            METRICS["missed_hedge_band"] += 1
                            continue
                    else:
                        if VERBOSE: 
                            print(f"🚫 Band invalid at place time (ratio={exposure_ratio:.2f}) — skip")
                    METRICS["missed_hedge_band"] += 1
                    continue
                
                # ✅ SIMPLIFIED: Removed rebalancing - just check if we're at max hedge
                # If we're already at or above max band, don't add more (profit protection will exit when profitable)
                if held_qty_hedge_side >= qh_i:
                    print(f"⏭️ Skip {match['match']} {side} — already at/above max hedge band: {held_qty_hedge_side} >= {qh_i} (no incremental hedge needed)")
                    METRICS["skip_counts"]["hedge_at_max_band"] = METRICS["skip_counts"].get("hedge_at_max_band", 0) + 1
                    continue
                
                existing_opposite["q_low"] = ql_i
                existing_opposite["q_high"] = qh_i
                hedge_band_for_new_pos = (ql_i, qh_i)
                bands_dirty = True
                
                # ✅ SIMPLIFIED: Hedge sizing without rebalancing complexity
                # Determine if this is first hedge for this event
                is_first_hedge = (held_qty_hedge_side == 0)
                
                # Handle fallback balance case (invalid bands but over-levered)
                if use_fallback_balance:
                    # For fallback, we already calculated target_qty above
                    if fallback_target_qty is None or fallback_target_qty <= 0:
                        if VERBOSE:
                            print("🚫 Fallback balance failed — invalid target quantity")
                        METRICS["missed_hedge_band"] += 1
                        continue
                    quantity = fallback_target_qty
                    if VERBOSE:
                        print(f"✅ Fallback balance: setting quantity to {quantity} (incremental: {quantity - total_hedge_stake_existing})")
                elif is_first_hedge:
                    # First hedge: use q_high of band (TOTAL quantity)
                    quantity = qh_i
                else:
                    # Subsequent hedge: use Kelly clamped to band (TOTAL quantity)
                    # Determine maker/taker based on kalshi_price vs yes_ask
                    yes_ask_formatted_live = format_price(market.get("yes_ask"))
                    is_maker_live = (yes_ask_formatted_live is not None and kalshi_price < yes_ask_formatted_live)
                    fee_pc_live = kalshi_fee_per_contract(kalshi_price, is_maker=is_maker_live)
                    kelly_h_live = kelly_yes_with_costs(odds_prob, kalshi_price, rt_cost=fee_pc_live)
                    kelly_h_live = max(0.0, min(kelly_h_live, KELLY_HARD_CAP))
                    kelly_dollars_live = capital * kelly_h_live * HEDGE_TRADE_FRACTIONAL_KELLY
                    max_qty_kelly_live = max_qty_with_cap(kelly_dollars_live, kalshi_price)
                    
                    # Clamp Kelly target to band [ql_i, qh_i]
                    kelly_target_clamped = max(ql_i, min(max_qty_kelly_live, qh_i))
                    
                    # If Kelly suggests buying, scale up to Kelly target (clamped to band)
                    # Otherwise stay at current holdings
                    if kelly_h_live > 0 and kelly_target_clamped > held_qty_hedge_side:
                        target_total_qty = kelly_target_clamped
                    else:
                        # Kelly doesn't suggest adding, stay at current
                        target_total_qty = held_qty_hedge_side
                    
                    quantity = target_total_qty
                
                # Validate that total quantity is within band (incremental validation happens later)
                # Skip validation for fallback balance case (bands are fake)
                if not use_fallback_balance:
                    if quantity < ql_i or quantity > qh_i:
                        if VERBOSE:
                            print(f"🚫 Total quantity {quantity} outside band [{ql_i}, {qh_i}] — skip")
                        METRICS["missed_hedge_band"] += 1
                        continue
                
                # ✅ CRITICAL: Validate profitability with ACTUAL weighted average entry price
                # When we have existing positions at different prices, the actual cost basis is different
                # Calculate what the weighted average entry price would be after adding new contracts
                # Skip strict ROI validation for fallback balance case (we're explicitly balancing despite invalid bands)
                if not use_fallback_balance and total_hedge_stake_existing > 0 and quantity > total_hedge_stake_existing and quantity > 0:
                    # We're adding incremental contracts
                    incremental_qty_estimate = quantity - total_hedge_stake_existing
                    total_cost_after = total_hedge_cost_existing + (incremental_qty_estimate * float(price_for_band))
                    actual_weighted_avg_price = total_cost_after / quantity
                    
                    # Re-validate bands using actual weighted average price
                    band_validation = hedge_qty_bounds_target_roi(
                        opp_stake,
                        opp_entry,
                        actual_weighted_avg_price,
                        r=MIN_HEDGE_RETURN
                    )
                    
                    if band_validation:
                        ql_val, qh_val = band_validation
                        ql_val_i, qh_val_i = int(math.ceil(ql_val)), int(math.floor(qh_val))
                        
                        # Clamp quantity to validated bands (don't just block if outside)
                        # If target is above max, use max. If below min, use min.
                        quantity_clamped = max(ql_val_i, min(quantity, qh_val_i))
                        
                        if quantity_clamped != quantity:
                            if VERBOSE:
                                print(f"🔁 Clamping quantity {quantity} to validated band [{ql_val_i}, {qh_val_i}] → {quantity_clamped}")
                            quantity = quantity_clamped
                            
                            # Recalculate weighted average with clamped quantity
                            incremental_qty_clamped = quantity - total_hedge_stake_existing
                            if incremental_qty_clamped > 0 and quantity > 0:
                                total_cost_after = total_hedge_cost_existing + (incremental_qty_clamped * float(price_for_band))
                                actual_weighted_avg_price = total_cost_after / quantity
                        
                        # Validate that clamped quantity is within bands
                        if quantity < ql_val_i or quantity > qh_val_i:
                            if VERBOSE:
                                print(f"🚫 Clamped quantity {quantity} still outside validated band [{ql_val_i}, {qh_val_i}] "
                                      f"(actual weighted avg price {actual_weighted_avg_price:.2%}) — skip")
                            METRICS["missed_hedge_band"] += 1
                            continue
                        
                        # Also validate that both outcomes are profitable with clamped quantity
                        roi_A, roi_B = hedge_outcome_rois(opp_stake, opp_entry, quantity, actual_weighted_avg_price)
                        if roi_A < MIN_HEDGE_RETURN or roi_B < MIN_HEDGE_RETURN:
                            if VERBOSE:
                                print(f"🚫 Actual ROI below minimum: A={roi_A:.2%}, B={roi_B:.2%} (min={MIN_HEDGE_RETURN:.0%}) — skip")
                            METRICS["missed_hedge_band"] += 1
                            continue

                if bands_dirty:
                    try:
                        save_positions()
                    except Exception as e:
                        print(f"⚠️ Could not persist hedge bands: {e}")

            # ✅ Case 1: Safe to do first entries (no hedge context, no previous exposure)
            # ✅ Case 1: Safe to do first entries (no hedge context, no previous exposure)
            if (
                not is_hedge_context
                and (
                    (not has_event_position and ticker_key not in EVENT_LOCKED_TILL_HEDGE)
                    or allow_add_on_entry
                )
            ):

                # ✅ 5.0 Trading restriction: Game state check with 70-minute fallback
                if not has_event_position:  # True first trade (not add-on)
                    # Note: Stop-loss check is done at the beginning of the side loop to block ALL entries
                    
                    # First, check if game is too early (block early Q1 trading)
                    match_name = match.get("match")
                    early_game_block = _should_block_early_game_trading(period_clock, match_name, event_ticker=ticker)
                    
                    if early_game_block is True:
                        # Game is too early - block trading
                        parsed = _parse_period_clock(period_clock)
                        if parsed:
                            period, minutes_remaining = parsed
                            is_nba = ticker and str(ticker).startswith("KXNBAGAME-")
                            is_womens = "(W)" in str(match_name) if match_name else False
                            
                            if is_nba:
                                game_type = "NBA"
                                threshold = 7.0
                            elif is_womens:
                                game_type = "women's"
                                threshold = 5.0
                            else:
                                game_type = "men's"
                                threshold = 15.0
                            
                            print(f"⏱️ Trading blocked: {game_type} game in Q1 with {minutes_remaining:.1f} minutes remaining (wait until ≤{threshold:.0f} min remaining)", flush=True)
                        else:
                            # Fallback if parsing fails
                            print(f"⏱️ Trading blocked: game too early in Q1 (period_clock: {period_clock})", flush=True)
                        continue
                    elif early_game_block is None and VERBOSE:
                        # Debug: Show why block check returned None
                        print(f"🔍 Early game block check returned None (period_clock: {period_clock}, match_name: {match_name})", flush=True)
                    
                    # Then, try late game state-based restriction
                    game_state_block = _should_block_trading_by_game_time(period_clock, match_name, event_ticker=ticker)
                    
                    if game_state_block is True:
                        # Game state indicates we should block trading
                        if VERBOSE:
                            parsed = _parse_period_clock(period_clock)
                            if parsed:
                                period, minutes_remaining = parsed
                                is_nba = ticker and str(ticker).startswith("KXNBAGAME-")
                                is_womens = "(W)" in str(match_name) if match_name else False
                                
                                if is_nba:
                                    game_type = "NBA"
                                    period_label = "4th quarter"
                                    threshold = ODDS_FEED_EXIT_TIME_MINUTES
                                elif is_womens:
                                    game_type = "women's"
                                    period_label = "4th quarter"
                                    threshold = 8.0
                                else:
                                    game_type = "men's"
                                    period_label = "2nd half"
                                    threshold = 8.0
                                
                                print(f"⏱️ Trading blocked: {game_type} game in {period_label} with {minutes_remaining:.1f} minutes remaining (≤{threshold:.1f} min limit)")
                        continue
                    elif game_state_block is None:
                        # Game state unavailable - fall back to 70-minute time-based restriction
                        first_detection = get_first_detection_time(ticker)
                        if first_detection:
                            current_time = now_utc()
                            elapsed_seconds = (current_time - first_detection).total_seconds()
                            window_seconds = FIRST_TRADE_WINDOW_MINUTES * 60
                            remaining_seconds = window_seconds - elapsed_seconds
                            
                            if elapsed_seconds > window_seconds:
                                if VERBOSE:
                                    elapsed_min = elapsed_seconds / 60.0
                                    print(f"⏱️ First trade window expired: {elapsed_min:.1f} minutes elapsed since first detection (fallback: game state unavailable)")
                                continue
                            
                            # Display time info
                            elapsed_min = elapsed_seconds / 60.0
                            remaining_min = remaining_seconds / 60.0
                            first_detection_str = first_detection.strftime('%H:%M:%S')
                            print(f"⏱️ First trade window (fallback): {elapsed_min:.1f} min elapsed, {remaining_min:.1f} min remaining (first detected: {first_detection_str})")
                        else:
                            if VERBOSE:
                                print(f"⚠️ Could not determine first detection time for {ticker} — allowing trade (game state unavailable)")

                # 5.1 Minimum Kalshi execution price for first entries (matching testing_new_strategy.py)
                if kalshi_price < MIN_PRICE:
                    if VERBOSE:
                        print(f"⏭️ Skip first entry — price {kalshi_price:.2%} < {MIN_PRICE:.0%} min{score_time_info}")
                    continue

                # 5.1b Maximum Kalshi execution price for first entries (matching testing_new_strategy.py)
                if kalshi_price > MAX_PRICE:
                    if VERBOSE:
                        print(f"⏭️ Skip first entry — Kalshi execution price {kalshi_price:.2%} > {MAX_PRICE:.0%} max (won't place order above this price){score_time_info}")
                    continue

                # 5.2 EV threshold for first entry (matching testing_new_strategy.py)
                # ev_buy is already calculated earlier for spread filtering
                # Only trade when EV is between min and max thresholds
                if ev_buy is None or ev_buy < MIN_EV_THRESHOLD or ev_buy > MAX_EV_THRESHOLD:
                    if VERBOSE:
                        ev_display = ev_buy if ev_buy is not None else "N/A"
                        reason = "N/A" if ev_buy is None else ("too low" if ev_buy < MIN_EV_THRESHOLD else "too high")
                        print(f"⏭️ Skip first entry — ev={ev_display} ({reason}, must be between {MIN_EV_THRESHOLD:.2%} and {MAX_EV_THRESHOLD:.2%}){score_time_info}")
                    continue

                # 5.3 Kelly sizing for first entry
                quantity = compute_first_entry_quantity(
                    kelly_fraction=kelly_fraction,
                    kalshi_price=kalshi_price,
                    odds_prob=odds_prob,
                    capital=capital
                )
                if not quantity:
                    continue
                if (not has_event_position) and quantity < FIRST_ENTRY_MIN_QTY:
                    if VERBOSE:
                        print(
                            f"⏭️ Skip first entry — Kelly sizing {quantity} < MIN {FIRST_ENTRY_MIN_QTY} contracts."
                        )
                    continue
                if allow_add_on_entry:
                    if remaining_qty_cap < 1:
                        if VERBOSE:
                            print("🚫 Add-on blocked — no remaining capacity under MAX_STAKE_PCT.")
                        continue
                    if quantity > remaining_qty_cap:
                        if VERBOSE:
                            print("🔁 Trimming add-on size to stay under MAX_STAKE_PCT cap.")
                        quantity = remaining_qty_cap
                    
                    # ✅ SIMPLIFIED: Removed ROI bounds check for add-ons
                    # Add-ons now only respect exposure caps (MAX_STAKE_PCT)
                    # Profit protection will exit when combined position is profitable

                # ✅ Finalize — we place this first-side trade here
                # (place order code continues...)

            # ✅ Case 2: You already have one side (e.g. Norrie) and no hedge yet — stop first entries
            elif not is_hedge_context and one_sided_exposure and not allow_add_on_entry:
                if VERBOSE:
                    print(f"⚠️ {match['match']}: Already holding this side — add-ons disabled. Waiting for hedge/opposite setup.")
                continue

            # 🎯 Convert desired sizing into incremental contracts (subtract what we already hold)
            try:
                target_qty_total = int(quantity)
            except (TypeError, ValueError):
                if VERBOSE:
                    print("⚠️ Invalid quantity computed — skipping trade sizing.")
                continue
            if target_qty_total <= 0:
                if VERBOSE:
                    print("⚠️ Non-positive target size — skipping.")
                continue

            # Calculate held quantity (for hedges, use aggregated value if available)
            # For hedges, we already calculated total_hedge_stake_existing which aggregates all positions
            if is_hedge_context and total_hedge_stake_existing > 0:
                # Use aggregated quantity for hedges (accounts for multiple positions)
                held_qty_this_market = int(round(total_hedge_stake_existing))
            else:
                # For first entries, use single position quantity
                held_qty_this_market = 0
                if existing_pos and not existing_pos.get("settled", False):
                    try:
                        held_qty_this_market = int(round(float(existing_pos.get("stake", 0))))
                    except (TypeError, ValueError):
                        held_qty_this_market = 0

            incremental_qty = target_qty_total if held_qty_this_market <= 0 else target_qty_total - held_qty_this_market
            if incremental_qty <= 0:
                if VERBOSE:
                    print(
                        f"⚖️ {match['match']} {side_choice.upper()} — already holding "
                        f"{held_qty_this_market} contracts (target {target_qty_total}). No order needed."
                    )
                continue

            quantity = incremental_qty

            # 4) Exposure cap enforcement
            # --- Exposure cap enforcement with hedge exception ---
            violates, reason, max_qty = exposure_violation(
                market_ticker=market.get("ticker"),
                event_ticker=ticker,
                added_qty=quantity,
                entry_price=kalshi_price,
                capital=capital,
                is_hedge_trade=is_hedge_context
            )
            if violates:
                if max_qty > 0:
                    # Scale down to max allowed
                    if VERBOSE:
                        print(f"🔁 {reason}")
                    quantity = max_qty
                    # ✅ Safety check: Ensure scaled-down quantity still meets minimum for first entries
                    if not is_hedge_context and held_qty_this_market == 0 and quantity < FIRST_ENTRY_MIN_QTY:
                        if VERBOSE:
                            print(f"🚫 Skipping — scaled-down quantity {quantity} < MIN {FIRST_ENTRY_MIN_QTY} contracts for first entry")
                        continue
                else:
                    # Already at/over limit, can't place any
                    if VERBOSE:
                        print(f"🚫 Skipping — exposure violation: {reason}")
                    continue


            # Compute exit proxy for a YES entry: you'd sell NO at the NO bid
            yes_bid_f = format_price(market.get("yes_bid"))
            yes_ask_f = format_price(market.get("yes_ask"))
            no_bid_f  = (1.0 - yes_ask_f) if (yes_ask_f is not None) else ((1.0 - yes_bid_f) if (yes_bid_f is not None) else None)
            # Choose proper exit proxy for print/debug
            if side_choice == "yes":
                exit_proxy = no_bid_f


            # Debug print (round-trip aware)
            exit_proxy_str = "_" if exit_proxy is None else f"{exit_proxy:.2%}"
            if rt_ev is None:
                rt_ev_str = "nan"
            else:
                rt_ev_str = f"${rt_ev:.3f}" if USE_CONSERVATIVE_EV else f"{rt_ev:.2%}"

            qty_note = ""
            if held_qty_this_market > 0:
                qty_note = f" (target {target_qty_total})"

            print(
                f"odds_prob={odds_prob:.2%}, side={side_choice}, "
                f"entry={kalshi_price:.2%}, "
                f"exit_proxy={exit_proxy_str}, "
                f"rtEV={rt_ev_str}, "
                f"Kelly={kelly_fraction:.3f}, qty={quantity}{qty_note}"
            )

            log_eval({
                "event_ticker": ticker.upper(),
                "event_id": match.get("id"),  # Store event_id for odds lookup
                "market_ticker": market.get("ticker"),
                "match": match["match"],
                "side_label": side,  # Use actual team name
                "odds_prob": odds_prob,
                "yes_bid": format_price(market.get("yes_bid")),
                "yes_ask": format_price(market.get("yes_ask")),
                "kalshi_price": kalshi_price,
                "edge": "",
                "kelly_fraction": kelly_fraction,
                "spread": (
                    ((market.get("yes_ask") or 0) - (market.get("yes_bid") or 0)) / 100.0
                    if (market.get("yes_ask") is not None and market.get("yes_bid") is not None)
                    else None
                ),
                "cost_buffer": cost_buffer,
                "decision": side_choice or "pass"
            })

            books_used_list = match.get("books_used") or []
            books_weight_str = " | ".join(f"{name}:{_book_weight(name):.2f}" for name in books_used_list) if books_used_list else ""
            odds_snapshot = match.get("odds_feed") or {}
            
            # Parse period_clock into separate period and time_remaining (matching old file)
            period_clock_raw = odds_snapshot.get("period_clock", "")
            game_period = ""
            time_remaining = ""
            if period_clock_raw and " - " in period_clock_raw:
                parts = period_clock_raw.strip().split(" - ")
                if len(parts) == 2:
                    game_period = parts[0].strip()
                    time_remaining = parts[1].strip()
            
            log_backtest_feed({
                "match": match["match"],
                "event_ticker": ticker.upper(),
                "market_ticker": market.get("ticker"),
                "side_label": side,  # Use actual team name
                "books_used": " | ".join(books_used_list),
                "books_weights": books_weight_str,
                "books_sampled": odds_snapshot.get("books_sampled", ""),
                "home_prob": odds_snapshot.get("home_prob", ""),
                "away_prob": odds_snapshot.get("away_prob", ""),
                "odds_prob": odds_prob,
                "yes_bid": yes_bid_f,
                "yes_ask": yes_ask_f,
                "kalshi_mid": mid,
                "kalshi_price": kalshi_price,
                "spread": spread,
                "edge_pct": edge_pct,
                "ev_buy": ev_buy,  # Add ev_buy for logging (matching testing_new_strategy.py)
                "fair_ev": fair_ev,
                "cons_ev": cons_ev,
                "rt_ev": rt_ev,
                "kelly_fraction": kelly_fraction,
                "volatility_mode": volatility_mode,
                "capital": capital,
                "min_qty_required": min_qty_required or "",
                "planned_qty": quantity,
                "has_event_position": has_event_position,
                "is_hedge": is_hedge_context,
                "decision": side_choice or "pass",
                "cost_buffer": cost_buffer,
                "score_snapshot": odds_snapshot.get("score_snapshot", ""),
                "game_period": game_period,
                "time_remaining": time_remaining,
            })

            # === Entry condition ===
            # === Entry condition ===
            # === Entry condition ===

            # === HEDGING: Prevent Over-Exposure on One Side ===
            # === HEDGING: Prevent Over-Exposure on One Side ===
            # === HEDGING: Prevent Over-Exposure on One Side ===
            # === HEDGING: Prevent Over-Exposure on One Side (Clean Version) ===
            # No fee added at entry; handled later in EV and PnL calculations
            if side_choice == "yes":
                effective_entry = kalshi_price
            else:
                effective_entry = 1 - kalshi_price

                ######
                # 🛒 Try passive entry first (sit on bid/ask ±1¢)
            ######
            # 🛒 Passive entry — sit *behind* current top of book (1¢ below bid)
            # --- Volatility-based entry price ---

            # Clamp to [0,1]
            entry_price = max(0.0, min(1.0, entry_price))

            def fmt_pct(x):
                return f"{x:.2%}" if isinstance(x, (float, int)) else "N/A"

            print(
                f"🛒 Posting *behind* top-of-book {side_choice.upper()} order "
                f"@ {fmt_pct(entry_price)} (bid={fmt_pct(yb)}, ask={fmt_pct(ya)})"
            )

            # 🚫 Final safety: only allow YES entries
            if side_choice.lower() != "yes":
                print(f"⛔ Blocked non-YES order attempt for {match['match']} (side={side_choice})")
                continue

            # 🚨 FINAL SAFETY: Block any new trade if event already has one side open and no hedge
            event_positions = [
                p for p in positions
                if normalize_event_ticker(p.get("event_ticker", "")) == normalize_event_ticker(ticker)
                and not p.get("settled", False)
            ]

            # If exactly one side open and it's not neutralized → block
            if (
                len(event_positions) == 1
                and not event_is_neutralized(ticker)
                and not is_hedge_context
                and not allow_add_on_entry
            ):
                print(f"🚫 FINAL BLOCK — {match['match']} already has one open side ({event_positions[0]['market_ticker']}), no hedge yet.")
                continue

            
            # 🔸 PART 6: After we have previously neutralized this event, re-apply directional cap to any new one-sided add
            evt_neutralized = any(
                p.get("neutralized") for p in positions if event_key(p.get("event_ticker")) == ticker_key
            )
            # Check if event is hedged (both sides exist) - more robust check
            event_is_hedged = event_is_neutralized(ticker)
            
            # Block one-sided additions to hedged positions if pyramiding is disabled
            # When event is already hedged (both sides exist), we're adding to existing hedge, not creating new one
            # Check if we already have a position on this side (meaning we're adding to existing, not creating hedge)
            has_position_on_this_side = any(
                p.get("market_ticker") == market.get("ticker")
                for p in event_positions
            )
            
            # If event is hedged AND we already have a position on this side, it's a one-sided add to existing hedge
            if (evt_neutralized or event_is_hedged) and has_position_on_this_side:
                # Check master pyramiding flag
                if not PYRAMIDING_ENABLED:
                    print(f"⏭️ Skip {match['match']} {side} — PYRAMIDING_ENABLED=False (preventing all pyramiding)")
                    METRICS["skip_counts"]["pyramiding_disabled"] = METRICS["skip_counts"].get("pyramiding_disabled", 0) + 1
                    continue
                
                # Block if pyramiding after hedge is disabled
                if not ALLOW_PYRAMID_AFTER_HEDGE:
                    print(f"⏭️ Skip {match['match']} {side} — already hedged and ALLOW_PYRAMID_AFTER_HEDGE=False (preventing one-sided additions to hedged positions)")
                    METRICS["skip_counts"]["pyramid_after_hedge_disabled"] = METRICS["skip_counts"].get("pyramid_after_hedge_disabled", 0) + 1
                    continue
                
                existing_exposure_evt = sum(
                    p["stake"] * p["entry_price"]
                    for p in positions
                    if event_key(p.get("event_ticker")) == ticker_key
                )
                future_exposure_evt = existing_exposure_evt + (quantity * kalshi_price)
                max_allowed_evt = capital * MAX_TOTAL_EXPOSURE_PCT
                if future_exposure_evt > max_allowed_evt:
                    print(f"⏭️ Skip {match['match']} {side} — post-hedge directional cap exceeded "
                          f"(${future_exposure_evt:.2f} > ${max_allowed_evt:.2f})")
                    METRICS["skip_counts"]["post_hedge_cap_exceeded"] = METRICS["skip_counts"].get("post_hedge_cap_exceeded", 0) + 1
                    continue

            # ✅ Exposure check before submitting any order
            # ✅ Exposure check before submitting any order (hedge-aware)
            violates, reason, max_qty = exposure_violation(
                market_ticker=market.get("ticker"),
                event_ticker=ticker,
                added_qty=quantity,
                entry_price=kalshi_price,
                capital=capital,
                is_hedge_trade=is_hedge_context
            )
            if violates:
                if max_qty > 0:
                    # Scale down to max allowed
                    if VERBOSE:
                        print(f"🔁 {reason}")
                    quantity = max_qty
                    # ✅ Safety check: Ensure scaled-down quantity still meets minimum for first entries
                    # (For hedges/add-ons, minimum check is not required)
                    if not is_hedge_context and quantity < FIRST_ENTRY_MIN_QTY:
                        # Check if this is a first entry (no existing position on this market)
                        existing_pos_this_market = next(
                            (p for p in positions 
                             if p.get("market_ticker") == market.get("ticker") 
                             and not p.get("settled", False)),
                            None
                        )
                        if not existing_pos_this_market:
                            print(f"⏭️ Skip {match['match']} {side} — scaled-down quantity {quantity} < MIN {FIRST_ENTRY_MIN_QTY} contracts for first entry")
                            METRICS["skip_counts"]["exposure_violation"] = METRICS["skip_counts"].get("exposure_violation", 0) + 1
                            continue
                else:
                    # Already at/over limit, can't place any
                    print(f"⏭️ Skip {match['match']} {side} — exposure violation: {reason}")
                    METRICS["skip_counts"]["exposure_violation"] = METRICS["skip_counts"].get("exposure_violation", 0) + 1
                    continue

            # ✅ Spread validation: Check if spread is too wide before placing order
            # Recalculate spread from current market data to ensure it's fresh
            yb_check = format_price(market.get("yes_bid"))
            ya_check = format_price(market.get("yes_ask"))
            if yb_check is not None and ya_check is not None:
                current_spread = abs(ya_check - yb_check)
                if current_spread > MAX_SPREAD:
                    print(f"⏭️ Skip {match['match']} {side} — spread too wide before order: {current_spread:.2%} > {MAX_SPREAD:.2%} "
                          f"(bid={yb_check:.2%}, ask={ya_check:.2%}, entry_price={kalshi_price:.2%})")
                    METRICS["missed_wide_spread"] += 1
                    METRICS["skip_counts"]["spread_too_wide_pre_order"] = METRICS["skip_counts"].get("spread_too_wide_pre_order", 0) + 1
                    continue
            elif yb_check is None or ya_check is None:
                # Missing bid or ask - skip to avoid placing orders in illiquid markets
                print(f"⏭️ Skip {match['match']} {side} — missing bid/ask before order (bid={yb_check}, ask={ya_check})")
                METRICS["skip_counts"]["missing_bid_ask_pre_order"] = METRICS["skip_counts"].get("missing_bid_ask_pre_order", 0) + 1
                if VERBOSE:
                    print(f"🚫 Skipping — missing bid/ask (bid={yb_check}, ask={ya_check})")
                continue

            # Price range already checked earlier for hedges, but double-check as safety
            if is_hedge_context and not (HEDGE_PRICE_MIN <= kalshi_price <= HEDGE_PRICE_MAX):
                if VERBOSE:
                    print(
                        f"🚫 Hedge price {kalshi_price:.2%} outside [{HEDGE_PRICE_MIN:.2%}, {HEDGE_PRICE_MAX:.2%}] — skip (safety check)"
                    )
                METRICS["missed_hedge_band"] += 1
                continue

            filled_qty = 0
            # Use the actual team name (side contains the team name like "Duke" or "UNC")
            side_name = side  # e.g., "Duke" or "UNC"
            position = {
                "match": match["match"],
                "side": side_choice,
                "side_name": side_name,  # Actual team name
                "event_ticker": ticker.upper(),
                "event_id": match.get("id"),  # Store event_id for odds lookup
                "market_ticker": market.get("ticker"),
                "entry_price": kalshi_price,
                "odds_prob": odds_prob,
                "entry_time": now_utc().isoformat(),
                "effective_entry": effective_entry,
                "max_price": kalshi_price,
                "stake": filled_qty,
                "peak_pnl_pct": 0.0,
                "peak_hit_ts": None,
                "breakeven_armed": False,
                "next_exit_after": now_utc().isoformat(),
                "yes_sub_title": market.get("yes_sub_title"),
            }
            if hedge_band_for_new_pos:
                ql_i, qh_i = hedge_band_for_new_pos
                position["q_low"] = ql_i
                position["q_high"] = qh_i

            _bump_fill("placed")

            # 🛡️ FINAL SAFETY CHECK: Verify position state before placing order
            # Get fresh live positions to ensure we're not about to create a duplicate
            if PLACE_LIVE_KALSHI_ORDERS == "YES":
                try:
                    live_check_positions = get_live_positions()
                    live_qty_on_market = sum(
                        p["contracts"] for p in live_check_positions 
                        if p["ticker"] == market.get("ticker") and p["side"] == "yes"
                    )
                    
                    # Calculate what we think we should have based on local positions
                    local_qty_on_market = sum(
                        p.get("stake", 0) for p in positions
                        if p.get("market_ticker") == market.get("ticker") 
                        and p.get("side") == "yes"
                        and not p.get("settled", False)
                    )
                    
                    # If live quantity already matches or exceeds our target, skip this order
                    if live_qty_on_market >= target_qty_total:
                        print(f"🛡️ DUPLICATE PREVENTION: Already have {live_qty_on_market} contracts on {market.get('ticker')} "
                              f"(target was {target_qty_total}). Skipping order to prevent duplicate trade.")
                        continue
                    
                    # If there's a mismatch between local and live, warn but allow (could be pending fill)
                    if local_qty_on_market != live_qty_on_market:
                        print(f"⚠️ Position mismatch detected: Local={local_qty_on_market}, Live={live_qty_on_market} for {market.get('ticker')}")
                        # Adjust quantity to match what we actually need
                        adjusted_qty = target_qty_total - live_qty_on_market
                        if adjusted_qty <= 0:
                            print(f"🛡️ Already at target quantity on live exchange. Skipping order.")
                            continue
                        if adjusted_qty != quantity:
                            print(f"📊 Adjusting order quantity from {quantity} to {adjusted_qty} to match live state")
                            quantity = adjusted_qty
                
                except Exception as e:
                    print(f"⚠️ Could not verify live positions before order: {e}")
                    # Continue with order anyway - don't block on verification errors

            # ✅ Check if odds have been updated on this turn before placing new entry orders
            # (Allow exits/stop-losses and hedging even if odds haven't updated, but block new first entries)
            if REQUIRE_ODDS_UPDATE_FOR_TRADES and match.get("_skip_new_entries", False) and not is_hedge_context:
                if VERBOSE:
                    print(f"⏸️ Skipping new entry order for {match['match']} — odds not updated on this turn (hedging allowed)")
                continue

            # Block NBA trades if NBA trading is disabled
            if is_nba_blocked:
                if VERBOSE:
                    print(f"🚫 Blocking trade for {match['match']} {side} — NBA trading is disabled (monitoring only)")
                continue

            # Use safe_prepare_kalshi_order to guard against accidental oversizing if
            # prior orders filled while cancels were in-flight. Cap total live size
            # on this market at target_qty_total.
            resp = safe_prepare_kalshi_order(
                market_ticker=market.get("ticker"),
                side="yes",   # force YES explicitly
                price=kalshi_price,
                quantity=quantity,              # final contract size for this attempt
                max_total_contracts=target_qty_total,
                action="buy",
            )

            order_id, client_oid = _extract_order_id(resp)
            if not order_id:
                print("⚠️ Could not parse order_id; skipping position.")
                continue

            # Wait for fill or cancel — do NOT cross if unfilled
            status, filled_qty = wait_for_fill_or_cancel(
                order_id,
                client_order_id=client_oid,
                timeout_s=ORDER_FILL_TIME,
                poll_s=1.0,
                expected_count=quantity,
                require_full=False,
                verify_ticker=market.get("ticker"),
                verify_side="yes"
            )

            if filled_qty <= 0:
                _bump_fill("timeout_cancel")
                print("⚠️ Passive entry not filled — cancelling and waiting for next recalculation.")
                continue

            if status == "filled" and filled_qty > 0:
                _bump_fill("filled")
            else:
                _bump_fill("timeout_cancel")
                print("⚠️ Passive entry not filled — cancelling and waiting for next recalculation.")
                continue

            # --- Slippage measurement (mid vs filled) ---
            mid = ((yb if yb is not None else kalshi_price) + (ya if ya is not None else kalshi_price)) / 2.0
            filled_price = kalshi_price  # unless you parse avg fill from response
            slip_bps = 10000.0 * (filled_price - mid)
            METRICS["avg_slippage_bps_sum"] += slip_bps
            METRICS["avg_slippage_bps_n"] += 1
            # --------------------------------------------

            ####################
            position["stake"] = filled_qty
            commit_trade_and_persist(position, order_id, filled_qty)
            set_event_neutralization_flags(position["event_ticker"])
            update_event_lock(position["event_ticker"])
            print(f"✅ LIVE TRADE RECORDED — {match['match']} {side_name} x{filled_qty} @ {kalshi_price:.2%}")
            report_event_hedge_bands(position["event_ticker"], kalshi, match["match"])

            ####################

            log_backtest_metrics({
                "match": match["match"],
                "market_ticker": market.get("ticker"),
                "side": side_choice,
                "entry_price": kalshi_price,
                "odds_prob": odds_prob,
                "spread": spread,
                "fair_ev": fair_ev,
                "kelly_fraction": kelly_fraction,
                "volatility_mode": volatility_mode,
                "stake": filled_qty
            })

            # ✅ Cross-verify it really exists on Kalshi right now
            try:
                live_positions = get_live_positions()
                found = next(
                    (p for p in live_positions
                    if p["ticker"] == position["market_ticker"] and p["side"] == position["side"]),
                    None
                )
                if found:
                    print(f"🔁 Verified live fill on Kalshi: {found['ticker']} | {found['side']} x{found['contracts']} @ {found['avg_price']:.2%}")
                else:
                    print("⚠️ Not yet visible in live positions (may take a few seconds). Will reconcile soon.")
            except Exception as e:
                print(f"⚠️ Live verification failed: {e}")

            # ✅ Update _LAST_RECONCILE_TS to avoid immediate overwrite
            global _LAST_RECONCILE_TS
            _LAST_RECONCILE_TS = time.time()
        
        # Close table for this match
        print(f"{'═' * 120}\n")

    # === Exit logic with Profit Protection ===
    # === Profit Protection: Max Profit Detection + Trailing Stop (Pyramiding-Aware) ===
    
    # Create mapping from event_ticker to match data for period_clock and match_name lookup
    event_to_match = {}
    for match in overlaps:
        ticker = match.get("ticker")
        if ticker:
            evt_key = event_key(ticker)
            event_to_match[evt_key] = {
                "match_name": match.get("match"),
                "period_clock": (match.get("odds_feed") or {}).get("period_clock")
            }
    
    new_positions = []
    positions_to_close = []
    positions_to_close_keys = set()
    
    # Group positions by event (including already-flagged closes so we can retry/finish them)
    events_dict = {}
    for pos in positions:
        if pos.get("settled", False):
            continue
        # ✅ Skip positions that are already being closed (prevent double-selling)
        if pos.get("closing_in_progress", False):
            continue
        evt_key = event_key(pos.get("event_ticker"))
        if evt_key not in events_dict:
            events_dict[evt_key] = []
        events_dict[evt_key].append(pos)
    
    # Get market data for all events needing checks
    kalshi_markets_cache = {}
    for evt_ticker in events_dict.keys():
        # Get event ticker from first position in event
        if events_dict[evt_ticker]:
            evt_ticker_str = events_dict[evt_ticker][0].get("event_ticker")
            if evt_ticker_str:
                try:
                    mkts = get_kalshi_markets(evt_ticker_str, force_live=True)
                    if mkts:
                        kalshi_markets_cache[evt_ticker] = mkts
                except Exception as e:
                    if VERBOSE:
                        print(f"⚠️ Could not fetch markets for profit protection check on {evt_ticker_str}: {e}")
    
    # Check each event for profit protection
    for evt_key, event_positions in events_dict.items():
        # Skip NBA positions if NBA trading is disabled
        evt_ticker_str = event_positions[0].get("event_ticker", "") if event_positions else ""
        if evt_ticker_str and str(evt_ticker_str).startswith("KXNBAGAME-") and not ENABLE_NBA_TRADING:
            if VERBOSE:
                print(f"🚫 Skipping profit protection for NBA event {evt_ticker_str} - NBA trading is disabled")
            # Keep positions but don't process profit protection exits
            new_positions.extend(event_positions)
            continue
        
        # Find the two sides (markets)
        markets_by_ticker = {}
        for pos in event_positions:
            mkt_ticker = pos.get("market_ticker")
            if mkt_ticker not in markets_by_ticker:
                markets_by_ticker[mkt_ticker] = []
            markets_by_ticker[mkt_ticker].append(pos)
        
        market_tickers = list(markets_by_ticker.keys())

        # If this event is already in closing state (e.g., partial fills, restart), re-queue closes now
        event_has_closing_flag = any(p.get("closing_in_progress", False) for p in event_positions)
        if event_has_closing_flag:
            markets = kalshi_markets_cache.get(evt_key, [])
            # Require fresh market data and both sides to justify selling; otherwise clear flag and keep evaluating
            if len(market_tickers) >= 2 and markets:
                side_A_ticker = market_tickers[0]
                side_B_ticker = market_tickers[1]
                side_A_market = next((m for m in markets if m.get("ticker") == side_A_ticker), None)
                side_B_market = next((m for m in markets if m.get("ticker") == side_B_ticker), None)
                side_A_bid = format_price(side_A_market.get("yes_bid")) if side_A_market else None
                side_B_bid = format_price(side_B_market.get("yes_bid")) if side_B_market else None

                if side_A_bid is not None and side_B_bid is not None:
                    side_A_sell_price = max(0.01, side_A_bid - TICK)
                    side_B_sell_price = max(0.01, side_B_bid - TICK)
                    
                    # Get ask prices for spread check
                    side_A_ask = format_price(side_A_market.get("yes_ask")) if side_A_market else None
                    side_B_ask = format_price(side_B_market.get("yes_ask")) if side_B_market else None
                    
                    # Get match data for time restrictions
                    match_data = event_to_match.get(evt_key, {})
                    period_clock = match_data.get("period_clock")
                    match_name = match_data.get("match_name")
                    
                    check_result = check_profit_protection(
                        event_positions[0].get("event_ticker", ""),
                        markets_by_ticker.get(side_A_ticker, []),
                        markets_by_ticker.get(side_B_ticker, []),
                        side_A_ticker,
                        side_B_ticker,
                        side_A_sell_price,
                        side_B_sell_price,
                        side_A_ask=side_A_ask,
                        side_B_ask=side_B_ask,
                        side_A_bid=side_A_bid,
                        side_B_bid=side_B_bid,
                        period_clock=period_clock,
                        match_name=match_name
                    )
                    if not check_result.get("should_close", False):
                        # Clear stale closing flags; keep positions active
                        for pos in event_positions:
                            pos["closing_in_progress"] = False
                            pos.pop("closing_check_result", None)
                            pos.pop("closing_initiated_at", None)
                        new_positions.extend(event_positions)
                        continue
                    
                    # Check if this is a partial exit
                    partial_exit_side = check_result.get("partial_exit_side")
                    if partial_exit_side:
                        # Partial exit: only mark the specified side for closing
                        side_to_close_ticker = side_A_ticker if partial_exit_side == "A" else side_B_ticker
                        positions_to_close_list = markets_by_ticker.get(side_to_close_ticker, [])
                        positions_to_keep_list = markets_by_ticker.get(side_B_ticker, []) if partial_exit_side == "A" else markets_by_ticker.get(side_A_ticker, [])
                        
                        # Mark ALL positions on the side to close (in case of multiple entries from pyramiding)
                        # The live position check will ensure we don't sell more than available
                        for pos in positions_to_close_list:
                            pos["closing_in_progress"] = True
                            pos.setdefault("closing_initiated_at", time.time())
                            pos["closing_check_result"] = check_result.copy()
                            key = (pos.get("market_ticker"), pos.get("side"))
                            # Add to queue even if key exists (multiple positions on same side need all to be closed)
                            # The live position check will prevent double-selling
                            if key not in positions_to_close_keys:
                                positions_to_close_keys.add(key)
                            positions_to_close.append(pos)
                        
                        # Keep the other side open
                        for pos in positions_to_keep_list:
                            pos["closing_in_progress"] = False
                            pos.pop("closing_check_result", None)
                            pos.pop("closing_initiated_at", None)
                        new_positions.extend(positions_to_keep_list)
                        
                        save_positions()
                        continue
                else:
                    # Missing bids - cannot safely justify selling; clear flags
                    for pos in event_positions:
                        pos["closing_in_progress"] = False
                        pos.pop("closing_check_result", None)
                        pos.pop("closing_initiated_at", None)
                    new_positions.extend(event_positions)
                    continue

            # Full exit (not partial) - check if we should still close all
            shared_check = next((p.get("closing_check_result") for p in event_positions if p.get("closing_check_result")), None)
            if shared_check and shared_check.get("partial_exit_side"):
                # This was a partial exit - handle it separately
                partial_exit_side = shared_check.get("partial_exit_side")
                side_to_close_ticker = side_A_ticker if partial_exit_side == "A" else side_B_ticker
                positions_to_close_list = markets_by_ticker.get(side_to_close_ticker, [])
                positions_to_keep_list = markets_by_ticker.get(side_B_ticker, []) if partial_exit_side == "A" else markets_by_ticker.get(side_A_ticker, [])
                
                # Mark ALL positions on the side to close (in case of multiple entries from pyramiding)
                # The live position check will ensure we don't sell more than available
                for pos in positions_to_close_list:
                    pos["closing_in_progress"] = True
                    pos.setdefault("closing_initiated_at", time.time())
                    if not pos.get("closing_check_result"):
                        pos["closing_check_result"] = shared_check.copy()
                    key = (pos.get("market_ticker"), pos.get("side"))
                    # Add to queue even if key exists (multiple positions on same side need all to be closed)
                    # The live position check will prevent double-selling
                    if key not in positions_to_close_keys:
                        positions_to_close_keys.add(key)
                    positions_to_close.append(pos)
                
                # Keep the other side open
                for pos in positions_to_keep_list:
                    pos["closing_in_progress"] = False
                    pos.pop("closing_check_result", None)
                    pos.pop("closing_initiated_at", None)
                new_positions.extend(positions_to_keep_list)
                save_positions()
                continue
            
            # Full exit - close all positions
            for pos in event_positions:
                # Ensure flags/timestamps exist so stale detection works
                pos["closing_in_progress"] = True
                pos.setdefault("closing_initiated_at", time.time())
                if shared_check and not pos.get("closing_check_result"):
                    pos["closing_check_result"] = shared_check.copy()
                key = (pos.get("market_ticker"), pos.get("side"))
                if key not in positions_to_close_keys:
                    positions_to_close_keys.add(key)
                    positions_to_close.append(pos)
            save_positions()
            continue
        
        if len(market_tickers) < 2:
            # Not hedged, keep position (or use other exit logic if needed)
            new_positions.extend(event_positions)
            continue
        
        # We have both sides (hedged) - check profit protection
        side_A_ticker = market_tickers[0]
        side_B_ticker = market_tickers[1]
        side_A_positions = markets_by_ticker[side_A_ticker]
        side_B_positions = markets_by_ticker[side_B_ticker]
        
        # Get current market prices
        evt_ticker_str = event_positions[0].get("event_ticker")
        markets = kalshi_markets_cache.get(evt_key, [])
        
        if not markets:
            # Can't check without market data, keep positions
            new_positions.extend(event_positions)
            continue
        
        side_A_market = next((m for m in markets if m.get("ticker") == side_A_ticker), None)
        side_B_market = next((m for m in markets if m.get("ticker") == side_B_ticker), None)
        
        if not side_A_market or not side_B_market:
            # Can't check without market data, keep positions
            new_positions.extend(event_positions)
            continue
        
        side_A_bid = format_price(side_A_market.get("yes_bid"))
        side_B_bid = format_price(side_B_market.get("yes_bid"))

        # Track max live price achieved (use bid as the sellable price proxy)
        if side_A_bid is not None:
            for p in side_A_positions:
                p["max_price"] = max(p.get("max_price", 0.0), float(side_A_bid))
        if side_B_bid is not None:
            for p in side_B_positions:
                p["max_price"] = max(p.get("max_price", 0.0), float(side_B_bid))
        
        if side_A_bid is None or side_B_bid is None:
            # Can't calculate profit without prices, keep positions
            new_positions.extend(event_positions)
            continue
        
        # Calculate actual sell prices (bid - 1 tick) - what we'd actually get when selling
        side_A_sell_price = max(0.01, side_A_bid - TICK) if side_A_bid is not None else None
        side_B_sell_price = max(0.01, side_B_bid - TICK) if side_B_bid is not None else None
        
        # Get ask prices for spread check
        side_A_ask = format_price(side_A_market.get("yes_ask"))
        side_B_ask = format_price(side_B_market.get("yes_ask"))
        
        # Get match data for time restrictions
        match_data = event_to_match.get(evt_key, {})
        period_clock = match_data.get("period_clock")
        match_name = match_data.get("match_name")
        
        # Run profit protection check (using actual sell prices - what you'd get when selling)
        check_result = check_profit_protection(
            evt_ticker_str,
            side_A_positions,
            side_B_positions,
            side_A_ticker,
            side_B_ticker,
            side_A_sell_price,
            side_B_sell_price,
            side_A_ask=side_A_ask,
            side_B_ask=side_B_ask,
            side_A_bid=side_A_bid,
            side_B_bid=side_B_bid,
            period_clock=period_clock,
            match_name=match_name
        )
        
        # Log status (even if not closing) - Enhanced display with full breakdown
        if VERBOSE and check_result:  # Show always, even if negative
            current = check_result['current_profit_pct']
            peak = check_result.get('peak_profit_pct', 0)
            max_pct = check_result.get('max_profit_pct', 0)
            settlement = check_result.get('settlement_roi', 0)
            settlement_min = check_result.get('settlement_roi_min', 0)
            
            # Calculate ratio to max
            ratio_to_max = (current / max_pct * 100) if max_pct > 0 else 0
            
            # Get probability breakdown
            prob_A = check_result.get('prob_A', 0)
            prob_B = check_result.get('prob_B', 0)
            roi_A = check_result.get('roi_A', 0)
            roi_B = check_result.get('roi_B', 0)
            
            # Get target prices
            target_A = check_result.get("target_price_A")
            target_B = check_result.get("target_price_B")
            
            # Build detailed log
            print(f"💰 {evt_key}:")
            print(f"   Current: {current:.2%} (peak: {peak:.2%}, {ratio_to_max:.0f}% of max)")
            print(f"   Settlement: {settlement:.2%} weighted (min: {settlement_min:.2%})")
            print(f"   Outcomes: A={roi_A:.2%} ({prob_A:.1%} likely), B={roi_B:.2%} ({prob_B:.1%} likely)")
            if target_A is not None and target_B is not None:
                print(f"   Target prices for max: A={target_A:.2%}, B={target_B:.2%}")
            print(f"   Pyramiding: {'YES' if check_result.get('is_pyramiding') else 'NO'}")
        
        if check_result.get("should_close", False):
            # Check if this is a partial exit (odds feed rule) or full exit
            partial_exit_side = check_result.get("partial_exit_side")
            
            if partial_exit_side:
                # Partial exit: only close the specified side (A or B)
                side_to_close_ticker = side_A_ticker if partial_exit_side == "A" else side_B_ticker
                positions_to_close_list = side_A_positions if partial_exit_side == "A" else side_B_positions
                positions_to_keep_list = side_B_positions if partial_exit_side == "A" else side_A_positions
                
                # Mark ALL positions on the side to close (in case of multiple entries from pyramiding)
                # The live position check will ensure we don't sell more than available
                for pos in positions_to_close_list:
                    # 7% exit bypasses unhedged hold time check - it should execute immediately
                    is_7pct_exit = check_result.get("kalshi_price_triggered", False)
                    
                    # Check if position is unhedged and hasn't been held for minimum time
                    # Skip this check for 7% exits (they should execute no matter what)
                    pos_is_hedged = pos.get("neutralized", False) or event_is_neutralized(evt_ticker_str)
                    if not pos_is_hedged and not is_7pct_exit:
                        entry_time_str = pos.get("entry_time")
                        if entry_time_str:
                            try:
                                entry_time = parse_iso_utc(entry_time_str)
                                if entry_time:
                                    entry_ts = entry_time.timestamp()
                                    current_ts = time.time()
                                    hold_duration = current_ts - entry_ts
                                    
                                    if hold_duration < MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS:
                                        remaining_seconds = int(MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS - hold_duration)
                                        if VERBOSE:
                                            print(f"🚫 Cannot sell unhedged position {pos.get('market_ticker')} - only held for {hold_duration:.0f}s, need {MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS}s minimum ({remaining_seconds}s remaining)")
                                        # Skip this position - don't mark for closing
                                        continue
                            except Exception as e:
                                if VERBOSE:
                                    print(f"⚠️ Error checking entry time for {pos.get('market_ticker')}: {e}")
                                # If we can't parse time, be conservative and allow (don't block)
                    
                    if not pos.get("closing_in_progress", False):
                        pos["closing_in_progress"] = True
                    pos.setdefault("closing_initiated_at", time.time())
                    pos["closing_check_result"] = check_result.copy()
                    key = (pos.get("market_ticker"), pos.get("side"))
                    # Add to queue even if key exists (multiple positions on same side need all to be closed)
                    # The live position check will prevent double-selling
                    if key not in positions_to_close_keys:
                        positions_to_close_keys.add(key)
                    positions_to_close.append(pos)
                
                # Keep the other side open
                new_positions.extend(positions_to_keep_list)
                
                # Save immediately to persist closing flags
                save_positions()
                reason = check_result.get("reason", "odds_feed_partial_exit")
                side_name = "A" if partial_exit_side == "A" else "B"
                other_side_name = "B" if partial_exit_side == "A" else "A"
                
                # Record which market ticker exited at 7% to disable stop loss on the other side
                if check_result.get("kalshi_price_triggered", False):
                    exited_ticker = side_to_close_ticker  # Store the actual market ticker that exited
                    EVENT_7PCT_EXITED_SIDE[evt_key] = exited_ticker
                    if VERBOSE:
                        print(f"📝 Recorded 7% exit on market {exited_ticker} (side {partial_exit_side}) for event {evt_key} - stop loss disabled on other side")
                
                print(f"🔒 ODDS FEED PARTIAL EXIT for {evt_key}: {reason} - closing side {side_name} only, keeping side {other_side_name} open")
            else:
                # Full exit: close both sides (normal profit protection)
                for pos in event_positions:
                    # Check if position is unhedged and hasn't been held for minimum time
                    pos_is_hedged = pos.get("neutralized", False) or event_is_neutralized(evt_ticker_str)
                    if not pos_is_hedged:
                        entry_time_str = pos.get("entry_time")
                        if entry_time_str:
                            try:
                                entry_time = parse_iso_utc(entry_time_str)
                                if entry_time:
                                    entry_ts = entry_time.timestamp()
                                    current_ts = time.time()
                                    hold_duration = current_ts - entry_ts
                                    
                                    if hold_duration < MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS:
                                        remaining_seconds = int(MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS - hold_duration)
                                        if VERBOSE:
                                            print(f"🚫 Cannot sell unhedged position {pos.get('market_ticker')} - only held for {hold_duration:.0f}s, need {MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS}s minimum ({remaining_seconds}s remaining)")
                                        # Skip this position - don't mark for closing
                                        continue
                            except Exception as e:
                                if VERBOSE:
                                    print(f"⚠️ Error checking entry time for {pos.get('market_ticker')}: {e}")
                                # If we can't parse time, be conservative and allow (don't block)
                    
                    if not pos.get("closing_in_progress", False):
                        pos["closing_in_progress"] = True
                    pos.setdefault("closing_initiated_at", time.time())  # Track when closing started
                    pos["closing_check_result"] = check_result.copy()  # Store check result for re-validation
                    key = (pos.get("market_ticker"), pos.get("side"))
                    if key not in positions_to_close_keys:
                        positions_to_close_keys.add(key)
                        positions_to_close.append(pos)
                # Save immediately to persist closing flags (prevents double-selling on restart)
                save_positions()
                reason = check_result.get("reason", "profit_protection")
                print(f"🔒 Closing hedged position for {evt_key}: {reason} "
                      f"(profit={check_result['current_profit_pct']:.2%})")
                # Clean up peak tracking when closing positions
                peak_key = f"{evt_key}_peak"
                if peak_key in _PEAK_PROFITS:
                    del _PEAK_PROFITS[peak_key]
        else:
            # Keep positions
            new_positions.extend(event_positions)
    
    # Execute closes - place actual sell orders at bid - 1 tick for aggressive fill
    for pos in positions_to_close:
        # Double-selling protection: verify position is still marked as closing
        if not pos.get("closing_in_progress", False):
            if VERBOSE:
                print(f"⚠️ Skipping {pos.get('market_ticker')} - closing flag was cleared")
            continue
        
        # Additional safety check: prevent selling unhedged positions before minimum hold time
        evt_ticker = pos.get("event_ticker")
        if evt_ticker:
            pos_is_hedged = pos.get("neutralized", False) or event_is_neutralized(evt_ticker)
            if not pos_is_hedged:
                # Position is unhedged - check minimum hold time
                entry_time_str = pos.get("entry_time")
                if entry_time_str:
                    try:
                        entry_time = parse_iso_utc(entry_time_str)
                        if entry_time:
                            entry_ts = entry_time.timestamp()
                            current_ts = time.time()
                            hold_duration = current_ts - entry_ts
                            
                            if hold_duration < MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS:
                                remaining_seconds = int(MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS - hold_duration)
                                if VERBOSE:
                                    print(f"🚫 BLOCKED: Cannot sell unhedged position {pos.get('market_ticker')} - only held for {hold_duration:.0f}s, need {MIN_HOLD_BEFORE_SELL_UNHEDGED_SECONDS}s minimum ({remaining_seconds}s remaining)")
                                pos["closing_in_progress"] = False
                                pos.pop("closing_check_result", None)
                                pos.pop("closing_initiated_at", None)
                                continue
                    except Exception as e:
                        if VERBOSE:
                            print(f"⚠️ Error checking entry time for {pos.get('market_ticker')}: {e}")
                        # If we can't parse time, allow the sell (don't block on errors)
        
        # Check for stale closing flags (older than 5 minutes - likely from previous run)
        closing_initiated_at = pos.get("closing_initiated_at")
        if closing_initiated_at:
            age_seconds = time.time() - closing_initiated_at
            if age_seconds > 300:  # 5 minutes
                if VERBOSE:
                    print(f"⚠️ Skipping {pos.get('market_ticker')} - closing flag is stale ({age_seconds:.0f}s old)")
                pos["closing_in_progress"] = False
                pos.pop("closing_check_result", None)
                pos.pop("closing_initiated_at", None)
                continue
        
        market_ticker = pos.get("market_ticker")
        stake_to_sell = int(pos.get("stake", 0))
        
        if stake_to_sell <= 0:
            # Already closed or invalid
            pos["settled"] = True
            pos["closing_in_progress"] = False  # Clear flag
            continue
        
        # CRITICAL: Refresh market prices right before selling (don't use stale cache)
        evt_key = event_key(pos.get("event_ticker"))
        evt_ticker_str = pos.get("event_ticker")
        
        try:
            # Fetch fresh market data - don't use stale cache
            fresh_markets = get_kalshi_markets(evt_ticker_str, force_live=True)
            if not fresh_markets:
                if VERBOSE:
                    print(f"⚠️ Cannot close {market_ticker}: could not fetch fresh market data")
                pos["closing_in_progress"] = False  # Clear flag to allow retry
                continue
            
            market = next((m for m in fresh_markets if m.get("ticker") == market_ticker), None)
        except Exception as e:
            if VERBOSE:
                print(f"⚠️ Error fetching fresh markets for {market_ticker}: {e}")
            pos["closing_in_progress"] = False
            continue
        
        if not market:
            if VERBOSE:
                print(f"⚠️ Cannot close {market_ticker}: market not found in fresh data")
            pos["closing_in_progress"] = False
            continue
        
        # Get FRESH current bid price
        current_bid = format_price(market.get("yes_bid"))
        if current_bid is None:
            if VERBOSE:
                print(f"⚠️ Cannot close {market_ticker}: no bid price in fresh data")
            pos["closing_in_progress"] = False
            continue
        
        # RE-VALIDATE: Check if selling is still better than holding (prices may have changed)
        stored_check_result = pos.get("closing_check_result")
        if stored_check_result:
            # SKIP re-validation for 7% absolute exit - it should execute no matter what
            if stored_check_result.get("kalshi_price_triggered"):
                # 7% exit bypasses all re-validation - proceed directly to sell
                if VERBOSE:
                    print(f"✅ 7% absolute exit - skipping re-validation, executing immediately")
            else:
                # Re-validation for other exit reasons (profit protection, max profit, trailing stop, etc.)
                try:
                    # Get all positions for this event to find both sides
                    evt_key = event_key(pos.get("event_ticker"))
                    event_positions_all = [p for p in positions if event_key(p.get("event_ticker")) == evt_key and not p.get("settled", False)]
                    
                    # Find both sides
                    side_A_ticker = None
                    side_B_ticker = None
                    for p in event_positions_all:
                        if p.get("market_ticker") == market_ticker:
                            # Find the other side
                            for other_pos in event_positions_all:
                                if other_pos.get("market_ticker") != market_ticker:
                                    side_A_ticker = market_ticker
                                    side_B_ticker = other_pos.get("market_ticker")
                                    break
                            break
                    
                    if side_A_ticker and side_B_ticker:
                        side_A_market_fresh = next((m for m in fresh_markets if m.get("ticker") == side_A_ticker), None)
                        side_B_market_fresh = next((m for m in fresh_markets if m.get("ticker") == side_B_ticker), None)
                        side_A_positions = [p for p in event_positions_all if p.get("market_ticker") == side_A_ticker]
                        side_B_positions = [p for p in event_positions_all if p.get("market_ticker") == side_B_ticker]
                        
                        if side_A_market_fresh and side_B_market_fresh:
                            side_A_bid_fresh = format_price(side_A_market_fresh.get("yes_bid"))
                            side_B_bid_fresh = format_price(side_B_market_fresh.get("yes_bid"))
                            
                            if side_A_bid_fresh and side_B_bid_fresh:
                                # Keep max_price tracking updated during closing flows
                                for p in side_A_positions:
                                    p["max_price"] = max(p.get("max_price", 0.0), float(side_A_bid_fresh))
                                for p in side_B_positions:
                                    p["max_price"] = max(p.get("max_price", 0.0), float(side_B_bid_fresh))

                                # Re-calculate sell prices
                                side_A_sell_fresh = max(0.01, side_A_bid_fresh - TICK)
                                side_B_sell_fresh = max(0.01, side_B_bid_fresh - TICK)
                                
                                # Re-calculate current profit with fresh prices
                                qty_A, entry_A, _ = aggregate_positions_on_side(side_A_positions, side_A_ticker)
                                qty_B, entry_B, _ = aggregate_positions_on_side(side_B_positions, side_B_ticker)
                                
                                if qty_A > 0 and qty_B > 0:
                                    _, current_profit_pct_fresh, roi_A, roi_B = calculate_current_profit_mtm(
                                        qty_A, entry_A, qty_B, entry_B,
                                        side_A_sell_fresh, side_B_sell_fresh
                                    )
                                    
                                    # Calculate settlement ROI using WEIGHTED AVERAGE (same as main decision logic)
                                    # This ensures re-validation is consistent with the exit decision
                                    roi_A_settle, roi_B_settle = hedge_outcome_rois(qty_A, entry_A, qty_B, entry_B)
                                    
                                    # Calculate probabilities from fresh prices (market's current view)
                                    total_price_fresh = side_A_sell_fresh + side_B_sell_fresh
                                    if total_price_fresh > 0:
                                        prob_A_fresh = side_A_sell_fresh / total_price_fresh
                                        prob_B_fresh = side_B_sell_fresh / total_price_fresh
                                    else:
                                        # Fallback to 50/50 if prices are invalid
                                        prob_A_fresh = 0.5
                                        prob_B_fresh = 0.5
                                    
                                    # Expected settlement ROI = probability-weighted average
                                    weighted_settlement_roi_fresh = (prob_A_fresh * roi_A_settle) + (prob_B_fresh * roi_B_settle)
                                    
                                    # Use calculate_theoretical_max_profit for consistency with decision phase
                                    _, max_settlement_roi = calculate_theoretical_max_profit(qty_A, entry_A, qty_B, entry_B)
                                    
                                    # Only proceed if still profitable vs WEIGHTED settlement
                                    if current_profit_pct_fresh < weighted_settlement_roi_fresh:
                                        if VERBOSE:
                                            print(f"⚠️ Canceling sell: Current profit {current_profit_pct_fresh:.2%} < weighted settlement {weighted_settlement_roi_fresh:.2%} "
                                                  f"(prob: {prob_A_fresh:.1%}/{prob_B_fresh:.1%}, outcomes: {roi_A_settle:.2%}/{roi_B_settle:.2%}) - prices changed unfavorably")
                                        pos["closing_in_progress"] = False
                                        pos.pop("closing_check_result", None)  # Remove stored check result
                                        continue
                                
                                # For max profit detection, re-check ratio
                                if stored_check_result.get("reason", "").startswith("max_profit"):
                                    max_profit_ratio_fresh = current_profit_pct_fresh / max_settlement_roi if max_settlement_roi > 0 else 0.0
                                    if max_profit_ratio_fresh < MAX_PROFIT_THRESHOLD:
                                        if VERBOSE:
                                            print(f"⚠️ Canceling sell: Ratio dropped to {max_profit_ratio_fresh:.0%} < {MAX_PROFIT_THRESHOLD:.0%} - prices changed unfavorably")
                                        pos["closing_in_progress"] = False
                                        pos.pop("closing_check_result", None)
                                        continue
                                
                                # For trailing stop, re-check drop from peak
                                if stored_check_result.get("reason", "").startswith("trailing_stop"):
                                    # Re-check if still below trailing stop threshold
                                    peak_profit_pct = stored_check_result.get("peak_profit_pct", 0)
                                    if peak_profit_pct > 0:
                                        drop_from_peak_fresh = max(0.0, peak_profit_pct - current_profit_pct_fresh)
                                        if peak_profit_pct >= TRAILING_STOP_TIGHTEN_THRESHOLD:
                                            stop_distance = TRAILING_STOP_TIGHTENED_PCT
                                        else:
                                            stop_distance = TRAILING_STOP_INITIAL_PCT
                                        
                                        if drop_from_peak_fresh < stop_distance:
                                            if VERBOSE:
                                                print(f"⚠️ Canceling sell: Drop from peak {drop_from_peak_fresh:.2%} < stop distance {stop_distance:.0%} - prices recovered")
                                            pos["closing_in_progress"] = False
                                            pos.pop("closing_check_result", None)
                                            continue
                except Exception as e:
                    if VERBOSE:
                        print(f"⚠️ Error re-validating prices for {market_ticker}: {e} - proceeding with caution")
                    # Continue with sell if re-validation fails (better to sell than get stuck)
        
        # Calculate actual sell price (use best bid for aggressive exit)
        # For odds feed or Kalshi price partial exits, use the actual best bid price
        stored_check_result = pos.get("closing_check_result", {})
        if stored_check_result.get("odds_feed_triggered") or stored_check_result.get("kalshi_price_triggered"):
            # Odds feed or Kalshi price exit: use best bid price (aggressive exit at threshold, default 10%)
            sell_price = max(0.01, current_bid)
        else:
            # Normal profit protection: use bid - 1 tick for guaranteed fill
            sell_price = max(0.01, current_bid - TICK)
        
        reason = pos.get("exit_reason", "profit_protection")
        side_display = pos.get('side_name', '')
        match_display = pos.get('match', '')
        side_info = f"{side_display} " if side_display else ""
        match_info = f"{match_display} " if match_display else ""
        print(f"💰 Selling {stake_to_sell} contracts of {match_info}{side_info}{market_ticker} at {sell_price:.2%} "
              f"(bid: {current_bid:.2%}) - {reason}")
        
        try:
            # 🛡️ SAFETY CHECK: Verify we actually have the position before selling
            # Use local positions.json instead of live API check (live check doesn't work reliably)
            if PLACE_LIVE_KALSHI_ORDERS == "YES":
                try:
                    # Check local positions list instead of live API
                    local_pos = next(
                        (p for p in positions 
                         if p.get("market_ticker") == market_ticker 
                         and p.get("side", "").lower() == "yes"
                         and not p.get("settled", False)),
                        None
                    )
                    
                    if not local_pos:
                        print(f"🛡️ SELL PREVENTION: Cannot sell {stake_to_sell} contracts - no position found in positions.json for {market_ticker}")
                        # Clear closing flag since we don't have the position
                        pos["closing_in_progress"] = False
                        pos.pop("closing_check_result", None)
                        pos.pop("closing_initiated_at", None)
                        continue
                    
                    local_qty = int(local_pos.get("stake", 0))
                    if local_qty == 0:
                        print(f"🛡️ SELL PREVENTION: Cannot sell {stake_to_sell} contracts - position in positions.json has 0 stake for {market_ticker}")
                        pos["closing_in_progress"] = False
                        pos.pop("closing_check_result", None)
                        pos.pop("closing_initiated_at", None)
                        continue
                    
                    if local_qty < stake_to_sell:
                        print(f"⚠️ Adjusting profit protection sell quantity from {stake_to_sell} to {local_qty} (actual position in positions.json)")
                        stake_to_sell = local_qty
                except Exception as e:
                    print(f"⚠️ Could not verify position in positions.json before sell: {e}")
                    # Continue anyway - better to try to sell than get stuck
            
            # Place sell order
            sell_resp = prepare_kalshi_order(
                market_ticker=market_ticker,
                side="yes",
                price=sell_price,
                quantity=stake_to_sell,
                action="sell"
            )
            
            sell_order_id, sell_client_oid = _extract_order_id(sell_resp)
            if sell_order_id:
                # ✅ IMMEDIATELY reduce stake to 0 to prevent double-selling on next loop iteration
                # This prevents the position from being evaluated again before the fill completes
                pos["stake"] = 0
                pos["settled"] = True  # Mark as settled so it won't be shown in open positions
                save_positions()  # Save immediately to persist the change
                
                # Wait for fill
                sell_status, sell_filled_qty = wait_for_fill_or_cancel(
                    sell_order_id,
                    client_order_id=sell_client_oid,
                    timeout_s=ORDER_FILL_TIME,
                    poll_s=1.0,
                    expected_count=stake_to_sell,
                    require_full=False,
                    verify_ticker=market_ticker,
                    verify_side="yes"
                )
                
                if sell_filled_qty > 0:
                    side_display = pos.get('side_name', '')
                    match_display = pos.get('match', '')
                    side_info = f"{side_display} " if side_display else ""
                    match_info = f"{match_display} " if match_display else ""
                    print(f"✅ Profit protection exit executed: Sold {match_info}{side_info}{sell_filled_qty} contracts at {sell_price:.2%}")
                    
                    # Mark event as 7% exited to prevent re-entry (if this was a 7% exit)
                    if stored_check_result.get("kalshi_price_triggered"):
                        evt_ticker = pos.get("event_ticker")
                        if evt_ticker:
                            mark_event_7pct_exited(evt_ticker)
                            # Record which market ticker exited at 7% to disable stop loss on the other side
                            evt_key_exit = event_key(evt_ticker)
                            partial_exit_side = stored_check_result.get("partial_exit_side")
                            if partial_exit_side:
                                # Store the market ticker of the position that exited
                                exited_ticker = pos.get("market_ticker")
                                if exited_ticker:
                                    EVENT_7PCT_EXITED_SIDE[evt_key_exit] = exited_ticker
                                    if VERBOSE:
                                        print(f"📝 Recorded 7% exit on market {exited_ticker} (side {partial_exit_side}) for event {evt_key_exit} - stop loss disabled on other side")
                    
                    # Update stake (reduce by sold amount)
                    pos["stake"] = max(0, pos.get("stake", 0) - sell_filled_qty)
                    if pos["stake"] <= 0:
                        pos["settled"] = True
                        pos["exit_reason"] = reason
                        pos["closing_in_progress"] = False  # Clear flag when fully closed
                    else:
                        # Partial fill - clear flags so next loop can re-evaluate and re-queue with fresh prices
                        if VERBOSE:
                            print(f"⚠️ Partial fill: {sell_filled_qty}/{stake_to_sell} contracts sold, {pos['stake']} remaining")
                        pos["closing_in_progress"] = False
                        pos.pop("closing_check_result", None)
                        pos.pop("closing_initiated_at", None)
                else:
                    print(f"⚠️ Profit protection sell order not filled: {sell_status}")
                    # Clear closing flag to allow retry on next loop
                    pos["closing_in_progress"] = False
                    pos.pop("closing_check_result", None)
                    pos.pop("closing_initiated_at", None)
                    # Don't mark as settled - will retry next loop
            else:
                print(f"⚠️ Could not extract order ID from profit protection sell order")
                # Clear closing flag to allow retry on next loop
                pos["closing_in_progress"] = False
                pos.pop("closing_check_result", None)
                pos.pop("closing_initiated_at", None)
                # Don't mark as settled - will retry next loop
        except Exception as e:
            print(f"⚠️ Error executing profit protection exit for {market_ticker}: {e}")
            # Clear closing flag on error to allow retry
            pos["closing_in_progress"] = False
            pos.pop("closing_check_result", None)
            pos.pop("closing_initiated_at", None)
            # Don't mark as settled - will retry next loop
    
    # Keep remaining positions (filter out settled ones)
    positions[:] = [p for p in positions if not p.get("settled", False)]
    save_positions()



# === MAIN LOOP ===
# === MAIN LOOP ===
# === MAIN LOOP ===
# === MAIN LOOP ===
