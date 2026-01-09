"""
Kalshi order management utilities.
"""

import json
import time
import uuid
import requests
from typing import Optional, Tuple, Dict, Any
from config import settings
from core.session import SESSION
from kalshi.auth import kalshi_headers
from kalshi.positions import get_live_positions


def prepare_kalshi_order(
    market_ticker: str,
    side: str,
    price: float,
    quantity: int,
    order_type: str = "limit",
    action: str = "buy",
) -> Dict[str, Any]:
    """Prepare a Kalshi order payload."""
    path = "/trade-api/v2/portfolio/orders"
    headers = kalshi_headers("POST", path)
    headers.update({"Content-Type": "application/json"})

    if side.lower() != "yes":
        raise ValueError(f"❌ Attempted to place a {side.upper()} order — only YES trades allowed.")

    payload = {
        "ticker": market_ticker,
        "action": action.lower(),
        "side": side.lower(),
        "count": int(quantity),
        "type": order_type,
        "client_order_id": str(uuid.uuid4()),
    }

    if side.lower() == "yes":
        payload["yes_price"] = int(round(float(price) * 100))
    elif side.lower() == "no":
        payload["no_price"] = int(round(float(price) * 100))
    else:
        raise ValueError(f"Invalid side: {side}")

    if settings.VERBOSE:
        print("\n📦 === Kalshi Order Build ===")
        print(f"Ticker: {market_ticker}")
        print(f"Action: {action}")
        print(f"Side:   {side}")
        print(f"Price:  {price:.2%}")
        print(f"Qty:    {quantity}")
        print(f"Type:   {order_type}")
        print(json.dumps(payload, indent=2))
        print("────────────────────────────────────────────")

    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
        if settings.VERBOSE:
            print("🚀 Sending live order to Kalshi...")
        response = SESSION.post(settings.KALSHI_BASE_URL + path, headers=headers, json=payload, timeout=10)
        if settings.VERBOSE:
            print("💬 Kalshi Response:", response.status_code, response.text)
        return {"response": response, "payload": payload}
    print("🧪 SAFE MODE: Order preview only, not submitted.")
    return {"response": None, "payload": payload}


def safe_prepare_kalshi_order(
    market_ticker: str,
    side: str,
    price: float,
    quantity: int,
    max_total_contracts: Optional[int] = None,
    order_type: str = "limit",
    action: str = "buy",
) -> Optional[Dict[str, Any]]:
    """Prepare a Kalshi order with position checking to prevent oversizing."""
    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES" and max_total_contracts is not None and quantity > 0:
        try:
            live_positions = get_live_positions() or []
            live_qty = sum(
                int(p.get("contracts") or 0)
                for p in live_positions
                if p.get("ticker") == market_ticker
                and (p.get("side") or "").lower() == side.lower()
            )

            if live_qty >= max_total_contracts:
                print(
                    f"🛡️ SAFE ORDER: Already have {live_qty} contracts on {market_ticker} "
                    f"(max {max_total_contracts}). Skipping new order to prevent oversizing."
                )
                return None

            allowed = max_total_contracts - live_qty
            if quantity > allowed:
                if allowed <= 0:
                    print(
                        f"🛡️ SAFE ORDER: Proposed quantity {quantity} would exceed max {max_total_contracts} "
                        f"given current live {live_qty}. Skipping order."
                    )
                    return None

                print(
                    f"🛡️ SAFE ORDER: Reducing quantity from {quantity} to {allowed} "
                    f"(already have {live_qty}/{max_total_contracts})"
                )
                quantity = allowed
        except Exception as e:
            print(f"⚠️ Error checking live positions for safe order: {e}")

    return prepare_kalshi_order(market_ticker, side, price, quantity, order_type, action)


def _extract_order_id(response) -> Optional[str]:
    """Extract order ID from Kalshi response."""
    if response is None:
        return None
    try:
        data = response.json()
        return data.get("order", {}).get("order_id") or data.get("order_id")
    except Exception:
        return None


def get_order(order_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """Get order details from Kalshi API.
    
    Returns:
        Tuple of (order_data, status_code)
    """
    path = f"/trade-api/v2/portfolio/orders/{order_id}"
    headers = kalshi_headers("GET", path)
    try:
        res = SESSION.get(settings.KALSHI_BASE_URL + path, headers=headers, timeout=10)
        try:
            data = res.json()
        except Exception:
            data = {"order": {"status": f"http_{res.status_code}", "remaining_count": None, "filled_count": 0}}
        return data, res.status_code
    except Exception as e:
        if settings.VERBOSE:
            print(f"⚠️ Error getting order {order_id}: {e}")
        return None, None


def get_order_fill_status(order_id: str) -> Tuple[bool, int, int]:
    """Get fill status of an order.
    
    Returns:
        Tuple of (is_filled, filled_count, remaining_count)
    """
    if not order_id:
        return False, 0, 0
    
    data, status_code = get_order(order_id)
    if not data or status_code != 200:
        return False, 0, 0
    
    order = data.get("order") or data
    status = str(order.get("status") or "").lower()
    
    # Extract filled count
    filled_count = 0
    for key in ("filled_count", "filled_qty", "count_filled", "taker_fill_count", "maker_fill_count"):
        if key in order and order[key] is not None:
            try:
                filled_count = int(order[key])
                break
            except (ValueError, TypeError):
                pass
    
    # Extract remaining count
    remaining_count = 0
    if "remaining_count" in order and order["remaining_count"] is not None:
        try:
            remaining_count = int(order["remaining_count"])
        except (ValueError, TypeError):
            pass
    
    # Check if fully filled
    cancelled_like = {"cancelled", "canceled", "closed_cancelled", "rejected"}
    is_filled = (
        status in ("filled", "closed") and 
        remaining_count == 0 and
        filled_count > 0
    ) or (
        ("executed" in status or "filled" in status) and 
        remaining_count == 0 and
        filled_count > 0
    )
    
    return is_filled, filled_count, remaining_count


def wait_for_fill_or_cancel(
    order_id: str, 
    timeout_secs: float = 30.0,
    require_full: bool = False
) -> Tuple[str, int]:
    """Wait for an order to fill (fully or partially) or timeout and cancel it.
    
    Args:
        order_id: Order ID to monitor
        timeout_secs: Timeout in seconds
        require_full: If True, only return filled if fully filled. If False, return on any fill.
    
    Returns:
        Tuple of (status, filled_count) where status is "filled", "cancelled", "timeout", or "partial"
        and filled_count is the number of contracts filled.
    """
    if settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
        return "filled", 0  # Sim mode: assume filled

    start_time = time.time()
    
    while time.time() - start_time < timeout_secs:
        is_filled, filled_count, remaining_count = get_order_fill_status(order_id)
        
        if is_filled:
            if settings.VERBOSE:
                print(f"✅ Order fully filled: {order_id} (qty={filled_count})")
            return "filled", filled_count
        
        # Check for partial fill if not requiring full fill
        if not require_full and filled_count > 0:
            if settings.VERBOSE:
                print(f"📊 Partial fill detected: {order_id} (filled={filled_count}, remaining={remaining_count})")
            return "partial", filled_count
        
        # Check if cancelled
        data, status_code = get_order(order_id)
        if data:
            order = data.get("order") or data
            status = str(order.get("status") or "").lower()
            if status in ["cancelled", "canceled", "rejected"]:
                return "cancelled", filled_count
        
        if settings.VERBOSE:
            print(f"⌛ Waiting fill... order={order_id}, filled={filled_count}, remaining={remaining_count}, elapsed={time.time()-start_time:.1f}s")
        
        time.sleep(1.0)

    # Timeout: try to cancel remaining
    if settings.VERBOSE:
        print(f"⏳ Order timeout after {timeout_secs}s: {order_id}, attempting to cancel remaining...")
    
    # Get final status before cancelling
    is_filled, filled_count, remaining_count = get_order_fill_status(order_id)
    
    if filled_count > 0 and remaining_count > 0:
        # Partial fill occurred, cancel remaining
        try:
            cancel_path = f"/trade-api/v2/portfolio/orders/{order_id}"
            cancel_headers = kalshi_headers("DELETE", cancel_path)
            cancel_res = SESSION.delete(
                f"{settings.KALSHI_BASE_URL}{cancel_path}",
                headers=cancel_headers,
                timeout=5
            )
            if cancel_res.status_code == 200:
                if settings.VERBOSE:
                    print(f"✅ Cancelled remaining {remaining_count} contracts for order {order_id}")
                return "partial", filled_count
        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error cancelling order: {e}")
    
    if filled_count > 0:
        return "partial", filled_count
    
    return "timeout", 0