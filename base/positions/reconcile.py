"""
Position reconciliation with live Kalshi positions.
"""

from typing import Dict, Any
from app import state
from config import settings
from core.time import now_utc
from kalshi.positions import get_live_positions
from positions.io import save_positions


def reconcile_positions():
    """Reconcile local positions with live Kalshi positions."""
    try:
        live = get_live_positions()
    except Exception as e:
        print(f"⚠️ Could not fetch live positions ({e}); continuing with local state")
        live = []

    live_now = now_utc().isoformat()
    live_keys = {(lp["ticker"], (lp["side"] or "").lower()) for lp in live}

    # Update last_seen for existing positions
    for p in state.positions:
        key = (p.get("market_ticker"), (p.get("side") or "").lower())
        if key in live_keys:
            p["last_seen_live"] = live_now

    # Build local position keys
    new_positions = list(state.positions)
    local_keys = {(p.get("market_ticker"), (p.get("side") or "").lower()): i 
                  for i, p in enumerate(new_positions)}

    # Add new positions from Kalshi that we don't have locally
    for lp in live:
        key = (lp["ticker"], (lp["side"] or "").lower())
        if key not in local_keys:
            print(f"✅ Added live fill from Kalshi: {lp['ticker']} {lp['side']}")
            new_positions.append({
                "match": lp["ticker"],
                "side": lp["side"].lower(),
                "event_ticker": lp.get("event_ticker", ""),
                "market_ticker": lp["ticker"],
                "entry_price": lp["avg_price"],
                "stake": lp["contracts"],
                "effective_entry": lp["avg_price"],
                "entry_time": now_utc().isoformat(),
                "odds_prob": 0.5,
            })

    # Mark positions as settled if they no longer exist on Kalshi
    # Also handle partial fills for positions in closing state
    for pos in new_positions:
        if pos.get("settled", False):
            continue

        key = (pos.get("market_ticker"), (pos.get("side") or "").lower())
        
        # Check if position is in closing state - handle partial fills
        if pos.get("closing_in_progress", False):
            live_pos = next((lp for lp in live if (lp["ticker"], (lp["side"] or "").lower()) == key), None)
            
            if live_pos:
                live_qty = int(live_pos.get("contracts", 0))
                local_qty = int(pos.get("stake", 0))
                
                # If live quantity is less than local, partial fill occurred
                if live_qty < local_qty and live_qty > 0:
                    filled_qty = local_qty - live_qty
                    print(f"📊 Partial fill detected via reconciliation: {pos.get('market_ticker')} - {filled_qty} filled, {live_qty} remaining")
                    
                    # Update position to reflect remaining contracts
                    pos["stake"] = live_qty
                    pos["closing_in_progress"] = False  # Reset to monitor remaining
                    pos["exit_order_id"] = None
                    
                    # If we have exit price info, calculate PnL
                    if pos.get("last_exit_price"):
                        from kalshi.fees import kalshi_fee_per_contract
                        from app import state
                        entry_price = float(pos.get("effective_entry", pos.get("entry_price", 0.0)))
                        exit_price = pos["last_exit_price"]
                        
                        # Calculate PnL for filled portion
                        exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
                        entry_fee = kalshi_fee_per_contract(entry_price, is_maker=True)
                        pnl_per_contract = (exit_price - entry_price) - exit_fee - entry_fee
                        total_pnl = pnl_per_contract * filled_qty
                        
                        state.realized_pnl += total_pnl
                        if total_pnl > 0:
                            state.wins += 1
                        elif total_pnl < 0:
                            state.losses += 1
                        
                        if settings.VERBOSE:
                            print(f"💰 Realized PnL for partial fill: {filled_qty} contracts = ${total_pnl:.2f}")
                elif live_qty == 0:
                    # Fully filled - position gone
                    pos["settled"] = True
                    pos["stake"] = 0
                    if settings.VERBOSE:
                        print(f"✅ Position fully exited: {pos.get('market_ticker')} {pos.get('side')}")
            elif key not in live_keys:
                # Position doesn't exist on Kalshi - fully settled
                pos["settled"] = True
                pos["stake"] = 0
                if settings.VERBOSE:
                    print(f"✅ Position fully settled: {pos.get('market_ticker')} {pos.get('side')}")
            continue

        # Position not in closing state - normal settlement check
        if key not in live_keys:
            pos["settled"] = True
            pos["stake"] = 0
            if settings.VERBOSE:
                print(
                    f"🗑️ Marking {pos.get('market_ticker')} {pos.get('side')} as settled "
                    "- no longer exists on Kalshi"
                )

    state.positions[:] = new_positions
    save_positions()