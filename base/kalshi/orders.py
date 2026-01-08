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


def wait_for_fill_or_cancel(order_id: str, timeout_secs: float = 30.0) -> Tuple[bool, Optional[str]]:
    """Wait for an order to fill or timeout and cancel it."""
    if settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
        return True, None  # Sim mode: assume filled

    start_time = time.time()
    path = "/trade-api/v2/portfolio/orders"

    while time.time() - start_time < timeout_secs:
        headers = kalshi_headers("GET", path)
        try:
            res = SESSION.get(
                f"{settings.KALSHI_BASE_URL}{path}?order_id={order_id}",
                headers=headers,
                timeout=5
            )
            if res.status_code == 200:
                data = res.json()
                order = data.get("order") or data
                status = (order.get("status") or "").lower()
                if status in ["filled", "closed"]:
                    return True, status
                if status in ["cancelled", "rejected"]:
                    return False, status
            time.sleep(1.0)
        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error checking order status: {e}")
            time.sleep(1.0)

    # Timeout: try to cancel
    try:
        cancel_path = f"{path}/{order_id}"
        cancel_headers = kalshi_headers("DELETE", cancel_path)
        cancel_res = SESSION.delete(
            f"{settings.KALSHI_BASE_URL}{cancel_path}",
            headers=cancel_headers,
            timeout=5
        )
        if cancel_res.status_code == 200:
            return False, "timeout_cancelled"
    except Exception as e:
        if settings.VERBOSE:
            print(f"⚠️ Error cancelling order: {e}")

    return False, "timeout"