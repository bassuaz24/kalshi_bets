"""
Position normalization and management utilities.
"""

from typing import List, Dict, Any
from app import state


def normalize_loaded_positions():
    """Normalize loaded positions (ensure all required fields exist)."""
    for p in state.positions:
        if "effective_entry" not in p:
            p["effective_entry"] = p.get("entry_price", 0.0)
        if "settled" not in p:
            p["settled"] = False
        if "closing_in_progress" not in p:
            p["closing_in_progress"] = False
        if "stop_loss" not in p:
            p["stop_loss"] = None
        if "take_profit" not in p:
            p["take_profit"] = None


def deduplicate_positions():
    """Remove duplicate positions based on market_ticker and side."""
    seen = set()
    unique_positions = []
    
    for p in state.positions:
        key = (p.get("market_ticker"), (p.get("side") or "").lower())
        if key not in seen:
            seen.add(key)
            unique_positions.append(p)
        else:
            # If duplicate, merge quantities (keep the one with larger stake)
            existing = next(pos for pos in unique_positions 
                          if (pos.get("market_ticker"), (pos.get("side") or "").lower()) == key)
            existing["stake"] = max(existing.get("stake", 0), p.get("stake", 0))
    
    state.positions[:] = unique_positions