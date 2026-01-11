"""
Kalshi market data utilities.
"""

import requests
from typing import Optional, List, Dict, Any
from config import settings
from core.session import SESSION
from kalshi.auth import kalshi_headers


def format_price(price, units_hint="usd_cent"):
    """Convert Kalshi price (cents) to decimal (0-1)."""
    if price is None:
        return None
    try:
        v = float(price)
    except Exception:
        return None
    if units_hint == "usd_cent":
        v /= 100.0
    return max(0.0, min(1.0, v))


def get_kalshi_markets(event_ticker: str, force_live: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Fetch active markets for an event ticker from Kalshi."""
    path = f"/trade-api/v2/markets?event_ticker={event_ticker}"
    url = f"{settings.KALSHI_BASE_URL}{path}"
    headers = kalshi_headers("GET", path)
    try:
        res = SESSION.get(url, headers=headers, timeout=1.5)
        if res.status_code == 200:
            markets = res.json().get("markets", [])
            markets = [
                m for m in markets
                if m.get("status") == "active" and (m.get("yes_bid") or m.get("yes_ask"))
            ]
            return markets
        if res.status_code == 429:
            error_data = res.json() if res.text else {}
            print(f"❌ Kalshi fetch error 429 (rate limited) for {event_ticker}: {error_data}")
            return None
        print(f"❌ Kalshi fetch error {res.status_code} for {event_ticker}: {res.text[:120]}")
        return []
    except requests.exceptions.Timeout:
        print(f"⚠️ Kalshi fetch timeout for {event_ticker}")
        return []
    except Exception as e:
        print(f"❌ Kalshi fetch error for {event_ticker}: {e}")
        return []


def get_event_total_volume(event_ticker: str, markets: Optional[List[Dict[str, Any]]] = None) -> Optional[int]:
    """Calculate total trading volume for an event."""
    if markets is None:
        markets = get_kalshi_markets(event_ticker, force_live=True)
    if not markets:
        return None
    total_volume = sum(
        market.get("volume", 0)
        for market in markets
        if market.get("volume") is not None
    )
    return total_volume if total_volume > 0 else None


def market_yes_mid(market: Optional[Dict[str, Any]]) -> Optional[float]:
    """Calculate mid price for YES side of a market."""
    if not market:
        return None
    yb = format_price(market.get("yes_bid"))
    ya = format_price(market.get("yes_ask"))
    if yb is not None and ya is not None:
        return (yb + ya) / 2.0
    return ya if ya is not None else yb