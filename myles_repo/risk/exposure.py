from app import state
from config import settings
from kalshi.fees import kalshi_fee
from utils.tickers import event_key


def per_ct_fee_for_qty(price: float, qty: int, is_maker: bool = False) -> float:
    return kalshi_fee(qty, price, is_maker=is_maker) / max(1, qty)


def total_dollars_needed(price: float, qty: int, is_maker: bool = False) -> float:
    return qty * price + kalshi_fee(qty, price, is_maker=is_maker)


def max_qty_with_cap(dollars_cap: float, price: float, q_hi: int = 5000) -> int:
    if price <= 0 or dollars_cap <= 0:
        return 0
    lo, hi, ans = 0, q_hi, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        need = total_dollars_needed(price, mid)
        if need <= dollars_cap:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def exposure_violation(
    market_ticker: str,
    event_ticker: str,
    added_qty: int,
    entry_price: float,
    capital: float,
    is_hedge_trade: bool,
) -> tuple[bool, str, int]:
    if added_qty <= 0 or entry_price <= 0:
        return True, "non_positive_size", 0

    side_exposure = sum(
        p["stake"] * p["entry_price"]
        for p in state.positions
        if p.get("market_ticker") == market_ticker
    )

    evt_key = event_key(event_ticker)

    evt_exposure = sum(
        p["stake"] * p["entry_price"]
        for p in state.positions
        if event_key(p.get("event_ticker")) == evt_key
    )

    cap_pct = settings.MAX_TOTAL_EXPOSURE_HEDGE_PCT if is_hedge_trade else settings.MAX_TOTAL_EXPOSURE_PCT
    side_limit = capital * cap_pct
    evt_limit = capital * cap_pct

    remaining_side_cap = max(0.0, side_limit - side_exposure)
    remaining_evt_cap = max(0.0, evt_limit - evt_exposure)

    if settings.MAX_EXPOSURE_PER_GAME > 0:
        remaining_evt_abs_cap = max(0.0, settings.MAX_EXPOSURE_PER_GAME - evt_exposure)
        max_qty_by_evt_abs = max_qty_with_cap(remaining_evt_abs_cap, entry_price) if remaining_evt_abs_cap > 0 else 0
    else:
        max_qty_by_evt_abs = added_qty

    max_qty_by_side = max_qty_with_cap(remaining_side_cap, entry_price) if remaining_side_cap > 0 else 0
    max_qty_by_evt_pct = max_qty_with_cap(remaining_evt_cap, entry_price) if remaining_evt_cap > 0 else 0

    max_allowed_qty = min(added_qty, max_qty_by_side, max_qty_by_evt_pct, max_qty_by_evt_abs)

    if max_allowed_qty <= 0:
        return True, f"side_exposure ${side_exposure:.2f} already at/over limit ${side_limit:.2f}", 0

    if max_allowed_qty < added_qty:
        max_exposure_used = side_exposure + (max_allowed_qty * entry_price)
        return True, (
            f"scaled down from {added_qty} to {max_allowed_qty} to respect limit "
            f"${side_limit:.2f} (would use ${max_exposure_used:.2f})"
        ), max_allowed_qty

    return False, "", added_qty


def side_exposure_dollars(event_ticker: str, market_ticker: str) -> float:
    key = event_key(event_ticker)
    total = 0.0
    for p in state.positions:
        if (
            event_key(p.get("event_ticker")) == key
            and p.get("market_ticker") == market_ticker
            and not p.get("settled", False)
        ):
            total += float(p.get("stake", 0)) * float(p.get("entry_price", 0.0))
    return total
