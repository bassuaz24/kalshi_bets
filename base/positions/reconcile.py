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
    for pos in new_positions:
        if pos.get("settled", False) or pos.get("closing_in_progress", False):
            continue

        key = (pos.get("market_ticker"), (pos.get("side") or "").lower())
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