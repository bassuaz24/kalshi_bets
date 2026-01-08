import json
import time
import uuid
import requests
from typing import Optional, Tuple
from config import settings
from core.session import SESSION
from kalshi.auth import kalshi_headers
from kalshi.positions import get_live_positions


def prepare_kalshi_order(
    market_ticker,
    side,
    price,
    quantity,
    order_type="limit",
    action="buy",
):
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
        return response
    print("🧪 SAFE MODE: Order preview only, not submitted.")
    return payload


def safe_prepare_kalshi_order(
    market_ticker: str,
    side: str,
    price: float,
    quantity: int,
    max_total_contracts: Optional[int] = None,
    order_type: str = "limit",
    action: str = "buy",
):
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
                    f"🛡️ SAFE ORDER: Adjusting quantity from {quantity} to {allowed} on {market_ticker} "
                    f"to respect max_total_contracts={max_total_contracts} (live={live_qty})."
                )
                quantity = allowed
        except Exception as e:
            print(f"⚠️ safe_prepare_kalshi_order position check failed: {e} — proceeding with original quantity {quantity}")

    return prepare_kalshi_order(
        market_ticker=market_ticker,
        side=side,
        price=price,
        quantity=quantity,
        order_type=order_type,
        action=action,
    )


def _extract_order_id(resp) -> Tuple[Optional[str], Optional[str]]:
    try:
        d = resp.json()
    except Exception:
        return None, None

    order_obj = d.get("order") or d
    order_id = order_obj.get("order_id") or order_obj.get("id")
    client_order_id = order_obj.get("client_order_id")
    return order_id, client_order_id


def get_order(order_id: str):
    path = f"/trade-api/v2/portfolio/orders/{order_id}"
    headers = kalshi_headers("GET", path)
    try:
        r = SESSION.get(settings.KALSHI_BASE_URL + path, headers=headers, timeout=10)
        try:
            data = r.json()
        except Exception:
            data = {"order": {"status": f"http_{r.status_code}", "remaining_count": None, "filled_count": 0}}
        return data, r.status_code
    except Exception as e:
        print(f"❌ get_order error for {order_id}: {e}")
        return None, None


def _is_filled(order_json: dict,
               expected_count: Optional[int] = None,
               require_full: bool = True) -> Tuple[bool, int]:
    if not isinstance(order_json, dict):
        return False, 0

    o = order_json.get("order") or order_json
    status = str(o.get("status") or "").lower()

    for key in (
        "filled_count", "filled_qty", "count_filled",
        "taker_fill_count", "maker_fill_count", "count",
    ):
        if key in o and o[key] is not None:
            try:
                filled_qty = int(o[key])
                break
            except Exception:
                filled_qty = 0
    else:
        filled_qty = 0

    remaining = o.get("remaining_count")
    try:
        remaining = int(remaining) if remaining is not None else None
    except Exception:
        remaining = None

    cancelled_like = {"cancelled", "canceled", "closed_cancelled", "rejected"}
    if any(c in status for c in cancelled_like):
        return False, 0

    if ("executed" in status or "filled" in status) and filled_qty == 0:
        if remaining == 0 and expected_count:
            filled_qty = expected_count
        else:
            return False, 0

    if filled_qty > 0:
        if require_full:
            if remaining == 0:
                return True, filled_qty
            if expected_count and filled_qty >= expected_count:
                return True, filled_qty
            return False, filled_qty
        return True, filled_qty

    return False, 0


def cancel_order_best_effort(order_id: Optional[str] = None,
                             client_order_id: Optional[str] = None):
    if order_id:
        try:
            path = f"/trade-api/v2/portfolio/orders/{order_id}"
            headers = kalshi_headers("DELETE", path)
            r = SESSION.delete(settings.KALSHI_BASE_URL + path, headers=headers, timeout=10)
            if r.status_code < 400 and r.status_code != 404:
                print(f"🛑 Cancel via DELETE succeeded ({r.status_code})")
                return r
            print(f"↪️ DELETE cancel returned {r.status_code}: {r.text[:180]}")
        except Exception as e:
            print(f"❌ DELETE cancel error: {e}")

    if order_id:
        try:
            path = f"/trade-api/v2/portfolio/orders/{order_id}/cancel"
            headers = kalshi_headers("POST", path)
            for body in [{"order_id": order_id}, {"order_ids": [order_id]}]:
                r = SESSION.post(settings.KALSHI_BASE_URL + path, headers=headers, json=body, timeout=10)
                if r.status_code < 400 and r.status_code != 404:
                    print(f"🛑 Cancel via POST /{order_id}/cancel succeeded ({r.status_code})")
                    return r
            if r.status_code < 400 and r.status_code != 404:
                print(f"🛑 Cancel via POST /{order_id}/cancel succeeded ({r.status_code})")
                return r
            print(f"↪️ /{order_id}/cancel returned {r.status_code}: {r.text[:180]}")
        except Exception as e:
            print(f"❌ POST cancel error: {e}")

    try:
        path = "/trade-api/v2/portfolio/orders/cancel"
        headers = kalshi_headers("POST", path)
        body_candidates = []
        if order_id:
            body_candidates.append({"order_id": order_id})
            body_candidates.append({"order_ids": [order_id]})
        if client_order_id:
            body_candidates.append({"client_order_id": client_order_id})

        for body in body_candidates:
            r = requests.post(settings.KALSHI_BASE_URL + path, headers=headers, json=body, timeout=10)
            if r.status_code < 400 and r.status_code != 404:
                print(f"🛑 Cancel via /orders/cancel {body} succeeded ({r.status_code})")
                return r
            print(f"↪️ /orders/cancel {body} returned {r.status_code}: {r.text[:180]}")
    except Exception as e:
        print(f"❌ POST /orders/cancel error: {e}")

    print("⚠️ All cancel attempts failed (likely already gone or API shape different).")
    return None


def wait_for_fill_or_cancel(order_id: str,
                            client_order_id: Optional[str] = None,
                            timeout_s: int = 30,
                            poll_s: float = 1.0,
                            expected_count: Optional[int] = None,
                            require_full: bool = True,
                            verify_ticker: Optional[str] = None,
                            verify_side: Optional[str] = "yes") -> Tuple[str, int]:
    t0 = time.time()
    time.sleep(0.25)

    while time.time() - t0 < timeout_s:
        data, code = get_order(order_id)

        if code == 404:
            print("⚠️ order temporarily not found (likely just filled or settling). Retrying...")
            continue

        filled, qty = _is_filled(
            data or {},
            expected_count=expected_count,
            require_full=require_full,
        )
        if filled:
            print(f"✅ Order filled: {order_id} (qty={qty})")
            return "filled", qty

        o = (data.get("order") or {}) if data else {}
        status = o.get("status", "unknown")
        remaining = o.get("remaining_count")
        print(f"⌛ Waiting fill... status={status}, remaining={remaining}, elapsed={time.time()-t0:.1f}s")

        time.sleep(max(0.25, poll_s))

    print(f"⏳ Not filled in {timeout_s}s → sending cancel for {order_id}")
    cancel_order_best_effort(order_id=order_id, client_order_id=client_order_id)

    t1 = time.time()
    while time.time() - t1 < 5.0:
        data, code = get_order(order_id)
        filled, qty = _is_filled(
            data or {},
            expected_count=expected_count,
            require_full=require_full,
        )

        if filled or (data and "executed" in str(data).lower()):
            print(f"✅ FILLED detected after cancel window: {order_id} (qty={qty or 1})")
            return "filled", qty or 1

        if code == 404:
            try:
                lp = get_live_positions() or []
                if verify_ticker and verify_side:
                    found = next(
                        (p for p in lp if p.get("ticker") == verify_ticker and (p.get("side") or "").lower() == (verify_side or "").lower()),
                        None,
                    )
                    if found and int(found.get("contracts") or 0) > 0:
                        print("✅ Order endpoint 404 but position present — treating as filled.")
                        return "filled", expected_count or int(found.get("contracts") or 1)
            except Exception:
                pass
            print("🛑 Order not found post-cancel; treating as cancelled.")
            return "cancelled", 0

        time.sleep(0.75)

    print("🛑 Cancel completed — no fill.")
    return "cancelled", 0
