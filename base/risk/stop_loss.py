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
    """Check all positions for stop loss triggers and execute exits if needed."""
    from kalshi.orders import prepare_kalshi_order
    
    stop_loss_orders = load_stop_loss_orders()
    
    for p in state.positions:
        if p.get("settled", False) or p.get("closing_in_progress", False):
            continue
        
        market_ticker = p.get("market_ticker")
        if not market_ticker:
            continue
        
        stop_loss = p.get("stop_loss")
        take_profit = p.get("take_profit")
        
        # Get current market price
        try:
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
            
            # Check stop loss (if long YES, stop loss is below entry; if short YES, above entry)
            if stop_loss is not None:
                if side == "yes":
                    # Long YES: stop loss if price falls below stop_loss
                    if current_price <= stop_loss:
                        print(f"🛑 Stop loss triggered for {market_ticker} at {current_price:.2%} (stop: {stop_loss:.2%})")
                        # Exit position
                        prepare_kalshi_order(market_ticker, side, current_price, stake, action="sell")
                        p["closing_in_progress"] = True
                        p["exit_reason"] = "stop_loss"
                        continue
            
            # Check take profit
            if take_profit is not None:
                if side == "yes":
                    # Long YES: take profit if price rises above take_profit
                    if current_price >= take_profit:
                        print(f"💰 Take profit triggered for {market_ticker} at {current_price:.2%} (target: {take_profit:.2%})")
                        # Exit position
                        prepare_kalshi_order(market_ticker, side, current_price, stake, action="sell")
                        p["closing_in_progress"] = True
                        p["exit_reason"] = "take_profit"
                        continue
        
        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error checking stop loss for {market_ticker}: {e}")
            continue
    
    save_positions()