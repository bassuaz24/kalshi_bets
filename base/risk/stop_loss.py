"""
Stop loss tracking and management.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from config import settings
from app import state
from kalshi.markets import get_kalshi_markets, format_price
from positions.metrics import get_position_unrealized_pnl
from positions.io import save_positions


STOP_LOSS_FILE = Path(settings.BASE_DIR) / "stop_loss_orders.json"


def load_stop_loss_orders() -> Dict[str, Dict[str, Any]]:
    """Load stop loss orders from file."""
    if not STOP_LOSS_FILE.exists():
        return {}
    try:
        with open(STOP_LOSS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load stop loss orders: {e}")
        return {}


def save_stop_loss_orders(stop_loss_orders: Dict[str, Dict[str, Any]]):
    """Save stop loss orders to file."""
    try:
        with open(STOP_LOSS_FILE, "w") as f:
            json.dump(stop_loss_orders, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save stop loss orders: {e}")


def check_stop_losses():
    """Check all positions for stop loss triggers and execute exits if needed.
    Handles partial fills by tracking order IDs and resubmitting orders for remaining contracts.
    """
    from kalshi.orders import prepare_kalshi_order, _extract_order_id, wait_for_fill_or_cancel, get_order_fill_status
    from core.time import now_utc
    
    stop_loss_orders = load_stop_loss_orders()
    
    for p in state.positions:
        if p.get("settled", False):
            continue
        
        market_ticker = p.get("market_ticker")
        if not market_ticker:
            continue
        
        # Handle positions already in closing state - check order status
        if p.get("closing_in_progress", False):
            exit_order_id = p.get("exit_order_id")
            if exit_order_id:
                # Check if order has filled (fully or partially)
                is_filled, filled_count, remaining_count = get_order_fill_status(exit_order_id)
                
                if is_filled and remaining_count == 0:
                    # Fully filled - will be handled by settlement
                    if settings.VERBOSE:
                        print(f"✅ Exit order fully filled: {market_ticker} (order_id: {exit_order_id})")
                    continue
                elif filled_count > 0 and remaining_count > 0:
                    # Partial fill detected - update position
                    original_stake = p.get("original_stake_on_exit", p.get("stake", 0))
                    remaining_stake = remaining_count if remaining_count > 0 else original_stake - filled_count
                    
                    if remaining_stake > 0:
                        print(f"📊 Partial fill detected for {market_ticker}: {filled_count} filled, {remaining_stake} remaining")
                        
                        # Update position with remaining contracts
                        p["stake"] = remaining_stake
                        p["partial_fills"] = p.get("partial_fills", [])
                        p["partial_fills"].append({
                            "qty": filled_count,
                            "price": p.get("last_exit_price"),
                            "time": now_utc().isoformat(),
                            "order_id": exit_order_id
                        })
                        
                        # Reset closing state to continue monitoring remaining position
                        p["closing_in_progress"] = False
                        p["exit_order_id"] = None
                        
                        # Realize PnL for filled portion
                        _realize_partial_fill(p, filled_count, p.get("last_exit_price"))
                    continue
                elif remaining_count > 0:
                    # Order still open, check if we should resubmit at better price or wait
                    if settings.VERBOSE:
                        print(f"⏳ Exit order still open for {market_ticker}: {remaining_count} remaining (order_id: {exit_order_id})")
                    continue
                else:
                    # Order cancelled or timed out - reset to try again
                    if settings.VERBOSE:
                        print(f"⚠️ Exit order cancelled/timed out for {market_ticker}, will retry")
                    p["closing_in_progress"] = False
                    p["exit_order_id"] = None
            else:
                # No order ID tracked - check live positions to detect fill
                # This will be handled by reconcile_positions
                continue
        
        stop_loss = p.get("stop_loss")
        take_profit = p.get("take_profit")
        
        # Get current market price (prefer WebSocket cache, fallback to REST)
        current_price = None
        try:
            # Try WebSocket cache first
            from kalshi.websocket_client import get_websocket_client
            ws_client = get_websocket_client()
            price_data = ws_client.get_price(market_ticker)
            
            if price_data:
                yes_bid = price_data.get("yes_bid")
                yes_ask = price_data.get("yes_ask")
                current_price = yes_bid if yes_bid is not None else yes_ask
            
            # Fallback to REST API if WebSocket cache unavailable
            if current_price is None:
                if settings.VERBOSE:
                    print(f"⚠️ WebSocket price unavailable for {market_ticker}, using REST API fallback")
                mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True)
                if not mkts:
                    continue
                m = next((m for m in mkts if m.get("ticker") == market_ticker), None)
                if not m:
                    continue
                
                yes_bid = format_price(m.get("yes_bid"))
                yes_ask = format_price(m.get("yes_ask"))
                current_price = yes_bid if yes_bid is not None else yes_ask
            
            if current_price is None:
                continue
            
            side = (p.get("side") or "").lower()
            entry_price = float(p.get("effective_entry", p.get("entry_price", 0.0)))
            stake = int(p.get("stake", 0))
            
            if stake <= 0:
                continue
            
            exit_triggered = False
            exit_reason = None
            
            # Check stop loss (if long YES, stop loss is below entry; if short YES, above entry)
            if stop_loss is not None:
                if side == "yes":
                    # Long YES: stop loss if price falls below stop_loss
                    if current_price <= stop_loss:
                        exit_triggered = True
                        exit_reason = "stop_loss"
                        print(f"🛑 Stop loss triggered for {market_ticker} at {current_price:.2%} (stop: {stop_loss:.2%})")
            
            # Check take profit
            if not exit_triggered and take_profit is not None:
                if side == "yes":
                    # Long YES: take profit if price rises above take_profit
                    if current_price >= take_profit:
                        exit_triggered = True
                        exit_reason = "take_profit"
                        print(f"💰 Take profit triggered for {market_ticker} at {current_price:.2%} (target: {take_profit:.2%})")
            
            if exit_triggered:
                # Place exit order
                result = prepare_kalshi_order(market_ticker, side, current_price, stake, action="sell")
                
                if result and result.get("response"):
                    # Extract order ID from response
                    order_id = _extract_order_id(result.get("response"))
                    
                    if order_id:
                        # Store order details
                        p["exit_order_id"] = order_id
                        p["original_stake_on_exit"] = stake
                        p["last_exit_price"] = current_price
                        p["exit_reason"] = exit_reason
                        p["exit_order_time"] = now_utc().isoformat()
                        p["closing_in_progress"] = True
                        
                        # Wait briefly for fill status (non-blocking check)
                        status, filled_count = wait_for_fill_or_cancel(order_id, timeout_secs=5.0, require_full=False)
                        
                        if status == "filled" and filled_count >= stake:
                            # Fully filled immediately
                            if settings.VERBOSE:
                                print(f"✅ Exit order fully filled immediately: {market_ticker} ({filled_count} contracts)")
                            # Will be handled by settlement
                        elif status == "partial" and filled_count > 0:
                            # Partial fill - handle it
                            remaining_stake = stake - filled_count
                            if remaining_stake > 0:
                                print(f"📊 Partial fill on exit order: {market_ticker} - {filled_count} filled, {remaining_stake} remaining")
                                
                                # Update position
                                p["stake"] = remaining_stake
                                p["partial_fills"] = p.get("partial_fills", [])
                                p["partial_fills"].append({
                                    "qty": filled_count,
                                    "price": current_price,
                                    "time": now_utc().isoformat(),
                                    "order_id": order_id,
                                    "reason": exit_reason
                                })
                                
                                # Realize PnL for filled portion
                                _realize_partial_fill(p, filled_count, current_price)
                                
                                # Reset to continue monitoring remaining position
                                p["closing_in_progress"] = False
                                p["exit_order_id"] = None
                        elif status in ("timeout", "cancelled"):
                            # Order didn't fill - will retry on next iteration
                            if settings.VERBOSE:
                                print(f"⚠️ Exit order {status} for {market_ticker}, will retry")
                            p["closing_in_progress"] = False
                            p["exit_order_id"] = None
                    else:
                        print(f"⚠️ Could not extract order ID for exit order on {market_ticker}")
                        # Don't mark as closing if we can't track the order
                elif settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
                    # Simulation mode - mark as closing
                    p["closing_in_progress"] = True
                    p["exit_reason"] = exit_reason
        
        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error checking stop loss for {market_ticker}: {e}")
            continue
    
    save_positions()


def _realize_partial_fill(position: Dict[str, Any], filled_qty: int, exit_price: float):
    """Calculate and record realized PnL for a partial fill.
    
    Args:
        position: Position dictionary
        filled_qty: Number of contracts filled
        exit_price: Exit price for filled contracts
    """
    try:
        entry_price = float(position.get("effective_entry", position.get("entry_price", 0.0)))
        from kalshi.fees import kalshi_fee_per_contract
        
        # Calculate PnL per contract (simplified - assumes entry and exit at same fees)
        exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
        entry_fee = kalshi_fee_per_contract(entry_price, is_maker=True)
        
        pnl_per_contract = (exit_price - entry_price) - exit_fee - entry_fee
        total_pnl = pnl_per_contract * filled_qty
        
        # Update realized PnL
        state.realized_pnl += total_pnl
        
        # Update wins/losses
        if total_pnl > 0:
            state.wins += 1
        elif total_pnl < 0:
            state.losses += 1
        
        if settings.VERBOSE:
            print(f"💰 Realized PnL for partial fill: {filled_qty} contracts @ {exit_price:.2%} = ${total_pnl:.2f}")
    except Exception as e:
        if settings.VERBOSE:
            print(f"⚠️ Error calculating partial fill PnL: {e}")