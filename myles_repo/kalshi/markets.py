import requests
from typing import Optional
from config import settings
from core.session import SESSION


def format_price(price, units_hint="usd_cent"):
    if price is None:
        return None
    try:
        v = float(price)
    except Exception:
        return None
    if units_hint == "usd_cent":
        v /= 100.0
    return max(0.0, min(1.0, v))


def get_kalshi_markets(event_ticker, force_live: bool = False):
    url = f"{settings.KALSHI_BASE_URL}/trade-api/v2/markets?event_ticker={event_ticker}"
    try:
        res = SESSION.get(url, timeout=1.5)
        if res.status_code == 200:
            markets = res.json().get("markets", [])
            markets = [
                m for m in markets
                if m.get("status") == "active" and (m.get("yes_bid") or m.get("yes_ask"))
            ]
            return markets
        if res.status_code == 429:
            error_data = res.json() if res.text else {}
            print(f"❌ Kalshi fetch error 429 for {event_ticker}: {error_data}")
            return None
        print(f"❌ Kalshi fetch error {res.status_code} for {event_ticker}: {res.text[:120]}")
        return []
    except requests.exceptions.Timeout:
        print(f"⚠️ Kalshi fetch timeout for {event_ticker}")
        return []
    except Exception as e:
        print(f"❌ Kalshi fetch error for {event_ticker}: {e}")
        return []


def get_event_total_volume(event_ticker, markets=None):
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


def market_yes_mid(market: Optional[dict]) -> Optional[float]:
    if not market:
        return None
    yb = format_price(market.get("yes_bid"))
    ya = format_price(market.get("yes_ask"))
    if yb is not None and ya is not None:
        return (yb + ya) / 2.0
    return ya if ya is not None else yb


def label_for_market_ticker(mkt_ticker, kalshi_markets):
    m = next((x for x in kalshi_markets if x.get("ticker") == mkt_ticker), None)
    return (m.get("yes_sub_title") if m else mkt_ticker) or mkt_ticker
