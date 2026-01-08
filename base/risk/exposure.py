"""
Risk management for position exposure.
"""

from typing import Optional, Tuple
from config import settings
from app import state
from positions.metrics import get_total_exposure
from kalshi.balance import get_kalshi_balance


def check_exposure_violation(additional_exposure: float = 0.0) -> Tuple[bool, str]:
    """Check if adding exposure would violate risk limits.
    
    Args:
        additional_exposure: Additional exposure to add (in dollars)
    
    Returns:
        Tuple of (is_violation, reason)
    """
    current_exposure = get_total_exposure()
    total_exposure = current_exposure + additional_exposure
    
    # Get current equity
    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
        equity = get_kalshi_balance()
    else:
        equity = state.capital_sim
    
    # Check total exposure limit
    max_total_exposure = equity * settings.MAX_TOTAL_EXPOSURE_PCT
    if total_exposure > max_total_exposure:
        return True, f"Total exposure ${total_exposure:.2f} exceeds limit ${max_total_exposure:.2f} ({settings.MAX_TOTAL_EXPOSURE_PCT*100}%)"
    
    return False, ""


def check_event_exposure_violation(event_ticker: str, additional_exposure: float) -> Tuple[bool, str]:
    """Check if adding exposure for an event would violate per-event limits.
    
    Args:
        event_ticker: Event ticker to check
        additional_exposure: Additional exposure to add (in dollars)
    
    Returns:
        Tuple of (is_violation, reason)
    """
    # Calculate current exposure for this event
    current_event_exposure = sum(
        float(p.get("effective_entry", p.get("entry_price", 0.0))) * float(p.get("stake", 0))
        for p in state.positions
        if p.get("event_ticker") == event_ticker and not p.get("settled", False)
    )
    
    total_event_exposure = current_event_exposure + additional_exposure
    
    # Get current equity
    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
        equity = get_kalshi_balance()
    else:
        equity = state.capital_sim
    
    # Check per-event exposure limit
    max_event_exposure = equity * settings.MAX_EXPOSURE_PER_EVENT_PCT
    if total_event_exposure > max_event_exposure:
        return True, f"Event exposure ${total_event_exposure:.2f} exceeds limit ${max_event_exposure:.2f} ({settings.MAX_EXPOSURE_PER_EVENT_PCT*100}%)"
    
    return False, ""


def max_quantity_with_cap(price: float, max_stake_dollars: float) -> int:
    """Calculate maximum quantity that can be purchased given a price and max stake.
    
    Args:
        price: Price per contract (0-1)
        max_stake_dollars: Maximum stake in dollars
    
    Returns:
        Maximum quantity (contracts)
    """
    if price <= 0:
        return 0
    return int(max_stake_dollars / price)