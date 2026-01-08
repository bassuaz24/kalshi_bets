"""
Position metrics and PnL calculations.
"""

from typing import Tuple
from config import settings
from app import state
from kalshi.markets import get_kalshi_markets, format_price
from kalshi.fees import kalshi_fee_per_contract
from kalshi.balance import get_kalshi_balance


def calculate_unrealized_pnl() -> Tuple[float, float]:
    """Calculate unrealized PnL and total equity.
    
    Returns:
        Tuple of (unrealized_pnl, total_equity)
    """
    unreal = 0.0

    for p in state.positions:
        if p.get("settled", False):
            continue
            
        try:
            mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True)
            if not mkts:
                continue
            m = next((m for m in mkts if m.get("ticker") == p.get("market_ticker")), None)
            if not m:
                continue

            yes_bid = format_price(m.get("yes_bid"))
            yes_ask = format_price(m.get("yes_ask"))

            if (p.get("side") or "").lower() == "yes":
                entry = float(p.get("effective_entry", p.get("entry_price", 0.0)))
                exit_price = yes_bid if yes_bid is not None else yes_ask
                if exit_price is None:
                    continue

                exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
                mtm_per_ct = (exit_price - entry) - exit_fee
                unreal += float(p.get("stake", 0)) * mtm_per_ct

        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error calculating PnL for position {p.get('market_ticker')}: {e}")
            continue

    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
        live_cash = get_kalshi_balance()
        equity = live_cash + unreal
    else:
        equity = state.capital_sim + state.realized_pnl + unreal

    return unreal, equity


def get_position_unrealized_pnl(position: dict) -> float:
    """Calculate unrealized PnL for a single position."""
    try:
        mkts = get_kalshi_markets(position.get("event_ticker", ""), force_live=True)
        if not mkts:
            return 0.0
        m = next((m for m in mkts if m.get("ticker") == position.get("market_ticker")), None)
        if not m:
            return 0.0

        yes_bid = format_price(m.get("yes_bid"))
        yes_ask = format_price(m.get("yes_ask"))

        if (position.get("side") or "").lower() == "yes":
            entry = float(position.get("effective_entry", position.get("entry_price", 0.0)))
            exit_price = yes_bid if yes_bid is not None else yes_ask
            if exit_price is None:
                return 0.0

            exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
            mtm_per_ct = (exit_price - entry) - exit_fee
            return float(position.get("stake", 0)) * mtm_per_ct

    except Exception:
        return 0.0

    return 0.0


def get_total_exposure() -> float:
    """Calculate total current exposure across all positions."""
    total = 0.0
    for p in state.positions:
        if p.get("settled", False):
            continue
        entry = float(p.get("effective_entry", p.get("entry_price", 0.0)))
        stake = float(p.get("stake", 0))
        total += entry * stake
    return total


def get_position_summary() -> dict:
    """Get summary statistics for all positions."""
    unrealized_pnl, equity = calculate_unrealized_pnl()
    total_exposure = get_total_exposure()
    
    open_positions = [p for p in state.positions if not p.get("settled", False)]
    
    return {
        "total_positions": len(open_positions),
        "realized_pnl": state.realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": state.realized_pnl + unrealized_pnl,
        "equity": equity,
        "total_exposure": total_exposure,
        "wins": state.wins,
        "losses": state.losses,
    }