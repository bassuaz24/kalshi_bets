"""
Position settlement utilities.
"""

from app import state
from config import settings
from core.time import now_utc
from kalshi.positions import get_live_positions
from positions.metrics import calculate_unrealized_pnl
from positions.io import save_positions


def realize_if_settled():
    """Check for settled positions and realize PnL.
    Handles full settlement only - partial fills are handled in check_stop_losses and reconcile_positions.
    """
    if settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
        return

    try:
        live = get_live_positions()
        live_keys = {(lp["ticker"], (lp["side"] or "").lower()) for lp in live}
        live_dict = {(lp["ticker"], (lp["side"] or "").lower()): lp for lp in live}
    except Exception:
        return

    for p in state.positions:
        if p.get("settled", False):
            continue
        
        # Skip positions that are in closing state - they're handled elsewhere
        if p.get("closing_in_progress", False):
            continue
        
        key = (p.get("market_ticker"), (p.get("side") or "").lower())
        if key not in live_keys:
            # Position no longer exists on Kalshi - it's been fully settled
            p["settled"] = True
            p["settled_time"] = now_utc().isoformat()
            
            # Calculate realized PnL using exit price if available, otherwise use current unrealized
            from kalshi.markets import get_kalshi_markets, format_price
            from kalshi.fees import kalshi_fee_per_contract
            
            exit_price = p.get("last_exit_price")
            if not exit_price:
                # Try to get current price as fallback
                try:
                    mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True)
                    if mkts:
                        m = next((m for m in mkts if m.get("ticker") == p.get("market_ticker")), None)
                        if m:
                            yes_bid = format_price(m.get("yes_bid"))
                            exit_price = yes_bid if yes_bid is not None else format_price(m.get("yes_ask"))
                except Exception:
                    pass
            
            entry_price = float(p.get("effective_entry", p.get("entry_price", 0.0)))
            stake = int(p.get("stake", 0))
            
            if exit_price and entry_price > 0 and stake > 0:
                # Calculate actual realized PnL
                exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
                entry_fee = kalshi_fee_per_contract(entry_price, is_maker=True)
                pnl_per_contract = (exit_price - entry_price) - exit_fee - entry_fee
                realized_pnl = pnl_per_contract * stake
            else:
                # Fallback: use unrealized PnL calculation
                unrealized, _ = calculate_unrealized_pnl()
                open_positions = [pos for pos in state.positions if not pos.get("settled", False)]
                if open_positions and len(open_positions) > 0:
                    realized_pnl = unrealized / len(open_positions)
                else:
                    realized_pnl = unrealized
            
            state.realized_pnl += realized_pnl
            
            if realized_pnl > 0:
                state.wins += 1
            else:
                state.losses += 1
            
            state.closed_trades.append({
                "match": p.get("match", ""),
                "side": p.get("side", ""),
                "market_ticker": p.get("market_ticker", ""),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stake": stake,
                "pnl": realized_pnl,
                "entry_time": p.get("entry_time", ""),
                "settled_time": p["settled_time"],
                "exit_reason": p.get("exit_reason", "settled"),
            })
            
            print(f"✅ Position fully settled: {p.get('market_ticker')} {p.get('side')} | PnL: ${realized_pnl:.2f}")
        else:
            # Position still exists - check if it's a partial fill situation
            live_pos = live_dict.get(key)
            if live_pos:
                live_qty = int(live_pos.get("contracts", 0))
                local_qty = int(p.get("stake", 0))
                
                # If live quantity is less than local, position has been partially exited
                # This should have been handled by reconcile_positions, but double-check
                if live_qty < local_qty and live_qty > 0:
                    # Position has been partially reduced - update local stake to match
                    if settings.VERBOSE:
                        print(f"📊 Adjusting position size: {p.get('market_ticker')} from {local_qty} to {live_qty} (partial exit)")
                    p["stake"] = live_qty
    
    save_positions()