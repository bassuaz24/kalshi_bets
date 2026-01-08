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
    """Check for settled positions and realize PnL."""
    if settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
        return

    try:
        live = get_live_positions()
        live_keys = {(lp["ticker"], (lp["side"] or "").lower()) for lp in live}
    except Exception:
        return

    for p in state.positions:
        if p.get("settled", False):
            continue
        
        key = (p.get("market_ticker"), (p.get("side") or "").lower())
        if key not in live_keys:
            # Position no longer exists on Kalshi - it's been settled
            p["settled"] = True
            p["settled_time"] = now_utc().isoformat()
            
            # Calculate realized PnL (simplified - actual calculation would use settlement price)
            # For now, we'll use the last known unrealized PnL
            unrealized = calculate_unrealized_pnl()[0] / len([pos for pos in state.positions if not pos.get("settled", False)])
            state.realized_pnl += unrealized
            
            if unrealized > 0:
                state.wins += 1
            else:
                state.losses += 1
            
            state.closed_trades.append({
                "match": p.get("match", ""),
                "side": p.get("side", ""),
                "market_ticker": p.get("market_ticker", ""),
                "entry_price": p.get("entry_price", 0.0),
                "stake": p.get("stake", 0),
                "pnl": unrealized,
                "entry_time": p.get("entry_time", ""),
                "settled_time": p["settled_time"],
            })
            
            print(f"✅ Position settled: {p.get('market_ticker')} {p.get('side')} | PnL: ${unrealized:.2f}")
    
    save_positions()