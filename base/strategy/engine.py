"""
Strategy engine that computes optimal trades.
This is a placeholder that will be filled in with the actual strategy implementation.
"""

from typing import List, Dict, Any, Optional, Tuple
from config import settings
from app import state
from kalshi.markets import get_kalshi_markets, market_yes_mid
from kalshi.orders import safe_prepare_kalshi_order
from risk.exposure import check_exposure_violation, check_event_exposure_violation, max_quantity_with_cap
from positions.io import save_positions
from core.time import now_utc


def compute_optimal_trade(market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Compute optimal trade entry point, bet size, stop loss, and take profit.
    
    This is a placeholder function. The actual strategy implementation will:
    1. Analyze market data to determine optimal entry points
    2. Calculate optimal bet size based on Kelly criterion or risk limits
    3. Compute dynamic stop loss and take profit values
    4. Return trade parameters
    
    Args:
        market_data: Dictionary containing market information including:
            - event_ticker: Event ticker
            - market_ticker: Market ticker
            - kalshi_markets: List of Kalshi markets
            - odds_data: OddsAPI data (if available)
            - current_price: Current market price
    
    Returns:
        Dictionary with trade parameters or None if no trade:
            - market_ticker: Market ticker to trade
            - side: "yes" or "no"
            - entry_price: Optimal entry price
            - quantity: Bet size (contracts)
            - stop_loss: Stop loss price
            - take_profit: Take profit price
    """
    # TODO: Implement actual strategy logic
    # For now, return None (no trades)
    return None


def run_engine(active_matches: List[Dict[str, Any]]):
    """
    Run the strategy engine on active matches.
    
    Args:
        active_matches: List of active match dictionaries with market data
    """
    if not active_matches:
        return
    
    print(f"🔄 Running strategy engine on {len(active_matches)} active matches...")
    
    for match in active_matches:
        event_ticker = match.get("ticker")
        kalshi_markets = match.get("kalshi", [])
        
        if not event_ticker or not kalshi_markets:
            continue
        
        # Process each market in the event
        for market in kalshi_markets:
            market_ticker = market.get("ticker")
            if not market_ticker:
                continue
            
            # Prepare market data for strategy
            current_price = market_yes_mid(market)
            if current_price is None:
                continue
            
            market_data = {
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "kalshi_markets": kalshi_markets,
                "current_price": current_price,
                "odds_data": match.get("odds_feed", {}),
                "match": match,
            }
            
            # Compute optimal trade
            trade = compute_optimal_trade(market_data)
            
            if trade is None:
                continue
            
            # Check risk limits before executing
            entry_price = trade.get("entry_price")
            quantity = trade.get("quantity")
            
            if entry_price <= 0 or quantity <= 0:
                continue
            
            exposure = entry_price * quantity
            
            # Check total exposure
            is_violation, reason = check_exposure_violation(exposure)
            if is_violation:
                print(f"⚠️ Skipping trade due to exposure violation: {reason}")
                continue
            
            # Check event exposure
            is_violation, reason = check_event_exposure_violation(event_ticker, exposure)
            if is_violation:
                print(f"⚠️ Skipping trade due to event exposure violation: {reason}")
                continue
            
            # Execute trade (taking the ask - optimized for fast fills)
            try:
                side = trade.get("side", "yes")
                max_total_contracts = max_quantity_with_cap(
                    entry_price,
                    exposure * 1.1  # Add 10% buffer
                )
                
                # Place order at ask price (taking liquidity)
                result = safe_prepare_kalshi_order(
                    market_ticker,
                    side,
                    entry_price,  # Should be ask price when taking the ask
                    quantity,
                    max_total_contracts=max_total_contracts,
                    order_type="limit",  # Limit order at ask price
                    action="buy"
                )
                
                if result is None:
                    print(f"⚠️ Trade execution failed for {market_ticker}")
                    continue
                
                # Extract order ID for tracking
                from kalshi.orders import _extract_order_id, wait_for_fill_or_cancel, get_order_fill_status
                import time
                
                order_id = _extract_order_id(result.get("response"))
                
                if not order_id:
                    # If no order ID, fall back to reconciliation
                    # But log as warning - we prefer order ID tracking
                    print(f"⚠️ Could not extract order ID for {market_ticker}, will rely on reconciliation")
                    # Still create position, but reconciliation will correct it
                    position = {
                        "match": match.get("match", ""),
                        "side": side,
                        "event_ticker": event_ticker,
                        "market_ticker": market_ticker,
                        "entry_price": entry_price,
                        "effective_entry": entry_price,
                        "stake": quantity,  # Assume full fill initially
                        "entry_time": now_utc().isoformat(),
                        "entry_order_id": None,  # No order ID tracked
                        "original_order_quantity": quantity,
                        "stop_loss": trade.get("stop_loss"),
                        "take_profit": trade.get("take_profit"),
                        "settled": False,
                        "closing_in_progress": False,
                        "odds_prob": 0.5,
                    }
                    state.positions.append(position)
                    state.METRICS["orders_placed"] += 1
                    continue
                
                # Quick initial check (asks typically fill in 1-2 seconds)
                time.sleep(0.5)  # Brief pause for order to process
                is_filled, filled_qty_immediate, remaining = get_order_fill_status(order_id)
                
                if is_filled and filled_qty_immediate >= quantity:
                    # Fully filled immediately - common when taking ask
                    actual_filled = filled_qty_immediate
                    fill_status = "filled"
                    if settings.VERBOSE:
                        print(f"⚡ Entry order filled immediately: {market_ticker} ({actual_filled} contracts)")
                elif filled_qty_immediate > 0:
                    # Partial fill already occurred
                    actual_filled = filled_qty_immediate
                    fill_status = "partial"
                    if settings.VERBOSE:
                        print(f"📊 Partial fill detected immediately: {market_ticker} ({actual_filled}/{quantity})")
                else:
                    # Not filled yet - wait a bit more (optimized timeout for taking ask)
                    fill_timeout = 5.0  # 5 seconds - asks typically fill in 1-2 seconds
                    status, filled_qty = wait_for_fill_or_cancel(
                        order_id,
                        timeout_secs=fill_timeout,
                        require_full=False  # Accept partial fills
                    )
                    
                    if filled_qty <= 0:
                        # Order didn't fill at ask - this can happen if:
                        # 1. Ask moved up before order reached exchange
                        # 2. Ask was filled by another order
                        # 3. Insufficient liquidity
                        print(f"⚠️ Entry order at ask did not fill for {market_ticker} (status: {status})")
                        # Don't create position - retry on next iteration if strategy wants
                        continue
                    
                    actual_filled = filled_qty
                    fill_status = status
                
                # Check if partial fill occurred
                if actual_filled < quantity:
                    remaining_qty = quantity - actual_filled
                    print(f"📊 Partial fill on entry (taking ask): {market_ticker} - {actual_filled}/{quantity} filled, {remaining_qty} remaining")
                    # Note: Strategy can re-evaluate and place new order for remaining on next iteration
                
                # Only create position with ACTUAL filled quantity
                position = {
                    "match": match.get("match", ""),
                    "side": side,
                    "event_ticker": event_ticker,
                    "market_ticker": market_ticker,
                    "entry_price": entry_price,
                    "effective_entry": entry_price,
                    "stake": actual_filled,  # ← Use actual filled quantity (not requested)
                    "entry_time": now_utc().isoformat(),
                    "entry_order_id": order_id,  # ← Track order ID
                    "original_order_quantity": quantity,  # ← Track what was ordered
                    "entry_fill_status": fill_status,  # ← "filled" or "partial"
                    "stop_loss": trade.get("stop_loss"),
                    "take_profit": trade.get("take_profit"),
                    "settled": False,
                    "closing_in_progress": False,
                    "odds_prob": 0.5,
                }
                
                state.positions.append(position)
                state.METRICS["orders_placed"] += 1
                state.METRICS["orders_filled"] += 1 if fill_status == "filled" else 0
                if 0 < actual_filled < quantity:
                    state.METRICS["orders_partial_filled"] += 1
                
                if actual_filled == quantity:
                    print(f"✅ Entry position created: {market_ticker} {side.upper()} x{actual_filled} @ {entry_price:.2%} (full fill) | "
                          f"SL: {trade.get('stop_loss'):.2%} | TP: {trade.get('take_profit'):.2%}")
                else:
                    print(f"✅ Entry position created: {market_ticker} {side.upper()} x{actual_filled} @ {entry_price:.2%} (partial fill: {actual_filled}/{quantity}) | "
                          f"SL: {trade.get('stop_loss'):.2%} | TP: {trade.get('take_profit'):.2%}")
                
            except Exception as e:
                print(f"❌ Error executing trade for {market_ticker}: {e}")
                if settings.VERBOSE:
                    import traceback
                    traceback.print_exc()
                continue
    
    save_positions()